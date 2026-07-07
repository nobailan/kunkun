"""权限管道 — 三层门禁安全系统.

借鉴:
- cc-haha canUseTool 回调 + deny list + rule match + ask user
- learn-claude-code s03 三层门禁
- FlowForge 角色白名单

设计:
- Deny list: 硬拒绝模式匹配 (rm -rf /, sudo, curl | bash, ...)
- Workspace check: 路径必须在 workspace 内
- Permission mode: default / accept_edits / bypass
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── 权限结果 ──────────────────────────────────────


class PermissionResult(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# ─── 危险命令模式 ──────────────────────────────────

# 硬拒绝列表 — 匹配到立即拒绝，不经过 ask
DENY_PATTERNS: list[str] = [
    # 文件系统破坏
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+~",
    r"rm\s+-rf\s+\$HOME",
    r"rm\s+-rf\s+--no-preserve-root",
    r"dd\s+if=.*of=/dev/",
    r"mkfs\.",
    r">\s*/dev/sd[a-z]",
    # 提权 / 下载执行
    r"sudo\s+",
    r"curl\s+.*\|\s*(ba)?sh",
    r"curl\s+.*\|\s*bash",
    r"wget\s+.*\s*-O\s*-.*\|.*sh",
    # fork 炸弹
    r":\(\)\s*\{\s*:\|\:&\s*\};:",
    # 系统破坏
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R\s+.*\s+/",
    # 数据删除
    r"DROP\s+TABLE",
    r"DELETE\s+FROM\s+.*WHERE",
    r"format\s+[c-zC-Z]:",
    r"del\s+/f\s+/s\s+/q\s+C:\\",
]


def _compile_patterns() -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in DENY_PATTERNS]


_DENY_RE: list[re.Pattern] = _compile_patterns()


# ─── 权限检查器 ────────────────────────────────────


@dataclass
class PermissionChecker:
    """三层门禁权限检查器.

    借鉴 cc-haha canUseTool 三层门禁:
    1. Deny list → 硬拒绝 (不可绕过)
    2. Rule match → workspace 边界检查
    3. Ask user → 交互模式询问 (v0.2 默认 ask，后续接入 CLI)
    """

    workspace: str = "."
    mode: str = "default"  # "default" | "accept_edits" | "bypass"

    def check_command(self, command: str) -> PermissionResult:
        """检查命令是否在拒绝列表中.

        Args:
            command: 要执行的 shell 命令

        Returns:
            DENY 如果匹配拒绝模式，ALLOW 否则 (继续下一层检查)
        """
        # bypass 模式跳过所有检查
        if self.mode == "bypass":
            return PermissionResult.ALLOW

        for pattern in _DENY_RE:
            if pattern.search(command):
                logger.warning("DENIED: command matches deny pattern '%s'", pattern.pattern)
                return PermissionResult.DENY

        return PermissionResult.ALLOW

    def check_path(self, path: str, tool_permission: str = "read") -> PermissionResult:
        """检查路径是否在 workspace 内.

        借鉴 cc-haha workspace 边界检查:
        - read 工具: 路径必须在 workspace 内
        - write 工具: 路径必须在 workspace 内 + 不允许覆盖系统文件

        Args:
            path: 要操作的文件路径
            tool_permission: 工具权限级别 ("read" | "write" | "destroy")

        Returns:
            ALLOW 如果在边界内，DENY 如果越界
        """
        if self.mode == "bypass":
            return PermissionResult.ALLOW

        try:
            resolved = Path(path).resolve()
            ws = Path(self.workspace).resolve()
        except (OSError, ValueError):
            logger.warning("Invalid path: %s", path)
            return PermissionResult.DENY

        # 相对路径默认在 workspace 内
        if not path.startswith("/") and not path.startswith("\\") and not path.startswith("~"):
            if not Path(path).is_absolute():
                return PermissionResult.ALLOW

        # 绝对路径检查是否在 workspace 下
        try:
            resolved.relative_to(ws)
            return PermissionResult.ALLOW
        except ValueError:
            logger.warning(
                "Path '%s' is outside workspace '%s'", path, ws
            )
            return PermissionResult.DENY

    def check_tool(
        self,
        tool_name: str,
        tool_input: dict,
        tool_permission: str = "read",
    ) -> PermissionResult:
        """完整的工具权限检查.

        三层门禁:
        1. Deny list (针对 bash 命令)
        2. Workspace 边界 (针对文件路径)
        3. Permission mode

        Args:
            tool_name: 工具名称
            tool_input: 工具参数
            tool_permission: 工具权限级别

        Returns:
            ALLOW / DENY / ASK
        """
        # 第 0 层: bypass
        if self.mode == "bypass":
            return PermissionResult.ALLOW

        # 第 1 层: deny list (仅 bash 工具)
        if tool_name == "bash":
            command = tool_input.get("command", "")
            if command:
                result = self.check_command(str(command))
                if result == PermissionResult.DENY:
                    return PermissionResult.DENY

        # 第 2 层: workspace 边界
        path = tool_input.get("path") or tool_input.get("file_path") or tool_input.get("file", "")
        if path and tool_permission in ("read", "write", "destroy"):
            result = self.check_path(str(path), tool_permission)
            if result == PermissionResult.DENY:
                return PermissionResult.DENY

        # 第 3 层: accept_edits 模式
        if self.mode == "accept_edits":
            if tool_permission in ("write", "destroy"):
                return PermissionResult.ALLOW  # accept_edits 模式自动批准写操作

        # 第 4 层: default → 需要询问 (v0.2 默认放行，GUI 阶段接入交互确认)
        if self.mode == "default":
            if tool_permission in ("write", "destroy"):
                return PermissionResult.ASK

        return PermissionResult.ALLOW

    def reason(self, result: PermissionResult, tool_name: str, detail: str = "") -> str:
        """生成权限拒绝原因."""
        if result == PermissionResult.DENY:
            return f"🚫 权限拒绝: {tool_name} — {detail or '操作被安全策略拦截'}"
        if result == PermissionResult.ASK:
            return f"❓ 需要确认: {tool_name} — {detail or '需要用户授权'}"
        return ""


# ─── 危险命令描述 ──────────────────────────────────


def describe_denied_pattern(command: str) -> str | None:
    """描述命令命中了哪个拒绝模式."""
    for pattern in _DENY_RE:
        if pattern.search(command):
            return pattern.pattern
    return None
