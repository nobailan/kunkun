"""Grep 工具 — 文件内容正则搜索.

借鉴 Claude Code Grep (ripgrep):
- 正则匹配 + 上下文行
- 结果按文件分组，先给摘要再给详细匹配
- DSv4 适配: 先产结构摘要让 ThinkBlock 有信息可"想"，
  再决定读哪个文件
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from kunkun.core.state import ToolResult
from kunkun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)

MAX_MATCHES = 50
MAX_LINE_LEN = 300
DEFAULT_CONTEXT = 2


class GrepInput(BaseModel):
    """grep 工具输入参数."""

    pattern: str = Field(description="正则搜索模式, 例如 'def function_name' 或 'TODO|FIXME'")
    path: str = Field(
        default=".",
        description="搜索目录或文件路径, 默认当前目录",
    )
    glob: str = Field(
        default="",
        description="文件名过滤, 例如 '*.py' 或 '*.{ts,tsx}'",
    )
    output_mode: str = Field(
        default="content",
        description="输出模式: content (匹配行), files_with_matches (仅文件路径), count (匹配计数)",
    )
    context_lines: int = Field(
        default=DEFAULT_CONTEXT,
        description="匹配行前后的上下文行数, 默认 2",
    )
    case_insensitive: bool = Field(
        default=False,
        description="是否忽略大小写, 默认 False",
    )


@tool(
    name="grep",
    description=(
        "文件内容正则搜索。搜索指定目录下的文件内容，返回匹配的行和上下文。"
        "支持正则模式，支持文件名过滤。"
        "先搜后读: 用 grep 找到目标位置 → ThinkBlock 分析 → read_file 读取详细内容。"
    ),
    permission="read",
    input_model=GrepInput,
)
async def grep_tool(args: GrepInput, ctx: ToolUseContext) -> ToolResult:
    """执行文件内容搜索."""
    search_path = Path(ctx.workspace) / args.path
    if not search_path.exists():
        return ToolResult(
            data=f"❌ 路径不存在: {search_path}",
            is_error=True,
        )

    # 编译正则
    flags = re.IGNORECASE if args.case_insensitive else 0
    try:
        pattern = re.compile(args.pattern, flags)
    except re.error as e:
        return ToolResult(
            data=f"❌ 正则表达式错误: {e}",
            is_error=True,
        )

    # 收集文件
    files = _collect_files(search_path, args.glob)
    if not files:
        return ToolResult(data=f"未找到匹配 '{args.glob}' 的文件。")

    # 搜索
    all_matches: list[tuple[Path, int, str]] = []  # (file, lineno, content)
    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(content.split("\n"), 1):
            if pattern.search(line):
                all_matches.append((filepath, i, line[:MAX_LINE_LEN]))
                if len(all_matches) >= MAX_MATCHES:
                    break
        if len(all_matches) >= MAX_MATCHES:
            break

    if not all_matches:
        return ToolResult(data=f"未找到匹配 '{args.pattern}' 的内容。")

    # ── 按模式输出 ──
    if args.output_mode == "files_with_matches":
        files = sorted(set(f for f, _, _ in all_matches))
        lines = [f"找到 {len(files)} 个匹配文件:\n"]
        for f in files:
            try:
                rel = f.relative_to(ctx.workspace)
            except ValueError:
                rel = f
            lines.append(f"  {rel}")
        return ToolResult(data="\n".join(lines))

    if args.output_mode == "count":
        by_file: dict[str, int] = {}
        for f, _, _ in all_matches:
            key = str(f)
            by_file[key] = by_file.get(key, 0) + 1
        lines = [f"匹配计数 ({len(all_matches)} total):\n"]
        for f, c in sorted(by_file.items()):
            lines.append(f"  {Path(f).name}: {c}")
        return ToolResult(data="\n".join(lines))

    # ── content 模式: 分组 + 摘要 ──
    by_file: dict[Path, list[tuple[int, str]]] = {}
    for f, lineno, content in all_matches:
        by_file.setdefault(f, []).append((lineno, content))

    # DSv4 适配: 先给结构摘要
    parts = [f"## Grep 结果: '{args.pattern}'\n"]
    parts.append(f"匹配: {len(all_matches)} 行 / {len(by_file)} 个文件\n")

    # 摘要表
    parts.append("| 文件 | 匹配数 |")
    parts.append("|------|--------|")
    for f in sorted(by_file.keys()):
        try:
            rel = f.relative_to(ctx.workspace)
        except ValueError:
            rel = f
        parts.append(f"| {rel} | {len(by_file[f])} |")

    # 详细匹配
    parts.append(f"\n## 详细匹配 (上下文 ±{args.context_lines} 行)\n")
    for f in sorted(by_file.keys()):
        try:
            rel = f.relative_to(ctx.workspace)
        except ValueError:
            rel = f
        parts.append(f"### {rel}\n")
        try:
            all_lines = f.read_text(encoding="utf-8", errors="replace").split("\n")
        except Exception:
            continue

        matches = by_file[f]
        shown: set[int] = set()
        for lineno, _ in matches:
            if lineno in shown:
                continue
            shown.add(lineno)
            start = max(0, lineno - args.context_lines - 1)
            end = min(len(all_lines), lineno + args.context_lines)
            parts.append(f"```")
            for i in range(start, end):
                prefix = "▶" if i == lineno - 1 else " "
                line_text = all_lines[i][:200]
                parts.append(f"{prefix}{i + 1:5d} {line_text}")
            parts.append("```\n")

        if len(matches) > MAX_MATCHES:
            parts.append(f"... 超过 {MAX_MATCHES} 条，已截断\n")

    result = "\n".join(parts)
    if len(result) > 8000:
        result = result[:8000] + "\n\n... 结果过长，已截断。尝试缩小搜索范围。"
    return ToolResult(data=result)


def _collect_files(search_path: Path, glob_pattern: str) -> list[Path]:
    """收集要搜索的文件."""
    if search_path.is_file():
        return [search_path]

    files = []
    if glob_pattern:
        for f in search_path.rglob("*"):
            if f.is_file() and f.match(glob_pattern):
                # 跳过常见忽略目录
                if any(p in f.parts for p in (".git", "__pycache__", ".venv", "node_modules", ".kun")):
                    continue
                files.append(f)
    else:
        for f in search_path.rglob("*"):
            if f.is_file():
                if any(p in f.parts for p in (".git", "__pycache__", ".venv", "node_modules", ".kun")):
                    continue
                files.append(f)
                if len(files) >= 500:
                    break

    return files[:500]
