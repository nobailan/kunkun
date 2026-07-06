"""Glob 工具 — 文件模式匹配.

借鉴:
- cc-haha src/tools/GlobTool/GlobTool.ts
  - glob 模式匹配
  - 按修改时间排序 (最近修改的在前面)
  - limit 结果数量
  - 排除 node_modules, .git 等常见目录
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

from kun.core.state import ToolResult
from kun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)

MAX_RESULTS = 200  # 借鉴 cc-haha GlobTool 的默认限制

# 默认排除目录 (借鉴 cc-haha 的 ignore patterns)
DEFAULT_EXCLUDES = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".cache",
    "coverage",
    ".coverage",
    ".DS_Store",
}


class GlobInput(BaseModel):
    """Glob 工具输入参数.

    借鉴 cc-haha GlobTool input schema:
    - pattern: glob 模式
    - path: 搜索起始目录
    """

    pattern: str = Field(description='Glob pattern, e.g. "**/*.py" or "src/**/*.ts"')
    path: str = Field(default=".", description="Directory to search in (default: workspace root)")


@tool(
    name="glob",
    description="Find files matching a glob pattern. Returns matching file paths sorted by "
    "modification time (most recently modified first). Excludes common directories "
    "like node_modules, .git, __pycache__ by default. "
    "Use this to discover files matching specific patterns in the project.",
    permission="read",
    is_concurrency_safe=True,
    input_model=GlobInput,
)
async def glob_tool(args: GlobInput, ctx: ToolUseContext) -> ToolResult:
    """执行文件搜索.

    借鉴 cc-haha GlobTool.call() 流程:
    1. 在指定目录下执行 glob
    2. 排除常见忽略目录
    3. 按修改时间排序
    4. 截断结果
    """
    search_path = Path(args.path).expanduser()
    if not search_path.is_absolute():
        search_path = (Path(ctx.workspace) / search_path).resolve()

    if not search_path.exists():
        return ToolResult(data=f"目录不存在: {args.path}", is_error=True)

    pattern = args.pattern

    try:
        # 使用 Path.glob 进行匹配
        matches: list[Path] = []

        # 支持递归 glob (**)
        if "**" in pattern:
            iterator = search_path.glob(pattern)
        else:
            iterator = search_path.glob(pattern)

        for match in iterator:
            # 检查是否在排除目录中
            if _should_exclude(match):
                continue
            matches.append(match)

        # 限制结果数量并排序
        if len(matches) > MAX_RESULTS:
            # 按修改时间排序 (最近的在前面)，然后截断
            matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            matches = matches[:MAX_RESULTS]
            truncated = True
        else:
            matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            truncated = False

        # 格式化输出
        if not matches:
            return ToolResult(data=f"未找到匹配 '{pattern}' 的文件")

        lines: list[str] = [f"🔍 找到 {len(matches)} 个匹配 '{pattern}' 的文件:\n"]
        for p in matches:
            try:
                rel_path = p.relative_to(search_path)
            except ValueError:
                rel_path = p
            size = p.stat().st_size if p.is_file() else 0
            lines.append(f"  {rel_path}  ({_format_size(size)})")

        if truncated:
            lines.append(f"\n... (仅显示前 {MAX_RESULTS} 个结果)")

        return ToolResult(data="\n".join(lines))

    except Exception as e:
        logger.exception("Glob error")
        return ToolResult(data=f"文件搜索失败: {e}", is_error=True)


def _should_exclude(path: Path) -> bool:
    """检查路径是否应被排除."""
    for part in path.parts:
        if part in DEFAULT_EXCLUDES or part.startswith(".") and part not in (".", ".."):
            return True
    return False


def _format_size(size: int) -> str:
    """格式化文件大小."""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / (1024 * 1024):.1f}MB"
