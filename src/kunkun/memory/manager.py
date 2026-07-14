"""记忆系统 — 文件级项目记忆管理.

借鉴:
- Claude Code s09 Memory System — 文件级 .memory/*.md + YAML frontmatter
- Hermes MemoryStore — 双轨存储 + 字符上限 + consolidation

v0.9 新增:
- MEMORY.md (2200 chars) / USER.md (1375 chars) 双轨存储
- 字符上限 + 满时提示整理
- on_pre_compress 压缩前提取
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

# v0.9: 字符上限 (借鉴 Hermes)
MEMORY_CHAR_LIMIT = 2200
USER_CHAR_LIMIT = 1375

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
    target: str = "memory"  # "memory" | "user"

    @property
    def memory_type(self) -> str:
        return self.metadata.get("type", "project")

    @property
    def char_limit(self) -> int:
        return USER_CHAR_LIMIT if self.target == "user" else MEMORY_CHAR_LIMIT

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

        策略:
        1. 如果总记忆数 ≤ n → 全部注入（无需筛选）
        2. 超过 n 条时 → 用字符级 n-gram 匹配排序（兼容中英文）
           - 中文: 2-4 字滑动窗口匹配
           - 英文: 空格分词匹配

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

        # 快捷路径: 记忆不多 → 全部注入
        if len(self._memories) <= n:
            logger.debug("All %d memories injected (≤%d)", len(self._memories), n)
            return list(self._memories)

        # 超出上限 → 按相关性排序取前 n 条
        prompt_lower = prompt.lower()
        scored: list[tuple[Memory, int]] = []

        for mem in self._memories:
            search_text = f"{mem.name} {mem.description}".lower()
            score = self._relevance_score(prompt_lower, search_text)

            # content 中的关键词也加分
            content_lower = mem.content.lower()[:500]
            score += self._relevance_score(prompt_lower, content_lower)

            if score > 0:
                scored.append((mem, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        result = [mem for mem, _ in scored[:n]]

        if result:
            logger.debug("Selected %d/%d memories", len(result), len(self._memories))

        return result

    @staticmethod
    def _relevance_score(query: str, target: str) -> int:
        """计算 query 和 target 的相关性得分.

        兼容中文（字符级 n-gram）和英文（空格分词）.
        """
        score = 0

        # ── 英文路径: 空格分词 ──
        words = [w for w in target.split() if len(w) >= 2]
        for word in words:
            if word in query:
                score += 5  # 完整词命中 → 高权重

        # ── 中文路径: 字符级 n-gram ──
        # 从 target 中提取 2-4 字片段，检测是否在 query 中出现
        if any('一' <= c <= '鿿' for c in target):
            for n in (3, 2):  # 优先 3-gram（更精准）
                for i in range(len(target) - n + 1):
                    gram = target[i:i + n]
                    # 只匹配含中文的片段
                    if any('一' <= c <= '鿿' for c in gram):
                        if gram in query:
                            score += n  # 越长匹配权重越高

        return score

    @staticmethod
    def _content_similarity(text1: str, text2: str) -> float:
        """计算两段文本的内容相似度 (0.0 ~ 1.0).

        使用字符级 bigram Jaccard 相似度，同时兼容中英文。
        - 中文：2-gram 滑动窗口
        - 英文：单词级 bigram
        返回 0.0（完全不同）到 1.0（完全相同）。
        """
        if not text1 or not text2:
            return 0.0

        # 归一化：小写 + 去多余空白
        t1 = text1.lower().strip()
        t2 = text2.lower().strip()

        if t1 == t2:
            return 1.0

        # 用字符 bigram 做统一的 n-gram 提取（兼容中英文）
        def bigrams(s: str) -> set:
            # 先用空白规范化
            chars = s.replace('\n', ' ').replace('\r', ' ')
            # 合并连续空格
            import re
            chars = re.sub(r'\s+', ' ', chars)
            return {chars[i:i+2] for i in range(len(chars) - 1)}

        b1, b2 = bigrams(t1), bigrams(t2)
        if not b1 or not b2:
            return 0.0

        intersection = len(b1 & b2)
        union = len(b1 | b2)
        return intersection / union if union > 0 else 0.0

    def format_for_system_prompt(self, memories: list[Memory]) -> str:
        """将记忆元数据（name + description）注入 System Prompt.

        不注入全文——只注入索引。Agent 看到索引后，自行判断是否需要用
        recall 工具获取全文。语义相关性判断交给 LLM，而非关键词匹配。

        Args:
            memories: 要注入的记忆列表

        Returns:
            格式化的元数据索引
        """
        if not memories:
            return ""

        # v0.9: 双轨展示: USER 优先
        user_mems = [m for m in memories if m.target == "user"]
        proj_mems = [m for m in memories if m.target != "user"]

        lines = []
        if user_mems:
            lines.append("\n## 用户画像")
            lines.append(f"共 {len(user_mems)} 条 (上限 {USER_CHAR_LIMIT} chars)。")
            for mem in user_mems:
                lines.append(f"- **{mem.name}**: {mem.description[:120]}")
            lines.append("")

        if proj_mems:
            lines.append("\n## 项目记忆")
            lines.append(f"共 {len(proj_mems)} 条 (上限 {MEMORY_CHAR_LIMIT} chars)。")
            lines.append("当判断某条记忆与当前任务相关时，用 recall 工具获取全文。")
            for mem in proj_mems:
                lines.append(f"- **{mem.name}**: {mem.description[:120]}")
            lines.append("")

        return "\n".join(lines)

    # ─── 写入 ───────────────────────────────────

    def save(self, memory: Memory, dedup: bool = True) -> tuple[Path, str]:
        """保存记忆到文件, 返回 (路径, 状态信息).

        v0.9: 同名冲突保护 — 检测与已有记忆的同名冲突：
        - 内容高度相似 (>80%) → 跳过，返回 "已存在" 状态
        - 内容不同但同名 → 合并追加（用时间戳分隔线隔开）
        - 无冲突 → 正常保存

        Args:
            memory: 要保存的记忆
            dedup: 是否启用去重检测（默认 True）
        """
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # ── v0.9: 同名冲突检测 ──
        existing = self._index.get(memory.name)
        dedup_status = ""  # 附加状态信息
        if dedup and existing is not None:
            similarity = self._content_similarity(existing.content, memory.content)
            if similarity > 0.8:
                # 内容高度相似 → 跳过
                logger.info(
                    "Skipped duplicate memory '%s' (similarity=%.0f%%)",
                    memory.name, similarity * 100,
                )
                return (
                    self.memory_dir / f"{memory.name}.md",
                    f"⏭️ 记忆 '{memory.name}' 已存在且内容高度相似 ({similarity:.0%})，跳过。",
                )
            else:
                # 内容不同但同名 → 合并追加
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                merged_content = (
                    existing.content.rstrip()
                    + f"\n\n---\n\n## 补充 ({timestamp})\n\n"
                    + memory.content
                )
                memory.content = merged_content
                dedup_status = (
                    f" 🔗 与已有记忆 '{memory.name}' 合并 (相似度 {similarity:.0%})。"
                )

        # 计算同 target 的已有记忆总字符数
        same_target = [m for m in self._memories if m.target == memory.target]
        current_total = sum(len(m.content) for m in same_target)
        limit = memory.char_limit

        filename = self.memory_dir / f"{memory.name}.md"
        filename.write_text(memory.to_md(), encoding="utf-8")

        self._index[memory.name] = memory
        memory.file_path = str(filename)

        status = ""
        if current_total + len(memory.content) > limit:
            pct = min(100, int((current_total + len(memory.content)) / limit * 100))
            status = (
                f"⚠️ {memory.target.upper()} 存储 {pct}% 满 ({current_total + len(memory.content)}/{limit} chars)。"
                f"考虑用 remember 的 operations 批量操作整理冗余条目。"
            )

        logger.info("Saved memory: %s → %s", memory.name, filename)
        self._update_index_file()
        return filename, status + dedup_status

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
        """全文搜索 (字符级 n-gram 匹配, v0.5+: FTS5).

        修复: 中文用 n-gram 匹配替代精确子串匹配，
        "代码风格" 可以匹配到 "编码风格偏好"。

        Args:
            query: 搜索关键词

        Returns:
            匹配的记忆列表（按相关性降序），无匹配时返回全部记忆
        """
        if not self._memories:
            self.load()

        if not query or not query.strip():
            return list(self._memories)

        query_lower = query.lower()
        scored: list[tuple[Memory, int]] = []

        for mem in self._memories:
            text = f"{mem.name} {mem.description} {mem.content}".lower()
            score = self._relevance_score(query_lower, text)
            if score > 0:
                scored.append((mem, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = [mem for mem, _ in scored]

        # 无精确匹配 → 返回全部记忆（用户可能记不清确切关键词）
        if not results:
            logger.debug("No exact match for '%s', returning all %d memories", query, len(self._memories))
            return list(self._memories)

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
    """从对话中提取新记忆（便捷函数）。

    实际提取逻辑在 kunkun.core.background_review.BackgroundReviewer 中：
    - review_turn(): 每轮结束后的后台反思（多轮上下文 + 全文去重）
    - extract_before_compress(): 上下文压缩前的紧急提取（on_pre_compress hook）

    本函数保留作为 API 入口，供外部直接调用。使用方式：
        reviewer = BackgroundReviewer(memory_manager, skill_loader, llm_client)
        await reviewer.review_turn(conversation_snapshot)
    """
    # 返回空列表——实际提取由 BackgroundReviewer 异步完成。
    # 此函数可用于同步场景的内存提取（未来可在此接入同步提取逻辑）。
    return []
