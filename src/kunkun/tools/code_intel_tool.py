"""代码智能工具 — 符号搜索 + 定义追踪 + 引用查找.

v0.4.3: 使用 Python AST 实现, 零外部依赖.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from kunkun.core.state import ToolResult
from kunkun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)


class FindSymbolInput(BaseModel):
    """findsymbol 工具输入参数."""

    query: str = Field(description="符号名称, 如类名/函数名/变量名")
    path: str = Field(default=".", description="搜索目录")


class GotoDefInput(BaseModel):
    """gotodef 工具输入参数."""

    symbol: str = Field(description="要查找定义的符号名称")
    file_path: str = Field(description="文件路径")
    line: int = Field(default=0, description="行号 (辅助定位)")


class FindRefsInput(BaseModel):
    """findrefs 工具输入参数."""

    symbol: str = Field(description="要查找引用的符号名称")
    path: str = Field(default=".", description="搜索目录")


# ─── AST 辅助 ──────────────────────────────────────────


class _SymbolVisitor(ast.NodeVisitor):
    """收集所有顶层符号."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.symbols: list[dict] = []

    def visit_FunctionDef(self, node):
        self.symbols.append({
            "name": node.name,
            "kind": "function",
            "file": self.filepath,
            "line": node.lineno,
            "docstring": ast.get_docstring(node) or "",
        })
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.symbols.append({
            "name": node.name,
            "kind": "async_function",
            "file": self.filepath,
            "line": node.lineno,
            "docstring": ast.get_docstring(node) or "",
        })
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.symbols.append({
            "name": node.name,
            "kind": "class",
            "file": self.filepath,
            "line": node.lineno,
            "docstring": ast.get_docstring(node) or "",
            "methods": methods,
        })
        self.generic_visit(node)


class _RefVisitor(ast.NodeVisitor):
    """收集对特定符号的引用."""

    def __init__(self, target: str, filepath: str):
        self.target = target
        self.filepath = filepath
        self.refs: list[dict] = []

    def visit_Name(self, node):
        if node.id == self.target:
            self.refs.append({"file": self.filepath, "line": node.lineno})
        self.generic_visit(node)


def _parse_file(path: str) -> ast.Module | None:
    """解析 Python 文件."""
    try:
        with open(path, encoding="utf-8") as f:
            return ast.parse(f.read(), filename=path)
    except Exception:
        return None


# ─── 工具 ──────────────────────────────────────────────


@tool(
    name="findsymbol",
    description=(
        "搜索 Python 文件中的符号定义 (类、函数、异步函数)。"
        "返回符号名、类型、文件、行号、docstring。"
    ),
    permission="read",
    input_model=FindSymbolInput,
)
async def findsymbol_tool(args: FindSymbolInput, ctx: ToolUseContext) -> ToolResult:
    """搜索符号定义."""
    search_path = Path(ctx.workspace) / args.path
    if not search_path.exists():
        return ToolResult(data=f"❌ 路径不存在: {search_path}", is_error=True)

    files = list(search_path.rglob("*.py")) if search_path.is_dir() else [search_path]
    files = [f for f in files[:200] if ".git" not in str(f) and "__pycache__" not in str(f)]

    all_symbols: list[dict] = []
    for f in files:
        tree = _parse_file(str(f))
        if tree is None:
            continue
        v = _SymbolVisitor(str(f))
        v.visit(tree)
        all_symbols.extend(v.symbols)

    if not all_symbols:
        return ToolResult(data=f"未找到符号定义。")

    # 筛选匹配
    query_lower = args.query.lower()
    matched = [s for s in all_symbols if query_lower in s["name"].lower()]

    if not matched:
        return ToolResult(data=f"未找到匹配 '{args.query}' 的符号。")

    lines = [f"🔍 符号搜索: '{args.query}' ({len(matched)} 个)\n"]
    for s in matched[:30]:
        try:
            rel = Path(s["file"]).relative_to(ctx.workspace)
        except ValueError:
            rel = s["file"]
        method_info = f" (methods: {', '.join(s.get('methods', [])[:5])})" if s.get("methods") else ""
        lines.append(f"- **{s['kind']}** `{s['name']}` @ {rel}:{s['line']}{method_info}")
        if s.get("docstring"):
            lines.append(f"  _{s['docstring'][:120]}_")

    return ToolResult(data="\n".join(lines))


@tool(
    name="gotodef",
    description=(
        "查找 Python 符号的定义位置。给定符号名和文件路径，"
        "返回该符号的 class/def 声明位置。"
    ),
    permission="read",
    input_model=GotoDefInput,
)
async def gotodef_tool(args: GotoDefInput, ctx: ToolUseContext) -> ToolResult:
    """查找定义."""
    file_path = Path(ctx.workspace) / args.file_path
    if not file_path.is_file():
        return ToolResult(data=f"❌ 文件不存在: {file_path}", is_error=True)

    tree = _parse_file(str(file_path))
    if tree is None:
        return ToolResult(data="❌ 无法解析文件", is_error=True)

    v = _SymbolVisitor(str(file_path))
    v.visit(tree)

    matched = [s for s in v.symbols if s["name"] == args.symbol]
    if not matched:
        return ToolResult(data=f"未在 {file_path} 中找到 '{args.symbol}' 的定义。")

    s = matched[0]
    lines = [
        f"📍 `{s['name']}` ({s['kind']})",
        f"   文件: {s['file']}",
        f"   行号: {s['line']}",
    ]
    if s.get("docstring"):
        lines.append(f"   Docstring: {s['docstring'][:200]}")
    if s.get("methods"):
        lines.append(f"   方法: {', '.join(s['methods'])}")

    return ToolResult(data="\n".join(lines))


@tool(
    name="findrefs",
    description=(
        "查找 Python 文件中所有引用指定符号的位置。"
        "用于理解代码中某个函数/类/变量被哪些地方使用。"
    ),
    permission="read",
    input_model=FindRefsInput,
)
async def findrefs_tool(args: FindRefsInput, ctx: ToolUseContext) -> ToolResult:
    """查找引用."""
    search_path = Path(ctx.workspace) / args.path
    if not search_path.exists():
        return ToolResult(data=f"❌ 路径不存在: {search_path}", is_error=True)

    files = list(search_path.rglob("*.py")) if search_path.is_dir() else [search_path]
    files = [f for f in files[:200] if ".git" not in str(f) and "__pycache__" not in str(f)]

    all_refs: list[dict] = []
    for f in files:
        tree = _parse_file(str(f))
        if tree is None:
            continue
        v = _RefVisitor(args.symbol, str(f))
        v.visit(tree)
        all_refs.extend(v.refs)

    if not all_refs:
        return ToolResult(data=f"未找到 '{args.symbol}' 的引用。")

    lines = [f"🔗 '{args.symbol}' 引用: {len(all_refs)} 处\n"]
    for r in all_refs[:30]:
        try:
            rel = Path(r["file"]).relative_to(ctx.workspace)
        except ValueError:
            rel = r["file"]
        lines.append(f"  {rel}:{r['line']}")

    return ToolResult(data="\n".join(lines))
