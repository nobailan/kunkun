"""Agent 编排工具 — 子 Agent + 任务管理.

v0.4.2: Agent (子代理) + TodoWrite (任务跟踪)
DSv4 适配: ThinkBlock 先规划 → Agent 拆解子任务 → TodoWrite 跟踪进度
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pydantic import BaseModel, Field

from kunkun.core.state import ToolResult
from kunkun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)


# ─── Agent 工具 ─────────────────────────────────────────


class AgentInput(BaseModel):
    """agent 工具输入参数."""

    description: str = Field(description="子任务简短描述 (3-5 字), 如 '搜索配置文件'")
    prompt: str = Field(description="子任务完整描述, 子 Agent 将独立执行此任务")
    subagent_type: str = Field(
        default="general",
        description="子 Agent 类型: general (通用), explore (只读搜索), plan (架构设计)",
    )


@tool(
    name="agent",
    description=(
        "启动一个子 Agent 独立处理子任务。子 Agent 拥有独立的上下文窗口，"
        "可以调用工具。用于并行处理独立子任务，提高效率。\n"
        "使用流程: ThinkBlock 规划 → 拆解为独立子任务 → 并行启动多个 agent → 汇总结果。\n"
        "注意: 子 Agent 之间不能通信，每个 agent 应该处理完全独立的子任务。"
    ),
    permission="write",
    input_model=AgentInput,
)
async def agent_tool(args: AgentInput, ctx: ToolUseContext) -> ToolResult:
    """启动子 Agent."""
    # 获取父 Agent 的配置和工具
    parent_config = ctx.metadata.get("_config")
    parent_loop = ctx.metadata.get("_agent_loop")

    if parent_config is None:
        return ToolResult(
            data="❌ Agent 工具无法获取父 Agent 配置，可能是运行环境不支持。",
            is_error=True,
        )

    from kunkun.core.agent_loop import AgentLoop

    # 创建子 Agent (只读模式)
    sub_config = parent_config
    if args.subagent_type == "explore":
        sub_config.permission_mode = "bypass"  # 只读子 Agent 不需要权限确认

    try:
        sub_agent = AgentLoop(sub_config)

        # 收集子 Agent 的输出
        output_parts: list[str] = []
        async for event in sub_agent.run(args.prompt):
            if event.type.value == "content_block_delta":
                if event.data.get("type") == "text":
                    output_parts.append(event.data["text"])
            elif event.type.value == "error":
                await sub_agent.close()
                return ToolResult(
                    data=f"❌ 子 Agent 错误: {event.data.get('error', 'Unknown')}",
                    is_error=True,
                )

        await sub_agent.close()
        result = "".join(output_parts).strip()

        if not result:
            return ToolResult(
                data="⚠️ 子 Agent 未产出文本输出",
                is_error=True,
            )

        return ToolResult(
            data=f"🤖 子 Agent [{args.description}] 完成:\n\n{result[:4000]}"
        )

    except Exception as e:
        logger.exception("Sub-agent failed")
        return ToolResult(
            data=f"❌ 子 Agent 执行失败: {e}",
            is_error=True,
        )


# ─── TodoWrite 工具 ─────────────────────────────────────


class TodoItem(BaseModel):
    """单条任务."""
    content: str = Field(description="任务内容")
    status: str = Field(description="状态: pending / in_progress / completed")
    activeForm: str = Field(default="", description="进行中的简短描述")


class TodoWriteInput(BaseModel):
    """todowrite 工具输入参数."""

    todos: list[TodoItem] = Field(description="任务列表 (完整替换, 不是追加)")


@tool(
    name="todowrite",
    description=(
        "创建和更新任务列表。用于跟踪复杂多步任务的进度。\n"
        "每次调用传入完整的任务列表 (替换而非追加)。\n"
        "使用流程: ThinkBlock 规划步骤 → TodoWrite 记录清单 → 每完成一步更新状态。"
    ),
    permission="write",
    input_model=TodoWriteInput,
)
async def todowrite_tool(args: TodoWriteInput, ctx: ToolUseContext) -> ToolResult:
    """管理任务列表."""
    todos = args.todos

    if not todos:
        return ToolResult(data="📋 任务列表已清空")

    # 统计
    total = len(todos)
    completed = sum(1 for t in todos if t.status == "completed")
    in_progress = sum(1 for t in todos if t.status == "in_progress")
    pending = sum(1 for t in todos if t.status == "pending")

    # 渲染任务列表
    lines = [f"📋 任务列表 ({completed}/{total} 完成)\n"]
    status_icons = {
        "completed": "✅",
        "in_progress": "🔄",
        "pending": "⬜",
    }

    for i, todo in enumerate(todos, 1):
        icon = status_icons.get(todo.status, "⬜")
        label = todo.activeForm or todo.content
        lines.append(f"  {icon} {i}. {label}")

    lines.append(f"\n  完成: {completed} | 进行中: {in_progress} | 待处理: {pending}")

    return ToolResult(data="\n".join(lines))
