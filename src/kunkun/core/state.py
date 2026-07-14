"""AgentState — 核心状态定义与配置管理.

借鉴:
- cc-haha src/bootstrap/state.ts — 全局 STATE 单例模式
- cc-haha src/state/AppStateStore.ts — Zustand 风格状态管理
- Hermes agent/context_engine.py — Token 追踪

设计原则:
- dataclass 轻量，避免 LangGraph 的 StateGraph 复杂度
- TypedDict 用于 API 边界，dataclass 用于内部状态
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

# ─── 枚举定义 ──────────────────────────────────────


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ContentType(str, Enum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"


class AgentStatus(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"  # DSv4 ThinkBlock 活跃中
    TOOL_EXECUTING = "tool_executing"
    STREAMING = "streaming"  # 文本流式输出中
    ERROR = "error"
    COMPLETED = "completed"


# ─── 消息/内容块 ───────────────────────────────────


@dataclass
class ContentBlock:
    """消息中的单个内容块.

    借鉴 cc-haha ContentBlock 联合类型:
    - type: "text" | "thinking" | "tool_use" | "tool_result"
    - content: 具体数据
    """

    type: ContentType
    content: str | dict  # text → str, tool_use/tool_result → dict
    # tool_use 专用字段
    tool_name: str | None = None
    tool_use_id: str | None = None


@dataclass
class Message:
    """对话消息.

    借鉴 cc-haha Message union type (src/types/message.ts):
    - 统一消息类型，role + content_blocks
    - usage 追踪 (token 统计)
    - thinking_content 分离存储 (DSv4 特有)
    """

    role: MessageRole
    content: str | list[ContentBlock]
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    # 元数据
    usage: dict | None = None  # {"input_tokens": N, "output_tokens": N}
    stop_reason: str | None = None  # "end_turn" | "tool_use" | "max_tokens"
    thinking_content: str | None = None  # DSv4 ThinkBlock 内容
    tool_use_id: str | None = None
    is_error: bool = False


# ─── 工具结果 ──────────────────────────────────────


@dataclass
class ToolResult:
    """工具执行结果.

    借鉴 cc-haha ToolResult<T> (src/Tool.ts:321-336):
    - data: 工具返回数据
    - is_error: 是否出错
    - new_messages: 可选的附加消息
    """

    data: str
    is_error: bool = False
    new_messages: list[Message] = field(default_factory=list)


# ─── AgentState ────────────────────────────────────


@dataclass
class AgentState:
    """Agent 会话状态.

    借鉴 cc-haha QueryEngine 的 mutable state (src/QueryEngine.ts:186-198):
    - messages: 消息历史
    - current_turn: 当前轮次
    - total_tokens: token 统计 (input/output/thinking)
    - status: Agent 当前状态

    借鉴 Hermes agent/context_engine.py 的 Token 追踪:
    - last_prompt_tokens / last_completion_tokens / last_total_tokens
    """

    messages: list[Message] = field(default_factory=list)
    current_turn: int = 0
    total_tokens: dict = field(
        default_factory=lambda: {"input": 0, "output": 0, "thinking": 0}
    )
    tool_calls: list[dict] = field(default_factory=list)
    start_time: float = field(default_factory=lambda: datetime.now().timestamp())
    model: str = "deepseek-v4-pro"
    status: AgentStatus = AgentStatus.IDLE
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Token 追踪 (借鉴 Hermes ContextEngine)
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    # 预算
    budget_remaining: float | None = None

    def add_message(self, msg: Message) -> None:
        """添加消息到历史."""
        self.messages.append(msg)

    def record_usage(self, usage: dict) -> None:
        """记录 token 使用 (借鉴 Hermes context_engine.update_from_response)."""
        self.last_prompt_tokens = usage.get("input_tokens", 0)
        self.last_completion_tokens = usage.get("output_tokens", 0)
        self.last_total_tokens = self.last_prompt_tokens + self.last_completion_tokens
        self.total_tokens["input"] += self.last_prompt_tokens
        self.total_tokens["output"] += self.last_completion_tokens
        self.total_tokens["thinking"] += usage.get("thinking_tokens", 0)

    @property
    def total_token_count(self) -> int:
        """总 token 消耗."""
        return self.total_tokens["input"] + self.total_tokens["output"]


# ─── 配置 ──────────────────────────────────────────


@dataclass
class HarnessConfig:
    """Kunkun 全局配置.

    借鉴 cc-haha QueryEngineConfig (src/QueryEngine.ts:130-173):
    - cwd, tools, maxTurns, maxBudgetUsd, thinkingConfig

    借鉴 Hermes ProviderProfile (providers/base.py):
    - base_url, api_key, model 声明式配置
    """

    # LLM 配置
    model: str = "deepseek-v4-pro"
    light_model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""

    # Agent 配置
    max_turns: int = 50
    max_tokens_per_turn: int = 64000
    max_budget_usd: float = 5.0
    daily_budget_usd: float = 20.0
    review_interval: int = 5  # v0.9: 每 N 个 user turn 触发一次中间 review

    # 路径配置
    workspace: str = "."
    memory_dir: str = ".kun/memory"
    report_dir: str = ".kun/reports"
    skill_dir: str = "skills"

    # 权限模式
    permission_mode: Literal["default", "accept_edits", "bypass"] = "default"

    # 显示配置
    locale: Literal["zh", "en", "auto"] = "auto"
    think_visibility: Literal["show", "hide"] = "show"
    verbose: bool = False

    @classmethod
    def from_env(cls) -> "HarnessConfig":
        """Load config from environment variables."""
        import os

        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        return cls(
            model=os.getenv("KUN_MODEL", "deepseek-v4-pro"),
            light_model=os.getenv("KUN_LIGHT_MODEL", "deepseek-v4-flash"),
            base_url=os.getenv("KUN_BASE_URL", "https://api.deepseek.com"),
            api_key=os.getenv("KUN_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")),
            max_turns=int(os.getenv("KUN_MAX_TURNS", "50")),
            max_tokens_per_turn=int(
                os.getenv("KUN_MAX_TOKENS_PER_TURN", "64000")
            ),
            max_budget_usd=float(os.getenv("KUN_MAX_BUDGET_USD", "5.0")),
            workspace=os.getenv("KUN_WORKSPACE", "."),
            permission_mode=os.getenv(
                "KUN_PERMISSION_MODE", "default"
            ),
            verbose=os.getenv("KUN_VERBOSE", "").lower() in ("1", "true", "yes"),
        )
