"""思考控制 — DSv4 ThinkBlock 实时干预.

三条 Harness 级干预策略 (非模型训练):

1. Dynamic Thinking Budget: thinking token 超阈值 → 截断流 → 强制行动
2. State Re-routing: 检测过度思考模式 → 中断当前轮 → 注入反思指令
3. Parallel Fast/Slow: 双通道竞速 → 快通道成功即杀慢通道

借鉴: AgentThink 论文 + DS 网页版建议
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from kunkun.core.events import Event, EventType

logger = logging.getLogger(__name__)


# ─── 1. Dynamic Thinking Budget ───────────────────────


@dataclass
class ThinkingBudget:
    """思考 Token 预算.

    Attributes:
        max_thinking_chars: thinking 最大字符数 (默认 2000)
        chars_since_last_action: 上次工具调用后的 thinking 字符数
        forced_action: 超出预算时是否已触发强制行动
    """

    max_thinking_chars: int = 2000
    chars_since_last_action: int = 0
    forced_action: bool = False
    total_thinking_chars: int = 0

    def feed(self, thinking_text: str) -> bool:
        """喂入 thinking chunk.

        Returns:
            True 如果超出预算需要强制行动
        """
        self.total_thinking_chars += len(thinking_text)
        self.chars_since_last_action += len(thinking_text)
        return (
            not self.forced_action
            and self.chars_since_last_action > self.max_thinking_chars
        )

    def on_action(self) -> None:
        """工具调用时重置计数器."""
        self.chars_since_last_action = 0
        self.forced_action = False

    def reset(self) -> None:
        """重置预算."""
        self.chars_since_last_action = 0
        self.forced_action = False
        self.total_thinking_chars = 0

    def on_force(self) -> None:
        """标记已触发强制行动."""
        self.forced_action = True

    @property
    def is_over_budget(self) -> bool:
        return self.chars_since_last_action > self.max_thinking_chars

    @property
    def summary(self) -> dict:
        return {
            "total_thinking": self.total_thinking_chars,
            "since_last_action": self.chars_since_last_action,
            "budget": self.max_thinking_chars,
            "forced": self.forced_action,
        }


FORCE_ACTION_INSTRUCTION = (
    "\n\n[系统指令: 思考已超出预算。立即停止分析，直接调用最合适的工具开始执行。"
    "如果不知道用什么工具，先用 glob 了解项目结构或 grep 搜索关键代码。]"
)


# ─── 2. State Re-routing ──────────────────────────────


@dataclass
class OverthinkingDetector:
    """过度思考模式检测.

    检测模式:
    - 重复规划: thinking 中反复出现相同的工具名或文件名
    - 自我犹豫: "maybe" / "perhaps" / "不确定" 累计出现
    - 无动作循环: 连续 N 轮 thinking 但无 tool_call
    """

    max_idle_rounds: int = 3
    idle_rounds: int = 0
    recent_tool_calls: list[str] = field(default_factory=list)  # "tool_name:key_param"
    hesitation_count: int = 0
    repeat_threshold: int = 3

    # 犹豫信号词
    HESITATION_WORDS = [
        "maybe", "perhaps", "不确定", "也许", "或者", "可能",
        "让我想想", "让我再看看", "让我重新", "wait", "let me",
    ]

    def feed_thinking(self, text: str) -> None:
        """喂入 thinking 文本进行检测."""
        text_lower = text.lower()
        for word in self.HESITATION_WORDS:
            if word in text_lower:
                self.hesitation_count += 1

    def feed_tool_call(self, tool_name: str, tool_input: dict | None = None) -> None:
        """记录工具调用 (名称+关键参数)."""
        # 提取关键参数做指纹, 区分"读3个不同文件"和"同一文件读3次"
        key = ""
        if tool_input:
            for k in ("file_path", "path", "pattern", "query", "symbol"):
                val = tool_input.get(k, "")
                if val:
                    key = val
                    break
        fingerprint = f"{tool_name}:{key}" if key else tool_name
        self.recent_tool_calls.append(fingerprint)
        self.idle_rounds = 0
        self.hesitation_count = 0  # 成功行动 → 重置犹豫计数

    def on_new_turn(self) -> None:
        """新一轮开始."""
        self.idle_rounds += 1

    def reset(self) -> None:
        """重置所有状态 (编排者调用子 Agent 后使用)."""
        self.recent_tool_calls.clear()
        self.idle_rounds = 0
        self.hesitation_count = 0

    def needs_intervention(self) -> tuple[bool, str]:
        """判断是否需要干预.

        Returns:
            (是否需要干预, 干预原因)
        """
        if self.idle_rounds >= self.max_idle_rounds:
            return True, f"连续 {self.idle_rounds} 轮无工具调用，陷入循环"

        if self.hesitation_count >= 5:
            return True, f"犹豫信号累计 {self.hesitation_count} 次，可能分析瘫痪"

        # 检查工具重复调用 (同一工具 + 同一参数才算重复)
        if len(self.recent_tool_calls) >= self.repeat_threshold * 2:
            recent = self.recent_tool_calls[-self.repeat_threshold :]
            if len(set(recent)) == 1:
                return True, f"连续 {self.repeat_threshold} 次调用 {recent[0]} (完全重复)"

        return False, ""


ROLLBACK_INSTRUCTION = (
    "\n\n[系统指令: 检测到过度思考模式。停止当前路径，"
    "换一种不同的方法重试。如果之前的方案一直不生效，"
    "从第一步重新开始，用完全不同的思路。]"
)


# ─── 3. Parallel Fast/Slow ─────────────────────────────


@dataclass
class FastSlowResult:
    """快慢通道竞速结果."""

    winner: str  # "fast" | "slow"
    fast_result: str = ""
    slow_result: str = ""
    fast_time_ms: float = 0
    slow_time_ms: float = 0
    slow_killed: bool = False

    @property
    def is_fast_win(self) -> bool:
        return self.winner == "fast"


async def run_fast_slow_race(
    fast_coro,
    slow_coro,
    fast_timeout: float = 8.0,
    slow_timeout: float = 30.0,
) -> FastSlowResult:
    """快慢通道竞速.

    Args:
        fast_coro: 快通道协程 (无 thinking, 直接行动)
        slow_coro: 慢通道协程 (有 thinking, 深度推理)
        fast_timeout: 快通道超时
        slow_timeout: 慢通道超时

    Returns:
        FastSlowResult
    """
    import time

    result = FastSlowResult(winner="slow")
    fast_task: asyncio.Task | None = None
    slow_task: asyncio.Task | None = None

    try:
        fast_task = asyncio.create_task(asyncio.wait_for(fast_coro, timeout=fast_timeout))
        slow_task = asyncio.create_task(asyncio.wait_for(slow_coro, timeout=slow_timeout))

        t0 = time.monotonic()

        # 竞速: 等快通道或慢通道任一完成
        done, pending = await asyncio.wait(
            [fast_task, slow_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if fast_task in done:
            # 快通道先完成
            try:
                fast_result_data = fast_task.result()
                result.winner = "fast"
                result.fast_result = str(fast_result_data)
                result.fast_time_ms = (time.monotonic() - t0) * 1000
                # 杀慢通道
                if slow_task in pending:
                    slow_task.cancel()
                    result.slow_killed = True
                    logger.info("Fast path won, killing slow path")
            except Exception as e:
                logger.debug("Fast path failed: %s", e)
                # 快通道失败 → 等慢通道
                if slow_task in pending:
                    try:
                        slow_result_data = await slow_task
                        result.winner = "slow"
                        result.slow_result = str(slow_result_data)
                    except Exception:
                        result.winner = "none"
            finally:
                # 取消所有 pending
                for t in pending:
                    if not t.done():
                        t.cancel()
        else:
            # 慢通道先完成 (不太可能但处理一下)
            try:
                slow_result_data = slow_task.result()
                result.winner = "slow"
                result.slow_result = str(slow_result_data)
            except Exception:
                pass
            if fast_task in pending:
                fast_task.cancel()

        result.slow_time_ms = (time.monotonic() - t0) * 1000

    except Exception as e:
        logger.debug("Fast/slow race failed: %s", e)
        result.winner = "none"

    return result
