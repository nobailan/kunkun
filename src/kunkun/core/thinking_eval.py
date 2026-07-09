"""ThinkBlock 过程评测 — 基于 AgentThink 论文的过度思考检测.

借鉴 AgentThink (Cuadron et al., 2025):
- 分析瘫痪 (Analysis Paralysis): 重规划轻行动
- 流氓操作 (Rogue Actions): 不等反馈连续调工具
- 过早放弃 (Premature Disengagement): 没验证就结束

Kunkun 独有优势: DSv4 暴露 reasoning_content, 可以精确检测这三种模式.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from kunkun.core.events import Event, EventType

logger = logging.getLogger(__name__)

# ─── 评分 Prompt (改编自 AgentThink 论文 Appendix) ─────

THINKING_EVAL_PROMPT = """You are an AI judge detecting three overthinking patterns in agent trajectories.

<INTERACTION>
{trajectory}
</INTERACTION>

Analyze the <INTERACTION> and score each of the three patterns below.

## Pattern 1: Analysis Paralysis
The model focuses on heavy planning instead of interacting with the environment.
- Score 0-3: Planning is brief, each thought leads to a concrete action.
- Score 4-7: Sometimes over-plans, but still acts. Long discussions eventually result in actions.
- Score 8-10: Endless planning with no action, or multiple turns of pure thinking without tool calls.

## Pattern 2: Rogue Actions
After facing setbacks, the model generates multiple actions without waiting for environment feedback.
- Score 0-3: Always waits for environment response before next action. One action per turn.
- Score 4-7: Occasionally outputs multiple actions at once, but usually waits.
- Score 8-10: Repeatedly fires multiple actions without waiting, especially after errors.

## Pattern 3: Premature Disengagement
The model concludes the task without verifying with the environment.
- Score 0-3: Always checks results before concluding. Tests changes, reads output.
- Score 4-7: Sometimes skips verification but usually checks.
- Score 8-10: Declares success without any verification, or gives up after first error.

<IMPORTANT>
Format your response EXACTLY as:
<answer>
{{
  "analysis_paralysis": 3,
  "rogue_actions": 1,
  "premature_disengagement": 2,
  "overall": 2,
  "summary": "Brief one-sentence diagnosis in Chinese"
}}
</answer>

Take your time. Think step by step about each pattern.
</IMPORTANT>"""


class ThinkingEvaluator:
    """ThinkBlock 过程评测器.

    Attributes:
        api_key: DeepSeek API key
        base_url: API base URL
        light_model: 评测用轻模型 (默认 flash)
        events: 收集的执行事件列表
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
        light_model: str = "deepseek-v4-flash",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.light_model = light_model
        self.events: list[Event] = []

    def record(self, event: Event) -> None:
        """收集事件."""
        self.events.append(event)

    def reset(self) -> None:
        """重置收集器."""
        self.events = []

    # ─── 轨迹转换 ───────────────────────────────

    def _build_trajectory(self) -> str:
        """将 Kun 事件流转换为论文格式的交互文本."""
        if not self.events:
            return "(No events)"

        lines: list[str] = []
        thinking_buf: list[str] = []
        tool_calls_buf: list[dict] = []
        current_turn = 0

        for event in self.events:
            et = event.type

            if et == EventType.TURN_START:
                current_turn = event.turn_number
                prompt = event.data.get("prompt", "")[:200]
                lines.append(f"--- Turn {current_turn} ---")
                lines.append(f"USER: {prompt}")

            elif et == EventType.THINKING_DELTA if hasattr(EventType, 'THINKING_DELTA') else None:
                pass  # 使用 content_block_delta

            elif et == EventType.CONTENT_BLOCK_DELTA:
                ct = event.data.get("type", "")
                if ct == "thinking":
                    thinking_buf.append(event.data.get("text", ""))
                elif ct == "text":
                    lines.append(f"ASSISTANT: {event.data.get('text', '')[:300]}")

            elif et == EventType.TOOL_USE:
                name = event.data.get("name", "?")
                inp = str(event.data.get("input", {}))[:200]
                lines.append(f"MODEL: calls {name}({inp})")

            elif et == EventType.TOOL_RESULT:
                content = str(event.data.get("content", ""))[:300]
                is_err = event.data.get("is_error", False)
                prefix = "ENVIRONMENT (error):" if is_err else "ENVIRONMENT:"
                lines.append(f"{prefix} {content}")

            elif et == EventType.ERROR:
                lines.append(f"ENVIRONMENT (error): {event.data.get('error', '')[:300]}")

            elif et == EventType.RETRY:
                lines.append(
                    f"SYSTEM: Retry attempt {event.data.get('attempt', '?')} - "
                    f"{event.data.get('error', '')[:200]}"
                )

            elif et == EventType.SESSION_END:
                lines.append(
                    f"--- Session End (success={event.data.get('success', False)}, "
                    f"turns={event.data.get('turns', 0)}) ---"
                )

        # 追加累积的 thinking 摘要
        if thinking_buf:
            full_thinking = "".join(thinking_buf)
            if len(full_thinking) > 500:
                lines.insert(0, f"[THINKING SUMMARY: {len(full_thinking)} chars total]\n{full_thinking[:500]}...")
            else:
                lines.insert(0, f"[THINKING]\n{full_thinking}")

        if not lines:
            return "(Empty trajectory)"

        return "\n".join(lines)

    # ─── 评测 ───────────────────────────────────

    async def evaluate(self) -> dict[str, Any]:
        """运行过程评测.

        Returns:
            {
                "analysis_paralysis": int,  # 0-10
                "rogue_actions": int,       # 0-10
                "premature_disengagement": int,  # 0-10
                "overall": int,             # 0-10
                "summary": str,             # 中文诊断
                "events_analyzed": int,
                "model_used": str,
            }
        """
        trajectory = self._build_trajectory()
        prompt = THINKING_EVAL_PROMPT.format(trajectory=trajectory)

        if not self.api_key:
            return {
                "analysis_paralysis": -1,
                "rogue_actions": -1,
                "premature_disengagement": -1,
                "overall": -1,
                "summary": "未配置 API Key, 跳过评测",
                "events_analyzed": len(self.events),
                "model_used": "none",
            }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": self.light_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 512,
                        "temperature": 0.1,
                    },
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )

                if response.status_code != 200:
                    logger.warning("Thinking eval API error: %s", response.status_code)
                    return self._empty_result(f"API error {response.status_code}")

                data = response.json()
                content = data["choices"][0]["message"]["content"]

        except Exception as e:
            logger.debug("Thinking eval failed: %s", e)
            return self._empty_result(f"Eval error: {e}")

        return self._parse_result(content, len(self.events))

    def _parse_result(self, response: str, event_count: int) -> dict:
        """解析 LLM 返回的评分 JSON."""
        # 提取 <answer> 内的 JSON
        json_str = response
        if "<answer>" in response:
            json_str = response.split("<answer>")[1].split("</answer>")[0]

        try:
            result = json.loads(json_str.strip())
        except json.JSONDecodeError:
            logger.debug("Failed to parse eval JSON: %s", response[:200])
            return self._empty_result("JSON parse error")

        return {
            "analysis_paralysis": int(result.get("analysis_paralysis", -1)),
            "rogue_actions": int(result.get("rogue_actions", -1)),
            "premature_disengagement": int(result.get("premature_disengagement", -1)),
            "overall": int(result.get("overall", -1)),
            "summary": str(result.get("summary", "")),
            "events_analyzed": event_count,
            "model_used": self.light_model,
        }

    def _empty_result(self, reason: str) -> dict:
        return {
            "analysis_paralysis": -1,
            "rogue_actions": -1,
            "premature_disengagement": -1,
            "overall": -1,
            "summary": reason,
            "events_analyzed": len(self.events),
            "model_used": "none",
        }

    # ─── 报告格式化 ──────────────────────────────

    @staticmethod
    def format_report(result: dict) -> str:
        """格式化为可读报告."""
        if result["overall"] < 0:
            return f"📊 思考质量: 跳过 ({result['summary']})"

        def bar(score: int) -> str:
            if score <= 3:
                return "🟢"
            elif score <= 6:
                return "🟡"
            return "🔴"

        lines = [
            "📊 ThinkBlock 过程评测",
            f"   综合: {result['overall']}/10 {bar(result['overall'])}",
            f"   分析瘫痪: {result['analysis_paralysis']}/10 {bar(result['analysis_paralysis'])}",
            f"   流氓操作: {result['rogue_actions']}/10 {bar(result['rogue_actions'])}",
            f"   过早放弃: {result['premature_disengagement']}/10 {bar(result['premature_disengagement'])}",
            f"   诊断: {result['summary']}",
            f"   分析事件: {result['events_analyzed']} 个 | 评测模型: {result['model_used']}",
        ]
        return "\n".join(lines)
