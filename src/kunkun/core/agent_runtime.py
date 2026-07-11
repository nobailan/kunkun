"""AgentRuntime 抽象层 — 可插拔的 Agent 运行时.

设计:
- AgentRuntime: ABC, 定义 Harness 接口
- KunkunHarness: Kunkun 原生实现
- 未来: OpenCodeHarness, ExternalHarness

v0.7.1: FlowForge 可接入外部 Harness 的基础
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Optional

logger = logging.getLogger(__name__)


# ─── 角色定义 ──────────────────────────────────────────


class TeamRole(str, Enum):
    """Agent Team 角色.

    每个角色有不同的工具白名单和权限级别.
    """

    LEADER = "leader"       # 任务拆解 + 委派 + 汇总
    EXPLORER = "explorer"   # 只读: read_file, glob, grep, findsymbol, websearch
    CODER = "coder"         # 读写: + write_file, edit, bash
    REVIEWER = "reviewer"   # 分析: + skill_load, recall, findrefs
    PLANNER = "planner"     # 设计: + grpo, agent, todowrite


# 角色 → 工具白名单
ROLE_TOOLS: dict[TeamRole, list[str]] = {
    TeamRole.LEADER: [
        "agent", "todowrite", "grpo", "fastslow",
        "read_file", "glob", "grep", "findsymbol", "findrefs", "gotodef",
        "websearch", "webfetch",
        "remember", "recall", "skill_load",
    ],
    TeamRole.EXPLORER: [
        "read_file", "glob", "grep", "findsymbol", "findrefs", "gotodef",
        "websearch", "webfetch",
    ],
    TeamRole.CODER: [
        "read_file", "write_file", "edit", "glob", "grep", "bash",
        "findsymbol", "findrefs", "gotodef",
    ],
    TeamRole.REVIEWER: [
        "read_file", "glob", "grep", "findsymbol", "findrefs", "gotodef",
        "skill_load", "recall", "remember",
    ],
    TeamRole.PLANNER: [
        "read_file", "glob", "grep", "findsymbol",
        "grpo", "agent", "todowrite",
        "remember", "recall",
    ],
}

# 角色 → System Prompt 追加
ROLE_PROMPTS: dict[TeamRole, str] = {
    TeamRole.EXPLORER: (
        "\n## 当前角色: 探索者\n"
        "你只负责搜索和阅读代码。不要修改任何文件。\n"
        "尽可能并行搜索以节省时间。"
    ),
    TeamRole.CODER: (
        "\n## 当前角色: 编码者\n"
        "你只负责实现代码。不要做架构设计或代码审查。\n"
        "严格按照 Leader 给出的规格实现。"
    ),
    TeamRole.REVIEWER: (
        "\n## 当前角色: 审查者\n"
        "你只负责分析代码质量。不要修改任何文件。\n"
        "关注: 错误处理、边界条件、代码风格、安全问题。"
    ),
    TeamRole.PLANNER: (
        "\n## 当前角色: 规划者\n"
        "你只负责架构设计和方案分析。不要写实现代码。\n"
        "输出结构化的设计方案，包含步骤、风险、备选方案。"
    ),
}

# ─── 消息模型 ──────────────────────────────────────────


@dataclass
class TeamMessage:
    """Agent 间消息."""

    sender: str
    receiver: str
    content: str
    msg_type: str = "info"
    task_id: str = ""


@dataclass
class AgentMessage:
    """异步信箱消息 — 子 Agent → 父 Agent 的结构化通信.

    替代 queue.Queue 纯文本, 支持:
    - text: 文本 chunk
    - result: 结构化结果 (token, tool_calls)
    - error: 错误信息
    - done: 任务完成
    """

    msg_type: str  # "text" | "result" | "error" | "done"
    text: str = ""
    data: dict | None = None


# ─── AgentBus (多订阅者) ────────────────────────────────


class AgentBus:
    """消息总线 — 多 Agent 发布/订阅.

    任何 Agent 可以 publish 消息, 任何 Agent 可以 subscribe (带过滤器).

    Usage:
        bus = AgentBus()
        bus.subscribe("explorer", lambda msg: print(f"Got: {msg}"))
        bus.publish(TeamMessage(sender="coder", receiver="all", content="done"))
    """

    def __init__(self):
        import asyncio as _asyncio

        self._subscribers: dict[str, list[callable]] = {}  # subscriber_id → [callback]
        self._filters: dict[str, callable] = {}  # subscriber_id → filter_fn
        self._pending: list[TeamMessage] = []
        self._lock = _asyncio.Lock()

    def subscribe(
        self,
        subscriber_id: str,
        callback,
        filter_fn: callable | None = None,
    ) -> None:
        """订阅消息.

        Args:
            subscriber_id: 订阅者标识
            callback: async (TeamMessage) -> None
            filter_fn: (TeamMessage) -> bool, 可选过滤器
        """
        if subscriber_id not in self._subscribers:
            self._subscribers[subscriber_id] = []
        self._subscribers[subscriber_id].append(callback)
        if filter_fn:
            self._filters[subscriber_id] = filter_fn

    def unsubscribe(self, subscriber_id: str) -> None:
        """取消订阅."""
        self._subscribers.pop(subscriber_id, None)
        self._filters.pop(subscriber_id, None)

    async def publish(self, msg: TeamMessage) -> None:
        """发布消息到所有匹配的订阅者.

        线程安全 — 可从任意线程调用.
        """
        async with self._lock:
            for sid, callbacks in list(self._subscribers.items()):
                filter_fn = self._filters.get(sid)
                if filter_fn and not filter_fn(msg):
                    continue
                for cb in callbacks:
                    try:
                        if hasattr(cb, "__call__"):
                            result = cb(msg)
                            if hasattr(result, "__await__"):
                                await result
                    except Exception:
                        pass

    def publish_sync(self, msg: TeamMessage) -> None:
        """同步发布 (从非 async 线程调用)."""
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.create_task(self.publish(msg))
            else:
                loop.run_until_complete(self.publish(msg))
        except RuntimeError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# ─── AgentRuntime ABC ──────────────────────────────────


class AgentRuntime(ABC):
    """Agent 运行时抽象基类.

    每个 Harness (Kunkun, OpenCode, External) 实现此接口.
    """

    def __init__(self, name: str, role: TeamRole):
        self.name = name
        self.role = role
        self._messages: list[TeamMessage] = []
        self._last_result: dict = {}

    @abstractmethod
    async def run(self, prompt: str) -> AsyncGenerator[str, None]:
        """执行任务, 流式产出文本."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """清理资源."""
        ...

    def receive(self, msg: TeamMessage) -> None:
        """接收来自其他 Agent 的消息."""
        self._messages.append(msg)

    @property
    def inbox(self) -> list[TeamMessage]:
        return list(self._messages)


# ─── KunkunHarness ──────────────────────────────────────


class KunkunHarness(AgentRuntime):
    """Kunkun 原生 Harness 实现.

    每个子 Agent 跑在独立线程的独立 event loop 中,
    保证主 Agent 和子 Agent 之间不会互相干扰.
    """

    def __init__(self, name: str, role: TeamRole, config: Any):
        super().__init__(name, role)
        self.config = config

    async def run(self, prompt: str) -> AsyncGenerator[str, None]:
        """执行任务 (同 event loop, 独立 httpx 连接)."""
        import asyncio as asyncio_mod

        role_config = type(self.config)(**self.config.__dict__)
        role_config.permission_mode = "accept_edits" if self.role == TeamRole.CODER else "default"

        role_hint = ROLE_PROMPTS.get(self.role, "")
        full_prompt = f"{prompt}\n{role_hint}" if role_hint else prompt

        from kunkun.core.agent_loop import AgentLoop
        agent = AgentLoop(role_config)

        try:
            async for event in agent.run(full_prompt):
                if event.type.value == "content_block_delta":
                    if event.data.get("type") == "text":
                        yield event.data["text"]
                elif event.type.value == "error":
                    yield f"\n[ERROR: {event.data.get('error', '')}]\n"
                elif event.type.value == "session_end":
                    self._last_result = {
                        "turns": event.data.get("turns", 0),
                        "total_tokens": event.data.get("total_tokens", {}),
                        "cost_usd": event.data.get("cost_usd", 0),
                        "success": event.data.get("success", False),
                    }
        finally:
            await agent.close()

    async def close(self) -> None:
        pass


# ─── AgentTeam ──────────────────────────────────────────


@dataclass
class TaskDelegation:
    """任务委派."""

    task_id: str
    description: str       # 任务描述
    assigned_to: TeamRole  # 分配给哪个角色
    depends_on: list[str] = field(default_factory=list)  # 依赖的任务 ID


@dataclass
class TaskResult:
    """委派任务的结果."""

    task_id: str
    assigned_to: TeamRole
    result: str
    success: bool
    error: str = ""


class AgentTeam:
    """Agent Team 编排器.

    Leader 拆解任务 → 委派给 Member → 收集结果 → 汇总.

    Usage:
        team = AgentTeam()
        team.add_runtime(PLANNER, KunkunHarness("planner", PLANNER, config))
        team.add_runtime(EXPLORER, KunkunHarness("explorer", EXPLORER, config))
        team.add_runtime(CODER, KunkunHarness("coder", CODER, config))

        async for msg in team.execute("重构 agent_loop.py"):
            print(msg)
    """

    def __init__(self, leader_config: Any = None):
        self._runtimes: dict[TeamRole, list[AgentRuntime]] = {}
        self._messages: list[TeamMessage] = []
        self._task_counter = 0
        self.leader_config = leader_config
        self.bus = AgentBus()  # v0.8: 共享消息总线

    def add_runtime(self, runtime: AgentRuntime) -> None:
        """注册一个 Agent 运行时."""
        if runtime.role not in self._runtimes:
            self._runtimes[runtime.role] = []
        self._runtimes[runtime.role].append(runtime)

    async def execute(self, task: str) -> AsyncGenerator[str, None]:
        """执行团队任务.

        流程:
        1. Planner 拆解任务 → 产生 TaskDelegation 列表
        2. 并行委派给各 Member
        3. Leader 汇总结果
        """
        yield f"🤖 AgentTeam 开始执行: {task[:100]}...\n"

        # ── Step 1: 规划 ──
        planners = self._runtimes.get(TeamRole.PLANNER, [])
        leaders = self._runtimes.get(TeamRole.LEADER, [])
        planner = (planners + leaders)[0] if (planners or leaders) else None

        # 无 Planner/Leader → 用任何可用角色
        if planner is None:
            for role in self._runtimes:
                if self._runtimes[role]:
                    planner = self._runtimes[role][0]
                    break
        if planner is None:
            yield "❌ 没有可用角色, 无法拆解任务\n"
            return

        plan_prompt = (
            f"分析以下任务，将其拆解为 2-4 个独立的子任务，分配给不同的角色。\n\n"
            f"任务: {task}\n\n"
            f"可用角色: explorer(搜索/阅读), coder(编码), reviewer(审查), planner(设计)\n\n"
            f"输出格式 (JSON):\n"
            f'{{"tasks": [{{"id": "1", "description": "...", "role": "explorer", "depends_on": []}}]}}\n'
        )

        plan_text = ""
        async for chunk in planner.run(plan_prompt):
            plan_text += chunk

        # 解析计划
        import json
        try:
            json_str = plan_text
            if "```json" in plan_text:
                json_str = plan_text.split("```json")[1].split("```")[0]
            elif "```" in plan_text:
                json_str = plan_text.split("```")[1].split("```")[0]
            plan = json.loads(json_str.strip())
        except Exception:
            yield f"⚠️ 规划解析失败, 直接执行\n"
            plan = {"tasks": [{"id": "1", "description": task, "role": "coder", "depends_on": []}]}

        tasks = plan.get("tasks", [])
        yield f"📋 拆解为 {len(tasks)} 个子任务\n"

        # ── Step 2: 并行执行 ──
        results: dict[str, TaskResult] = {}

        async def _run_task(tsk: dict) -> TaskResult:
            role_name = tsk.get("role") or tsk.get("assigned_to") or "explorer"
            role_map = {"探索者": TeamRole.EXPLORER, "编码者": TeamRole.CODER,
                        "审查者": TeamRole.REVIEWER, "规划者": TeamRole.PLANNER}
            role = role_map.get(role_name, TeamRole.EXPLORER) if isinstance(role_name, str) else TeamRole(role_name)
            runners = self._runtimes.get(role, [])
            if not runners:
                return TaskResult(tsk["id"], role, "", False, f"没有 {role.value} 角色可用")

            runner = runners[0]
            text = ""
            errors = []
            async for chunk in runner.run(tsk["description"]):
                text += chunk
                if chunk.startswith("[ERROR:"):
                    errors.append(chunk.strip())

            success = len(text) > 10 and not errors
            error_msg = "; ".join(errors[:3]) if errors else ""
            return TaskResult(tsk["id"], role, text, success, error_msg)

        # 按依赖分组执行
        executed: set[str] = set()
        while len(executed) < len(tasks):
            batch = [
                t for t in tasks
                if t["id"] not in executed
                and all(d in executed for d in t.get("depends_on", []))
            ]
            if not batch:
                break

            batch_results = await asyncio.gather(*[_run_task(t) for t in batch])
            for r in batch_results:
                results[r.task_id] = r
                executed.add(r.task_id)
                icon = "✅" if r.success else "❌"
                detail = r.result[:150] if r.success else (r.error or "执行失败")[:150]
                yield f"  {icon} [{r.assigned_to.value}] {r.task_id}: {detail}...\n"
                # 通过总线广播结果
                self.bus.publish_sync(TeamMessage(
                    sender=r.assigned_to.value,
                    receiver="all",
                    content=f"[{r.task_id}] {'OK' if r.success else 'FAIL'}: {detail[:100]}",
                    msg_type="result",
                ))

        # ── Step 3: 汇总 ──
        yield f"\n📊 完成: {sum(1 for r in results.values() if r.success)}/{len(results)} 成功\n"

        # Leader 汇总 (无 Leader 时用 Planner 或第一个可用角色)
        summarizer = (leaders + planners)[0] if (leaders or planners) else None
        if summarizer is None:
            for role in self._runtimes:
                if self._runtimes[role]:
                    summarizer = self._runtimes[role][0]
                    break
        if summarizer and results:
            summary_prompt = (
                f"汇总以下任务的执行结果:\n\n"
                + "\n".join(f"[{r.task_id}] {r.assigned_to.value}: {r.result[:500]}" for r in results.values())
            )
            yield "\n📝 汇总:\n"
            async for chunk in summarizer.run(summary_prompt):
                yield chunk

    async def close(self) -> None:
        """清理所有 runtime."""
        for runners in self._runtimes.values():
            for r in runners:
                await r.close()
