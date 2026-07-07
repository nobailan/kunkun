"""Kun v0.4.0 测试 — Grep + Edit 工具.

Run: python -m pytest tests/test_v04.py -v -k "not trio"
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_ctx(workspace: str):
    from kunkun.tools.decorators import ToolUseContext
    return ToolUseContext(workspace=workspace)


# ═══════════════════════════════════════════════════════════
# 1. Grep 工具测试
# ═══════════════════════════════════════════════════════════

class TestGrep:

    @pytest.mark.anyio
    async def test_basic_search(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("def hello():\n    print('hi')\n", encoding="utf-8")
            (Path(tmp) / "b.py").write_text("def world():\n    print('hey')\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await grep_tool(GrepInput(pattern="def hello", path="."), ctx)

            assert "def hello" in result.data
            assert "a.py" in result.data
            assert not result.is_error

    @pytest.mark.anyio
    async def test_regex_pattern(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test.py").write_text("TODO: fix bug\nFIXME: cleanup\nnormal line\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await grep_tool(GrepInput(pattern=r"TODO|FIXME", path=".", context_lines=0), ctx)

            assert "TODO" in result.data
            assert "FIXME" in result.data
            assert "▶" in result.data  # match marker

    @pytest.mark.anyio
    async def test_case_insensitive(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "x.py").write_text("Hello World\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            r1 = await grep_tool(GrepInput(pattern="hello", path="."), ctx)
            assert "未找到" in r1.data

            r2 = await grep_tool(GrepInput(pattern="hello", path=".", case_insensitive=True), ctx)
            assert "Hello World" in r2.data

    @pytest.mark.anyio
    async def test_files_with_matches_mode(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("TODO: a\n", encoding="utf-8")
            (Path(tmp) / "b.py").write_text("TODO: b\n", encoding="utf-8")
            (Path(tmp) / "c.txt").write_text("no match\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await grep_tool(GrepInput(pattern="TODO", path=".", output_mode="files_with_matches"), ctx)

            assert "a.py" in result.data
            assert "b.py" in result.data
            assert "c.txt" not in result.data

    @pytest.mark.anyio
    async def test_count_mode(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "f.py").write_text("TODO: one\nTODO: two\nok\nTODO: three\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await grep_tool(GrepInput(pattern="TODO", path=".", output_mode="count"), ctx)

            assert "3" in result.data

    @pytest.mark.anyio
    async def test_glob_filter(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("TODO\n", encoding="utf-8")
            (Path(tmp) / "b.js").write_text("TODO\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await grep_tool(GrepInput(pattern="TODO", path=".", glob="*.py"), ctx)

            assert "a.py" in result.data
            assert "b.js" not in result.data

    @pytest.mark.anyio
    async def test_no_match(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "x.py").write_text("hello\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await grep_tool(GrepInput(pattern="NONEXISTENT", path="."), ctx)

            assert "未找到" in result.data

    @pytest.mark.anyio
    async def test_invalid_regex(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        with tempfile.TemporaryDirectory() as tmp:
            ctx = _make_ctx(tmp)
            result = await grep_tool(GrepInput(pattern="[unclosed", path="."), ctx)

            assert result.is_error
            assert "正则表达式错误" in result.data

    @pytest.mark.anyio
    async def test_nonexistent_path(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        ctx = _make_ctx("/tmp")
        result = await grep_tool(GrepInput(pattern="x", path="/nonexistent/path"), ctx)

        assert result.is_error
        assert "不存在" in result.data

    @pytest.mark.anyio
    async def test_context_lines(self):
        from kunkun.tools.grep_tool import grep_tool, GrepInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "f.py").write_text("line1\nline2\nMATCH\nline4\nline5\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await grep_tool(GrepInput(pattern="MATCH", path=".", context_lines=1), ctx)

            assert "line2" in result.data
            assert "line4" in result.data


# ═══════════════════════════════════════════════════════════
# 2. Edit 工具测试
# ═══════════════════════════════════════════════════════════

class TestEdit:

    @pytest.mark.anyio
    async def test_single_replace(self):
        from kunkun.tools.edit_tool import edit_tool, EditInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test.py").write_text("x = 1\ny = 2\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await edit_tool(EditInput(
                file_path="test.py",
                old_string="x = 1",
                new_string="x = 42",
            ), ctx)

            assert not result.is_error
            assert "已修改" in result.data

            content = (Path(tmp) / "test.py").read_text(encoding="utf-8")
            assert "x = 42" in content
            assert "x = 1" not in content

    @pytest.mark.anyio
    async def test_replace_all(self):
        from kunkun.tools.edit_tool import edit_tool, EditInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test.py").write_text("TODO: a\nTODO: b\nTODO: c\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await edit_tool(EditInput(
                file_path="test.py",
                old_string="TODO",
                new_string="DONE",
                replace_all=True,
            ), ctx)

            assert not result.is_error
            content = (Path(tmp) / "test.py").read_text(encoding="utf-8")
            assert content.count("DONE") == 3
            assert "TODO" not in content

    @pytest.mark.anyio
    async def test_multiple_matches_without_replace_all(self):
        from kunkun.tools.edit_tool import edit_tool, EditInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test.py").write_text("TODO: a\nTODO: b\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await edit_tool(EditInput(
                file_path="test.py",
                old_string="TODO",
                new_string="DONE",
            ), ctx)

            assert result.is_error
            assert "2 处匹配" in result.data

    @pytest.mark.anyio
    async def test_old_string_not_found(self):
        from kunkun.tools.edit_tool import edit_tool, EditInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test.py").write_text("hello\n", encoding="utf-8")

            ctx = _make_ctx(tmp)
            result = await edit_tool(EditInput(
                file_path="test.py",
                old_string="NONEXISTENT",
                new_string="world",
            ), ctx)

            assert result.is_error
            assert "未找到匹配" in result.data

    @pytest.mark.anyio
    async def test_create_new_file(self):
        from kunkun.tools.edit_tool import edit_tool, EditInput

        with tempfile.TemporaryDirectory() as tmp:
            ctx = _make_ctx(tmp)
            result = await edit_tool(EditInput(
                file_path="new_file.py",
                old_string="",
                new_string="print('hello')",
            ), ctx)

            assert not result.is_error
            assert "已创建" in result.data
            assert (Path(tmp) / "new_file.py").read_text() == "print('hello')"

    @pytest.mark.anyio
    async def test_multiline_replace(self):
        from kunkun.tools.edit_tool import edit_tool, EditInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test.py").write_text(
                "def old_func():\n    pass\n\nother\n",
                encoding="utf-8",
            )

            ctx = _make_ctx(tmp)
            result = await edit_tool(EditInput(
                file_path="test.py",
                old_string="def old_func():\n    pass",
                new_string="def new_func():\n    return 42",
            ), ctx)

            assert not result.is_error
            content = (Path(tmp) / "test.py").read_text(encoding="utf-8")
            assert "def new_func()" in content
            assert "return 42" in content
            assert "def old_func()" not in content

    @pytest.mark.anyio
    async def test_preserves_other_content(self):
        from kunkun.tools.edit_tool import edit_tool, EditInput

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test.py").write_text(
                "import os\n\nx = 1\ny = 2\n\nprint(x)\n",
                encoding="utf-8",
            )

            ctx = _make_ctx(tmp)
            await edit_tool(EditInput(
                file_path="test.py",
                old_string="x = 1",
                new_string="x = 42",
            ), ctx)

            content = (Path(tmp) / "test.py").read_text(encoding="utf-8")
            assert "import os" in content
            assert "y = 2" in content
            assert "print(x)" in content

    @pytest.mark.anyio
    async def test_file_not_found_no_create(self):
        from kunkun.tools.edit_tool import edit_tool, EditInput

        with tempfile.TemporaryDirectory() as tmp:
            ctx = _make_ctx(tmp)
            result = await edit_tool(EditInput(
                file_path="missing.py",
                old_string="x",
                new_string="y",
            ), ctx)

            assert result.is_error
            assert "不存在" in result.data


# ═══════════════════════════════════════════════════════════
# 3. 集成测试
# ═══════════════════════════════════════════════════════════

class TestIntegration:

    @pytest.mark.anyio
    async def test_grep_then_edit(self):
        """模拟 DSv4 工作流: grep 找到位置 → edit 精确修改."""
        from kunkun.tools.grep_tool import grep_tool, GrepInput
        from kunkun.tools.edit_tool import edit_tool, EditInput

        with tempfile.TemporaryDirectory() as tmp:
            # 创建多文件项目
            (Path(tmp) / "src").mkdir()
            (Path(tmp) / "src/a.py").write_text("API_KEY = 'old-secret'\n", encoding="utf-8")
            (Path(tmp) / "src/b.py").write_text("DEBUG = True\n", encoding="utf-8")

            ctx = _make_ctx(tmp)

            # Step 1: grep 找到 API_KEY
            r1 = await grep_tool(GrepInput(pattern="API_KEY", path="."), ctx)
            assert "a.py" in r1.data
            assert "old-secret" in r1.data

            # Step 2: edit 修改
            r2 = await edit_tool(EditInput(
                file_path="src/a.py",
                old_string="API_KEY = 'old-secret'",
                new_string="API_KEY = 'new-secret'",
            ), ctx)
            assert not r2.is_error

            # Step 3: 验证
            content = (Path(tmp) / "src/a.py").read_text(encoding="utf-8")
            assert "new-secret" in content
            assert "old-secret" not in content

    def test_tools_registered(self):
        """验证 Grep + Edit 已注册."""
        from kunkun.tools import init_tools
        registry = init_tools()
        names = registry.list_names()
        assert "grep" in names
        assert "edit" in names
        assert len(names) == 11  # bash, read, write, glob, grep, edit, remember, recall, skill_load, websearch, webfetch


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
