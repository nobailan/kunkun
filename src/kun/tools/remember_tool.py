"""记忆写入工具 — 让 Agent 可以持久化项目记忆.

Agent 调用此工具来"记住"用户偏好、项目事实等，写入 .kun/memory/*.md.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from kun.core.state import ToolResult
from kun.memory.manager import Memory, MemoryManager
from kun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)


class RememberInput(BaseModel):
    """remember 工具输入参数."""

    name: str = Field(description="记忆名称 (kebab-case slug), 例如 python-style-preferences")
    description: str = Field(description="一句话描述, 用于后续检索匹配, 例如 'Python 代码风格偏好'")
    content: str = Field(description="记忆正文 (Markdown 格式), 包含具体的约定、偏好、事实")
    type: str = Field(
        default="project",
        description="记忆类型: project (项目约定), user (用户偏好), feedback (反馈)",
    )


class RecallInput(BaseModel):
    """recall 工具输入参数."""

    query: str = Field(description="搜索关键词, 用于查找已保存的记忆")


@tool(
    name="remember",
    description=(
        "持久化保存一条项目记忆到 .kun/memory/ 目录。"
        "当你需要记住用户偏好、项目约定、重要事实时调用此工具。"
        "记忆将在后续对话启动时自动加载到上下文。"
    ),
    permission="write",
    input_model=RememberInput,
)
async def remember_tool(args: RememberInput, ctx: ToolUseContext) -> ToolResult:
    """保存一条项目记忆."""
    if not args.name.strip() or not args.content.strip():
        return ToolResult(
            data="❌ 记忆保存失败: name 和 content 不能为空。",
            is_error=True,
        )

    memory_dir = ctx.metadata.get("memory_dir", ".kun/memory")
    mgr = MemoryManager(memory_dir=memory_dir)
    mgr.load()

    memory = Memory(
        name=args.name.strip(),
        description=args.description.strip(),
        content=args.content.strip(),
        metadata={"type": args.type},
    )

    try:
        path = mgr.save(memory)
        logger.info("Memory saved via remember tool: %s", memory.name)
        return ToolResult(
            data=f"✅ 记忆已保存: {memory.name}\n"
                 f"   路径: {path}\n"
                 f"   描述: {memory.description}\n"
                 f"   当前共 {len(mgr.memories)} 条记忆",
        )
    except Exception as e:
        logger.exception("Failed to save memory via remember tool")
        return ToolResult(
            data=f"❌ 记忆保存失败: {e}",
            is_error=True,
        )


@tool(
    name="recall",
    description=(
        "搜索已保存的项目记忆。当你需要回忆之前记住的偏好、约定或事实时使用。"
    ),
    permission="read",
    input_model=RecallInput,
)
async def recall_tool(args: RecallInput, ctx: ToolUseContext) -> ToolResult:
    """搜索已保存的记忆."""
    if not args.query.strip():
        return ToolResult(
            data="❌ 请提供搜索关键词 (query 参数)。",
            is_error=True,
        )

    memory_dir = ctx.metadata.get("memory_dir", ".kun/memory")
    mgr = MemoryManager(memory_dir=memory_dir)
    mgr.load()

    results = mgr.search(args.query.strip())

    if not results:
        return ToolResult(
            data=f"🔍 未找到与 '{args.query}' 相关的记忆。"
                 f"当前共有 {len(mgr.memories)} 条记忆。",
        )

    lines = [f"🔍 找到 {len(results)} 条与 '{args.query}' 相关的记忆:\n"]
    for i, mem in enumerate(results, 1):
        lines.append(f"### {i}. {mem.name}")
        lines.append(f"_{mem.description}_")
        lines.append(f"```\n{mem.content[:500]}\n```")
        lines.append("")

    return ToolResult(data="\n".join(lines))
