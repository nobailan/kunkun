"""事件分发链 — 替代 agent_loop 中的 if-elif 链.

设计:
- EventHandler: 注册到特定事件类型, 处理事件后返回 EventContext 变更
- EventDispatchChain: 按注册顺序调用 handler, 首个匹配的获胜
- EventContext: 在 handler 间传递的共享状态

借鉴: AgentThink 分析报告方案 B (EventDispatch + Strategy)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from kunkun.core.events import Event, EventType

# ─── EventContext ───────────────────────────────────────


@dataclass
class EventContext:
    """事件处理共享状态.

    在 dispatch chain 中被各 handler 读写.
    """

    all_text: list[str] = field(default_factory=list)
    all_thinking: list[str] = field(default_factory=list)
    tool_uses: list[dict] = field(default_factory=list)
    stop_reason: str | None = None
    final_usage: dict = field(default_factory=dict)
    is_error: bool = False


# ─── EventHandler ───────────────────────────────────────

EventHandler = Callable[..., Awaitable[bool]]
"""事件处理器签名.

Args:
    event: 要处理的事件
    ctx: 共享上下文

Returns:
    True 如果事件被处理 (停止链), False 继续传递给下一个 handler
"""


# ─── EventDispatchChain ─────────────────────────────────


class EventDispatchChain:
    """事件分发责任链.

    借鉴 Chain of Responsibility 模式:
    - 按注册顺序调用 handler
    - 首个返回 True 的 handler 结束分发
    - 未匹配的事件被静默忽略

    Usage:
        chain = EventDispatchChain()
        chain.on(EventType.CONTENT_BLOCK_DELTA, text_handler)
        chain.on(EventType.TOOL_USE, tool_handler)

        async for event in llm_stream:
            chain.dispatch(event, ctx)
    """

    def __init__(self):
        self._handlers: list[tuple[EventType, EventHandler]] = []

    def on(self, event_type: EventType, handler: EventHandler) -> "EventDispatchChain":
        """注册事件处理器 (链式调用).

        Args:
            event_type: 要处理的事件类型
            handler: async (event, ctx) -> bool
        """
        self._handlers.append((event_type, handler))
        return self

    def on_many(self, event_types: list[EventType], handler: EventHandler) -> "EventDispatchChain":
        """注册处理器到多个事件类型."""
        for et in event_types:
            self._handlers.append((et, handler))
        return self

    async def dispatch(self, event: Event, ctx: EventContext) -> bool:
        """分发事件.

        遍历所有匹配 event.type 的 handler, 首个返回 True 的停止.
        未匹配则返回 False (静默忽略).

        Returns:
            True 如果事件被处理
        """
        for et, handler in self._handlers:
            if event.type == et:
                handled = await handler(event, ctx)
                if handled:
                    return True
        return False


# ─── 内置 Handler ───────────────────────────────────────


def make_text_handler() -> EventHandler:
    """创建文本增量处理器."""
    async def _handle(event: Event, ctx: EventContext) -> bool:
        if event.data.get("type") == "text":
            ctx.all_text.append(event.data["text"])
            return True
        return False
    return _handle


def make_thinking_handler(think_visible: bool = True) -> EventHandler:
    """创建思考增量处理器."""
    async def _handle(event: Event, ctx: EventContext) -> bool:
        if event.data.get("type") == "thinking":
            ctx.all_thinking.append(event.data["text"])
            return think_visible  # hide 模式仍收集但不标记为已处理
        return False
    return _handle


def make_tool_use_handler() -> EventHandler:
    """创建工具调用处理器."""
    async def _handle(event: Event, ctx: EventContext) -> bool:
        ctx.tool_uses.append(event.data)
        return True
    return _handle


def make_message_stop_handler() -> EventHandler:
    """创建消息结束处理器."""
    async def _handle(event: Event, ctx: EventContext) -> bool:
        ctx.stop_reason = event.data.get("stop_reason", "end_turn")
        ctx.final_usage = event.data.get("usage", {})
        return True
    return _handle


def make_error_handler() -> EventHandler:
    """创建错误处理器."""
    async def _handle(event: Event, ctx: EventContext) -> bool:
        ctx.is_error = True
        return True
    return _handle


def make_passthrough_handler() -> EventHandler:
    """创建透传处理器 (CONTENT_BLOCK_START/STOP, MESSAGE_DELTA)."""
    async def _handle(event: Event, ctx: EventContext) -> bool:
        return True  # 已处理 (静默吞掉)
    return _handle


# ─── StopReason 策略 ────────────────────────────────────


@dataclass
class StopReasonResult:
    """stop_reason 处理结果."""

    action: str  # "break" | "continue" | "break_warn"
    message: str = ""
    final_result: str = ""


class StopReasonRouter:
    """按 stop_reason 分发处理策略.

    借鉴 Strategy 模式:
    - 每种 stop_reason 对应一个处理函数
    - 返回统一的 StopReasonResult
    """

    def __init__(self):
        self._strategies: dict[str, Callable[[EventContext], StopReasonResult]] = {}

    def register(self, reason: str, strategy: Callable[[EventContext], StopReasonResult]) -> None:
        self._strategies[reason] = strategy

    def route(self, ctx: EventContext, has_tool_uses: bool) -> StopReasonResult:
        reason = ctx.stop_reason or "end_turn"
        # 特殊情况: tool_use 但没收集到 tool → 安全刹车
        if reason == "tool_use" and not has_tool_uses:
            return StopReasonResult(action="break", message="stop_reason=tool_use but no tool_uses collected")
        strategy = self._strategies.get(reason)
        if strategy:
            return strategy(ctx)
        return StopReasonResult(action="break")  # 未知 reason, 安全退出


# ─── 默认策略集 ─────────────────────────────────────────


def default_stop_reason_router() -> StopReasonRouter:
    """创建默认的 stop_reason 路由器."""
    router = StopReasonRouter()

    router.register(
        "end_turn",
        lambda ctx: StopReasonResult(
            action="break",
            final_result="".join(ctx.all_text),
        ),
    )

    router.register(
        "max_tokens",
        lambda ctx: StopReasonResult(
            action="break_warn",
            message="模型输出达到 max_tokens 限制，部分内容可能被截断",
            final_result="".join(ctx.all_text),
        ),
    )

    router.register(
        "tool_use",
        lambda ctx: StopReasonResult(action="continue"),
    )

    return router
