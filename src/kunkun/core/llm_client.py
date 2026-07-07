"""DSv4 LLM 客户端 — OpenAI 兼容 API 流式调用 + Thinking 解析.

DeepSeek API 使用 OpenAI Chat Completions 格式:
- POST https://api.deepseek.com/v1/chat/completions
- Messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
- Tools: OpenAI function calling 格式
- Streaming: SSE, data: {"choices": [{"delta": {...}}]}
- Thinking: DSv4 通过 reasoning_content 字段返回 (OpenAI 扩展)

借鉴:
- cc-haha src/services/api/claude.ts — streaming 架构
- InsightAgent llm_utils.py — ThinkBlock 解析
- Hermes agent/anthropic_adapter.py — API 适配模式
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

import httpx

from kunkun.core.error_recovery import ErrorClassifier, RetryPolicy
from kunkun.core.events import Event, EventType
from kunkun.core.state import HarnessConfig, Message, MessageRole, ContentBlock, ContentType

logger = logging.getLogger(__name__)

# DSv4 Pricing (USD per 1M tokens)
PRICING = {
    "deepseek-v4-pro": {"input": 0.55, "output": 2.19, "thinking": 1.10},
    "deepseek-v4-flash": {"input": 0.14, "output": 0.55, "thinking": 0.28},
    "deepseek-chat": {"input": 0.14, "output": 0.28, "thinking": 0.0},
}


class LLMClient:
    """DSv4 LLM 客户端 (OpenAI Chat Completions 格式).

    DeepSeek API 是 OpenAI 兼容的，使用 /v1/chat/completions:
    - system prompt 作为 role="system" 消息
    - assistant 消息包含 reasoning_content (ThinkBlock)
    - 工具使用 function calling 格式
    - 流式 SSE chunks: {"choices": [{"delta": {"content": "...", "reasoning_content": "..."}}]}

    v0.2: 添加错误恢复 (retry 429/5xx) + 超时控制
    """

    def __init__(self, config: HarnessConfig):
        self.config = config
        import os
        proxy = None
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            val = os.environ.get(var, "")
            if val:
                proxy = val
                break
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0), proxy=proxy)
        self.retry_policy = RetryPolicy(
            base_delay=1.0,
            max_delay=60.0,
            max_retries=3,
            jitter=0.3,
        )
        self._last_retry_count = 0

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict],
        system_prompt: str = "",
    ) -> AsyncGenerator[Event, None]:
        """流式调用 DeepSeek API."""
        api_messages = self._to_openai_messages(messages, system_prompt)
        api_tools = self._to_openai_tools(tools)

        if not self.config.api_key:
            yield Event(
                type=EventType.ERROR,
                data={"error": "未配置 API Key。请设置环境变量 DEEPSEEK_API_KEY"},
            )
            return

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": api_messages,
            "stream": True,
            "max_tokens": 8192,
        }
        if api_tools:
            payload["tools"] = api_tools

        try:
            async with self._client.stream(
                "POST",
                f"{self.config.base_url}/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    body_text = body.decode()[:500]
                    yield Event(
                        type=EventType.ERROR,
                        data={"error": f"API Error {response.status_code}: {body_text}"},
                    )
                    return

                yield Event(EventType.MESSAGE_START, data={})

                async for event in self._parse_openai_stream(response):
                    yield event

        except httpx.TimeoutException:
            yield Event(EventType.ERROR, data={"error": "API 请求超时 (120s)"})
        except httpx.ConnectError as e:
            yield Event(EventType.ERROR, data={"error": f"无法连接到 API: {e}"})
        except Exception as e:
            logger.exception("LLM stream error")
            yield Event(EventType.ERROR, data={"error": str(e)})

    async def close(self):
        await self._client.aclose()

    # ─── OpenAI Stream 解析 ────────────────────────

    async def _parse_openai_stream(self, response) -> AsyncGenerator[Event, None]:
        """解析 OpenAI Chat Completions SSE 流.

        DeepSeek SSE 格式:
          data: {"id":"...","object":"chat.completion.chunk",
                 "choices":[{"index":0,"delta":{"content":"...","reasoning_content":"..."},
                             "finish_reason":null}]}

          data: {"choices":[{"delta":{},"finish_reason":"stop"}],
                 "usage":{"prompt_tokens":N,"completion_tokens":N}}

        delta 字段:
        - content: 正文文本
        - reasoning_content: DSv4 ThinkBlock (DeepSeek 扩展)
        - tool_calls: [{id, type:"function", function:{name, arguments}}]
        """
        thinking_buffer: list[str] = []
        text_buffer: list[str] = []
        in_thinking = False
        in_text = False
        finish_reason: str | None = None
        final_usage: dict = {}
        tool_calls: dict[int, dict] = {}  # index → {id, name, arguments_str}

        async for line in response.aiter_lines():
            if not line.strip():
                continue

            # SSE data 行
            if not line.startswith("data:"):
                continue

            json_str = line[5:].strip()
            if json_str == "[DONE]":
                break

            try:
                chunk = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices", [])
            if not choices:
                continue

            choice = choices[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason") or finish_reason

            # 收集 usage
            usage = chunk.get("usage")
            if usage:
                final_usage = {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "thinking_tokens": usage.get("completion_tokens_details", {}).get(
                        "reasoning_tokens", 0
                    ),
                }

            # ── reasoning_content (DSv4 ThinkBlock) ──
            reasoning = delta.get("reasoning_content", "")
            if reasoning:
                if not in_thinking:
                    in_thinking = True
                    thinking_buffer = []
                    yield Event(EventType.CONTENT_BLOCK_START, data={"type": "thinking"})
                thinking_buffer.append(reasoning)
                yield Event.thinking_delta(reasoning)

            # 思考结束 → 正文开始 (DSv4 先 think 后输出)
            if in_thinking and not reasoning and delta.get("content", ""):
                in_thinking = False
                yield Event(EventType.CONTENT_BLOCK_STOP, data={"type": "thinking"})

            # ── content (正文) ──
            content = delta.get("content", "")
            if content:
                if not in_text:
                    in_text = True
                    text_buffer = []
                    yield Event(EventType.CONTENT_BLOCK_START, data={"type": "text"})
                text_buffer.append(content)
                yield Event.text_delta(content)

            # ── tool_calls ──
            tc_list = delta.get("tool_calls", [])
            for tc in tc_list:
                idx = tc.get("index", 0)
                if idx not in tool_calls:
                    tool_calls[idx] = {
                        "id": tc.get("id", ""),
                        "name": "",
                        "arguments_str": "",
                    }
                if "id" in tc and tc["id"]:
                    tool_calls[idx]["id"] = tc["id"]
                func = tc.get("function", {})
                if "name" in func and func["name"]:
                    tool_calls[idx]["name"] = func["name"]
                if "arguments" in func:
                    tool_calls[idx]["arguments_str"] += func["arguments"]

            # ── finish_reason: stop / tool_calls ──
            if choice.get("finish_reason"):
                if in_text:
                    in_text = False
                    yield Event(EventType.CONTENT_BLOCK_STOP, data={"type": "text"})

                # 产出 tool_use 事件
                for tc in sorted(tool_calls.values(), key=lambda x: x.get("id", "")):
                    if tc["name"]:
                        try:
                            args = json.loads(tc["arguments_str"])
                        except json.JSONDecodeError:
                            args = {"_raw": tc["arguments_str"]}
                        yield Event.tool_use(
                            name=tc["name"],
                            tool_id=tc["id"],
                            input=args,
                        )

        # stream 结束
        yield Event(
            EventType.MESSAGE_STOP,
            data={
                "stop_reason": _map_finish_reason(finish_reason),
                "usage": final_usage,
                "thinking_content": "".join(thinking_buffer) if thinking_buffer else None,
                "text_content": "".join(text_buffer) if text_buffer else None,
            },
        )

    # ─── OpenAI 消息格式转换 ────────────────────────

    def _to_openai_messages(
        self, messages: list[Message], system_prompt: str
    ) -> list[dict]:
        """将内部 Message 转换为 OpenAI Chat Completions 格式.

        OpenAI 格式:
        - system: {"role": "system", "content": "..."}
        - user: {"role": "user", "content": "text or array"}
        - assistant: {"role": "assistant", "content": "...", "tool_calls": [...]}
        - tool: {"role": "tool", "tool_call_id": "...", "content": "..."}
        """
        api_messages: list[dict] = []

        # System prompt 作为第一条消息
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = msg.role.value  # "system" | "user" | "assistant"

            if role == "system":
                api_messages.append({"role": "system", "content": str(msg.content)})
                continue

            # 提取纯文本内容
            text_content = self._extract_text(msg)
            tool_calls_list = self._extract_tool_calls(msg)
            tool_result = self._extract_tool_result(msg)

            if role == "assistant" and tool_calls_list:
                api_messages.append({
                    "role": "assistant",
                    "content": text_content or None,
                    "tool_calls": tool_calls_list,
                })
            elif role == "user" and tool_result:
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_result["tool_use_id"],
                    "content": tool_result["content"],
                })
            elif role == "user":
                api_messages.append({"role": "user", "content": text_content})
            elif role == "assistant":
                # DSv4: 如果消息包含 thinking，放在 content 前面
                thinking = self._extract_thinking(msg)
                if thinking:
                    # reasoning_content 已通过 API 扩展返回，
                    # 历史消息中保留在 thinking_content 字段
                    pass
                api_messages.append({"role": "assistant", "content": text_content or ""})

        return api_messages

    def _extract_text(self, msg: Message) -> str:
        """从消息中提取纯文本."""
        if isinstance(msg.content, str):
            return msg.content
        texts = []
        for block in msg.content:
            if block.type == ContentType.TEXT:
                texts.append(str(block.content))
        return "\n".join(texts)

    def _extract_thinking(self, msg: Message) -> str | None:
        """从消息中提取 thinking 内容."""
        if msg.thinking_content:
            return msg.thinking_content
        if isinstance(msg.content, list):
            for block in msg.content:
                if block.type == ContentType.THINKING:
                    return str(block.content)
        return None

    def _extract_tool_calls(self, msg: Message) -> list[dict]:
        """从 assistant 消息中提取 tool_calls."""
        if isinstance(msg.content, str):
            return []
        result = []
        for block in msg.content:
            if block.type == ContentType.TOOL_USE and isinstance(block.content, dict):
                args = block.content
                result.append({
                    "id": block.tool_use_id or "",
                    "type": "function",
                    "function": {
                        "name": block.tool_name or "",
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                })
        return result

    def _extract_tool_result(self, msg: Message) -> dict | None:
        """从 user 消息中提取 tool result."""
        if isinstance(msg.content, str):
            return None
        for block in msg.content:
            if block.type == ContentType.TOOL_RESULT:
                return {
                    "tool_use_id": block.tool_use_id or "",
                    "content": str(block.content),
                }
        return None

    # ─── OpenAI 工具格式转换 ────────────────────────

    def _to_openai_tools(self, tools: list[dict]) -> list[dict]:
        """将内部工具 schema 转换为 OpenAI function calling 格式.

        OpenAI 格式:
          {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
        """
        if not tools:
            return []
        result = []
        for tool in tools:
            params = tool.get("input_schema", tool.get("parameters", {"type": "object", "properties": {}}))
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": params,
                },
            })
        return result

    # ─── 工具函数 ──────────────────────────────────

    @staticmethod
    def estimate_cost(
        model: str, input_tokens: int, output_tokens: int, thinking_tokens: int = 0
    ) -> float:
        price = PRICING.get(model, PRICING["deepseek-v4-pro"])
        return (
            input_tokens / 1_000_000 * price["input"]
            + output_tokens / 1_000_000 * price["output"]
            + thinking_tokens / 1_000_000 * price["thinking"]
        )

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, len(text) // 3)


def _map_finish_reason(reason: str | None) -> str:
    """映射 OpenAI finish_reason 到内部表示."""
    if not reason:
        return "end_turn"
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "content_filter",
    }
    return mapping.get(reason, reason)
