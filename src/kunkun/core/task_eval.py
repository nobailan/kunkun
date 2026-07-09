"""AdaRubric 任务自适应评测 — 动态生成评分维度 + 打分.

借鉴 AdaRubric (Ding et al., 2025):
- 不为所有任务用固定标准, 而是根据任务内容动态生成评分维度
- 每个维度 0-3 分, 按任务相关性排权重
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from kunkun.core.events import Event, EventType

logger = logging.getLogger(__name__)

# ─── 评分 Prompt (借鉴 AdaRubric 论文) ───────────────

RUBRIC_PROMPT = """你是 AI 任务评测专家。根据任务内容和执行结果, 生成任务专属的评分维度并打分。

## 任务描述
{task_prompt}

## 执行结果
{trajectory}

## 评分要求
1. 分析任务内容, 生成 **3-6 个** 任务专属的评分维度 (不是通用的 Helpfulness/Fluency)
2. 每个维度打分 0-3:
   - 0: 完全未做或错误
   - 1: 部分完成, 有较大缺陷
   - 2: 基本完成, 有小问题
   - 3: 完美完成
3. 维度必须**对当前任务有区分度** (不能是"代码格式正确"这种普适标准)
4. 每个维度附一行具体的判断依据

## 示例
任务: "搜索 Python 3.13 asyncio 新特性并总结"
正确维度: "找到官方文档"、"asyncio 相关特性提取完整"、"中文总结准确"
错误维度: "回复长度合适"、"格式清晰" (太通用, 无区分度)

## 输出格式
返回 JSON (不要包含其他文字):
{{
  "dimensions": [
    {{"name": "维度名", "score": 2, "max": 3, "comment": "判断依据"}}
  ],
  "overall": 2.3,
  "summary": "中文一句话总结"
}}
"""


class TaskEvaluator:
    """AdaRubric 任务自适应评测器."""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
        light_model: str = "deepseek-v4-flash",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.light_model = light_model

    async def evaluate(self, task_prompt: str, trajectory: str) -> dict:
        """生成评分维度并打分.

        Args:
            task_prompt: 用户原始任务
            trajectory: 执行轨迹摘要

        Returns:
            {"dimensions": [...], "overall": float, "summary": str}
        """
        if not self.api_key or not trajectory.strip():
            return self._empty_result("无 API key 或空轨迹")

        prompt = RUBRIC_PROMPT.format(
            task_prompt=task_prompt[:3000],
            trajectory=trajectory[:6000],
        )

        try:
            proxy = os.environ.get("KUN_PROXY", "") or None
            async with httpx.AsyncClient(timeout=60, proxy=proxy) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": self.light_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1024,
                        "temperature": 0.2,
                    },
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code != 200:
                    return self._empty_result(f"API {resp.status_code}")
                content = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return self._empty_result(str(e))

        return self._parse(content)

    def _parse(self, response: str) -> dict:
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0]

        try:
            data = json.loads(json_str.strip())
        except json.JSONDecodeError:
            return self._empty_result("JSON parse error")

        return {
            "dimensions": data.get("dimensions", []),
            "overall": data.get("overall", -1),
            "summary": data.get("summary", ""),
        }

    def _empty_result(self, reason: str) -> dict:
        return {"dimensions": [], "overall": -1, "summary": reason}

    @staticmethod
    def format_report(result: dict) -> str:
        """格式化为可读报告."""
        if result.get("overall", -1) < 0:
            return f"📋 任务评测: 跳过 ({result.get('summary')})"

        dims = result.get("dimensions", [])
        overall = result["overall"]

        def bar(score: int) -> str:
            if score >= 3: return "🟢"
            if score >= 2: return "🟡"
            if score >= 1: return "🟠"
            return "🔴"

        lines = [f"📋 AdaRubric 任务评测 (综合 {overall:.1f}/3.0)\n"]
        for d in dims:
            lines.append(f"  {bar(d['score'])} {d['name']}: {d['score']}/{d['max']} — {d.get('comment', '')[:80]}")
        if result.get("summary"):
            lines.append(f"\n  💬 {result['summary']}")
        return "\n".join(lines)
