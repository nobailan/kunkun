"""会话搜索工具 — FTS5 全文搜索历史对话."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from kunkun.core.state import ToolResult
from kunkun.memory.store import MessageStore
from kunkun.tools.decorators import tool, ToolUseContext


class SessionSearchInput(BaseModel):
    query: str = Field(description="搜索关键词")
    limit: int = Field(default=10, description="返回条数上限")


@tool(
    name="session_search",
    description=(
        "搜索历史会话消息（跨会话全文搜索）。用于查找之前讨论过的内容、"
        "技术方案、bug 修复记录等。返回匹配的消息片段和所属会话。"
    ),
    permission="read",
    input_model=SessionSearchInput,
)
async def session_search_tool(args: SessionSearchInput, ctx: ToolUseContext) -> ToolResult:
    store = MessageStore(Path(ctx.metadata.get("db_path", ".kun/messages.db")))
    results = store.search(args.query, limit=args.limit)
    store.close()

    if not results:
        return ToolResult(data=f"🔍 未找到与 '{args.query}' 相关的历史消息。")

    lines = [f"🔍 搜索 '{args.query}': {len(results)} 条结果\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r['timestamp'][:19]}] [{r['role']}] {r['content'][:200]}")
        if r.get("prompt"):
            lines.append(f"   会话: {r['prompt'][:100]}")
        lines.append("")
    return ToolResult(data="\n".join(lines))
