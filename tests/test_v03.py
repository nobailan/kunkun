"""Kun v0.3 单元测试 — Skill 系统.

Run: python -m pytest tests/test_v03.py -v -k "not trio"
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ═══════════════════════════════════════════════════════════
# 1. Skill 解析测试
# ═══════════════════════════════════════════════════════════

class TestSkillParsing:

    def test_parse_valid_skill(self):
        from kunkun.skills.loader import Skill

        text = """---
name: test-skill
description: A test skill for unit testing
triggers:
  - test
  - 测试
  - unittest
---

## Test Skill Content

This is the skill body.
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(text, encoding="utf-8")

            skill = Skill.from_md(path)
            assert skill is not None
            assert skill.name == "test-skill"
            assert skill.description == "A test skill for unit testing"
            assert skill.triggers == ["test", "测试", "unittest"]
            assert "Test Skill Content" in skill.content

    def test_parse_skill_without_triggers(self):
        from kunkun.skills.loader import Skill

        text = """---
name: no-trigger-skill
description: A skill without triggers
---

Content here.
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(text, encoding="utf-8")

            skill = Skill.from_md(path)
            assert skill is not None
            assert skill.name == "no-trigger-skill"
            assert skill.triggers == []

    def test_parse_invalid_skill_no_frontmatter(self):
        from kunkun.skills.loader import Skill

        text = "Just content without frontmatter"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(text, encoding="utf-8")

            skill = Skill.from_md(path)
            assert skill is None

    def test_parse_missing_file(self):
        from kunkun.skills.loader import Skill

        skill = Skill.from_md(Path("/nonexistent/SKILL.md"))
        assert skill is None


# ═══════════════════════════════════════════════════════════
# 2. Skill 加载测试
# ═══════════════════════════════════════════════════════════

class TestSkillLoader:

    def _create_skill(self, parent_dir: str, name: str, triggers: list[str]) -> Path:
        skill_dir = Path(parent_dir) / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        triggers_yaml = "\n".join(f"  - {t}" for t in triggers)
        text = f"""---
name: {name}
description: Skill {name}
triggers:
{triggers_yaml}
---

Content for {name}.
"""
        path.write_text(text, encoding="utf-8")
        return path

    def test_load_from_directory(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "skill-a", ["python", "code"])
            self._create_skill(tmp, "skill-b", ["git", "commit"])
            self._create_skill(tmp, "skill-c", ["review", "审查"])

            loader = SkillLoader(skill_dir=tmp)
            skills = loader.load()

            assert len(skills) == 3
            names = {s.name for s in skills}
            assert names == {"skill-a", "skill-b", "skill-c"}

    def test_load_empty_directory(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            loader = SkillLoader(skill_dir=tmp)
            skills = loader.load()
            assert skills == []

    def test_load_nonexistent_directory(self):
        from kunkun.skills.loader import SkillLoader

        loader = SkillLoader(skill_dir="/nonexistent/path")
        skills = loader.load()
        assert skills == []

    def test_count(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "s1", ["a"])
            self._create_skill(tmp, "s2", ["b"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()
            assert loader.count == 2

    def test_list_names(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "alpha", ["x"])
            self._create_skill(tmp, "beta", ["y"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()
            assert set(loader.list_names()) == {"alpha", "beta"}


# ═══════════════════════════════════════════════════════════
# 3. Skill 匹配测试
# ═══════════════════════════════════════════════════════════

class TestSkillMatching:

    def _create_skill(self, parent_dir: str, name: str, triggers: list[str]) -> Path:
        skill_dir = Path(parent_dir) / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        triggers_yaml = "\n".join(f"  - {t}" for t in triggers)
        text = f"""---
name: {name}
description: Skill {name}
triggers:
{triggers_yaml}
---

Content for {name}.
"""
        path.write_text(text, encoding="utf-8")
        return path

    def test_match_single_trigger(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "code-review", ["代码审查", "review", "检查代码"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()

            matched = loader.match("帮我做一下代码审查")
            assert len(matched) == 1
            assert matched[0].name == "code-review"

    def test_match_multiple_triggers(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "python", ["Python", "python"])
            self._create_skill(tmp, "review", ["审查", "review"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()

            matched = loader.match("帮我审查这段 Python 代码")
            assert len(matched) == 2

    def test_match_case_insensitive(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "git", ["git", "commit"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()

            matched = loader.match("帮我写一个 Git 提交信息")
            assert len(matched) == 1
            assert matched[0].name == "git"

    def test_match_score_ordering(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "best-match", ["python", "review", "代码"])
            self._create_skill(tmp, "partial-match", ["review"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()

            matched = loader.match("帮我 review 一下 Python 代码")
            assert len(matched) >= 1
            # best-match 应该排在前面 (更多 trigger 命中)
            assert matched[0].name == "best-match"

    def test_no_match(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "git", ["git", "commit"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()

            matched = loader.match("今天天气怎么样")
            assert matched == []

    def test_max_skills_limit(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                self._create_skill(tmp, f"skill-{i}", ["python"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()

            matched = loader.match("python 代码")
            assert len(matched) <= loader.MAX_SKILLS


# ═══════════════════════════════════════════════════════════
# 4. Skill 注入测试
# ═══════════════════════════════════════════════════════════

class TestSkillInjection:

    def _create_skill(self, parent_dir: str, name: str, triggers: list[str]) -> Path:
        skill_dir = Path(parent_dir) / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        triggers_yaml = "\n".join(f"  - {t}" for t in triggers)
        text = f"""---
name: {name}
description: Skill {name}
triggers:
{triggers_yaml}
---

## {name} Content

This is the skill body for {name}.
"""
        path.write_text(text, encoding="utf-8")
        return path

    def test_inject_appends_to_prompt(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "tester", ["test"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()

            base_prompt = "You are a helpful assistant."
            result = loader.inject("run some test", base_prompt)

            assert base_prompt in result
            assert "可用 Skill 索引" in result
            assert "tester" in result  # name in metadata
            assert "tester Content" not in result  # full content NOT injected

    def test_inject_no_match_returns_unchanged(self):
        from kunkun.skills.loader import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            self._create_skill(tmp, "git", ["git"])

            loader = SkillLoader(skill_dir=tmp)
            loader.load()

            base_prompt = "You are a helpful assistant."
            result = loader.inject("今天天气怎么样", base_prompt)

            assert result == base_prompt
            assert "可用 Skill 索引" not in result


# ═══════════════════════════════════════════════════════════
# 5. 集成测试
# ═══════════════════════════════════════════════════════════

class TestIntegration:

    def test_prebuilt_skills_exist(self):
        """验证预置的 3 个 SKILL.md 文件存在且可解析."""
        from kunkun.skills.loader import SkillLoader

        # 使用项目实际路径
        project_root = Path(__file__).parent.parent
        skills_dir = project_root / "skills"

        loader = SkillLoader(skill_dir=str(skills_dir))
        skills = loader.load()

        assert len(skills) >= 3, f"Expected >=3 prebuilt skills, got {len(skills)}"
        names = {s.name for s in skills}
        assert "code-review" in names
        assert "python-project" in names
        assert "git-conventions" in names

        # 每个预置 Skill 都应该有 triggers
        for skill in skills:
            assert skill.triggers, f"Skill '{skill.name}' has no triggers"
            assert skill.content, f"Skill '{skill.name}' has no content"

    def test_agent_loop_has_skill_loader(self):
        """验证 AgentLoop 初始化包含 SkillLoader."""
        from kunkun.core.state import HarnessConfig
        from kunkun.core.agent_loop import AgentLoop

        config = HarnessConfig()
        agent = AgentLoop(config)

        assert agent.skills is not None
        assert hasattr(agent.skills, 'load')
        assert hasattr(agent.skills, 'match')
        assert hasattr(agent.skills, 'inject')

    def test_memory_and_skill_independent(self):
        """验证 Memory 和 Skill 是两个独立系统."""
        from kunkun.core.state import HarnessConfig
        from kunkun.core.agent_loop import AgentLoop

        config = HarnessConfig()
        agent = AgentLoop(config)

        # 它们是不同的实例
        assert agent.memory is not agent.skills
        # 不同的目录
        assert agent.memory.memory_dir != agent.skills.skill_dir


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
