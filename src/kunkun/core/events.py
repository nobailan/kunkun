"""事件系统 — Agent Loop 的统一输出协议.

借鉴:
- cc-haha SDKMessage union type (src/entrypoints/agentSdkTypes.ts)
- cc-haha query.ts AsyncGenerator 模式
- OpenCode LLMEvent + EventV2Bridge

设计原则:
- 所有 Agent Loop 输出统一为 Event 类型
- AsyncGenerator[Event] 流式输出
- EventBus 收集事件用于评测 (v0.3)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """事件类型枚举.

    借鉴 cc-haha SDKMessage 的各子类型:
    - assistant, user, progress, stream_event, system, result
    """

    # ── LLM Stream ──
    MESSAGE_START = "message_start"
    CONTENT_BLOCK_START = "content_block_start"
    CONTENT_BLOCK_DELTA = "content_block_delta"
    CONTENT_BLOCK_STOP = "content_block_stop"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_STOP = "message_stop"

    # ── Tool ──
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"

    # ── System ──
    PERMISSION_DENIED = "permission_denied"
    ERROR = "error"
    RETRY = "retry"
    WARNING = "warning"

    # ── Lifecycle ──
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # ── Status ──
    STATUS_CHANGE = "status_change"


@dataclass
class Event:
    """Agent Loop 的统一事件.

    借鉴 cc-haha SDKMessage:
    - 每个事件有 type + data + 元数据
    - 通过 AsyncGenerator 流式产出
    """

    type: EventType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    session_id: str = ""
    turn_number: int = 0

    @classmethod
    def text_delta(cls, text: str, **kwargs) -> "Event":
        """创建文本增量事件."""
        return cls(
            type=EventType.CONTENT_BLOCK_DELTA,
            data={"type": "text", "text": text},
            **kwargs,
        )

    @classmethod
    def thinking_delta(cls, text: str, **kwargs) -> "Event":
        """创建思考增量事件 (DSv4 ThinkBlock)."""
        return cls(
            type=EventType.CONTENT_BLOCK_DELTA,
            data={"type": "thinking", "text": text},
            **kwargs,
        )

    @classmethod
    def tool_use(cls, name: str, tool_id: str, input: dict, **kwargs) -> "Event":
        """创建工具调用事件."""
        return cls(
            type=EventType.TOOL_USE,
            data={"name": name, "id": tool_id, "input": input},
            **kwargs,
        )

    @classmethod
    def tool_result(
        cls, tool_use_id: str, content: str, is_error: bool = False, **kwargs
    ) -> "Event":
        """创建工具结果事件."""
        return cls(
            type=EventType.TOOL_RESULT,
            data={"tool_use_id": tool_use_id, "content": content, "is_error": is_error},
            **kwargs,
        )

    @classmethod
    def error(cls, message: str, **kwargs) -> "Event":
        """创建错误事件."""
        return cls(
            type=EventType.ERROR,
            data={"error": message},
            **kwargs,
        )

    @classmethod
    def status(cls, status: str, **kwargs) -> "Event":
        """创建状态变更事件."""
        return cls(
            type=EventType.STATUS_CHANGE,
            data={"status": status},
            **kwargs,
        )


class EventBus:
    """事件总线 — 收集和查询事件 (v0.3 评测用).

    借鉴 FlowForge 评估引擎的 EventBus 模式:
    - record() 收集事件
    - drain() 获取所有事件用于分析
    """

    def __init__(self):
        self._events: list[Event] = []

    def record(self, event: Event) -> None:
        self._events.append(event)

    def drain(self) -> list[Event]:
        """导出所有事件并清空."""
        events = self._events[:]
        self._events.clear()
        return events

    def count_by_type(self, event_type: EventType) -> int:
        """按类型统计事件数量."""
        return sum(1 for e in self._events if e.type == event_type)
