"""工具层 — 装饰器注册 + 内置工具.

v0.1: 手动注册 4 个基础工具
v0.2+: AST 扫描自发现 (借鉴 Hermes tools/registry.py)
"""

from kun.tools.decorators import ToolRegistry, ToolInstance, ToolUseContext, tool
from kun.tools.registry import get_registry

# ─── 初始化: 注册所有内置工具 ────────────────────

from kun.tools.bash_tool import bash_tool
from kun.tools.read_file import read_file_tool
from kun.tools.write_file import write_file_tool
from kun.tools.glob_tool import glob_tool


def init_tools() -> ToolRegistry:
    """初始化工具注册中心."""
    registry = ToolRegistry()

    # 注册内置工具
    registry.register(bash_tool._tool_instance)
    registry.register(read_file_tool._tool_instance)
    registry.register(write_file_tool._tool_instance)
    registry.register(glob_tool._tool_instance)

    return registry
