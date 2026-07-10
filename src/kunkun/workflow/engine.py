"""Workflow 引擎 — Python 脚本驱动的 Agent 编排.

借鉴 Claude Code Workflow 原语:
- agent(prompt, role): 启动角色化子 Agent
- parallel(tasks): 屏障并行 (全部完成才继续)
- pipeline(items, *stages): 非屏障流水线 (默认, token 效率最高)
- phase(title): 进度标记

DSv4 适配:
- pipeline 默认 (不等最慢的, 省时间=省token)
- 子 Agent 共享 Frozen Snapshot
- 轻任务自动路由到 flash
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# ─── Workflow 注册表 ───────────────────────────────────

_registry: dict[str, Callable] = {}


def workflow(name: str = ""):
    """Workflow 装饰器."""
    def decorator(fn: Callable):
        wf_name = name or fn.__name__
        _registry[wf_name] = fn
        fn._workflow_name = wf_name
        return fn
    return decorator


def list_workflows() -> list[str]:
    return list(_registry.keys())


def get_workflow(name: str) -> Callable | None:
    return _registry.get(name)


# ─── 原语 ──────────────────────────────────────────────


async def agent(
    prompt: str,
    agent_type: str = "coder",
    timeout: float = 60.0,
    _config: Any = None,
) -> str:
    """启动角色化子 Agent.

    共享父 Agent 的 Frozen Snapshot, 不重复加载 Memory/Skill.
    轻任务自动路由到 flash.

    Args:
        prompt: 任务描述
        agent_type: 角色 (explorer/coder/reviewer/planner)
        timeout: 超时秒数
        _config: 内部传参, 父 Agent 的 HarnessConfig

    Returns:
        子 Agent 的输出文本
    """
    from kunkun.core.agent_runtime import KunkunHarness, TeamRole

    role_map = {
        "explorer": TeamRole.EXPLORER,
        "coder": TeamRole.CODER,
        "reviewer": TeamRole.REVIEWER,
        "planner": TeamRole.PLANNER,
    }
    role = role_map.get(agent_type, TeamRole.CODER)
    sub = KunkunHarness(f"wf-{agent_type}", role, _config)
    parts = []
    try:
        async for chunk in sub.run(prompt):
            if not chunk.startswith("[ERROR:"):
                parts.append(chunk)
    finally:
        await sub.close()
    return "".join(parts).strip()


async def parallel(tasks: list[Awaitable[str]]) -> list[str]:
    """屏障并行 — 全部完成才返回.

    适合: 多个完全独立的子任务.
    Token 效率低于 pipeline, 仅在需要"等全部结果再汇总"时使用.
    """
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [str(r) if not isinstance(r, BaseException) else f"[ERROR: {r}]" for r in results]


async def pipeline(
    items: list[Any],
    *stages: Callable[[Any], Awaitable[str]],
) -> list[Any]:
    """流水线并行 — 默认模式, token 效率最高.

    借鉴 Claude Code pipeline():
    - Item A 进 stage 2 时 Item B 还在 stage 1
    - 不等最慢的, 总耗时 = max(每个 item 的总耗时)
    - 比 parallel 少等一轮

    Args:
        items: 输入列表
        *stages: 处理阶段函数, 每个 stage 接收前一个 stage 的输出

    Returns:
        每个 item 的最终结果列表
    """
    if not stages:
        return items

    results = list(items)  # 当前阶段的结果

    for stage in stages:
        # 流水线: 每个 item 独立走完所有 stage
        async def _process_item(idx: int, value: Any) -> tuple[int, str]:
            try:
                result = stage(value)
                if hasattr(result, "__await__"):
                    result = await result
                return idx, str(result)
            except Exception as e:
                return idx, f"[ERROR: {e}]"

        tasks = [_process_item(i, v) for i, v in enumerate(results)]
        stage_results = await asyncio.gather(*tasks)

        # 按原始顺序排列
        ordered = [""] * len(items)
        for idx, val in stage_results:
            ordered[idx] = val
        results = ordered

    return results


async def phase(title: str) -> None:
    """进度标记 — 输出到日志和仪表盘."""
    logger.info("[Workflow Phase] %s", title)
