"""Kun 入口点.

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

from kun.core.agent_loop import AgentLoop
from kun.core.state import HarnessConfig
from kun.cli.tui import ConsoleRenderer, HeadlessRenderer

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False):
    """配置日志."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


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

    print("=" * 60)
    print("  Kun v0.2.0 — DeepSeek 专属编码 Agent")
    print(f"  模型: {config.model} | 轻模型: {config.light_model}")
    print(f"  工作目录: {Path(config.workspace).resolve()}")
    print(f"  工具: {', '.join(agent.tools.list_names())}")
    print(f"  记忆: {memory_count} 条 | 预算: ${agent.router.budget.daily_budget:.0f}/天")
    print(f"  权限模式: {config.permission_mode}")
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
        prog="kun",
        description="Kun (鲲) — DeepSeek 原生编码 Agent",
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
        "--version",
        action="version",
        version="kun 0.2.0",
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

    # 分发模式
    if args.prompt:
        exit_code = asyncio.run(run_once(args.prompt, config))
    else:
        exit_code = asyncio.run(run_interactive(config))

    sys.exit(exit_code)


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
