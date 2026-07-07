"""工具装饰器 + 注册中心.

借鉴:
- cc-haha src/Tool.ts — Tool<Input,Output,Progress> 泛型接口 + buildTool() 工厂
- Hermes tools/registry.py — AST 扫描自发现 + ToolEntry slots
- FlowForge 装饰器注册模式

设计:
  @tool(name="bash", description="...", permission="write")
  async def bash_tool(args, context) -> ToolResult: ...

  registry = ToolRegistry()
  registry.discover()  # 自动扫描 tools/ 目录

v0.1: 手动注册 4 个工具
v0.2+: AST 扫描自动发现 (借鉴 Hermes)
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Awaitable

from pydantic import BaseModel

from kunkun.core.state import ToolResult

logger = logging.getLogger(__name__)

# ─── 工具实例 ─────────────────────────────────────


class ToolInstance:
    """工具实例.

    借鉴 cc-haha Tool<Input, Output, Progress>:
    - name, description, input_schema, permission
    - call(), check_permissions(), is_enabled()
    """

    __slots__ = (
        "name",
        "description",
        "permission",
        "is_concurrency_safe",
        "_handler",
        "_input_model",
    )

    def __init__(
        self,
        name: str,
        description: str,
        permission: str,
        is_concurrency_safe: bool,
        handler: Callable[..., Awaitable[ToolResult]],
        input_model: type[BaseModel] | None = None,
    ):
        self.name = name
        self.description = description
        self.permission = permission  # "read" | "write" | "destroy"
        self.is_concurrency_safe = is_concurrency_safe
        self._handler = handler
        self._input_model = input_model

    async def call(self, args: dict, context: "ToolUseContext") -> ToolResult:
        """执行工具.

        如果定义了 input_model，使用 Pydantic 验证参数。
        """
        if self._input_model:
            try:
                validated = self._input_model(**args)
            except Exception as e:
                return ToolResult(
                    data=f"参数验证失败: {e}",
                    is_error=True,
                )
            return await self._handler(validated, context)
        return await self._handler(args, context)

    def is_read_only(self) -> bool:
        """是否为只读工具."""
        return self.permission == "read"

    def is_destructive(self) -> bool:
        """是否为破坏性工具."""
        return self.permission == "destroy"

    def to_api_schema(self) -> dict:
        """生成 Anthropic API 格式的工具 schema.

        借鉴 cc-haha toolToAPISchema (src/utils/api.ts).
        """
        schema: dict = {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

        if self._input_model:
            json_schema = self._input_model.model_json_schema()
            schema["input_schema"] = {
                "type": "object",
                "properties": json_schema.get("properties", {}),
                "required": json_schema.get("required", []),
            }

        return schema


# ─── 工具注册中心 ─────────────────────────────────


class ToolRegistry:
    """工具注册中心.

    借鉴 Hermes tools/registry.py — ToolEntry 管理:
    - register(): 注册单个工具
    - get(): 按名称查找
    - schemas(): 导出所有工具的 API schema
    """

    def __init__(self):
        self._tools: dict[str, ToolInstance] = {}
        self._disabled: set[str] = set()

    def register(self, tool: ToolInstance) -> None:
        """注册工具."""
        if tool.name in self._tools:
            logger.warning("Tool '%s' already registered, overwriting", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolInstance | None:
        """按名称查找工具."""
        return self._tools.get(name)

    def disable(self, name: str) -> None:
        """禁用工具."""
        self._disabled.add(name)

    def enable(self, name: str) -> None:
        """启用工具."""
        self._disabled.discard(name)

    def schemas(self) -> list[dict]:
        """导出所有可用工具的 API schema.

        借鉴 cc-haha getTools() → toolToAPISchema() 链路.
        """
        return [
            t.to_api_schema()
            for t in self._tools.values()
            if t.name not in self._disabled
        ]

    def list_names(self) -> list[str]:
        """列出所有注册的工具名."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)


# ─── 装饰器 ──────────────────────────────────────


def tool(
    name: str,
    description: str,
    permission: str = "read",
    is_concurrency_safe: bool = False,
    input_model: type[BaseModel] | None = None,
):
    """工具装饰器.

    借鉴 cc-haha buildTool(def) + FlowForge 装饰器:
    - 自动创建 ToolInstance
    - 注册到全局 registry (如果可用)

    Usage:
        @tool(name="bash", description="Execute a bash command", permission="write")
        async def bash_tool(args: BashInput, ctx: ToolUseContext) -> ToolResult:
            ...
    """

    def decorator(
        func: Callable[..., Awaitable[ToolResult]],
    ) -> Callable[..., Awaitable[ToolResult]]:

        tool_instance = ToolInstance(
            name=name,
            description=description,
            permission=permission,
            is_concurrency_safe=is_concurrency_safe,
            handler=func,
            input_model=input_model,
        )
        func._tool_instance = tool_instance  # type: ignore

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        wrapper._tool_instance = tool_instance  # type: ignore
        return wrapper

    return decorator


# ─── ToolUseContext ──────────────────────────────


class ToolUseContext:
    """工具执行上下文.

    借鉴 cc-haha ToolUseContext (src/Tool.ts:158-300):
    - workspace: 工作目录
    - session_id: 当前会话 ID
    - abort_signal: 中断信号
    """

    def __init__(
        self,
        workspace: str = ".",
        session_id: str = "",
    ):
        self.workspace = workspace
        self.session_id = session_id
        self.metadata: dict[str, Any] = {}
