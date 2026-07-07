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

    # 文件大小检查 (>10MB 拒绝读取)
    try:
        file_size = file_path.stat().st_size
    except OSError as e:
        return ToolResult(data=f"无法访问文件: {e}", is_error=True)

    max_size = 10 * 1024 * 1024  # 10 MB
    if file_size > max_size:
        return ToolResult(
            data=f"文件过大 ({file_size / (1024*1024):.1f}MB)，超过上限 ({max_size / (1024*1024):.0f}MB)。"
            f"请使用 bash 命令查看或使用 offset/limit 分段读取。",
            is_error=True,
        )

    # 读取文件
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult(
            data="文件不是 UTF-8 文本文件，可能是二进制文件。请使用 bash 命令处理。",
            is_error=True,
        )
    except Exception as e:
        return ToolResult(data=f"读取文件失败: {e}", is_error=True)

    lines = content.split("\n")
    total_lines = len(lines)

    # 应用 offset/limit
    start = max(0, args.offset)
    end = min(start + max(1, min(args.limit, MAX_LINES)), total_lines)

    # 行号标注输出 (借鉴 cc-haha: `{line_number}\t{line_content}`)
    result_lines: list[str] = []
    for i in range(start, end):
        result_lines.append(f"{i + 1:6d}\t{lines[i]}")

    result_text = "\n".join(result_lines)

    # 附加文件信息
    header = f"📄 {file_path} ({total_lines} 行, {file_size:,} bytes)"
    if start > 0 or end < total_lines:
        header += f" [显示行 {start + 1}-{end}]"

    return ToolResult(data=f"{header}\n\n{result_text}")
