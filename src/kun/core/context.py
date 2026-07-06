"""上下文管理器 — 滑动窗口 + 首条永保留.

借鉴:
- cc-haha src/services/compact/autoCompact.ts — auto-compaction 触发逻辑
- 20 题库 SlidingWindowManager — 首条 system + 首条 user 永保留
- Hermes agent/context_engine.py — ContextEngine ABC + 阈值控制

v0.1 策略 (简化版):
  - 首条 system prompt 永保留
  - 首条 user 输入永保留
  - 后续消息: 总 token ≤ max_tokens (默认 64K)
  - 裁剪策略: FIFO 删除最旧的中间轮次

v0.5 升级路径:
  - LLM 摘要压缩 (借鉴 Hermes conversation_compression.py)
  - 智能轮次保护 (protect_first_n + protect_last_n)
  - FTS5 检索历史关键信息
"""

from __future__ import annotations

import logging
from pathlib import Path

from kun.core.state import HarnessConfig, Message, MessageRole, ContentType

logger = logging.getLogger(__name__)

# 中文 System Prompt (DSv4 优化版)
DS_SYSTEM_PROMPT = """你是 DS-Harness，一个基于 DeepSeek v4 模型的 AI 编码 Agent。

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
"""


class ContextManager:
    """上下文管理器.

    借鉴 cc-haha autoCompact + Hermes ContextEngine:
    - trim(): 滑动窗口裁剪
    - build_system_prompt(): 组装 system prompt
    - estimate_tokens(): token 估算

    Attributes:
        config: Harness 配置
        max_tokens: 上下文窗口上限 (token 数)
    """

    # 中文约 2.5 chars/token, 英文约 4 chars/token
    CHARS_PER_TOKEN = 3

    # 保留的最小上下文比例 (确保对话连贯性)
    MIN_CONTEXT_RATIO = 0.3

    def __init__(self, config: HarnessConfig):
        self.config = config
        self.max_tokens = config.max_tokens_per_turn

    # ─── 公共 API ────────────────────────────────

    def trim(self, messages: list[Message]) -> list[Message]:
        """滑动窗口裁剪消息历史.

        策略 (借鉴 20 题库 SlidingWindowManager):
        1. 首条 system prompt 永保留
        2. 首条 user 输入永保留
        3. 最近 3 轮对话永保留 (protect_last_n)
        4. 其余中间消息从旧到新删除，直到总 token ≤ max_tokens

        Args:
            messages: 完整消息历史

        Returns:
            裁剪后的消息列表
        """
        if not messages:
            return messages

        # 快捷路径: 消息量小，无需裁剪
        total = self._total_tokens(messages)
        if total <= self.max_tokens:
            return messages

        n = len(messages)

        # 找出首条 system (如果有)
        first_system_idx = -1
        first_user_idx = -1
        for i, msg in enumerate(messages):
            if msg.role == MessageRole.SYSTEM and first_system_idx < 0:
                first_system_idx = i
            if msg.role == MessageRole.USER and first_user_idx < 0:
                first_user_idx = i

        # 保护集: 首条 system + 首条 user + 末尾 4 条
        protected_indices: set[int] = set()
        if first_system_idx >= 0:
            protected_indices.add(first_system_idx)
        if first_user_idx >= 0 and first_user_idx != first_system_idx:
            protected_indices.add(first_user_idx)
        # 保护最后 4 条消息 (约 2 轮对话)
        for i in range(max(0, n - 4), n):
            protected_indices.add(i)

        # 构建结果: 保留 protected 消息，其余按需裁剪
        result: list[Message] = []
        for i, msg in enumerate(messages):
            if i in protected_indices:
                result.append(msg)
            else:
                # 检查当前 result 的 token 是否已超限
                current_tokens = self._total_tokens(result)
                if current_tokens + self._estimate_msg_tokens(msg) <= self.max_tokens:
                    result.append(msg)
                # else: 跳过此消息 (被裁剪)

        return result

    def build_system_prompt(self) -> str:
        """组装 System Prompt.

        借鉴 cc-haha fetchSystemPromptParts (src/utils/queryContext.ts):
        - base system prompt
        - context files (CLAUDE.md / .kun/config.yaml)
        - memory context (v0.5+)
        """
        parts: list[str] = [DS_SYSTEM_PROMPT]

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
        else:
            base = sum(self.estimate_tokens(str(c.content)) for c in msg.content)

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
        lines = [
            "\n## 运行环境\n",
            f"- 工作目录: `{cwd}`",
            f"- 操作系统: {platform.system()} {platform.release()}",
            f"- Python 版本: {sys.version.split()[0]}",
            f"- Shell: {os.environ.get('SHELL', os.environ.get('COMSPEC', 'unknown'))}",
        ]
        return "\n".join(lines)
