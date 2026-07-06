"""工具注册中心 (简化的 registry.py).

v0.1: 手动注册，后续版本可升级为 AST 扫描自发现 (借鉴 Hermes tools/registry.py).
"""

from kun.tools.decorators import ToolRegistry

# 全局单例 (借鉴 cc-haha getTools() 模式)
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """获取全局工具注册中心."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def reset_registry() -> None:
    """重置注册中心 (测试用)."""
    global _registry
    _registry = ToolRegistry()
