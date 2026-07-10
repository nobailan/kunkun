"""上下文管理器 — 按轮次滑动窗口 + 首条永保留.

借鉴:
- cc-haha src/services/compact/autoCompact.ts — auto-compaction 触发逻辑
- 20 题库 SlidingWindowManager — 按 user 切轮次，一轮要么全留要么全不留
- Hermes agent/context_engine.py — ContextEngine ABC + 阈值控制

核心策略 (v0.2 修复版):
  1. 首条 system 永保留 (锚点)
  2. 首条 user 永保留 (锚点)
  3. 非锚点消息按 user 边界切分为"轮次"——一轮 = user + 后续 assistant/tool，直到下个 user
  4. tool 消息不占 window_size 名额，但只保护被选中轮次内的 tool
  5. 以轮次为单位从后往前选取，一轮要么全留要么全不留
  6. 被跳过的连续轮次合并为一条占位消息

修复前的问题:
  - "逐条检查 token 是否超限"的策略在长对话下产生碎片——裁剪后是零散的 tool 结果，
    缺少上下文连贯性
  - tool 消息的保留逻辑是全局的，会保留不在窗口内的旧 tool，导致孤立 tool
  - window_size 被 tool 挤占后，剩余名额往尾部取，可能取到后一轮的片段
"""

from __future__ import annotations

import logging
from pathlib import Path

from kunkun.core.state import HarnessConfig, Message, MessageRole, ContentType

logger = logging.getLogger(__name__)

# ─── System Prompt ──────────────────────────────────────

# 中文 System Prompt (DSv4 优化版)
KUN_SYSTEM_PROMPT = """你是 Kunkun，一个基于 DeepSeek v4 模型的 AI 编码 Agent。

## 你的能力
- 读写文件系统 (read_file, write_file)
- 执行 Shell 命令 (bash)
- 搜索文件 (glob)
- 理解项目结构，提供符合规范的代码修改

## 工作原则
1. **先理解再动手**：修改代码前先阅读相关文件，了解上下文
2. **最小改动**：只改需要改的，不动无关代码。保持代码风格一致
3. **主动验证**：改完代码后运行相关测试或检查确认效果
4. **清晰沟通**：说明你在做什么、为什么这样做

## ThinkBlock 使用
- 在 thinking 块中思考复杂问题 (中英文均可)
- 思考内容不会被计入对话历史
- 工具调用前说明原因和预期结果

## 输出格式
- 使用 Markdown 格式回复
- 代码块使用正确的语言标记
- 文件路径使用反引号标注

## 中文优先
- 始终用中文回复用户
- 代码、命令、文件名、技术术语保持原文

## 记忆与 Skill
- 下文的"项目记忆"和"项目 Skill"是当前会话的完整内容
- 直接阅读并遵守其中的约定和规范，无需额外加载
- 会话中途新增/修改的记忆和 Skill 将在下次会话生效

## 复杂任务处理
遇到多步骤任务时，使用 ThinkBlock 先规划 → TodoWrite 记录步骤 → 逐步执行并更新状态:
1. ThinkBlock: 分析任务，拆解为独立步骤
2. TodoWrite: 创建任务清单
3. 按顺序执行，每完成一步更新状态
如果步骤之间彼此独立（如同时搜索多个目录、同时读取多个文件），用 agent 工具并行执行以提高效率
"""


class ContextManager:
    """上下文管理器 — 按轮次滑动窗口.

    借鉴 cc-haha autoCompact + 20 题库 SlidingWindowManager:
    - trim(): 按轮次裁剪 → 一轮要么全留要么全不留
    - build_system_prompt(): 组装 system prompt
    - estimate_tokens(): token 估算

    Attributes:
        config: Harness 配置
        max_tokens: 上下文窗口上限 (token 数)
        min_tool_keep: 每轮至少保留的 tool 消息数
    """

    # 中文约 2.5 chars/token, 英文约 4 chars/token
    CHARS_PER_TOKEN = 3

    # 保留的最小上下文比例
    MIN_CONTEXT_RATIO = 0.3

    def __init__(self, config: HarnessConfig, min_tool_keep: int = 3):
        self.config = config
        self.max_tokens = config.max_tokens_per_turn
        self.min_tool_keep = min_tool_keep

    # ─── 公共 API ────────────────────────────────

    def trim(self, messages: list[Message]) -> list[Message]:
        """按轮次滑动窗口裁剪消息历史.

        策略 (借鉴 20 题库 SlidingWindowManager 修复版):
        1. 找锚点: 首条 system + 首条 user → 永保留
        2. 非锚点消息按 user 边界切轮次: 一轮 = user + 后续 assistant/tool
        3. 从后往前以轮次为单位分配 token 预算:
           - tool 消息不占 token 名额 (只受 min_tool_keep 限制)
           - 一轮要么整轮保留，要么整轮跳过
        4. 连续跳过的轮次合并为一条占位消息 `[上下文已省略 N 轮对话]`

        这修掉了旧版"逐条检查 token"的三个 bug:
        - bug①: tool 不再被孤立保留 → 选了 tool 等于选了它所在的整轮
        - bug②: token 预算不再被 tool 挤占 → 分配可预测
        - bug③: min_tool_keep 只作用于已选窗口内 → 不会越界拉入无上下文的旧 tool

        Args:
            messages: 完整消息历史

        Returns:
            裁剪后的消息列表
        """
        n = len(messages)
        if n == 0:
            return []

        # ── Step 1: 找锚点 ──
        first_system_idx: int | None = None
        first_user_idx: int | None = None

        for i, msg in enumerate(messages):
            if first_system_idx is None and msg.role == MessageRole.SYSTEM:
                first_system_idx = i
            if first_user_idx is None and msg.role == MessageRole.USER:
                first_user_idx = i
            if first_system_idx is not None and first_user_idx is not None:
                break

        anchors: set[int] = set()
        if first_system_idx is not None:
            anchors.add(first_system_idx)
        if first_user_idx is not None:
            anchors.add(first_user_idx)

        # 快捷路径: 锚点之外的 token 总量已经在预算内 → 无需裁剪
        non_anchor_tokens = sum(
            self._estimate_msg_tokens(m)
            for i, m in enumerate(messages)
            if i not in anchors
        )
        anchor_tokens = sum(
            self._estimate_msg_tokens(messages[i])
            for i in anchors
        )
        if anchor_tokens + non_anchor_tokens <= self.max_tokens:
            return list(messages)

        # ── Step 2: 非锚点消息按 user 边界切轮次 ──
        non_anchor = [i for i in range(n) if i not in anchors]

        turns: list[list[int]] = []  # 每个元素是一个轮次 (索引列表)
        cur: list[int] = []

        for i in non_anchor:
            if messages[i].role == MessageRole.USER and cur:
                turns.append(cur)
                cur = []
            cur.append(i)

        if cur:
            turns.append(cur)

        if not turns:
            return list(messages)  # 没有非锚点消息，直接返回

        # ── Step 3: 从后往前选轮次，直到填满 token 预算 ──
        kept: set[int] = set(anchors)
        budget_used = anchor_tokens

        for turn in reversed(turns):
            # 计算本轮 token: tool 不占名额
            regular = [i for i in turn if messages[i].role != MessageRole.USER or messages[i].role == MessageRole.USER]
            # 重新定义: "普通消息" = user + assistant (不含 tool)
            regular_indices = [
                i for i in turn
                if messages[i].role in (MessageRole.USER, MessageRole.ASSISTANT)
            ]
            regular_tokens = sum(
                self._estimate_msg_tokens(messages[i]) for i in regular_indices
            )
            # tool 消息 token (不占预算，但会被 min_tool_keep 限制)
            tool_indices = [
                i for i in turn
                if _is_tool_result(messages[i])
            ]

            if budget_used + regular_tokens <= self.max_tokens:
                # 整轮放得下
                for i in turn:
                    kept.add(i)
                budget_used += regular_tokens
            else:
                # 放不下整轮 → 从该轮尾部截取不足的 regular 名额
                remaining = self.max_tokens - budget_used
                if remaining > 0 and regular_indices:
                    # 从后往前取 regular 消息直到填满
                    taken = []
                    taken_tokens = 0
                    for i in reversed(regular_indices):
                        t = self._estimate_msg_tokens(messages[i])
                        if taken_tokens + t <= remaining:
                            taken.append(i)
                            taken_tokens += t
                        else:
                            break
                    # 保留截取到的 regular 消息
                    for i in taken:
                        kept.add(i)
                    budget_used += taken_tokens

                    # 保留这些 regular 之后的 tool (在截取范围内)
                    if taken:
                        min_kept = min(taken)
                        for i in tool_indices:
                            if i > min_kept:
                                kept.add(i)
                break  # 预算已满

        # ── Step 4: 在已选窗口内限制 tool 消息数量 ──
        kept_tools = [i for i in kept if _is_tool_result(messages[i])]
        if len(kept_tools) > self.min_tool_keep:
            # 保留最近的 min_tool_keep 条 tool
            tools_sorted = sorted(kept_tools)
            tools_to_remove = tools_sorted[:-self.min_tool_keep]
            for i in tools_to_remove:
                kept.discard(i)

        # ── Step 5: 构建输出 ──
        result: list[Message] = []
        skipped_turns = 0
        in_skip = False

        # 统计轮次边界用于计数
        turn_boundaries: set[int] = set()
        for turn in turns:
            if turn:
                turn_boundaries.add(turn[0])  # 每轮第一条作为边界标记

        for i in range(n):
            if i in kept:
                if in_skip and skipped_turns > 0:
                    result.append(_make_placeholder(skipped_turns))
                    skipped_turns = 0
                    in_skip = False
                result.append(messages[i])
            else:
                if not in_skip:
                    in_skip = True
                # 在轮次边界处计数
                if i in turn_boundaries:
                    skipped_turns += 1

        # 末尾的跳过段
        if in_skip and skipped_turns > 0:
            result.append(_make_placeholder(skipped_turns))

        logger.debug(
            "trim: %d messages → %d messages (%d turns skipped)",
            n, len(result), skipped_turns,
        )
        return result

    def build_system_prompt(self) -> str:
        """组装 System Prompt.

        借鉴 cc-haha fetchSystemPromptParts (src/utils/queryContext.ts):
        - base system prompt
        - context files (CLAUDE.md / .kun/config.yaml)
        - memory context (v0.5+)
        """
        parts: list[str] = [KUN_SYSTEM_PROMPT]

        # 尝试加载项目级上下文文件
        context_files = self._load_context_files()
        if context_files:
            parts.append("\n## 项目上下文\n")
            parts.append(context_files)

        # 环境信息
        parts.append(self._build_environment_hints())

        return "\n".join(parts)

    def estimate_tokens(self, text: str) -> int:
        """估算文本 token 数."""
        return max(1, len(text) // self.CHARS_PER_TOKEN)

    # ─── 内部方法 ────────────────────────────────

    def _total_tokens(self, messages: list[Message]) -> int:
        """计算消息列表的总 token 数."""
        return sum(self._estimate_msg_tokens(m) for m in messages)

    def _estimate_msg_tokens(self, msg: Message) -> int:
        """估算单条消息的 token 数."""
        if isinstance(msg.content, str):
            base = self.estimate_tokens(msg.content)
        elif isinstance(msg.content, list):
            base = sum(self.estimate_tokens(str(c.content)) for c in msg.content)
        else:
            base = 0

        # 消息元数据开销 ~10 tokens
        return base + 10

    def _load_context_files(self) -> str:
        """加载项目上下文文件.

        借鉴 Hermes agent/prompt_builder.py load_context_files():
        - 扫描 AGENTS.md, CLAUDE.md, .hermes.md / HERMES.md
        """
        workspace = Path(self.config.workspace)
        context_files = [
            workspace / "CLAUDE.md",
            workspace / "AGENTS.md",
            workspace / ".kun" / "config.yaml",
        ]

        parts: list[str] = []
        for cf in context_files:
            if cf.is_file():
                try:
                    content = cf.read_text(encoding="utf-8")
                    # 截断过长文件 (借鉴 cc-haha truncateEntrypointContent)
                    if len(content) > 5000:
                        content = content[:5000] + "\n\n> (内容过长，已截断)"
                    parts.append(f"### {cf.name}\n{content}")
                except Exception:
                    logger.debug("Failed to read context file: %s", cf)

        return "\n\n".join(parts)

    def _build_environment_hints(self) -> str:
        """构建环境提示.

        借鉴 Hermes build_environment_hints (agent/prompt_builder.py):
        - 工作目录
        - 平台信息
        - Shell 类型
        """
        import os
        import platform
        import sys

        cwd = Path(self.config.workspace).resolve()
        from datetime import datetime
        lines = [
            "\n## 运行环境\n",
            f"- 当前日期: {datetime.now().strftime('%Y年%m月%d日')}",
            f"- 工作目录: `{cwd}`",
            f"- 操作系统: {platform.system()} {platform.release()}",
            f"- Python 版本: {sys.version.split()[0]}",
            f"- Shell: {os.environ.get('SHELL', os.environ.get('COMSPEC', 'unknown'))}",
        ]
        return "\n".join(lines)


# ─── 辅助函数 ──────────────────────────────────────────


def _is_tool_result(msg: Message) -> bool:
    """判断消息是否为 tool 结果."""
    if isinstance(msg.content, list):
        for block in msg.content:
            if getattr(block, 'type', None) == ContentType.TOOL_RESULT:
                return True
    # 检查 tool_use_id 作为辅助判断
    if msg.tool_use_id:
        return True
    return False


def _make_placeholder(skipped_turns: int) -> Message:
    """生成占位消息."""
    return Message(
        role=MessageRole.SYSTEM,
        content=f"[上下文已省略 {skipped_turns} 轮对话]",
    )
