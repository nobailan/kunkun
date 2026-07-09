"""Read File 工具 — 读取文件内容.

借鉴:
- cc-haha src/tools/FileReadTool/FileReadTool.tsx
  - 行号范围读取 (offset + limit)
  - 编码检测 (detectFileEncoding)
  - 行尾检测 (detectLineEndings)
  - maxResultSizeChars: Infinity (不截断到磁盘)

简化版 v0.1:
  - offset + limit 参数
  - UTF-8 编码 (后续版本加编码检测)
  - 行号标注 (借鉴 cc-haha 的 `{i+1}\t{line}` 格式)
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from kunkun.core.state import ToolResult
from kunkun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)

MAX_LINES = 2000  # 借鉴 cc-haha FileReadTool: 默认 2000 行


class ReadFileInput(BaseModel):
    """读取文件参数.

    借鉴 cc-haha FileReadTool input schema:
    - file_path: 绝对路径
    - offset: 起始行号 (0-indexed)
    - limit: 最大行数
    """

    file_path: str = Field(description="Absolute path to the file to read")
    offset: int = Field(default=0, description="Line number to start reading from (0-indexed)")
    limit: int = Field(
        default=MAX_LINES,
        description=f"Maximum number of lines to read (default {MAX_LINES})",
    )


@tool(
    name="read_file",
    description="Read a file from the local filesystem. Returns line-numbered content. "
    "Use this to examine file contents before making changes. "
    "Supports reading specific ranges with offset and limit parameters.",
    permission="read",
    is_concurrency_safe=True,
    input_model=ReadFileInput,
)
async def read_file_tool(args: ReadFileInput, ctx: ToolUseContext) -> ToolResult:
    """读取文件.

    借鉴 cc-haha FileReadTool.call() 流程:
    1. 路径解析 (expandPath → resolve)
    2. 文件存在性检查
    3. 二进制文件检测 (简化: 用 read_text 的 encoding 错误)
    4. 行号标注输出
    """
    file_path = Path(args.file_path).expanduser()

    # 如果是相对路径，基于 workspace 解析
    if not file_path.is_absolute():
        file_path = (Path(ctx.workspace) / file_path).resolve()

    if not file_path.exists():
        return ToolResult(data=f"文件不存在: {args.file_path}", is_error=True)

    if not file_path.is_file():
        return ToolResult(data=f"路径不是文件: {args.file_path}", is_error=True)

    # 文件大小检查
    try:
        file_size = file_path.stat().st_size
    except OSError as e:
        return ToolResult(data=f"无法访问文件: {e}", is_error=True)

    file_mb = file_size / (1024 * 1024)
    is_large = file_size > 2 * 1024 * 1024  # > 2MB 视为大文件

    # 读取文件
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult(
            data="文件不是 UTF-8 文本文件，可能是二进制文件。请使用 bash 命令处理。",
            is_error=True,
        )
    except MemoryError:
        return ToolResult(
            data=f"文件过大 ({file_mb:.1f}MB)，内存不足。"
                 f"请用 bash (head/tail/grep/wc) 分段处理。",
            is_error=True,
        )
    except Exception as e:
        return ToolResult(data=f"读取文件失败: {e}", is_error=True)

    lines = content.split("\n")
    total_lines = len(lines)

    # 大文件: 返回元数据 + 头中尾采样
    if is_large:
        parts = [f"📂 大文件: {file_path.name} ({file_mb:.1f}MB, {total_lines:,} 行)\n"]
        parts.append("═══ 文件头部 (前 30 行) ═══")
        for i in range(min(30, total_lines)):
            parts.append(f"{i + 1:6d}\t{lines[i][:200]}")
        if total_lines > 80:
            mid = total_lines // 2
            parts.append(f"\n═══ 文件中部 (第 {mid:,} 行) ═══")
            for i in range(mid - 10, min(mid + 10, total_lines)):
                parts.append(f"{i + 1:6d}\t{lines[i][:200]}")
        if total_lines > 60:
            parts.append(f"\n═══ 文件尾部 (最后 30 行) ═══")
            for i in range(max(0, total_lines - 30), total_lines):
                parts.append(f"{i + 1:6d}\t{lines[i][:200]}")
        parts.append(
            f"\n💡 大文件自动采样。用 offset + limit 分段读取指定区间，"
            f"或用 grep 搜索关键词定位目标行。"
        )
        return ToolResult(data="\n".join(parts))

    # 小文件: 正常读取
    start = max(0, args.offset)
    end = min(start + max(1, min(args.limit, MAX_LINES)), total_lines)

    result_lines: list[str] = []
    for i in range(start, end):
        result_lines.append(f"{i + 1:6d}\t{lines[i]}")

    result_text = "\n".join(result_lines)

    header = f"📄 {file_path.name} ({total_lines} 行, {file_mb:.2f}MB)"
    if start > 0 or end < total_lines:
        header += f" [行 {start + 1}-{end}]"

    return ToolResult(data=f"{header}\n\n{result_text}")
