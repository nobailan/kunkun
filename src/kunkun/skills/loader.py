"""Skill 加载器 — 扫描 skills/ 目录，解析 SKILL.md.

格式 (和 Memory 相同的 .md + YAML frontmatter):

    ---
    name: code-review
    description: 中文代码审查 Skill
    triggers:
      - 代码审查
      - code review
      - 检查代码
    ---
    ## 审查维度
    ...

Skill 和 Memory 的区别:
- Memory: 项目运行时数据 (Agent 写入 .kun/memory/)
- Skill: 预置领域知识 (开发者写入 skills/)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """单条 Skill.

    借鉴 Claude Code skill 系统 (skills/ 目录 + SKILL.md):
    - name: kebab-case 标识
    - description: 一句话描述 (用于匹配显示)
    - triggers: 触发关键词列表 (匹配用户 prompt)
    - content: Markdown 正文 (注入到 System Prompt)
    """

    name: str
    description: str
    content: str
    triggers: list[str] = field(default_factory=list)
    file_path: str = ""

    @classmethod
    def from_md(cls, path: Path) -> "Skill | None":
        """从 SKILL.md 文件解析 Skill.

        Args:
            path: SKILL.md 文件路径

        Returns:
            Skill 对象，解析失败返回 None
        """
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read skill file %s: %s", path, e)
            return None

        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not fm_match:
            logger.warning("Skill file %s has no frontmatter", path)
            return None

        frontmatter = fm_match.group(1)
        body = fm_match.group(2).strip()

        # 简单 YAML 解析
        parsed: dict = {"triggers": []}
        in_triggers = False

        for line in frontmatter.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # triggers 列表
            if stripped == "triggers:":
                in_triggers = True
                continue

            if in_triggers:
                item_match = re.match(r"-\s+(.+)", stripped)
                if item_match:
                    parsed["triggers"].append(item_match.group(1).strip().strip('"').strip("'"))
                    continue
                else:
                    in_triggers = False

            kv_match = re.match(r"^(\w[\w_-]*):\s*(.*)", stripped)
            if kv_match:
                key = kv_match.group(1)
                value = kv_match.group(2).strip().strip('"').strip("'")
                parsed[key] = value

        return cls(
            name=parsed.get("name", path.stem),
            description=parsed.get("description", ""),
            content=body,
            triggers=parsed.get("triggers", []),
            file_path=str(path),
        )


class SkillLoader:
    """Skill 加载与匹配.

    借鉴 Claude Code skill 系统:
    - load(): 扫描 skills/ 目录加载全部 Skill
    - match(): 按 trigger 关键词匹配用户 prompt
    - inject(): 格式化匹配的 Skill 注入 System Prompt

    Attributes:
        skill_dir: Skill 目录路径
        max_skills: 单次注入最大 Skill 数 (默认 3)
    """

    MAX_SKILLS = 3

    def __init__(self, skill_dir: str = "skills", usage_store=None):
        self.skill_dir = Path(skill_dir)
        self._skills: list[Skill] = []
        self._index: dict[str, Skill] = {}
        self._usage = usage_store  # SkillUsageStore, injected by AgentLoop

    @property
    def skills(self) -> list[Skill]:
        return self._skills

    @property
    def count(self) -> int:
        return len(self._skills)

    # ─── 加载 ───────────────────────────────────

    def load(self) -> list[Skill]:
        """扫描 skills/ 目录，加载所有 SKILL.md 文件.

        Returns:
            加载的 Skill 列表
        """
        if not self.skill_dir.is_dir():
            logger.debug("Skill directory not found: %s", self.skill_dir)
            return []

        self._skills = []
        self._index = {}

        # 递归扫描所有 SKILL.md
        for md_file in sorted(self.skill_dir.rglob("SKILL.md")):
            skill = Skill.from_md(md_file)
            if skill:
                self._skills.append(skill)
                self._index[skill.name] = skill

        logger.info("Loaded %d skills from %s", len(self._skills), self.skill_dir)
        return self._skills

    # ─── 匹配 ───────────────────────────────────

    def match(self, prompt: str) -> list[Skill]:
        """按 trigger 关键词匹配用户 prompt.

        策略:
        1. 遍历所有 Skill 的 triggers 列表
        2. 任意 trigger 出现在 prompt 中 → 匹配
        3. 按匹配得分排序 (触发词越多得分越高)
        4. 取前 MAX_SKILLS 条

        Args:
            prompt: 用户输入

        Returns:
            匹配到的 Skill 列表 (按相关性降序)
        """
        if not self._skills:
            self.load()

        if not prompt or not self._skills:
            return []

        prompt_lower = prompt.lower()
        scored: list[tuple[Skill, int]] = []

        for skill in self._skills:
            score = 0
            for trigger in skill.triggers:
                if trigger.lower() in prompt_lower:
                    score += 1

            if score > 0:
                scored.append((skill, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        result = [s for s, _ in scored[:self.MAX_SKILLS]]
        if result:
            logger.debug(
                "Matched %d skills for prompt: %s",
                len(result), prompt[:80],
            )
            # v0.3.1: 记录使用量
            if self._usage:
                for skill in result:
                    self._usage.bump_load(skill.name)

        return result

    # ─── 注入 ───────────────────────────────────

    def inject(self, prompt: str, current_prompt: str = "") -> str:
        """将匹配的 Skill 元数据（name + description + triggers）注入 System Prompt.

        不注入全文——Agent 自行判断是否需要调用 skill_load 工具获取完整内容。
        和 Memory 的元数据索引策略一致。

        Args:
            prompt: 用户输入 (用于匹配)
            current_prompt: 当前 System Prompt (会在末尾追加 Skill 索引)

        Returns:
            注入 Skill 索引后的 System Prompt
        """
        matched = self.match(prompt)
        if not matched:
            return current_prompt

        lines = [
            "\n## 可用 Skill 索引",
            f"共匹配 {len(matched)} 个。当 Skill 内容与当前任务相关时，用 skill_load 工具获取全文。",
            "",
        ]
        for skill in matched:
            triggers_str = ", ".join(skill.triggers[:5])
            lines.append(f"- **{skill.name}**: {skill.description}（触发词: {triggers_str}）")

        lines.append("")

        return current_prompt + "\n".join(lines)

    def get_full_text(self, name: str) -> str | None:
        """获取 Skill 全文内容（供 skill_load 工具调用）."""
        skill = self._index.get(name)
        if not skill:
            return None
        return skill.content

    # ─── 查询 ───────────────────────────────────

    def get(self, name: str) -> Skill | None:
        """按名称获取 Skill."""
        return self._index.get(name)

    def list_names(self) -> list[str]:
        """列出所有 Skill 名称."""
        return [s.name for s in self._skills]

    def reload(self) -> list[Skill]:
        """重新加载."""
        self._skills = []
        self._index = {}
        return self.load()
