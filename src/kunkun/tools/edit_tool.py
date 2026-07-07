"""Edit 工具 — 精确字符串替换.

借鉴 Claude Code Edit:
- old_string → new_string 精确替换
- 只替换第一个匹配 (避免意外批量修改)
- 文件不存在时自动创建
- DSv4 适配: 配合 Grep 先搜后改, ThinkBlock 规划修改顺序
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from kunkun.core.state import ToolResult
from kunkun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)


class EditInput(BaseModel):
    """edit 工具输入参数."""

    file_path: str = Field(description="要编辑的文件路径 (绝对或相对)")
    old_string: str = Field(description="要替换的原始文本, 必须精确匹配 (含缩进和空行)")
    new_string: str = Field(description="替换后的新文本")
    replace_all: bool = Field(
        default=False,
        description="是否替换所有匹配 (默认 False, 只替换第一个)",
    )


@tool(
    name="edit",
    description=(
        "精确替换文件中的文本。old_string → new_string。\n"
        "使用规则:\n"
        "1. old_string 必须包含足够的上下文来唯一定位 (含前后几行)\n"
        "2. 默认只替换第一个匹配, 批量替换用 replace_all=True\n"
        "3. 文件不存在时会自动创建 (old_string 为空字符串时)\n"
        "4. 配合 grep 使用: 先 grep 找到位置 → edit 精确修改"
    ),
    permission="write",
    input_model=EditInput,
)
async def edit_tool(args: EditInput, ctx: ToolUseContext) -> ToolResult:
    """执行精确替换."""
    file_path = Path(ctx.workspace) / args.file_path
    file_path = file_path.resolve()

    # ── 文件不存在: 创建 ──
    if not file_path.exists():
        if args.old_string == "":
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(args.new_string, encoding="utf-8")
            return ToolResult(
                data=f"✅ 已创建 `{file_path.name}`，{len(args.new_string)} 字符"
            )
        return ToolResult(
            data=f"❌ 文件不存在: {file_path}。如要创建新文件, old_string 留空。",
            is_error=True,
        )

    # ── 读取并匹配 ──
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return ToolResult(data=f"❌ 读取文件失败: {e}", is_error=True)

    if args.old_string not in content:
        return ToolResult(
            data=(
                f"❌ 未找到匹配的 old_string。\n"
                f"文件: {file_path}\n"
                f"提示: old_string 必须包含精确的原始文本 (含缩进)。"
                f"用 read_file 确认当前内容。"
            ),
            is_error=True,
        )

    occurrences = content.count(args.old_string)

    if not args.replace_all and occurrences > 1:
        return ToolResult(
            data=(
                f"⚠️ 找到 {occurrences} 处匹配, 但没有设置 replace_all=True。\n"
                f"请添加更多上下文使 old_string 唯一, "
                f"或设置 replace_all=True 替换全部 {occurrences} 处。\n"
                f"文件: {file_path}"
            ),
            is_error=True,
        )

    # ── 执行替换 ──
    if args.replace_all:
        new_content = content.replace(args.old_string, args.new_string)
        count = occurrences
    else:
        new_content = content.replace(args.old_string, args.new_string, 1)
        count = 1

    try:
        # 备份
        backup = content
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        # 恢复备份
        file_path.write_text(backup, encoding="utf-8")
        return ToolResult(data=f"❌ 写入失败 (已恢复): {e}", is_error=True)

    # ── 生成 diff 摘要 ──
    old_lines = args.old_string.strip().split("\n")
    new_lines = args.new_string.strip().split("\n")

    if not old_lines[0].strip():
        old_preview = "(空)"
    else:
        old_preview = old_lines[0][:80]

    if not new_lines[0].strip():
        new_preview = "(空)"
    else:
        new_preview = new_lines[0][:80]

    return ToolResult(
        data=(
            f"✅ 已修改 `{file_path.name}`\n"
            f"   替换: {count} 处\n"
            f"   旧: {old_preview}{'...' if len(old_lines) > 1 else ''}\n"
            f"   新: {new_preview}{'...' if len(new_lines) > 1 else ''}"
        )
    )
