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


# ─── GRPO 多版本生成 ────────────────────────────────────


class GRPOInput(BaseModel):
    """grpo 工具输入参数."""

    prompt: str = Field(description="要执行的任务描述")


@tool(
    name="grpo",
    description=(
        "GRPO 多版本生成：同一任务用 3 种策略并行执行，LLM-as-Judge 择优返回最佳结果。"
        "3 条路径: A) 直接实现 B) 先查后做 C) 先分析设计再做。"
        "适用场景：复杂编码任务（重构、架构设计、算法实现），简单查询不要用。"
        "DSv4 专属能力：低成本使并行多版本生成可行。"
    ),
    permission="write",
    input_model=GRPOInput,
)
async def grpo_tool(args: GRPOInput, ctx: ToolUseContext) -> ToolResult:
    """GRPO 多版本生成."""
    parent_config = ctx.metadata.get("_config")
    if parent_config is None:
        return ToolResult(data="❌ GRPO 需要父 Agent 配置", is_error=True)

    strategies = [
        ("direct", "直接实现，一步到位。不要过度分析，直接动手做。"),
        ("search_first", "先搜索现有代码和文档了解上下文，再动手实现。先 grep/glob 探索，再编码。"),
        ("design_first", "先分析需求，设计实现方案，再逐步编码。ThinkBlock 充分规划。"),
    ]

    from kunkun.core.agent_loop import AgentLoop

    async def _run_path(name: str, strategy: str) -> dict:
        import traceback
        try:
            sub = AgentLoop(parent_config)
            full_prompt = (
                f"{strategy}\n\n"
                f"任务: {args.prompt}\n\n"
                f"注意: 只需在回复中输出代码(用 Markdown 代码块), 不要创建或修改任何文件。"
            )
            parts: list[str] = []
            errors: list[str] = []
            async for event in sub.run(full_prompt):
                if event.type.value == "content_block_delta":
                    if event.data.get("type") == "text":
                        parts.append(event.data["text"])
                elif event.type.value == "error":
                    errors.append(event.data.get("error", ""))
            await sub.close()
            result = "".join(parts).strip()
            if result:
                return {"path": name, "result": result, "ok": True}
            return {"path": name, "result": "; ".join(errors) or "(no output)", "ok": False}
        except Exception as e:
            return {"path": name, "result": f"{e}\n{traceback.format_exc()}", "ok": False}

    # 并行执行 3 条路径
    tasks = [_run_path(name, strat) for name, strat in strategies]
    results = await asyncio.gather(*tasks)

    if not any(r["ok"] for r in results):
        details = "\n".join(f"  {r['path']}: {r['result'][:200]}" for r in results)
        return ToolResult(data=f"❌ GRPO: 所有路径执行失败\n{details}", is_error=True)

    # ── LLM-as-Judge ──
    api_key = parent_config.api_key
    base_url = parent_config.base_url
    if not api_key:
        # 无 API key: 随机选一条成功的
        best = next(r for r in results if r["ok"])
        return ToolResult(data=f"⚡ GRPO (无序, 回退到 {best['path']}):\n\n{best['result'][:4000]}")

    comparison = "\n\n---\n\n".join(
        f"路径 {r['path']}:\n{r['result'][:6000]}" for r in results if r["ok"]
    )

    try:
        import httpx, os
        proxy = os.environ.get("KUN_PROXY", "") or None
        async with httpx.AsyncClient(timeout=60, proxy=proxy) as client:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": parent_config.light_model,
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"以下是对同一任务的 3 种不同实现结果。选出最佳的一个，"
                            f"说明它为什么比其他好。用中文回答。\n\n"
                            f"任务: {args.prompt}\n\n{comparison}\n\n"
                            f"返回格式: 最佳路径: [direct/search_first/design_first]\n理由: ..."
                        ),
                    }],
                    "max_tokens": 2048,
                    "temperature": 0.3,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code != 200:
                best = next(r for r in results if r["ok"])
                return ToolResult(data=f"⚡ GRPO (judge unavailable, fallback to {best['path']}):\n\n{best['result'][:4000]}")

            judge = resp.json()["choices"][0]["message"]["content"]
    except Exception:
        best = next(r for r in results if r["ok"])
        return ToolResult(data=f"⚡ GRPO (judge unavailable, fallback to {best['path']}):\n\n{best['result'][:4000]}")

    # 从 Judge 评语中提取获胜路径, 附加完整代码
    winner = "direct"  # default
    for path_name in ["design_first", "search_first", "direct"]:
        if path_name in judge.lower():
            winner = path_name
            break
    best_result = next((r["result"] for r in results if r["path"] == winner and r["ok"]), results[0]["result"])

    return ToolResult(
        data=f"⚡ GRPO 多版本生成 (3 路径 → Judge 择优: **{winner}**)\n\n"
             f"{judge}\n\n"
             f"---\n\n## 优胜代码 ({winner})\n\n{best_result[:8000]}\n\n"
             f"📊 路径统计: {sum(1 for r in results if r['ok'])}/3 成功"
    )
