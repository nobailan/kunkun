"""记忆系统 — 文件级项目记忆管理.

借鉴:
- Claude Code s09 Memory System — 文件级 .memory/*.md + YAML frontmatter
- Claude Code MEMORY.md 索引机制
- Hermes FTS5 全文搜索 (v0.2 保留接口，可选启用)

设计:
- 每个记忆一个 .md 文件，包含 YAML frontmatter
- 使用 name + description 做关键词匹配
- MEMORY.md 作为索引文件
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── 数据模型 ──────────────────────────────────────


@dataclass
class Memory:
    """单条记忆.

    借鉴 Claude Code memory 文件格式:
    ---
    name: <short-kebab-case-slug>
    description: <one-line summary>
    metadata:
      type: user | feedback | project | reference
    ---
    <body>
    """

    name: str
    description: str
    content: str
    metadata: dict = field(default_factory=dict)
    file_path: str = ""

    @property
    def memory_type(self) -> str:
        return self.metadata.get("type", "project")

    @classmethod
    def from_md(cls, path: Path) -> "Memory | None":
        """从 .md 文件解析记忆.

        Args:
            path: .md 文件路径

        Returns:
            Memory 对象，解析失败返回 None
        """
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read memory file %s: %s", path, e)
            return None

        # 解析 YAML frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not fm_match:
            logger.warning("Memory file %s has no frontmatter", path)
            return None

        frontmatter = fm_match.group(1)
        body = fm_match.group(2).strip()

        # 简单 YAML 解析 (name, description, metadata.type)
        parsed: dict = {"metadata": {}}
        in_metadata = False
        for line in frontmatter.split("\n"):
            # Strip leading whitespace but preserve indent for hierarchy
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Detect metadata section
            if stripped == "metadata:" or stripped.startswith("metadata:"):
                in_metadata = True
                continue

            # Simple key: value parsing
            kv_match = re.match(r"^(\w[\w_-]*):\s*(.*)", stripped)
            if kv_match:
                key = kv_match.group(1)
                value = kv_match.group(2).strip().strip('"').strip("'")
                if in_metadata:
                    parsed["metadata"][key] = value
                else:
                    parsed[key] = value

        return cls(
            name=parsed.get("name", path.stem),
            description=parsed.get("description", ""),
            content=body,
            metadata=parsed.get("metadata", {}),
            file_path=str(path),
        )

    def to_md(self) -> str:
        """生成 .md 文件内容."""
        lines = [
            "---",
            f"name: {self.name}",
            f"description: {self.description}",
            "metadata:",
            f"  type: {self.memory_type}",
            "---",
            "",
            self.content,
        ]
        return "\n".join(lines)


# ─── 记忆管理器 ────────────────────────────────────


class MemoryManager:
    """文件级记忆管理器.

    借鉴 Claude Code .memory/*.md + MEMORY.md 索引:
    - load(): 扫描目录加载所有记忆
    - select(): 根据 prompt 关键词选择最相关记忆
    - save(): 写入新记忆文件
    - search(): 全文搜索 (FTS5 可选，v0.2 做关键词匹配)
    """

    # 最大注入记忆数
    MAX_MEMORIES = 5

    def __init__(self, memory_dir: str = ".kun/memory"):
        self.memory_dir = Path(memory_dir)
        self._memories: list[Memory] = []
        self._index: dict[str, Memory] = {}

    @property
    def memories(self) -> list[Memory]:
        return self._memories

    # ─── 加载 ───────────────────────────────────

    def load(self) -> list[Memory]:
        """扫描记忆目录，加载所有 .md 文件.

        Returns:
            加载的记忆列表
        """
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        self._memories = []
        self._index = {}

        for md_file in sorted(self.memory_dir.glob("*.md")):
            # 跳过索引文件
            if md_file.name == "MEMORY.md":
                continue

            memory = Memory.from_md(md_file)
            if memory:
                self._memories.append(memory)
                self._index[memory.name] = memory

        logger.debug("Loaded %d memories from %s", len(self._memories), self.memory_dir)
        return self._memories

    def reload(self) -> list[Memory]:
        """重新加载."""
        self._memories = []
        self._index = {}
        return self.load()

    # ─── 选择 ───────────────────────────────────

    def select(self, prompt: str, n: int = MAX_MEMORIES) -> list[Memory]:
        """根据 prompt 关键词选择最相关的记忆.

        策略 (借鉴 Claude Code memory 加载):
        1. 关键词匹配: name + description 中的词是否在 prompt 中出现
        2. 排序: 按匹配得分降序
        3. 截断: 取前 n 条

        Args:
            prompt: 用户输入的 prompt
            n: 返回的记忆数上限

        Returns:
            按相关性排序的记忆列表
        """
        if not self._memories:
            self.load()

        if not prompt or not self._memories:
            return []

        # 分词 (中文 + 英文)
        prompt_lower = prompt.lower()
        scored: list[tuple[Memory, int]] = []

        for mem in self._memories:
            score = 0
            search_text = f"{mem.name} {mem.description}".lower()

            # 完整词匹配 (高权重)
            for word in search_text.split():
                if word in prompt_lower:
                    score += 3

            # name 匹配 (最高权重)
            name_parts = mem.name.replace("-", " ")
            for part in name_parts.split():
                if part in prompt_lower:
                    score += 5

            # description 中的关键词
            desc_words = mem.description.lower().split()
            for word in desc_words:
                if len(word) >= 2 and word in prompt_lower:
                    score += 1

            if score > 0:
                scored.append((mem, score))

        # 按得分降序
        scored.sort(key=lambda x: x[1], reverse=True)

        result = [mem for mem, _ in scored[:n]]
        if result:
            logger.debug(
                "Selected %d memories for prompt: %s",
                len(result),
                prompt[:80],
            )

        return result

    def format_for_system_prompt(self, memories: list[Memory]) -> str:
        """将选中记忆格式化为 System Prompt 注入文本.

        Args:
            memories: 要注入的记忆列表

        Returns:
            格式化的文本
        """
        if not memories:
            return ""

        lines = ["\n## 项目记忆\n"]
        for i, mem in enumerate(memories, 1):
            lines.append(f"### {i}. {mem.name}")
            lines.append(f"_{mem.description}_")
            lines.append("")
            lines.append(mem.content[:2000])  # 截断长内容
            lines.append("")

        return "\n".join(lines)

    # ─── 写入 ───────────────────────────────────

    def save(self, memory: Memory) -> Path:
        """保存记忆到文件.

        Args:
            memory: 要保存的记忆

        Returns:
            文件路径
        """
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        filename = self.memory_dir / f"{memory.name}.md"
        filename.write_text(memory.to_md(), encoding="utf-8")

        # 更新内存索引
        self._index[memory.name] = memory
        memory.file_path = str(filename)

        logger.info("Saved memory: %s → %s", memory.name, filename)
        self._update_index_file()
        return filename

    def delete(self, name: str) -> bool:
        """删除记忆.

        Args:
            name: 记忆名称 (slug)

        Returns:
            是否成功删除
        """
        self._index.pop(name, None)
        self._memories = [m for m in self._memories if m.name != name]

        path = self.memory_dir / f"{name}.md"
        if path.is_file():
            path.unlink()
            self._update_index_file()
            return True
        return False

    # ─── 搜索 ───────────────────────────────────

    def search(self, query: str) -> list[Memory]:
        """全文搜索 (v0.2: 关键词匹配, v0.5+: FTS5).

        Args:
            query: 搜索关键词

        Returns:
            匹配的记忆列表
        """
        if not self._memories:
            self.load()

        query_lower = query.lower()
        results = []

        for mem in self._memories:
            text = f"{mem.name} {mem.description} {mem.content}".lower()
            if query_lower in text:
                results.append(mem)

        return results

    # ─── 索引文件 ───────────────────────────────

    def _update_index_file(self) -> None:
        """更新 MEMORY.md 索引文件."""
        if not self._memories:
            self.load()

        lines = ["# Kun 项目记忆索引\n"]
        for mem in sorted(self._memories, key=lambda m: m.name):
            lines.append(f"- [{mem.name}]({mem.name}.md) — {mem.description}")

        index_path = self.memory_dir / "MEMORY.md"
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─── 记忆提取 (v0.2 基础版) ─────────────────────────


def extract_memory_slug(text: str) -> str:
    """从文本生成 kebab-case slug."""
    # 简单实现: 取前 50 个字符，转小写，替换空格
    slug = text.lower().strip()[:50]
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


async def extract_memories_from_conversation(
    messages: list,
    memory_manager: MemoryManager,
) -> list[Memory]:
    """从对话中提取新记忆 (v0.2 占位，v0.5+ LLM 提取).

    v0.2 实现: 手动标记 (`/remember` 命令)
    v0.5+ 实现: LLM 自动提取
    """
    # v0.2: 返回空列表，记忆由用户手动管理
    return []
