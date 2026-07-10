"""Cron 调度器 — asyncio 原生, 零外部依赖.

DSv4 适配:
- 任务默认走 flash (省 token)
- 复用 Frozen Snapshot (不重复加载 Memory/Skill)
- 独立 session (不带历史对话)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable

from kunkun.cron.parser import parse_cron

logger = logging.getLogger(__name__)


@dataclass
class CronTask:
    """定时任务定义."""

    name: str
    expression: str   # cron 表达式
    handler: Callable[[], Awaitable[str]]
    enabled: bool = True

    # 运行时状态
    last_run: str = ""
    last_result: str = ""
    last_success: bool = False
    last_elapsed: float = 0.0
    run_count: int = 0
    error_count: int = 0
    next_run: str = ""


class CronScheduler:
    """异步 Cron 调度器.

    Usage:
        scheduler = CronScheduler()
        scheduler.add_task("daily", "0 9 * * 1-5", my_handler)
        await scheduler.start()
    """

    def __init__(self, storage_dir: str = ".kun/cron"):
        self._tasks: dict[str, CronTask] = {}
        self._storage = Path(storage_dir)
        self._storage.mkdir(parents=True, exist_ok=True)
        self._running = False
        self._sem = asyncio.Semaphore(1)  # 同一时间只跑一个 cron 任务

    # ─── 注册 ───────────────────────────────────

    def add_task(
        self, name: str, expression: str, handler: Callable[[], Awaitable[str]],
    ) -> "CronScheduler":
        """注册定时任务."""
        schedule = parse_cron(expression)
        task = CronTask(
            name=name,
            expression=expression,
            handler=handler,
            next_run=schedule.next_after().isoformat(),
        )
        self._tasks[name] = task
        return self

    def task(self, expression: str, name: str = ""):
        """装饰器注册."""
        def decorator(fn: Callable[[], Awaitable[str]]):
            task_name = name or fn.__name__
            self.add_task(task_name, expression, fn)
            return fn
        return decorator

    def remove_task(self, name: str) -> bool:
        if name in self._tasks:
            del self._tasks[name]
            return True
        return False

    # ─── 启动/停止 ──────────────────────────────

    async def start(self) -> None:
        """启动调度器 (后台运行)."""
        self._running = True
        self._load_state()
        logger.info("Cron scheduler started (%d tasks)", len(self._tasks))
        asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """停止调度器."""
        self._running = False
        self._save_state()

    # ─── 主循环 ─────────────────────────────────

    async def _loop(self) -> None:
        """主调度循环."""
        while self._running:
            now = datetime.now()
            for task in list(self._tasks.values()):
                if not task.enabled:
                    continue
                try:
                    next_dt = datetime.fromisoformat(task.next_run)
                except (ValueError, TypeError):
                    schedule = parse_cron(task.expression)
                    task.next_run = schedule.next_after(now).isoformat()
                    continue

                if now >= next_dt:
                    asyncio.create_task(self._run_task(task))

            # 每秒检查一次
            await asyncio.sleep(1)

    async def _run_task(self, task: CronTask) -> None:
        """执行单个 cron 任务."""
        if not self._sem.locked():
            async with self._sem:
                await self._execute(task)
        # sem 被占用 → 跳过本次 (上一个任务还没跑完)

    async def _execute(self, task: CronTask) -> None:
        """实际执行."""
        t0 = time.monotonic()
        try:
            result = await task.handler()
            task.last_success = True
            task.last_result = result[:500]
        except Exception as e:
            task.last_success = False
            task.last_result = str(e)[:500]
            task.error_count += 1
            logger.warning("Cron task '%s' failed: %s", task.name, e)

        task.last_elapsed = time.monotonic() - t0
        task.last_run = datetime.now().isoformat()
        task.run_count += 1

        # 计算下次触发
        try:
            schedule = parse_cron(task.expression)
            task.next_run = schedule.next_after().isoformat()
        except Exception:
            pass

        self._save_state()

    # ─── 状态持久化 ─────────────────────────────

    def _save_state(self) -> None:
        """保存任务状态."""
        state = {}
        for name, task in self._tasks.items():
            state[name] = {
                "name": task.name,
                "expression": task.expression,
                "enabled": task.enabled,
                "last_run": task.last_run,
                "last_result": task.last_result,
                "last_success": task.last_success,
                "last_elapsed": task.last_elapsed,
                "run_count": task.run_count,
                "error_count": task.error_count,
                "next_run": task.next_run,
            }
        try:
            self._storage.mkdir(parents=True, exist_ok=True)
            (self._storage / "tasks.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load_state(self) -> None:
        """加载任务状态."""
        path = self._storage / "tasks.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for name, state in data.items():
                if name in self._tasks:
                    t = self._tasks[name]
                    t.enabled = state.get("enabled", True)
                    t.last_run = state.get("last_run", "")
                    t.last_result = state.get("last_result", "")
                    t.last_success = state.get("last_success", False)
                    t.last_elapsed = state.get("last_elapsed", 0)
                    t.run_count = state.get("run_count", 0)
                    t.error_count = state.get("error_count", 0)
                    t.next_run = state.get("next_run", "")
        except Exception:
            pass

    # ─── 查询 ───────────────────────────────────

    def status(self) -> list[dict]:
        """返回所有任务的状态."""
        return [
            {
                "name": t.name,
                "expression": t.expression,
                "enabled": t.enabled,
                "next_run": t.next_run[:19] if t.next_run else "-",
                "last_run": t.last_run[:19] if t.last_run else "从未执行",
                "last_result": t.last_result[:200],
                "last_success": t.last_success,
                "last_elapsed": round(t.last_elapsed, 1),
                "run_count": t.run_count,
                "error_count": t.error_count,
            }
            for t in self._tasks.values()
        ]

    @property
    def task_count(self) -> int:
        return len(self._tasks)
