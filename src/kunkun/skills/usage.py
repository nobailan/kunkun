"""Skill 使用量追踪 — 记录每个 Skill 的加载/查看/修补次数.

借鉴 Hermes tools/skill_usage.py:
- .usage.json 文件记录使用统计
- bump_load() — Skill 被注入 System Prompt 时
- bump_patch() — Skill 被 background_review 修补时
- bump_view() — Skill 被 manual 查看时
- 用于 Curator 判断生命周期 (active/stale/archive)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Skill 生命周期状态
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"
_VALID_STATES = {STATE_ACTIVE, STATE_STALE, STATE_ARCHIVED}

# 默认过期阈值
DEFAULT_STALE_AFTER_DAYS = 30
DEFAULT_ARCHIVE_AFTER_DAYS = 90


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _empty_record() -> dict[str, Any]:
    return {
        "load_count": 0,
        "patch_count": 0,
        "view_count": 0,
        "last_loaded_at": None,
        "last_patched_at": None,
        "last_viewed_at": None,
        "created_at": _now_iso(),
        "state": STATE_ACTIVE,
        "pinned": False,
        "archived_at": None,
    }


# ─── Usage Store ────────────────────────────────────────


class SkillUsageStore:
    """Skill 使用量持久化存储.

    Attributes:
        skill_dir: skills/ 目录路径
    """

    def __init__(self, skill_dir: str = "skills"):
        self.skill_dir = Path(skill_dir)

    def _usage_file(self) -> Path:
        return self.skill_dir / ".usage.json"

    def load(self) -> dict[str, dict[str, Any]]:
        """读取 .usage.json."""
        path = self._usage_file()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}

    def save(self, data: dict[str, dict[str, Any]]) -> None:
        """原子写入 .usage.json."""
        path = self._usage_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".usage_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.debug("Failed to write .usage.json: %s", e)

    def get(self, skill_name: str) -> dict[str, Any]:
        """获取单个 Skill 的使用记录，不存在时返回空记录."""
        data = self.load()
        rec = data.get(skill_name)
        if not isinstance(rec, dict):
            return _empty_record()
        # 回填缺失字段
        base = _empty_record()
        for k, v in base.items():
            rec.setdefault(k, v)
        return rec

    def _mutate(self, skill_name: str, fn) -> None:
        """读取 → 修改 → 写入."""
        if not skill_name:
            return
        try:
            data = self.load()
            rec = data.get(skill_name)
            if not isinstance(rec, dict):
                rec = _empty_record()
            fn(rec)
            data[skill_name] = rec
            self.save(data)
        except Exception as e:
            logger.debug("UsageStore._mutate(%s) failed: %s", skill_name, e)

    # ─── Public API ──────────────────────────────────

    def bump_load(self, skill_name: str) -> None:
        """Skill 被注入 System Prompt."""
        def fn(rec):
            rec["load_count"] = int(rec.get("load_count") or 0) + 1
            rec["last_loaded_at"] = _now_iso()
        self._mutate(skill_name, fn)

    def bump_patch(self, skill_name: str) -> None:
        """Skill 被 background_review 修补."""
        def fn(rec):
            rec["patch_count"] = int(rec.get("patch_count") or 0) + 1
            rec["last_patched_at"] = _now_iso()
        self._mutate(skill_name, fn)

    def bump_view(self, skill_name: str) -> None:
        """Skill 被 manual 查看."""
        def fn(rec):
            rec["view_count"] = int(rec.get("view_count") or 0) + 1
            rec["last_viewed_at"] = _now_iso()
        self._mutate(skill_name, fn)

    def mark_created(self, skill_name: str) -> None:
        """标记 Skill 为 Agent 创建."""
        def fn(rec):
            rec["created_by"] = "agent"
        self._mutate(skill_name, fn)

    def set_state(self, skill_name: str, state: str) -> bool:
        """设置生命周期状态. 返回是否成功."""
        if state not in _VALID_STATES:
            return False
        def fn(rec):
            rec["state"] = state
            if state == STATE_ARCHIVED:
                rec["archived_at"] = _now_iso()
            elif state == STATE_ACTIVE:
                rec["archived_at"] = None
        self._mutate(skill_name, fn)
        return True

    def set_pinned(self, skill_name: str, pinned: bool) -> None:
        def fn(rec):
            rec["pinned"] = bool(pinned)
        self._mutate(skill_name, fn)

    def forget(self, skill_name: str) -> None:
        """删除 Skill 的使用记录."""
        if not skill_name:
            return
        try:
            data = self.load()
            if skill_name in data:
                del data[skill_name]
                self.save(data)
        except Exception as e:
            logger.debug("UsageStore.forget(%s) failed: %s", skill_name, e)

    def latest_activity(self, record: dict) -> datetime | None:
        """返回最近一次活动时间."""
        latest: datetime | None = None
        for key in ("last_loaded_at", "last_patched_at", "last_viewed_at"):
            dt = _parse_iso(record.get(key))
            if dt is None:
                continue
            if latest is None or dt > latest:
                latest = dt
        return latest
