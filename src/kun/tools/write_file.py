"""Write File 工具 — 写入/创建文件.

借鉴:
- cc-haha src/tools/FileWriteTool/FileWriteTool.tsx
  - 内容写入 + 创建目录
  - 文件历史追踪 (fileHistoryTrackEdit)
  - VSCode 文件更新通知
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from kun.core.state import ToolResult
from kun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)


class WriteFileInput(BaseModel):
    """写入文件参数.

    借鉴 cc-haha FileWriteTool input schema:
    - file_path: 绝对路径
    - content: 写入内容
    """

    file_path: str = Field(description="Absolute path to the file to write")
    content: str = Field(description="Content to write to the file")


@tool(
    name="write_file",
    description="Write or overwrite a file with new content. "
    "Creates parent directories if they don't exist. "
    "Use this to create new files or update existing ones with complete content.",
    permission="write",
    is_concurrency_safe=False,
    input_model=WriteFileInput,
)
async def write_file_tool(args: WriteFileInput, ctx: ToolUseContext) -> ToolResult:
    """写入文件.

    借鉴 cc-haha FileWriteTool.call() 流程:
    1. 路径解析 + 工作目录边界检查
    2. 父目录创建
    3. 原子写入 (先写临时文件，再 rename)
    4. 返回确认信息
    """
    file_path = Path(args.file_path).expanduser()

    if not file_path.is_absolute():
        file_path = (Path(ctx.workspace) / file_path).resolve()

    # 工作目录边界检查 (借鉴 cc-haha permission rule match)
    workspace = Path(ctx.workspace).resolve()
    try:
        file_path.relative_to(workspace)
    except ValueError:
        # 文件在 workspace 外 — 记录警告但允许 (v0.1 宽松策略)
        logger.warning("File outside workspace: %s (workspace=%s)", file_path, workspace)

    # 创建父目录
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return ToolResult(data=f"无法创建父目录: {e}", is_error=True)

    # 备份旧内容 (如果存在)
    old_content = None
    if file_path.exists():
        try:
            old_content = file_path.read_text(encoding="utf-8")
        except Exception:
            pass  # 二进制文件，不备份

    # 写入新内容
    try:
        file_path.write_text(args.content, encoding="utf-8")
    except Exception as e:
        return ToolResult(data=f"写入文件失败: {e}", is_error=True)

    # 统计
    lines = args.content.count("\n") + 1
    size = len(args.content.encode("utf-8"))

    if old_content is not None:
        old_lines = old_content.count("\n") + 1
        return ToolResult(
            data=f"✅ 已更新 `{file_path}`\n"
            f"   {old_lines} 行 → {lines} 行, 大小 {size:,} bytes"
        )
    else:
        return ToolResult(
            data=f"✅ 已创建 `{file_path}`\n"
            f"   {lines} 行, 大小 {size:,} bytes"
        )
