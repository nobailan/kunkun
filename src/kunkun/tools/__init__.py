"""工具层 — 装饰器注册 + 内置工具.

v0.1: 手动注册 4 个基础工具
v0.2+: 6 个工具 (新增 remember, recall)
"""

from kunkun.tools.decorators import ToolRegistry, ToolInstance, ToolUseContext, tool
from kunkun.tools.registry import get_registry

# ─── 初始化: 注册所有内置工具 ────────────────────

from kunkun.tools.bash_tool import bash_tool
from kunkun.tools.read_file import read_file_tool
from kunkun.tools.write_file import write_file_tool
from kunkun.tools.glob_tool import glob_tool
from kunkun.tools.remember_tool import remember_tool, recall_tool, skill_load_tool
from kunkun.tools.grep_tool import grep_tool
from kunkun.tools.edit_tool import edit_tool
from kunkun.tools.web_tools import websearch_tool, webfetch_tool
from kunkun.tools.agent_tool import agent_tool, todowrite_tool, grpo_tool
from kunkun.tools.code_intel_tool import findsymbol_tool, gotodef_tool, findrefs_tool


def init_tools() -> ToolRegistry:
    """初始化工具注册中心."""
    registry = ToolRegistry()

    # v0.1 文件工具
    registry.register(bash_tool._tool_instance)
    registry.register(read_file_tool._tool_instance)
    registry.register(write_file_tool._tool_instance)
    registry.register(glob_tool._tool_instance)

    # v0.4 文件工具
    registry.register(grep_tool._tool_instance)
    registry.register(edit_tool._tool_instance)

    # v0.2 记忆工具
    registry.register(remember_tool._tool_instance)
    registry.register(recall_tool._tool_instance)

    # v0.3 Skill 工具
    registry.register(skill_load_tool._tool_instance)

    # v0.4.1 Web 工具
    registry.register(websearch_tool._tool_instance)
    registry.register(webfetch_tool._tool_instance)

    # v0.4.2 Agent 编排
    registry.register(agent_tool._tool_instance)
    registry.register(todowrite_tool._tool_instance)

    # v0.6 GRPO 多版本生成
    registry.register(grpo_tool._tool_instance)

    # v0.4.3 代码智能
    registry.register(findsymbol_tool._tool_instance)
    registry.register(gotodef_tool._tool_instance)
    registry.register(findrefs_tool._tool_instance)

    return registry
