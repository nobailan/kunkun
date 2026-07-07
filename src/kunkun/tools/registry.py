"""Tool registry (simplified).

v0.1: Manual registration, v0.2+: AST scan auto-discovery (inspired by Hermes tools/registry.py).
"""

from __future__ import annotations

from typing import Optional

from kunkun.tools.decorators import ToolRegistry

# Global singleton (inspired by cc-haha getTools() pattern)
_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """Get global tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def reset_registry() -> None:
    """Reset registry (for testing)."""
    global _registry
    _registry = ToolRegistry()
