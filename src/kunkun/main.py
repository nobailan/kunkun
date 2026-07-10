"""Kunkun 入口点.

借鉴:
- cc-haha src/entrypoints/cli.tsx — CLI 解析 + 模式分发
- cc-haha src/main.tsx — Commander.js 全功能 CLI
- Hermes cli.py — fire-based CLI 入口

模式:
  kun "task description"    → 单次执行 (类 cc-haha -p)
  kun-interactive           → 交互模式 (类 cc-haha REPL)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from kunkun.core.agent_loop import AgentLoop
from kunkun.core.state import HarnessConfig
from kunkun.cli.tui import ConsoleRenderer, HeadlessRenderer

from kunkun.core.thinking_eval import ThinkingEvaluator
logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False):
    """配置日志 — 日志写文件, 终端只显示 ERROR."""
    from pathlib import Path
    log_dir = Path(".kun/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    # 根 logger: DEBUG 级别 (文件)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    # 文件 handler: 所有级别 → .kun/logs/kunkun.log
    fh = logging.FileHandler(log_dir / "kunkun.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(fh)

    # 终端 handler: 只显示 ERROR (不干扰用户输入)
    import sys
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.ERROR)
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root.addHandler(sh)


async def run_once(prompt: str, config: HarnessConfig) -> int:
    """单次执行模式 (类 cc-haha -p/--print 模式).

    Args:
        prompt: 用户任务描述
        config: Harness 配置

    Returns:
        0 表示成功，非 0 表示失败
    """
    agent = AgentLoop(config)

    try:
        if config.verbose:
            renderer = ConsoleRenderer(
                think_visible=config.think_visibility == "show",
                verbose=True,
            )
        else:
            renderer = HeadlessRenderer()

        result = await renderer.render_stream(agent.run(prompt))

        # 输出结果
        if not config.verbose and result:
            print(f"\n{'─' * 60}")
            print(result)
            print(f"{'─' * 60}")

        # 打印指标
        if config.verbose:
            print(f"\n{'─' * 40}")
            print(f"📊 执行报告")
            print(f"   模型: {agent.state.model}")
            print(f"   轮次: {agent.state.current_turn}")
            print(f"   Token: {agent.state.total_tokens['input']:,} 入 / {agent.state.total_tokens['output']:,} 出")
            if agent.state.total_tokens.get('thinking', 0) > 0:
                print(f"   Thinking: {agent.state.total_tokens['thinking']:,}")
            print(f"   工具调用: {len(agent.state.tool_calls)} 次")
            print(f"   重试次数: {agent._last_retry_count}")
            print(f"   估算费用: ${agent.router.budget.spent_task:.4f}")
            print(f"   执行日志: {agent.execution_log.flush()}")

        return 0
    except KeyboardInterrupt:
        agent.interrupt()
        print("\n⏹ 已中断")
        return 130
    except Exception as e:
        logger.exception("Fatal error")
        print(f"\n❌ 致命错误: {e}", file=sys.stderr)
        return 1
    finally:
        await agent.close()


async def run_interactive(config: HarnessConfig) -> int:
    """交互模式 (类 cc-haha REPL).

    v0.1: 简单的 readline 循环
    v0.2: prompt_toolkit 全功能 TUI
    """
    agent = AgentLoop(config)
    renderer = ConsoleRenderer(
        think_visible=config.think_visibility == "show",
        verbose=True,
    )

    # v0.2: 加载记忆
    memory_count = len(agent.memory.load())
    # v0.3: 加载 Skill
    skill_count = len(agent.skills.load())

    print("=" * 60)
    print("  Kunkun v0.9.0 — DeepSeek 专属编码 Agent")
    print(f"  模型: {config.model} | 轻模型: {config.light_model}")
    print(f"  Prompt: {agent.prompt_compiler.profile.value} | 工作目录: {Path(config.workspace).resolve()}")
    print(f"  工具: {', '.join(agent.tools.list_names())}")
    print(f"  记忆: {memory_count} 条 | Skill: {skill_count} 个")
    print(f"  预算: ${agent.router.budget.daily_budget:.0f}/天 | 权限: {config.permission_mode}")
    print("=" * 60)
    print()
    print("输入任务开始 (输入 'exit' 或 'quit' 退出, Ctrl+C 中断)\n")

    try:
        while True:
            try:
                user_input = input("🧑 You › ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 再见!")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                print("👋 再见!")
                break

            print()
            await renderer.render_stream(agent.run(user_input))
            print()

    except KeyboardInterrupt:
        agent.interrupt()
    finally:
        await agent.close()

    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数.

    借鉴 cc-haha main.tsx Commander.js 全功能 CLI.
    """
    parser = argparse.ArgumentParser(
        prog="kunkun",
        description="Kunkun — DeepSeek 原生编码 Agent",
    )

    parser.add_argument(
        "prompt",
        nargs="?",
        help="任务描述 (省略则进入交互模式)",
    )

    parser.add_argument(
        "-m", "--model",
        default=None,
        help=f"模型选择 (默认: deepseek-v4-pro)",
    )
    parser.add_argument(
        "-w", "--workspace",
        default=".",
        help="工作目录 (默认: 当前目录)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=50,
        help="最大轮次 (默认: 50)",
    )
    parser.add_argument(
        "--hide-thinking",
        action="store_true",
        help="隐藏 ThinkBlock 输出",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="详细输出 (包含 ThinkBlock, 状态, 指标)",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="生成 HTML 评测仪表盘并打开",
    )
    parser.add_argument(
        "--flowforge",
        action="store_true",
        help="FlowForge 模式: 执行任务并以 JSON 输出结果",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="kunkun 0.9.0",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    """CLI 入口."""
    args = parse_args(argv)
    setup_logging(args.verbose)

    # 加载配置
    config = HarnessConfig.from_env()

    # 命令行参数覆盖
    if args.model:
        config.model = args.model
    if args.workspace:
        config.workspace = args.workspace
    config.max_turns = args.max_turns
    if args.hide_thinking:
        config.think_visibility = "hide"
    if args.verbose:
        config.verbose = True

    # 仪表盘模式
    if args.dashboard:
        from kunkun.core.dashboard import build_dashboard
        import webbrowser
        for old in Path(".kun").glob("dashboard-*.html"):
            old.unlink()
        path = build_dashboard(report_dir=config.report_dir)
        webbrowser.open(path.as_uri())
        print(f"📊 仪表盘已生成: {path}")
        return

    # FlowForge 模式
    if args.flowforge:
        if not args.prompt:
            print("❌ FlowForge 模式需要提供任务描述", file=sys.stderr)
            sys.exit(1)
        asyncio.run(_run_flowforge(args.prompt, config))
        return

    # 分发模式
    if args.prompt:
        exit_code = asyncio.run(run_once(args.prompt, config))
    else:
        exit_code = asyncio.run(run_interactive(config))

    sys.exit(exit_code)


async def _run_flowforge(prompt: str, config: HarnessConfig) -> None:
    """FlowForge 模式: 执行任务, 输出 JSON."""
    import json, uuid, sys
    from kunkun.core.flowforge_adapter import FlowForgeAdapter, FlowForgeTask

    print(f"🚀 FlowForge 执行中: {prompt[:80]}...", file=sys.stderr)
    adapter = FlowForgeAdapter(config)
    result = await adapter.execute(FlowForgeTask(
        task_id=uuid.uuid4().hex[:8],
        prompt=prompt,
        model=config.model,
    ))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


async def main_interactive():
    """交互模式入口 (kun-interactive 命令)."""
    config = HarnessConfig.from_env()
    config.verbose = True
    config.think_visibility = "show"
    exit_code = await run_interactive(config)
    sys.exit(exit_code)


# ─── setuptools 入口点 (同步包装) ──────────────

def _entry_interactive():
    """setuptools console_scripts 入口 (同步)."""
    asyncio.run(main_interactive())


def _entry_main():
    """setuptools console_scripts 入口 (同步)."""
    main()


if __name__ == "__main__":
    main()
