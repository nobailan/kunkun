"""FlowForge 适配器 — Kunkun 作为 FlowForge 的 Harness 内核.

提供:
- 标准任务接口 (FlowForge → Kunkun)
- 评测数据回传 (Kunkun → FlowForge)
- 可插拔 Harness (AgentRuntime ABC)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

from kunkun.core.agent_runtime import AgentRuntime, TeamRole, KunkunHarness
from kunkun.core.state import HarnessConfig

logger = logging.getLogger(__name__)


# ─── 任务模型 ──────────────────────────────────────────


@dataclass
class FlowForgeTask:
    """FlowForge 提交的任务."""

    task_id: str
    prompt: str
    model: str = "deepseek-v4-pro"
    max_turns: int = 50
    metadata: dict = field(default_factory=dict)


@dataclass
class FlowForgeResult:
    """返回给 FlowForge 的执行结果."""

    task_id: str
    success: bool
    output: str
    elapsed_ms: float
    turns: int
    tokens: dict = field(default_factory=dict)
    tool_calls: int = 0
    cost_usd: float = 0.0
    # 评测数据
    thinking_eval: dict | None = None
    task_eval: dict | None = None
    # FlowForge 自定义维度
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "output": self.output[:1000],
            "elapsed_ms": round(self.elapsed_ms, 0),
            "turns": self.turns,
            "tokens": self.tokens,
            "tool_calls": self.tool_calls,
            "cost_usd": round(self.cost_usd, 6),
            "thinking_eval": self.thinking_eval,
            "task_eval": self.task_eval,
        }


# ─── FlowForge 适配器 ──────────────────────────────────


class FlowForgeAdapter:
    """FlowForge 适配器.

    封装 Kunkun Harness, 提供 FlowForge 兼容的任务执行接口.

    Usage:
        adapter = FlowForgeAdapter()
        result = await adapter.execute(FlowForgeTask(
            task_id="eval-001",
            prompt="分析 src/kunkun/core/ 的架构",
        ))
        print(result.to_dict())
    """

    def __init__(self, config: HarnessConfig | None = None):
        self.config = config or HarnessConfig.from_env()
        self._harness: AgentRuntime | None = None
        self._results: list[FlowForgeResult] = []

    # ─── 单任务执行 ─────────────────────────────────

    async def execute(self, task: FlowForgeTask) -> FlowForgeResult:
        """执行单个 FlowForge 任务.

        Args:
            task: FlowForge 任务

        Returns:
            执行结果 (含评测数据)
        """
        t0 = time.monotonic()

        # 创建 Harness
        task_config = type(self.config)(**self.config.__dict__)
        task_config.model = task.model or self.config.model
        task_config.max_turns = task.max_turns
        harness = KunkunHarness(f"ff-{task.task_id}", TeamRole.CODER, task_config)

        # 执行
        output_parts: list[str] = []
        try:
            async for chunk in harness.run(task.prompt):
                output_parts.append(chunk)
        except Exception as e:
            return FlowForgeResult(
                task_id=task.task_id,
                success=False,
                output=str(e),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        finally:
            await harness.close()

        output = "".join(output_parts).strip()
        elapsed = (time.monotonic() - t0) * 1000

        # 等待评测落盘 (最多 10 秒)
        sub_result = getattr(harness, "_last_result", {})
        thinking_eval = self._load_latest_eval()
        if not thinking_eval:
            for _ in range(20):
                await asyncio.sleep(0.5)
                thinking_eval = self._load_latest_eval()
                if thinking_eval:
                    break

        result = FlowForgeResult(
            task_id=task.task_id,
            success=len(output) > 10 and "ERROR:" not in output,
            output=output,
            elapsed_ms=elapsed,
            turns=sub_result.get("turns", 0),
            tokens=sub_result.get("total_tokens", {}),
            cost_usd=sub_result.get("cost_usd", 0),
            thinking_eval=thinking_eval.get("thinking_eval"),
            task_eval=thinking_eval.get("task_eval"),
            metadata=task.metadata,
        )

        self._results.append(result)
        return result

    # ─── 批量执行 ─────────────────────────────────

    async def execute_batch(
        self, tasks: list[FlowForgeTask], max_concurrency: int = 3
    ) -> list[FlowForgeResult]:
        """批量执行任务 (受并发上限控制).

        Args:
            tasks: 任务列表
            max_concurrency: 最大并发数

        Returns:
            结果列表
        """
        sem = asyncio.Semaphore(max_concurrency)

        async def _run_one(task: FlowForgeTask) -> FlowForgeResult:
            async with sem:
                return await self.execute(task)

        return list(await asyncio.gather(*[_run_one(t) for t in tasks]))

    # ─── 评测报告 ─────────────────────────────────

    def evaluation_report(self) -> dict:
        """生成评测报告 (供 FlowForge 消费).

        Returns:
            {
                "total_tasks": N,
                "success_rate": 0.85,
                "avg_elapsed_ms": 12345,
                "avg_thinking_score": 2.3,
                "tasks": [...]
            }
        """
        if not self._results:
            return {"total_tasks": 0}

        success_count = sum(1 for r in self._results if r.success)
        think_scores = [
            r.thinking_eval["overall"]
            for r in self._results
            if r.thinking_eval and r.thinking_eval.get("overall", -1) >= 0
        ]

        return {
            "total_tasks": len(self._results),
            "success_rate": success_count / len(self._results),
            "avg_elapsed_ms": sum(r.elapsed_ms for r in self._results) / len(self._results),
            "avg_thinking_score": sum(think_scores) / len(think_scores) if think_scores else 0,
            "tasks": [r.to_dict() for r in self._results],
        }

    def reset(self) -> None:
        """重置结果列表."""
        self._results = []

    # ─── 内部 ───────────────────────────────────────

    def _load_latest_eval(self) -> dict:
        """加载最新的评测数据."""
        path = Path(".kun/evaluations.jsonl")
        if not path.exists():
            return {}
        try:
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            if lines:
                return json.loads(lines[-1])
        except Exception:
            pass
        return {}


# ─── 便捷函数 ──────────────────────────────────────────


async def run_flowforge_task(
    prompt: str,
    task_id: str = "",
    model: str = "deepseek-v4-pro",
) -> FlowForgeResult:
    """快捷执行单个 FlowForge 任务.

    Args:
        prompt: 任务描述
        task_id: 任务 ID (留空自动生成)
        model: 模型名

    Returns:
        执行结果
    """
    import uuid

    adapter = FlowForgeAdapter()
    return await adapter.execute(FlowForgeTask(
        task_id=task_id or uuid.uuid4().hex[:8],
        prompt=prompt,
        model=model,
    ))
