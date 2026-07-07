"""记忆写入工具 — 让 Agent 可以持久化项目记忆.

Agent 调用此工具来"记住"用户偏好、项目事实等，写入 .kun/memory/*.md.

v0.3.1: 新增批量写入 — operations 数组一次完成增删改
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from kun.core.state import ToolResult
from kun.memory.manager import Memory, MemoryManager
from kun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)


class MemoryOp(BaseModel):
    """单条记忆/Skill 操作."""

    action: str = Field(description="操作类型: add (新增), replace (替换), remove (删除)")
    target: str = Field(
        default="memory",
        description="写入目标: memory (事实) 或 skill (约定/规范)",
    )
    name: str = Field(
        default="",
        description="记忆/Skill 名称 (kebab-case slug)。add/replace 时必填",
    )
    content: str = Field(
        default="",
        description="正文 (Markdown)。add/replace 时必填",
    )
    description: str = Field(
        default="",
        description="一句话描述。add/replace 时建议填写",
    )
    type: str = Field(
        default="project",
        description="[target=memory] 记忆类型: project / user / feedback",
    )
    triggers: Optional[list[str]] = Field(
        default=None,
        description="[target=skill] 触发词列表",
    )
    old_name: str = Field(
        default="",
        description="要替换/删除的旧名称。replace/remove 时必填",
    )


class RememberInput(BaseModel):
    """remember 工具输入参数.

    两种用法:
    1. 单条: 默认 action=add，填写 name, description, content, type
             action=remove 时只需 name 参数
    2. 批量: 填写 operations 数组，原子执行
    """

    action: str = Field(
        default="add",
        description="操作类型: add (新增/覆盖), remove (删除)。默认 add",
    )
    target: str = Field(
        default="memory",
        description="写入目标: memory (事实/偏好 → .kun/memory/) 或 skill (约定/规范 → skills/)。区分标准：事实型（项目路径、用户名、技术栈）→ memory；约定型（编码规范、工作流规则、禁止事项）→ skill",
    )
    name: str = Field(default="", description="记忆/Skill 名称 (kebab-case slug)。add/remove 时必填")
    description: str = Field(default="", description="[add 模式] 一句话描述")
    content: str = Field(default="", description="[add 模式] 正文 (Markdown)")
    type: str = Field(default="project", description="[target=memory] 记忆类型: project / user / feedback")
    triggers: Optional[list[str]] = Field(
        default=None,
        description="[target=skill] 触发词列表，用于匹配用户 prompt。例如 ['Python', '编码', '代码风格']",
    )
    operations: Optional[list[MemoryOp]] = Field(
        default=None,
        description="[批量模式] 操作列表，原子执行。每项也支持 target 字段",
    )


class RecallInput(BaseModel):
    """recall 工具输入参数."""

    query: str = Field(description="搜索关键词, 用于查找已保存的记忆")


@tool(
    name="remember",
    description=(
        "管理项目记忆和 Skill。支持单条和批量操作。\n\n"
        "## 写入目标 (target)\n"
        "- target='memory': 事实/偏好（项目路径、用户名、技术栈、个人偏好）→ .kun/memory/\n"
        "- target='skill': 约定/规范（编码规范、工作流规则、禁止事项）→ skills/ 目录，后续对话按触发词自动激活\n\n"
        "## 判断标准\n"
        "事实型 → memory: '用户名是张三'、'项目在 E:\\projects'\n"
        "约定型 → skill: '用2空格缩进'、'注释写中文'、'禁止 sudo rm'、'推送前先测试'\n\n"
        "单条: 填 action, target, name, content\n"
        "批量: 填 operations 数组，一次完成增删改"
    ),
    permission="write",
    input_model=RememberInput,
)
async def remember_tool(args: RememberInput, ctx: ToolUseContext) -> ToolResult:
    """管理项目记忆 (单条/批量)."""
    memory_dir = ctx.metadata.get("memory_dir", ".kun/memory")
    mgr = MemoryManager(memory_dir=memory_dir)
    mgr.load()

    # ── 批量模式 ──
    if args.operations:
        return await _batch_remember(args.operations, mgr, ctx)

    # ── 单条模式 ──
    if not args.name.strip():
        return ToolResult(
            data="❌ 记忆操作失败: name 不能为空。",
            is_error=True,
        )

    if args.action == "remove":
        if args.target == "skill":
            return _single_remove_skill(args.name, ctx)
        return _single_remove(args.name, mgr)

    # 默认 action=add
    if not args.content.strip():
        return ToolResult(
            data="❌ 保存失败: content 不能为空。",
            is_error=True,
        )

    if args.target == "skill":
        return _single_add_skill(
            args.name, args.description, args.content,
            args.triggers or [], ctx,
        )
    return _single_add(args.name, args.description, args.content, args.type, mgr)


async def _batch_remember(
    operations: list[MemoryOp],
    mgr: MemoryManager,
    ctx: ToolUseContext,
) -> ToolResult:
    """批量原子执行记忆/Skill 操作.

    add: 新增文件 (target=memory → .kun/memory/, target=skill → skills/)
    replace: 删除旧文件 + 创建新文件
    remove: 删除文件

    原子性: 先校验全部操作，再执行。任一操作参数无效则全部放弃。
    """
    # ── 校验阶段 ──
    errors: list[str] = []
    for i, op in enumerate(operations):
        pos = f"操作 {i + 1}"
        tgt = op.target or "memory"
        if op.action == "add":
            if not op.name.strip():
                errors.append(f"{pos} (add → {tgt}): name 不能为空")
            if not op.content.strip():
                errors.append(f"{pos} (add → {tgt}): content 不能为空")
        elif op.action == "replace":
            if not op.old_name.strip():
                errors.append(f"{pos} (replace): old_name 不能为空")
            if not op.name.strip() and not op.content.strip():
                errors.append(f"{pos} (replace): 至少需要 name 或 content")
        elif op.action == "remove":
            if not op.old_name.strip():
                errors.append(f"{pos} (remove): old_name 不能为空")
        else:
            errors.append(f"{pos}: 未知操作 '{op.action}'，支持 add / replace / remove")

    if errors:
        return ToolResult(
            data="❌ 批量操作校验失败:\n" + "\n".join(f"  - {e}" for e in errors),
            is_error=True,
        )

    # ── 执行阶段 ──
    results: list[str] = []
    for op in operations:
        tgt = op.target or "memory"
        try:
            if op.action == "add":
                if tgt == "skill":
                    _single_add_skill(op.name, op.description, op.content, op.triggers or [], ctx)
                else:
                    _single_add(op.name, op.description, op.content, op.type, mgr)
                results.append(f"  ✅ add → {tgt}: {op.name}")
            elif op.action == "replace":
                if tgt == "skill":
                    _single_remove_skill(op.old_name.strip(), ctx)
                    new_name = op.name.strip() or op.old_name.strip()
                    _single_add_skill(new_name, op.description, op.content, op.triggers or [], ctx)
                else:
                    mgr.delete(op.old_name.strip())
                    new_name = op.name.strip() or op.old_name.strip()
                    _single_add(new_name, op.description, op.content, op.type, mgr)
                results.append(f"  🔄 replace → {tgt}: {op.old_name} → {new_name}")
            elif op.action == "remove":
                if tgt == "skill":
                    _single_remove_skill(op.old_name.strip(), ctx)
                else:
                    mgr.delete(op.old_name.strip())
                results.append(f"  🗑️ remove → {tgt}: {op.old_name}")
        except Exception as e:
            logger.exception("Batch op failed: %s", op.action)
            return ToolResult(
                data=f"❌ 批量操作在 '{op.action} {op.old_name or op.name}' 时失败: {e}\n"
                     f"已执行: {len(results)} 条",
                is_error=True,
            )

    mgr.reload()
    return ToolResult(
        data=f"✅ 批量操作完成 ({len(results)} 条):\n" + "\n".join(results) +
             f"\n\n当前记忆: {len(mgr.memories)} 条",
    )


def _single_remove(name: str, mgr: MemoryManager) -> ToolResult:
    """删除单条记忆."""
    if not mgr.delete(name.strip()):
        return ToolResult(
            data=f"❌ 未找到名为 '{name}' 的记忆，删除失败。"
                 f"当前共有 {len(mgr.memories)} 条记忆。",
            is_error=True,
        )
    logger.info("Memory removed: %s", name)
    return ToolResult(
        data=f"🗑️ 记忆已删除: {name}\n"
             f"   当前共 {len(mgr.memories)} 条记忆",
    )


def _single_add_skill(
    name: str, description: str, content: str, triggers: list[str], ctx: ToolUseContext,
) -> ToolResult:
    """创建/更新一条 Skill."""
    import os
    from pathlib import Path

    skill_dir = Path(ctx.metadata.get("skill_dir", "skills")) / name.strip()
    skill_dir.mkdir(parents=True, exist_ok=True)

    triggers_yaml = "\n".join(f"  - {t}" for t in triggers) if triggers else ""
    skill_md = f"""---
name: {name.strip()}
description: {description.strip()}
triggers:
{triggers_yaml}
---

{content.strip()}
"""
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    return ToolResult(
        data=f"✅ Skill 已保存: {name.strip()}\n"
             f"   路径: {skill_dir / 'SKILL.md'}\n"
             f"   描述: {description.strip()}\n"
             f"   触发词: {', '.join(triggers) if triggers else '(未设置)'}",
    )


def _single_remove_skill(name: str, ctx: ToolUseContext) -> ToolResult:
    """删除一条 Skill."""
    import shutil
    from pathlib import Path

    skill_dir = Path(ctx.metadata.get("skill_dir", "skills")) / name.strip()
    if not skill_dir.is_dir():
        return ToolResult(
            data=f"❌ 未找到名为 '{name}' 的 Skill，删除失败。",
            is_error=True,
        )
    shutil.rmtree(skill_dir)
    return ToolResult(data=f"🗑️ Skill 已删除: {name.strip()}")


def _single_add(
    name: str,
    description: str,
    content: str,
    mem_type: str,
    mgr: MemoryManager,
) -> ToolResult:
    """添加单条记忆."""
    memory = Memory(
        name=name.strip(),
        description=description.strip(),
        content=content.strip(),
        metadata={"type": mem_type or "project"},
    )

    path = mgr.save(memory)
    logger.info("Memory saved: %s", memory.name)
    return ToolResult(
        data=f"✅ 记忆已保存: {memory.name}\n"
             f"   路径: {path}\n"
             f"   描述: {memory.description}\n"
             f"   当前共 {len(mgr.memories)} 条记忆",
    )


@tool(
    name="recall",
    description=(
        "搜索已保存的项目记忆。当你需要回忆之前记住的偏好、约定或事实时使用。"
    ),
    permission="read",
    input_model=RecallInput,
)
async def recall_tool(args: RecallInput, ctx: ToolUseContext) -> ToolResult:
    """搜索已保存的记忆."""
    if not args.query.strip():
        return ToolResult(
            data="❌ 请提供搜索关键词 (query 参数)。",
            is_error=True,
        )

    memory_dir = ctx.metadata.get("memory_dir", ".kun/memory")
    mgr = MemoryManager(memory_dir=memory_dir)
    mgr.load()

    results = mgr.search(args.query.strip())
    exact_match = any(
        args.query.strip().lower() in f"{m.name} {m.description} {m.content}".lower()
        for m in results
    )

    if not results:
        return ToolResult(
            data=f"🔍 未找到与 '{args.query}' 相关的记忆。"
                 f"当前共有 {len(mgr.memories)} 条记忆。",
        )

    prefix = (
        f"🔍 精确匹配 {len(results)} 条" if exact_match
        else f"🔍 未精确匹配，以下是全部 {len(results)} 条记忆"
    )
    lines = [f"{prefix}:\n"]
    for i, mem in enumerate(results, 1):
        lines.append(f"### {i}. {mem.name}")
        lines.append(f"_{mem.description}_")
        lines.append(f"```\n{mem.content[:500]}\n```")
        lines.append("")

    return ToolResult(data="\n".join(lines))


# ─── Skill 加载工具 ───────────────────────────────────

class SkillLoadInput(BaseModel):
    """skill_load 工具输入参数."""

    name: str = Field(description="Skill 名称, 例如 code-review, python-project")


@tool(
    name="skill_load",
    description=(
        "获取指定 Skill 的完整内容。当你看到'可用 Skill 索引'中有与当前任务相关的 Skill 时，"
        "调用此工具获取全文，然后按 Skill 规范执行。"
    ),
    permission="read",
    input_model=SkillLoadInput,
)
async def skill_load_tool(args: SkillLoadInput, ctx: ToolUseContext) -> ToolResult:
    """加载 Skill 全文."""
    from kun.skills.loader import SkillLoader

    skill_dir = ctx.metadata.get("skill_dir", "skills")
    loader = SkillLoader(skill_dir=skill_dir)
    loader.load()

    content = loader.get_full_text(args.name.strip())
    if content is None:
        names = loader.list_names()
        return ToolResult(
            data=f"❌ 未找到 Skill '{args.name}'。可用 Skill: {', '.join(names) if names else '(无)'}",
            is_error=True,
        )

    return ToolResult(
        data=f"📋 Skill: {args.name}\n\n{content[:4000]}"
    )
