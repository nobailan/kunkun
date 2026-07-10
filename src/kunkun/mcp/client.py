"""MCP 客户端 — Model Context Protocol 集成.

支持 stdio 连接的 MCP Server:
- 启动子进程 (npx/uvx/python)
- JSON-RPC 握手 + 工具发现
- 工具注册到 Kunkun ToolRegistry
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kunkun.core.state import ToolResult
from kunkun.tools.decorators import ToolInstance, ToolUseContext

logger = logging.getLogger(__name__)


@dataclass
class MCPServer:
    """MCP Server 连接."""

    name: str
    command: str       # 启动命令, 如 "npx @modelcontextprotocol/server-filesystem /tmp"
    process: asyncio.subprocess.Process | None = None
    tools: list[dict] = field(default_factory=list)
    _request_id: int = 0

    async def connect(self) -> bool:
        """启动 MCP Server 子进程并握手."""
        parts = self.command.split()
        try:
            self.process = await asyncio.create_subprocess_exec(
                *parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.warning("MCP server '%s' not found: %s", self.name, parts[0])
            return False
        except Exception as e:
            logger.warning("MCP server '%s' start failed: %s", self.name, e)
            return False

        # JSON-RPC initialize
        try:
            init_resp = await self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "kunkun", "version": "0.9.0"},
            })
            if not init_resp:
                return False
        except Exception:
            return False

        # Discover tools
        try:
            tools_resp = await self._request("tools/list", {})
            if tools_resp and "tools" in tools_resp:
                self.tools = tools_resp["tools"]
                logger.info("MCP '%s': discovered %d tools", self.name, len(self.tools))
        except Exception:
            pass

        return True

    async def call_tool(self, tool_name: str, args: dict) -> str:
        """调用 MCP 工具."""
        try:
            result = await self._request("tools/call", {
                "name": tool_name,
                "arguments": args,
            })
            if result is None:
                return "MCP tool call failed: no response"
            # Extract content from MCP response
            content = result.get("content", [])
            if isinstance(content, list):
                return "\n".join(
                    c.get("text", str(c)) for c in content if isinstance(c, dict)
                )
            return str(content)
        except Exception as e:
            return f"MCP tool error: {e}"

    async def _request(self, method: str, params: dict) -> dict | None:
        """发送 JSON-RPC 请求."""
        if not self.process or self.process.returncode is not None:
            return None

        self._request_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        try:
            self.process.stdin.write((json.dumps(req) + "\n").encode())
            await self.process.stdin.drain()
        except Exception:
            return None

        try:
            line = await asyncio.wait_for(
                self.process.stdout.readline(),
                timeout=30,
            )
            if not line:
                return None
            resp = json.loads(line.decode())
            if "error" in resp:
                logger.debug("MCP error: %s", resp["error"])
                return None
            return resp.get("result", {})
        except asyncio.TimeoutError:
            logger.debug("MCP timeout: %s", method)
            return None
        except Exception:
            return None

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except Exception:
                self.process.kill()


class MCPRegistry:
    """MCP Server 注册中心 — 管理多个 MCP 连接."""

    def __init__(self):
        self._servers: dict[str, MCPServer] = {}
        self._tool_registry: Any = None
        self._mcp_tools: dict[str, MCPServer] = {}  # tool_name → server

    async def connect(self, name: str, command: str) -> bool:
        """连接 MCP Server."""
        server = MCPServer(name=name, command=command)
        if await server.connect():
            self._servers[name] = server
            return True
        return False

    def discover_tools(self) -> list[dict]:
        """收集所有 MCP Server 的工具 schema."""
        all_tools = []
        self._mcp_tools.clear()
        for server in self._servers.values():
            for tool in server.tools:
                tool_name = f"mcp_{server.name}_{tool['name']}"
                self._mcp_tools[tool_name] = server
                all_tools.append({
                    "name": tool_name,
                    "description": tool.get("description", f"MCP tool from {server.name}"),
                    "input_schema": tool.get("inputSchema", {
                        "type": "object",
                        "properties": {},
                    }),
                })
        return all_tools

    def create_tool_instance(self, schema: dict) -> ToolInstance:
        """为 MCP 工具创建 ToolInstance."""
        tool_name = schema["name"]
        server = self._mcp_tools[tool_name]

        async def _handler(args: dict, ctx: ToolUseContext) -> ToolResult:
            try:
                result = await server.call_tool(
                    tool_name[len(f"mcp_{server.name}_"):],
                    args,
                )
                return ToolResult(data=result)
            except Exception as e:
                return ToolResult(data=f"MCP error: {e}", is_error=True)

        return ToolInstance(
            name=tool_name,
            description=schema["description"],
            permission="read",
            is_concurrency_safe=True,
            handler=_handler,
        )

    async def close_all(self) -> None:
        for server in self._servers.values():
            await server.close()


# ─── 全局单例 ──────────────────────────────────────────

_registry: MCPRegistry | None = None


def get_mcp_registry() -> MCPRegistry:
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry


def init_mcp(config_path: str = ".kun/mcp.json") -> int:
    """从配置文件初始化 MCP 连接.

    配置文件格式 (.kun/mcp.json):
    {
      "servers": {
        "filesystem": {"command": "npx @modelcontextprotocol/server-filesystem /tmp"},
        "github": {"command": "npx @modelcontextprotocol/server-github"}
      }
    }

    Returns:
        成功连接的 Server 数量
    """
    path = Path(config_path)
    if not path.exists():
        return 0

    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    servers = config.get("servers", {})
    if not servers:
        return 0

    async def _connect_all():
        registry = get_mcp_registry()
        count = 0
        for name, cfg in servers.items():
            cmd = cfg.get("command", "")
            if cmd and await registry.connect(name, cmd):
                count += 1
        return count

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在运行的 loop 中: create_task
            task = asyncio.create_task(_connect_all())
            # 等最多 10 秒
            return asyncio.get_event_loop().run_until_complete(
                asyncio.wait_for(task, timeout=10)
            )
        else:
            return asyncio.run(_connect_all())
    except Exception:
        return 0
