"""Skill 系统 — 领域知识/工作流模板注入 + 生命周期管理.

和 Memory 系统的关系:
- Memory: 项目事实/偏好 → 由 Agent 通过 remember 工具写入 → .kun/memory/
- Skill:  领域知识/模板 → 由开发者预置 + Agent 自动进化 → skills/ 目录

两者都是 .md + YAML frontmatter + System Prompt 注入。

v0.3.1 新增:
- SkillUsageStore: 使用量追踪 (.usage.json)
- SkillCurator: 自动生命周期管理 (active → stale → archived)
"""

from kunkun.skills.loader import Skill, SkillLoader
from kunkun.skills.usage import SkillUsageStore
from kunkun.skills.curator import SkillCurator

__all__ = ["Skill", "SkillLoader", "SkillUsageStore", "SkillCurator"]
