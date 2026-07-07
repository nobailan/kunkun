"""Agent Loop — Kun 核心引擎.

借鉴:
- cc-haha src/query.ts queryLoop() — while(true) + State 跨迭代
- cc-haha src/QueryEngine.ts submitMessage() — AsyncGenerator[SDKMessage]
- Hermes agent/conversation_loop.py — run_conversation() 完整 turn 流程

核心数据流:
   用户输入
     │
     ▼
   [Pre-LLM]  记忆加载 → 上下文裁剪 → Prompt 组装
     │
     ▼
   [LLM]      DSv4 API stream (ThinkBlock 解析)
     │
     ├── text → 流式输出到 CLI
     ├── thinking → 灰色斜体输出
     └── tool_use → 进入后处理
     │
     ▼
   [Post-LLM] 权限检查 → 工具执行 → 结果回传 → 回到 LLM
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import AsyncGenerator

from kun.core.context import ContextManager
from kun.core.error_recovery import async_retry, ErrorClassifier, RetryPolicy
from kun.core.events import Event, EventType, EventBus
from kun.core.execution_log import ExecutionLogger
from kun.core.llm_client import LLMClient
from kun.core.permission import PermissionChecker, PermissionResult
from kun.core.state import (
    AgentState,
    AgentStatus,
    HarnessConfig,
    Message,
    MessageRole,
    ContentBlock,
    ContentType,
    ToolResult,
)
from kun.memory.manager import MemoryManager
from kun.routing.cost_router import CostRouter
from kun.core.background_review import BackgroundReviewer
from kun.skills.loader import SkillLoader
from kun.skills.usage import SkillUsageStore
from kun.skills.curator import SkillCurator
from kun.tools.decorators import ToolRegistry, ToolUseContext

logger = logging.getLogger(__name__)


class AgentLoop:
    """DS-Native Harness 核心 Agent Loop.

    借鉴 cc-haha QueryEngine (src/QueryEngine.ts):
    - submitMessage() = run() — AsyncGenerator 流式输出
    - interrupt() — 中断执行
    - mutable state 跨迭代保持

    借鉴 Hermes AIAgent.run_conversation():
    - build system prompt → model call → tool dispatch → retry → compression
    """

    def __init__(self, config: HarnessConfig):
        self.config = config
        self.state = AgentState(model=config.model)
        self.llm = LLMClient(config)
        self.context_mgr = ContextManager(config)
        self.tools = self._init_tools()
        self.event_bus = EventBus()

        # ─── v0.2 模块 ───
        self.permission = PermissionChecker(
            workspace=config.workspace,
            mode=config.permission_mode,
        )
        self.execution_log = ExecutionLogger(
            report_dir=config.report_dir,
            session_id=self.state.session_id,
        )
        self.memory = MemoryManager(memory_dir=config.memory_dir)
        # v0.3.1: Skill 使用量追踪 + 生命周期管理
        self.skill_usage = SkillUsageStore(skill_dir=config.skill_dir)
        self.skills = SkillLoader(skill_dir=config.skill_dir, usage_store=self.skill_usage)
        self.curator = SkillCurator(skill_dir=config.skill_dir)
        self.router = CostRouter(config)
        # v0.3.1: 自动反思引擎
        self.reviewer = BackgroundReviewer(
            memory_manager=self.memory,
            skill_loader=self.skills,
            llm_client=self.llm,
            skill_usage=self.skill_usage,
            light_model=config.light_model,
        )
        self.retry_policy = RetryPolicy()

        self._abort = None  # Lazy init in run()
        self._last_retry_count = 0

        # v0.3.1: Frozen Snapshot — 会话启动时生成一次，后续轮次复用
        self._frozen_prompt: str | None = None
        self._frozen_memory: str = ""
        self._frozen_skills: str = ""

    def _init_tools(self) -> ToolRegistry:
        """初始化工具注册中心."""
        from kun.tools import init_tools

        return init_tools()

    # ─── 公共 API ────────────────────────────────

    async def run(
        self, prompt: str
    ) -> AsyncGenerator[Event, None]:
        """执行用户任务 — 主入口.

        借鉴 cc-haha QueryEngine.submitMessage():
        - 处理用户输入
        - 进入 query loop
        - 流式产出事件

        Args:
            prompt: 用户输入的任务描述

        Yields:
            Event 事件流 (text, thinking, tool_use, tool_result, error, ...)
        """
        self.state.status = AgentStatus.IDLE
        self.state.start_time = datetime.now().timestamp()

        # v0.2: lazy init abort event
        if self._abort is None:
            self._abort = asyncio.Event()

        yield Event(
            EventType.SESSION_START,
            data={"prompt": prompt, "model": self.state.model},
            session_id=self.state.session_id,
        )
        yield Event(EventType.TURN_START, data={"prompt": prompt})

        # Step 1: 添加用户消息到历史
        user_msg = Message(role=MessageRole.USER, content=prompt)
        self.state.add_message(user_msg)

        # ─── v0.3.1: Frozen Snapshot ───
        # 会话首次调用时：加载全部记忆 + Skill 全文 → 冻结
        # 后续调用：复用冻结快照（Mid-session 写入只落盘，下次会话生效）
        if self._frozen_prompt is None:
            self.memory.load()
            all_memories = self.memory.memories
            # Frozen snapshot: inject FULL memory text + skill text
            memory_parts = []
            for m in all_memories:
                memory_parts.append(f"### {m.name}\n_{m.description}_\n\n{m.content[:2000]}")
            self._frozen_memory = "\n\n".join(memory_parts)

            self.skills.load()
            all_skills = self.skills.skills
            skill_parts = []
            for s in all_skills:
                skill_parts.append(f"### Skill: {s.name}\n_{s.description}_\n\n{s.content[:3000]}")
            self._frozen_skills = "\n\n".join(skill_parts)

            # Build frozen base prompt once
            self._frozen_prompt = self.context_mgr.build_system_prompt()
            if self._frozen_memory:
                self._frozen_prompt += f"\n## 项目记忆\n\n{self._frozen_memory}"
            if self._frozen_skills:
                self._frozen_prompt += f"\n## 项目 Skill\n\n{self._frozen_skills}"

        memory_context = ""  # already in frozen prompt
        skill_context = ""   # already in frozen prompt

        # ─── v0.2: 成本路由 ───
        routed_model = self.router.route(prompt)
        if routed_model != self.config.model:
            yield Event(
                EventType.STATUS_CHANGE,
                data={"status": f"routing: {routed_model}"},
            )
            self.config.model = routed_model
            self.state.model = routed_model

        # Step 2: 进入 Agent Loop
        self.state.current_turn = 0

        final_result = ""
        is_error = False

        while self.state.current_turn < self.config.max_turns:
            if self._abort and self._abort.is_set():
                yield Event(
                    EventType.SESSION_END,
                    data={"reason": "aborted_by_user"},
                    session_id=self.state.session_id,
                )
                return

            self.state.current_turn += 1
            self.state.status = AgentStatus.THINKING
            yield Event.status("thinking")

            # --- Pre-LLM: 上下文裁剪 ---
            trimmed_messages = self.context_mgr.trim(self.state.messages)
            # v0.3.1: 使用 Frozen Snapshot (首次构建, 后续复用)
            system_prompt = self._frozen_prompt or self.context_mgr.build_system_prompt()
            tool_schemas = self.tools.schemas()

            # --- LLM Stream (with retry) ---
            all_text: list[str] = []
            all_thinking: list[str] = []
            tool_uses: list[dict] = []
            stop_reason: str | None = None
            final_usage: dict = {}
            self._last_retry_count = 0

            try:
                async for event in self._stream_with_retry(
                    trimmed_messages, tool_schemas, system_prompt
                ):
                    self.event_bus.record(event)
                    # v0.2: 记录事件到执行日志
                    self.execution_log.record(event)

                    if event.type == EventType.MESSAGE_START:
                        self.state.status = AgentStatus.STREAMING

                    elif event.type == EventType.CONTENT_BLOCK_DELTA:
                        if event.data.get("type") == "text":
                            all_text.append(event.data["text"])
                            yield event
                        elif event.data.get("type") == "thinking":
                            all_thinking.append(event.data["text"])
                            if self.config.think_visibility == "show":
                                yield event

                    elif event.type == EventType.TOOL_USE:
                        # 收集工具调用 (OpenAI stream 中在 finish_reason 前产出)
                        tool_uses.append(event.data)
                        yield event

                    elif event.type == EventType.MESSAGE_STOP:
                        stop_reason = event.data.get("stop_reason", "end_turn")
                        final_usage = event.data.get("usage", {})
                        break

                    elif event.type in (
                        EventType.CONTENT_BLOCK_START,
                        EventType.CONTENT_BLOCK_STOP,
                        EventType.MESSAGE_DELTA,
                    ):
                        pass

                    elif event.type == EventType.ERROR:
                        yield event
                        is_error = True
                        break

            except Exception as e:
                logger.exception("Agent loop error during LLM stream")
                yield Event.error(str(e))
                is_error = True
                break

            if is_error:
                break

            # 记录 token 使用
            self.state.record_usage(final_usage)

            # --- 处理 LLM 响应 ---
            full_text = "".join(all_text)

            # --- 判断下一步动作 ---
            if stop_reason == "end_turn":
                final_result = full_text
                # v0.3.1: 自动反思 — 从对话中提取记忆 + 评估 Skill 更新
                self.reviewer.schedule_review(prompt, full_text)
                break

            elif stop_reason == "tool_use":
                if not tool_uses:
                    logger.warning("stop_reason=tool_use but no tool_uses collected, breaking")
                    break

                # 执行工具调用
                self.state.status = AgentStatus.TOOL_EXECUTING
                yield Event.status("tool_executing")

                # 添加 assistant 消息 (含 tool_calls)
                assistant_blocks: list[ContentBlock] = []
                for tu in tool_uses:
                    assistant_blocks.append(ContentBlock(
                        type=ContentType.TOOL_USE,
                        content=tu.get("input", {}),
                        tool_name=tu.get("name", ""),
                        tool_use_id=tu.get("id", ""),
                    ))
                if full_text:
                    assistant_blocks.append(ContentBlock(type=ContentType.TEXT, content=full_text))

                assistant_msg = Message(
                    role=MessageRole.ASSISTANT,
                    content=assistant_blocks,
                    stop_reason=stop_reason,
                    usage=final_usage,
                )
                self.state.add_message(assistant_msg)

                # 逐个执行工具
                for tu in tool_uses:
                    # v0.2: 权限检查
                    tool = self.tools.get(tu["name"])
                    perm = tool.permission if tool else "read"
                    perm_result = self.permission.check_tool(
                        tu["name"], tu.get("input", {}), perm
                    )
                    if perm_result == PermissionResult.DENY:
                        yield Event(
                            EventType.PERMISSION_DENIED,
                            data={
                                "tool": tu["name"],
                                "reason": self.permission.reason(
                                    perm_result, tu["name"],
                                    "操作被安全策略拒绝",
                                ),
                            },
                        )
                        tool_msg = self._error_tool_result(
                            tu.get("id", ""),
                            f"🚫 权限拒绝: {tu['name']} 操作被安全策略拦截",
                        )
                        self.state.add_message(tool_msg)
                        continue

                    if perm_result == PermissionResult.ASK:
                        # v0.2: ask 模式先放行，GUI 阶段接入交互确认
                        yield Event(
                            EventType.WARNING,
                            data={"warning": f"需要确认: {tu['name']} (v0.2 默认放行)"},
                        )

                    tool_msg = await self._handle_tool_use(
                        tu["name"], tu.get("input", {}), tu.get("id", "")
                    )
                    self.state.add_message(tool_msg)
                    # 产出工具结果事件
                    result_block = tool_msg.content[0] if isinstance(tool_msg.content, list) and tool_msg.content else None
                    if result_block:
                        yield Event.tool_result(
                            tool_use_id=tu.get("id", ""),
                            content=str(result_block.content),
                            is_error=tool_msg.is_error,
                        )

                    # v0.3.1: Frozen Snapshot — 写入只落盘，不更新 System Prompt

                # 工具执行完，继续循环 (把结果送回 LLM)
                continue

            elif stop_reason == "max_tokens":
                yield Event.warning("模型输出达到 max_tokens 限制，部分内容可能被截断")
                final_result = full_text
                break

            # 安全阀: 只有 thinking 没有 text，且 stop_reason 为空 → 异常
            if not full_text:
                logger.warning(
                    "Turn %d: no text output (stop_reason=%s). Breaking.",
                    self.state.current_turn, stop_reason,
                )
                break

        # --- 完成 ---
        self.state.status = AgentStatus.COMPLETED

        # v0.2: 记录成本
        cost = self.router.record_usage(
            input_tokens=self.state.total_tokens.get("input", 0),
            output_tokens=self.state.total_tokens.get("output", 0),
            model=self.state.model,
            thinking_tokens=self.state.total_tokens.get("thinking", 0),
        )

        yield Event(
            EventType.TURN_END,
            data={
                "turns": self.state.current_turn,
                "total_tokens": self.state.total_tokens,
                "cost_usd": round(cost, 6),
                "result": final_result[:500] if final_result else "(无输出)",
            },
            session_id=self.state.session_id,
            turn_number=self.state.current_turn,
        )

        yield Event(
            EventType.SESSION_END,
            data={
                "success": not is_error,
                "turns": self.state.current_turn,
                "total_tokens": self.state.total_tokens,
                "cost_usd": round(self.router.budget.spent_task, 6),
                "result": final_result[:500] if final_result else "",
            },
            session_id=self.state.session_id,
        )

        # v0.3.1: 等待后台反思完成
        await self.reviewer.wait_pending()

        # v0.2: 刷新执行日志
        log_path = self.execution_log.flush()

    def interrupt(self) -> None:
        """中断当前执行.

        借鉴 cc-haha QueryEngine.interrupt() (src/QueryEngine.ts:1158-1160):
        - 设置 abort event
        - run() 循环在下次迭代时检查并退出
        """
        if self._abort is not None:
            self._abort.set()
        self.state.status = AgentStatus.IDLE

    async def close(self) -> None:
        """清理资源."""
        await self.llm.close()

    # ─── 内部方法 ────────────────────────────────

    def _tool_context(self) -> ToolUseContext:
        """构建工具执行上下文."""
        ctx = ToolUseContext(
            workspace=self.config.workspace,
            session_id=self.state.session_id,
        )
        # v0.2: 传递 memory_dir / skill_dir 给 remember/recall 工具
        ctx.metadata["memory_dir"] = self.config.memory_dir
        ctx.metadata["skill_dir"] = self.config.skill_dir
        return ctx

    def _error_tool_result(self, tool_use_id: str, message: str) -> Message:
        """生成工具错误结果消息."""
        return Message(
            role=MessageRole.USER,
            content=[
                ContentBlock(
                    type=ContentType.TOOL_RESULT,
                    content=message,
                    tool_use_id=tool_use_id,
                )
            ],
            tool_use_id=tool_use_id,
            is_error=True,
        )

    async def _stream_with_retry(
        self, messages: list[Message], tool_schemas: list[dict], system_prompt: str
    ) -> AsyncGenerator[Event, None]:
        """带重试的 LLM 流式调用.

        v0.2: 包裹 LLM stream，处理 429/5xx 自动重试.
        """
        policy = self.retry_policy
        last_error: Exception | None = None

        for attempt in range(policy.max_retries + 1):
            try:
                async for event in self.llm.stream(messages, tool_schemas, system_prompt):
                    if event.type == EventType.ERROR:
                        error_msg = event.data.get("error", "")
                        # 检查是否是 retryable HTTP 错误
                        raise RuntimeError(error_msg)
                    yield event
                # 成功 → 返回
                self._last_retry_count = attempt
                return
            except Exception as e:
                last_error = e
                category = ErrorClassifier.classify(e)

                if category.value == "fatal":
                    yield Event.error(f"Fatal error: {e}")
                    return

                if not policy.should_retry(attempt):
                    yield Event.error(
                        f"Retries exhausted ({attempt + 1} attempts). Last error: {e}"
                    )
                    return

                delay = policy.retry_after(attempt)
                yield Event(
                    EventType.RETRY,
                    data={
                        "attempt": attempt + 1,
                        "max_retries": policy.max_retries,
                        "delay": round(delay, 1),
                        "error": str(e)[:200],
                    },
                )
                await asyncio.sleep(delay)

        # 不应到达这里
        if last_error:
            yield Event.error(f"Unexpected: {last_error}")

    async def _handle_tool_use(self, tool_name: str, tool_input: dict, tool_use_id: str) -> Message:
        """处理工具调用: 权限检查 → 执行 → 格式化结果."""
        tool = self.tools.get(tool_name)
        if tool is None:
            return self._error_tool_result(tool_use_id, f"未知工具: {tool_name}")

        logger.info(
            "Tool call: %s(%s)",
            tool_name,
            str(tool_input)[:200],
        )

        try:
            result = await tool.call(tool_input, self._tool_context())
        except Exception as e:
            logger.exception("Tool execution error: %s", tool_name)
            return self._error_tool_result(tool_use_id, str(e))

        return Message(
            role=MessageRole.USER,
            content=[
                ContentBlock(
                    type=ContentType.TOOL_RESULT,
                    content=result.data,
                    tool_use_id=tool_use_id,
                )
            ],
            tool_use_id=tool_use_id,
            is_error=result.is_error,
        )
