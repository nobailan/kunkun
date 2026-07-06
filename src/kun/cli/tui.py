"""CLI 终端交互 — Rich 渲染.

借鉴:
- cc-haha src/screens/REPL.tsx — Ink/React TUI 组件化
- Hermes KawaiiSpinner (agent/display.py) — 可爱的加载动画
- Hermes Rich 终端渲染

v0.1: Rich 库实现的终端渲染
  - ThinkBlock 灰色斜体
  - Tool Call 高亮
  - 流式 Markdown 输出
  - 彩色状态展示

v0.2 升级: prompt_toolkit 全功能 TUI
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import AsyncGenerator

from kun.core.events import Event, EventType
from kun.core.state import AgentStatus

logger = logging.getLogger(__name__)


class ConsoleRenderer:
    """终端渲染器 — 将 Event 流转换为 Rich 格式化输出.

    借鉴 cc-haha Ink 组件化渲染 + Hermes rich 输出:
    - text: 白色正文
    - thinking: 灰色斜体 (DSv4 ThinkBlock)
    - tool_use: 青色高亮
    - tool_result: 绿色
    - error: 红色粗体
    - status: 黄色状态栏
    """

    def __init__(self, think_visible: bool = True, verbose: bool = False):
        self.think_visible = think_visible
        self.verbose = verbose
        self._console = None
        self._in_text_block = False
        self._in_thinking_block = False

    @property
    def console(self):
        """Lazy init Rich Console."""
        if self._console is None:
            from rich.console import Console
            from rich.theme import Theme

            theme = Theme(
                {
                    "thinking": "italic dim",
                    "tool.call": "bold cyan",
                    "tool.result": "green",
                    "tool.error": "bold red",
                    "status": "yellow",
                    "error": "bold red",
                    "warning": "yellow",
                    "info": "blue",
                }
            )
            self._console = Console(theme=theme)
        return self._console

    async def render_stream(
        self, events: AsyncGenerator[Event, None]
    ) -> str:
        """渲染事件流到终端.

        返回最终结果文本 (用于 -p 模式).
        """
        final_result = ""
        start_time = datetime.now()

        async for event in events:
            self._render_event(event)

            # 提取最终结果
            if event.type == EventType.SESSION_END:
                result = event.data.get("result", "")
                if result:
                    final_result = result

        elapsed = (datetime.now() - start_time).total_seconds()
        self._print_footer(elapsed)

        return final_result

    def _render_event(self, event: Event) -> None:
        """渲染单个事件."""
        et = event.type

        # --- LLM Stream ---
        if et == EventType.CONTENT_BLOCK_START:
            ct = event.data.get("type", "")
            if ct == "thinking" and self.think_visible:
                self._in_thinking_block = True
                self.console.print("\n[thinking]💭 思考中...[/]")
            elif ct == "text":
                self._in_text_block = True

        elif et == EventType.CONTENT_BLOCK_DELTA:
            ct = event.data.get("type", "")
            if ct == "text":
                self.console.print(event.data["text"], end="")
            elif ct == "thinking" and self.think_visible:
                self.console.print(f"[thinking]{event.data['text']}[/]", end="")

        elif et == EventType.CONTENT_BLOCK_STOP:
            ct = event.data.get("type", "")
            if ct == "thinking":
                self._in_thinking_block = False
                self.console.print("\n[thinking]✅ 思考完成[/]\n")
            elif ct == "text":
                self._in_text_block = False
                self.console.print()  # 换行

        # --- Tool ---
        elif et == EventType.TOOL_USE:
            tool_data = event.data
            name = tool_data.get("name", "?")
            inp = tool_data.get("input", {})
            # 截断显示
            inp_str = str(inp)
            if len(inp_str) > 150:
                inp_str = inp_str[:150] + "..."
            self.console.print(f"\n[tool.call]🔧 {name}[/] {inp_str}")

        elif et == EventType.TOOL_RESULT:
            content = event.data.get("content", "")
            is_err = event.data.get("is_error", False)
            preview = str(content)[:300]
            if len(str(content)) > 300:
                preview += "..."
            style = "tool.error" if is_err else "tool.result"
            self.console.print(f"[{style}]📋 {preview}[/]")

        # --- System ---
        elif et == EventType.ERROR:
            self.console.print(f"\n[error]❌ {event.data.get('error', 'Unknown error')}[/]")

        elif et == EventType.WARNING:
            self.console.print(f"\n[warning]⚠️ {event.data.get('warning', '')}[/]")

        elif et == EventType.RETRY:
            self.console.print(
                f"[warning]🔄 重试中 (第 {event.data.get('attempt', '?')} 次)...[/]"
            )

        # --- Lifecycle ---
        elif et == EventType.TURN_START:
            if self.verbose:
                prompt = event.data.get("prompt", "")[:100]
                self.console.print(f"[info]🚀 开始处理: {prompt}...[/]")

        elif et == EventType.STATUS_CHANGE:
            status = event.data.get("status", "")
            emoji = {
                "thinking": "🤔",
                "streaming": "📝",
                "tool_executing": "🔧",
                "completed": "✅",
                "error": "❌",
            }.get(status, "•")
            if self.verbose:
                self.console.print(f"[status]{emoji} {status}[/]")

        # --- SESSION_START / TURN_END / SESSION_END ---
        elif et in (EventType.SESSION_START, EventType.MESSAGE_START, EventType.MESSAGE_DELTA):
            pass  # 不渲染这些内部事件

    def _print_footer(self, elapsed: float) -> None:
        """打印执行摘要."""
        self.console.print(f"\n[dim]⏱ 总耗时: {elapsed:.1f}s[/]")


class HeadlessRenderer:
    """无头渲染器 — 用于 -p/--print 模式和脚本调用.

    简化输出: 只显示 text 和 error，不显示 thinking 和 status.
    """

    async def render_stream(
        self, events: AsyncGenerator[Event, None]
    ) -> str:
        """消费事件流，返回最终文本结果."""
        final_result = ""
        async for event in events:
            if event.type == EventType.CONTENT_BLOCK_DELTA:
                ct = event.data.get("type", "")
                if ct == "text":
                    print(event.data["text"], end="", flush=True)
            elif event.type == EventType.ERROR:
                print(f"\n[ERROR] {event.data.get('error', '')}", file=__import__("sys").stderr)
            elif event.type == EventType.SESSION_END:
                final_result = event.data.get("result", "")
        print(flush=True)
        return final_result
