"""Kun v0.3.1 测试 — 自动记忆提取 + Skill 进化.

Run: python -m pytest tests/test_learning.py -v -k "not trio"
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ═══════════════════════════════════════════════════════════
# 1. BackgroundReviewer 测试
# ═══════════════════════════════════════════════════════════

class TestBackgroundReviewer:

    def _make_mock_llm(self):
        from kunkun.core.state import HarnessConfig
        llm = MagicMock()
        llm.config = HarnessConfig(api_key="test-key")
        return llm

    def _make_reviewer(self, memory_dir: str, skill_dir: str):
        from kunkun.core.background_review import BackgroundReviewer
        from kunkun.memory.manager import MemoryManager
        from kunkun.skills.loader import SkillLoader

        llm = self._make_mock_llm()
        memory = MemoryManager(memory_dir=memory_dir)
        skills = SkillLoader(skill_dir=skill_dir)
        return BackgroundReviewer(memory, skills, llm)

    def test_parse_memory_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self._make_reviewer(
                memory_dir=f"{tmp}/memory",
                skill_dir=f"{tmp}/skills",
            )

            response = '''```json
{
  "memories": [
    {"name": "user-pref-tabs", "description": "缩进偏好", "content": "用户使用 4 空格缩进，不要用 tab"},
    {"name": "project-stack", "description": "技术栈", "content": "项目使用 Python 3.11 + FastAPI"}
  ],
  "nothing_worth_saving": false,
  "skill_updates": [],
  "nothing_to_update": true
}
```'''
            result = reviewer._parse_and_apply(response)

            assert result["memories_saved"] == 2
            assert result["skills_updated"] == 0

            # 验证记忆文件确实写入了
            reviewer.memory.load()
            names = {m.name for m in reviewer.memory.memories}
            assert "user-pref-tabs" in names
            assert "project-stack" in names

    def test_parse_skill_create_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = f"{tmp}/skills"
            Path(skill_dir).mkdir(parents=True)

            reviewer = self._make_reviewer(
                memory_dir=f"{tmp}/memory",
                skill_dir=skill_dir,
            )

            response = '''```json
{
  "memories": [],
  "nothing_worth_saving": true,
  "skill_updates": [
    {
      "skill_name": "debug-patterns",
      "action": "create",
      "description": "调试模式集合",
      "triggers": ["debug", "调试", "排查"],
      "content": "## 调试检查清单\\n\\n1. 检查日志级别\\n2. 确认环境变量"
    }
  ],
  "nothing_to_update": false
}
```'''
            result = reviewer._parse_and_apply(response)

            assert result["memories_saved"] == 0
            assert result["skills_updated"] == 1

            # 验证 Skill 目录和文件创建了
            skill_md = Path(skill_dir) / "debug-patterns" / "SKILL.md"
            assert skill_md.is_file()
            content = skill_md.read_text(encoding="utf-8")
            assert "debug-patterns" in content
            assert "debug" in content or "debug" in content

    def test_parse_skill_patch_json(self):
        """修补已有 Skill."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = f"{tmp}/skills"
            # 先创建一个已有 Skill
            existing_dir = Path(skill_dir) / "existing-skill"
            existing_dir.mkdir(parents=True)
            (existing_dir / "SKILL.md").write_text("""---
name: existing-skill
description: 已有 Skill
triggers:
  - existing
---

原始内容。
""", encoding="utf-8")

            reviewer = self._make_reviewer(
                memory_dir=f"{tmp}/memory",
                skill_dir=skill_dir,
            )

            response = '''```json
{
  "memories": [],
  "nothing_worth_saving": true,
  "skill_updates": [
    {
      "skill_name": "existing-skill",
      "action": "patch",
      "description": "补充更新",
      "content": "补充: 还需要检查数据库连接"
    }
  ],
  "nothing_to_update": false
}
```'''
            result = reviewer._parse_and_apply(response)

            assert result["skills_updated"] == 1
            content = (Path(skill_dir) / "existing-skill" / "SKILL.md").read_text(encoding="utf-8")
            assert "existing-skill" in content

    def test_parse_nothing_to_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self._make_reviewer(
                memory_dir=f"{tmp}/memory",
                skill_dir=f"{tmp}/skills",
            )

            response = '{"memories": [], "nothing_worth_saving": true, "skill_updates": [], "nothing_to_update": true}'
            result = reviewer._parse_and_apply(response)

            assert result["memories_saved"] == 0
            assert result["skills_updated"] == 0

    def test_parse_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self._make_reviewer(
                memory_dir=f"{tmp}/memory",
                skill_dir=f"{tmp}/skills",
            )

            response = "这不是 JSON，只是一段普通文字"
            result = reviewer._parse_and_apply(response)

            assert result["memories_saved"] == 0
            assert result["skills_updated"] == 0

    def test_build_combined_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = self._make_reviewer(
                memory_dir=f"{tmp}/memory",
                skill_dir=f"{tmp}/skills",
            )

            prompt = reviewer._build_combined_prompt(
                "用户说: 用 tab 缩进",
                "Agent: 好的"
            )

            assert "Task 1: Memory" in prompt
            assert "Task 2: Skill" in prompt
            assert "用 tab 缩进" in prompt
            assert "Agent: 好的" in prompt


# ═══════════════════════════════════════════════════════════
# 2. 集成测试
# ═══════════════════════════════════════════════════════════

class TestIntegration:

    def test_agent_loop_has_reviewer(self):
        """验证 AgentLoop 包含 BackgroundReviewer."""
        from kunkun.core.state import HarnessConfig
        from kunkun.core.agent_loop import AgentLoop

        config = HarnessConfig()
        agent = AgentLoop(config)

        assert agent.reviewer is not None
        assert hasattr(agent.reviewer, 'review_turn')
        assert hasattr(agent.reviewer, 'schedule_review')
        assert hasattr(agent.reviewer, 'wait_pending')

    def test_reviewer_uses_light_model(self):
        """验证 BackgroundReviewer 使用轻模型."""
        from kunkun.core.state import HarnessConfig
        from kunkun.core.agent_loop import AgentLoop

        config = HarnessConfig()
        agent = AgentLoop(config)

        assert agent.reviewer.light_model == config.light_model
        assert "flash" in agent.reviewer.light_model

    def test_memory_and_skill_shared_with_reviewer(self):
        """验证 BackgroundReviewer 共享 MemoryManager 和 SkillLoader."""
        from kunkun.core.state import HarnessConfig
        from kunkun.core.agent_loop import AgentLoop

        config = HarnessConfig()
        agent = AgentLoop(config)

        assert agent.reviewer.memory is agent.memory
        assert agent.reviewer.skills is agent.skills


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
