"""Bash 工具 — 执行 Shell 命令.

借鉴:
- cc-haha src/tools/BashTool/BashTool.tsx (~1100 lines) — 完整的 bash 工具
  - Sandbox 管理
  - 超时控制 (DEFAULT_TIMEOUT=120s, MAX=600s)
  - 搜索/读/列表命令分类
  - git 操作追踪
  - 图片输出检测
- Hermes tools/terminal_tool.py — 终端执行 + env passthrough
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from kun.core.state import ToolResult, ContentBlock, ContentType
from kun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)

# 借鉴 cc-haha BashTool 的常量
DEFAULT_TIMEOUT_S = 120  # 默认超时
MAX_TIMEOUT_S = 600  # 最大超时 (借鉴 cc-haha getMaxTimeoutMs → 10 min)
OUTPUT_MAX_CHARS = 100_000  # 输出截断上限 (借鉴 cc-haha maxResultSizeChars)

# 危险命令检测 (借鉴 Hermes tool_dispatch_helpers._DESTRUCTIVE_PATTERNS)
DANGEROUS_PATTERNS = re.compile(
    r"""(?:^|\s|&&|\|\||;|`)   (?:
        rm\s+-rf\s+/ |          # 递归删除根目录
        sudo\s+rm |             # sudo 删除
        >\s*/dev/[sh]d[a-z] |   # 覆写磁盘
        mkfs\. |                # 格式化
        dd\s+if= |              # 磁盘直接操作
        :\(\)\s*\{\s*:\|:&\s*\} # fork bomb
    )""",
    re.VERBOSE,
)


class BashInput(BaseModel):
    """bash 工具输入参数.

    借鉴 cc-haha BashTool 的 input schema:
    - command: 要执行的命令 (required)
    - timeout: 超时秒数 (optional, default 120)
    - description: 命令用途描述 (optional, 辅助权限判断)
    """

    command: str = Field(description="The bash command to execute")
    timeout: int = Field(
        default=DEFAULT_TIMEOUT_S,
        description=f"Timeout in seconds (max {MAX_TIMEOUT_S})",
    )
    description: str = Field(default="", description="Description of what this command does")


@tool(
    name="bash",
    description="Execute a bash command in the workspace. Returns stdout and stderr output. "
    "Use for running tests, installing packages, git operations, file searches, "
    "and other shell commands. Returns truncated output for very large results.",
    permission="write",
    is_concurrency_safe=False,
    input_model=BashInput,
)
async def bash_tool(args: BashInput, ctx: ToolUseContext) -> ToolResult:
    """执行 Shell 命令.

    借鉴 cc-haha BashTool.call() 的执行流程:
    1. 危险命令检测
    2. 超时限制 (min(args.timeout, MAX_TIMEOUT))
    3. asyncio.create_subprocess_shell 执行
    4. stdout/stderr 分别收集
    5. 输出截断
    """
    command = args.command.strip()
    timeout = min(args.timeout, MAX_TIMEOUT_S)
    workspace = Path(ctx.workspace).resolve()

    # 危险命令检测 (借鉴 Hermes _is_destructive_command)
    if DANGEROUS_PATTERNS.search(command):
        return ToolResult(
            data=f"❌ 危险命令被拒绝: {command}\n匹配危险模式，操作被阻止。",
            is_error=True,
        )

    logger.info("Executing bash: %s (timeout=%ds)", command[:100], timeout)

    try:
        # 执行命令 (借鉴 cc-haha exec() via Shell.ts)
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(
                data=f"⏱ 命令超时 ({timeout}s): {command[:100]}...",
                is_error=True,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # 组装输出 (借鉴 cc-haha BashTool 的输出格式)
        parts: list[str] = []
        if stdout:
            # 截断过长输出 (借鉴 cc-haha EndTruncatingAccumulator)
            if len(stdout) > OUTPUT_MAX_CHARS:
                stdout = stdout[:OUTPUT_MAX_CHARS] + "\n\n... (输出过长，已截断)"
            parts.append(stdout)
        if stderr:
            if len(stderr) > OUTPUT_MAX_CHARS // 2:
                stderr = stderr[:OUTPUT_MAX_CHARS // 2] + "\n... (stderr 已截断)"
            parts.append(f"\n[stderr]\n{stderr}")

        if not parts:
            parts.append("(命令无输出)")

        exit_code = process.returncode or 0
        if exit_code != 0:
            parts.insert(0, f"[exit code: {exit_code}]")

        result_text = "\n".join(parts)
        return ToolResult(data=result_text, is_error=exit_code != 0)

    except FileNotFoundError:
        return ToolResult(
            data=f"命令未找到: {command.split()[0] if command else '?'}",
            is_error=True,
        )
    except Exception as e:
        logger.exception("Bash execution error")
        return ToolResult(data=f"执行命令时出错: {e}", is_error=True)
