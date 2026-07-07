"""Skill Curator — 自动生命周期管理.

借鉴 Hermes agent/curator.py:
- 基于 usage.json 中的活动时间戳判断 Skill 是否过期
- active → stale (stale_after_days 未使用)
- stale → archived (archive_after_days 未使用)
- pinned 状态跳过自动转换
- 只在 Agent 创建或本地手动创建的 Skill 上生效
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kunkun.skills.usage import (
    SkillUsageStore,
    STATE_ACTIVE,
    STATE_STALE,
    STATE_ARCHIVED,
    DEFAULT_STALE_AFTER_DAYS,
    DEFAULT_ARCHIVE_AFTER_DAYS,
)

logger = logging.getLogger(__name__)

# 内置 Skill 不会被 Curator 管理
PROTECTED_SKILLS = {"code-review", "python-project", "git-conventions"}


class SkillCurator:
    """Skill 生命周期管理器.

    Attributes:
        skill_dir: skills/ 目录路径
        usage: SkillUsageStore 实例
        stale_days: 未使用多少天后标记为 stale (默认 30)
        archive_days: 未使用多少天后归档 (默认 90)
    """

    def __init__(
        self,
        skill_dir: str = "skills",
        stale_days: int = DEFAULT_STALE_AFTER_DAYS,
        archive_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
    ):
        self.skill_dir = Path(skill_dir)
        self.usage = SkillUsageStore(skill_dir=skill_dir)
        self.stale_days = stale_days
        self.archive_days = archive_days
        self._checked_at: str | None = None

    # ─── 生命周期扫描 ───────────────────────────────

    def scan(self) -> dict:
        """扫描所有 Skill，自动转换生命周期状态.

        规则:
        1. protected 的 Skill (预置) — 跳过
        2. pinned 的 Skill — 跳过
        3. 最近活动 > archive_days → archived
        4. 最近活动 > stale_days → stale

        Returns:
            {"transitions": [{"name": ..., "from": ..., "to": ...}], ...}
        """
        transitions: list[dict] = []
        now = datetime.now(timezone.utc)

        if not self.skill_dir.is_dir():
            return {"transitions": [], "checked_at": now.isoformat()}

        # 收集所有 Skill (通过扫描 SKILL.md)
        all_skills: set[str] = set()
        for md_file in self.skill_dir.rglob("SKILL.md"):
            if ".archive" in md_file.parts:
                continue
            name = self._read_skill_name(md_file)
            if name:
                all_skills.add(name)

        for skill_name in all_skills:
            if skill_name in PROTECTED_SKILLS:
                continue

            record = self.usage.get(skill_name)
            if record.get("pinned"):
                continue

            current_state = record.get("state", STATE_ACTIVE)
            if current_state == STATE_ARCHIVED:
                continue

            activity = self.usage.latest_activity(record)

            # 无记录 — 跳过 (等待首次活动)
            if activity is None:
                continue

            days_since_activity = (now - activity).days

            if days_since_activity > self.archive_days:
                old_state = current_state
                self.usage.set_state(skill_name, STATE_ARCHIVED)
                transitions.append({
                    "name": skill_name,
                    "from": old_state,
                    "to": STATE_ARCHIVED,
                    "days_inactive": days_since_activity,
                })
                logger.info("Curator: archived %s (%d days inactive)", skill_name, days_since_activity)

            elif days_since_activity > self.stale_days and current_state == STATE_ACTIVE:
                self.usage.set_state(skill_name, STATE_STALE)
                transitions.append({
                    "name": skill_name,
                    "from": STATE_ACTIVE,
                    "to": STATE_STALE,
                    "days_inactive": days_since_activity,
                })
                logger.debug("Curator: marked %s as stale (%d days inactive)", skill_name, days_since_activity)

        self._checked_at = now.isoformat()
        return {"transitions": transitions, "checked_at": self._checked_at}

    # ─── 查询 ───────────────────────────────────────

    def report(self) -> list[dict]:
        """返回所有 Skill 的状态报告."""
        if not self.skill_dir.is_dir():
            return []

        rows: list[dict] = []
        for md_file in sorted(self.skill_dir.rglob("SKILL.md")):
            if ".archive" in md_file.parts:
                continue
            name = self._read_skill_name(md_file)
            if not name:
                continue

            record = self.usage.get(name)
            activity = self.usage.latest_activity(record)
            rows.append({
                "name": name,
                "state": record.get("state", STATE_ACTIVE),
                "pinned": record.get("pinned", False),
                "load_count": record.get("load_count", 0),
                "patch_count": record.get("patch_count", 0),
                "protected": name in PROTECTED_SKILLS,
                "last_activity": activity.isoformat() if activity else None,
            })

        return sorted(rows, key=lambda r: r["name"])

    def pin(self, skill_name: str) -> bool:
        """固定 Skill (跳过自动转换)."""
        if skill_name not in self._list_managed():
            return False
        self.usage.set_pinned(skill_name, True)
        return True

    def unpin(self, skill_name: str) -> bool:
        """取消固定."""
        if skill_name not in self._list_managed():
            return False
        self.usage.set_pinned(skill_name, False)
        return True

    # ─── 内部 ───────────────────────────────────────

    def _list_managed(self) -> set[str]:
        """列出 Curator 可管理的 Skill (非 protected)."""
        if not self.skill_dir.is_dir():
            return set()
        names: set[str] = set()
        for md_file in self.skill_dir.rglob("SKILL.md"):
            if ".archive" in md_file.parts:
                continue
            name = self._read_skill_name(md_file)
            if name and name not in PROTECTED_SKILLS:
                names.add(name)
        return names

    @staticmethod
    def _read_skill_name(md_file: Path) -> str | None:
        """从 SKILL.md 的 YAML frontmatter 读取 name 字段."""
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            return None

        in_fm = False
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped == "---":
                if in_fm:
                    break
                in_fm = True
                continue
            if in_fm and stripped.startswith("name:"):
                return stripped.split(":", 1)[1].strip().strip("\"'")
        return None
