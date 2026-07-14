"""自动反思引擎 — 对话后自动提取记忆 + 进化 Skill.

借鉴 Hermes agent/background_review.py:
- 每轮对话结束后，用轻模型反思"有什么值得记住的？""Skill 需要改进吗？"
- 异步执行，不阻塞主对话
- 直接写入 MemoryManager 和 SkillLoader

核心闭环:
  对话 → review_turn() → 自动写入 memory + 自动修补 skill
       → 下次会话加载改进后的 memory + skill
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ─── Review Prompts ──────────────────────────────────────

MEMORY_REVIEW_PROMPT = """你是 Kun 的记忆反思助手。回顾以下对话片段，判断是否有值得持久化保存的信息。

## 应该记住的
1. 用户透露的偏好、习惯、工作风格
2. 用户对代码风格、工具、流程的明确要求
3. 项目的重要事实（技术栈、架构约定、命名规范）
4. 用户纠正你的方式（"不要这样做"、"以后用 X 替代 Y"）

## 不应该记住的
- 任务进度（每次会话独立）
- 临时调试信息
- 显而易见的事实

## 输出格式
返回 JSON（不要包含其他文字）:
{
  "memories": [
    {"name": "kebab-case-slug", "description": "一句话描述", "content": "详细内容"}
  ],
  "nothing_worth_saving": false
}

如果没有值得保存的，返回: {"memories": [], "nothing_worth_saving": true}
"""

COMPRESS_EXTRACT_PROMPT = """你是 Kun 的记忆压缩助手。以下对话片段即将因上下文窗口裁剪而被永久遗忘。

你的任务：在遗忘之前，从中提取值得持久化保存的关键信息。

## 提取标准（严格）
只提取**跨会话有价值**的信息：
1. 用户的偏好、习惯、工作风格声明
2. 用户对代码风格、工具、流程的明确要求/纠正
3. 项目的重要事实（技术栈、架构约定、命名规范）
4. 用户对你行为方式的反馈（"以后用 X 替代 Y"、"不要做 Z"）

## 不要提取的
- 任务进度、临时调试信息
- 工具调用的技术细节（除非包含用户偏好）
- 已经显而易见的项目事实
- 任何可以在代码仓库中直接获取的信息

## 输出格式
返回 JSON（不要包含其他文字）:
{
  "memories": [
    {"name": "kebab-case-slug", "description": "一句话描述", "content": "详细内容"}
  ],
  "nothing_worth_saving": false
}

如果没有值得保存的，返回: {"memories": [], "nothing_worth_saving": true}
注意：宁可漏过，不可把临时信息当成永久记忆。只提取明确有价值的内容。
"""

SKILL_REVIEW_PROMPT = """你是 Kun 的 Skill 进化助手。回顾以下对话片段，判断现有的 Skill 是否需要改进。

## 应该更新 Skill 的信号
1. 用户纠正了你的工作方式、风格或格式
2. 出现了新的技术模式、调试路径或工具组合技巧
3. 某个加载的 Skill 被证明是错误的、缺少步骤或过时的
4. 用户表达了对你行为方式的偏好（这些应该嵌入 Skill 而不是 Memory）

## Skill 更新策略（优先级从高到低）
1. **修补已有 Skill**: 如果某个现有 Skill 覆盖了这个领域 → 补充/修正它
2. **创建新 Skill**: 如果出现了全新的、可复用的工作模式 → 创建 Skill

## 输出格式
返回 JSON（不要包含其他文字）:
{
  "skill_updates": [
    {
      "skill_name": "已有 Skill 的名字（修补）或新名字（创建）",
      "action": "patch 或 create",
      "description": "一句话描述",
      "triggers": ["触发词1", "触发词2"],
      "content": "Skill 正文（Markdown，包含具体的规则/步骤/模板）"
    }
  ],
  "nothing_to_update": false
}

如果没有需要更新的，返回: {"skill_updates": [], "nothing_to_update": true}
"""

# ─── Helpers ──────────────────────────────────────────


def _extract_headed_section(text: str, heading: str) -> str:
    """从 Markdown 文本中提取指定 heading 下的内容（不含 heading 行）。"""
    import re
    pattern = rf'^##\s+{re.escape(heading)}\s*$'
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        return text.strip()
    line_end = text.index('\n', match.end()) if '\n' in text[match.end():] else len(text)
    body_start = line_end + 1 if line_end < len(text) else len(text)
    next_heading = re.search(r'^##\s+', text[body_start:], re.MULTILINE)
    body_end = body_start + next_heading.start() if next_heading else len(text)
    return text[body_start:body_end].strip()


# ─── Review Engine ───────────────────────────────────────


class BackgroundReviewer:
    """自动反思引擎.

    借鉴 Hermes background_review:
    - review_memory(): 从对话中提取值得记住的事实
    - review_skills(): 从对话中评估 Skill 是否需要进化
    - 使用轻模型（flash）以降低成本
    """

    def __init__(self, memory_manager, skill_loader, llm_client, skill_usage=None, light_model: str = "deepseek-v4-flash", review_interval: int = 5):
        self.memory = memory_manager
        self.skills = skill_loader
        self.llm = llm_client
        self.skill_usage = skill_usage  # SkillUsageStore
        self.light_model = light_model
        self._task: asyncio.Task | None = None
        # v0.9: review 节流 — 每 N 轮触发一次中间 review
        self._review_interval = review_interval
        self._turn_counter = 0

    async def review_turn(self, conversation_snapshot: list[dict]) -> dict:
        """反思多轮对话，提取记忆 + 评估 Skill 更新.

        异步执行，不阻塞主对话。

        v0.9 P0: 改造为多轮上下文输入，而非仅最后一轮。
        - 传入最近 N 轮完整对话历史
        - 注入已有记忆全文（而非仅名称列表）用于去重
        - 注入匹配的 Skill 全文用于精确对比

        Args:
            conversation_snapshot: 最近 N 轮对话 [{role, content}, ...]

        Returns:
            {"memories_saved": N, "skills_updated": N}
        """
        if not conversation_snapshot:
            return {"memories_saved": 0, "skills_updated": 0}

        # 合并为一个 prompt：一次 LLM 调用完成两项评估
        prompt = self._build_combined_prompt(conversation_snapshot)

        try:
            response = await self._call_light_model(prompt)
            result = self._parse_and_apply(response)
            if result["memories_saved"] > 0 or result["skills_updated"] > 0:
                logger.info(
                    "Background review: %d memories, %d skills updated",
                    result["memories_saved"], result["skills_updated"],
                )
            return result
        except Exception as e:
            logger.info("Background review failed (non-fatal): %s", e)
            return {"memories_saved": 0, "skills_updated": 0, "error": str(e)}

    def schedule_review(self, conversation_snapshot: list[dict], force: bool = False) -> bool:
        """调度后台反思任务（fire-and-forget），支持节流。

        v0.9: 节流机制 — 并非每轮都触发 review：
        - force=True（会话结束 / 警告退出）→ 总是触发
        - force=False（中间检查点）→ 每 review_interval 轮触发一次
        - 返回 True 表示已调度，False 表示被节流跳过

        Args:
            conversation_snapshot: 最近 N 轮对话 [{role, content}, ...]
            force: 是否强制触发（忽略节流计数）

        Returns:
            是否已调度 review 任务
        """
        self._turn_counter += 1
        if not force and self._turn_counter % self._review_interval != 0:
            return False

        self._task = asyncio.create_task(self.review_turn(conversation_snapshot))
        return True

    async def wait_pending(self) -> None:
        """等待 pending 的 review 完成."""
        if self._task and not self._task.done():
            try:
                await self._task
            except Exception:
                pass

    # ─── P0: on_pre_compress — 压缩前记忆提取 ──────────

    async def extract_before_compress(self, skipped_messages: list) -> int:
        """在上下文压缩前，从即将丢弃的消息中提取关键记忆。

        借鉴 Hermes MemoryProvider.on_pre_compress():
        - 消息即将因窗口裁剪被永久遗忘
        - 在丢弃前用一个轻量 prompt 提取值得持久化的信息
        - 直接写入 MemoryManager，下次会话加载时生效

        Args:
            skipped_messages: 即将被丢弃的消息列表（dict 或 Message 对象）

        Returns:
            成功提取的记忆条数
        """
        if not skipped_messages:
            return 0

        # 构建"即将遗忘的对话摘要"
        conversation_text = self._format_skipped_messages(skipped_messages)
        if not conversation_text.strip():
            return 0

        prompt = f"""{COMPRESS_EXTRACT_PROMPT}

## 已有记忆（避免重复提取）
{self._existing_memory_summary()}

## 即将被遗忘的对话片段

{conversation_text}

---
返回 JSON only:
{{"memories": [...], "nothing_worth_saving": false}}
"""

        try:
            response = await self._call_light_model(prompt)
            result = self._parse_memory_only(response)
            if result["memories_saved"] > 0:
                logger.info(
                    "on_pre_compress: extracted %d memories from %d skipped messages",
                    result["memories_saved"], len(skipped_messages),
                )
            return result["memories_saved"]
        except Exception as e:
            logger.info("on_pre_compress extraction failed (non-fatal): %s", e)
            return 0

    def _existing_memory_summary(self) -> str:
        """生成已有记忆的摘要（名称+描述），用于去重判断。"""
        try:
            self.memory.load()
            if not self.memory.memories:
                return "(无已有记忆)"
            lines = []
            for m in self.memory.memories:
                lines.append(f"- {m.name}: {m.description[:100]}")
            return "\n".join(lines)
        except Exception:
            return "(无法加载已有记忆)"

    @staticmethod
    def _format_skipped_messages(messages: list) -> str:
        """将即将丢弃的消息格式化为可读文本。

        支持 dict（{role, content}）和 Message 对象两种格式。
        """
        lines = []
        for i, msg in enumerate(messages):
            if isinstance(msg, dict):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
            else:
                role = getattr(msg, "role", "unknown")
                if hasattr(role, "value"):
                    role = role.value
                content = str(getattr(msg, "content", ""))
                # 截断过长内容
                if len(content) > 1500:
                    content = content[:1500] + "…"

            if role.lower() == "system":
                continue  # 跳过系统消息
            role_label = {"user": "👤 用户", "assistant": "🤖 Agent", "tool": "🔧 工具结果"}.get(
                role.lower() if isinstance(role, str) else str(role), f"📝 {role}"
            )
            lines.append(f"[{i+1}] {role_label}:\n{content[:2000]}\n")

        return "\n".join(lines)

    def _parse_memory_only(self, response: str) -> dict:
        """解析 LLM 返回的 JSON，只处理记忆部分（不处理 Skill）。

        用于 on_pre_compress 场景——压缩前只提取记忆，
        不评估 Skill 更新（Skill 更新需要完整对话上下文）。
        """
        result = {"memories_saved": 0}

        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0]

        try:
            data = json.loads(json_str.strip())
        except json.JSONDecodeError:
            logger.info("Failed to parse compress-extract JSON: %s", response[:200])
            return result

        memories = data.get("memories", [])
        for mem in memories:
            if not mem.get("name") or not mem.get("content"):
                continue
            try:
                from kunkun.memory.manager import Memory
                memory = Memory(
                    name=mem["name"],
                    description=mem.get("description", ""),
                    content=mem["content"],
                    metadata={"type": "project", "source": "on_pre_compress"},
                )
                self.memory.save(memory)
                result["memories_saved"] += 1
            except Exception as e:
                logger.info("Failed to save compress-extracted memory: %s", e)

        return result

    # ─── Internal ────────────────────────────────────

    def _build_combined_prompt(self, conversation_snapshot: list[dict]) -> str:
        """构建合并的 review prompt（v0.9 P0: 多轮上下文 + 全文去重）。

        改进点（vs 旧版）：
        1. 传入最近 N 轮完整对话，而非仅最后一轮
        2. 注入已有记忆全文（而非仅名称列表），让轻模型判断是否重复
        3. 注入匹配的 Skill 全文用于精确对比
        """
        # 加载已有记忆全文用于去重
        self.memory.load()
        existing_memories_text = self._format_existing_memories_full()

        # 加载匹配的 Skill 全文
        self.skills.load()
        skill_names = self.skills.list_names()

        # 从对话快照中提取最后一条 user 消息用于 Skill 匹配
        last_user_msg = ""
        for msg in reversed(conversation_snapshot):
            if isinstance(msg, dict) and msg.get("role") == "user":
                last_user_msg = str(msg.get("content", ""))[:500]
                break

        matched = self.skills.match(last_user_msg) if last_user_msg else []
        skill_full = ""
        if matched:
            parts = []
            for s in matched:
                parts.append(
                    f"### Skill: {s.name}\n"
                    f"Description: {s.description}\n\n"
                    f"Full content:\n{s.content[:3000]}"
                )
            skill_full = "\n\n---\n\n".join(parts)

        # 格式化对话历史
        conversation_text = self._format_conversation_snapshot(conversation_snapshot)

        return f"""You are Kun's review assistant. Analyze this conversation:

## Existing Memories (FULL content — check for duplicates before adding)
{existing_memories_text}

## Existing Skills (FULL content for precise comparison)
{skill_full if skill_full else '(none matched)'}
All skill names: {skill_names if skill_names else '(none)'}

## Task 1: Memory
{MEMORY_REVIEW_PROMPT}

**IMPORTANT**: Before adding a new memory, compare it against the EXISTING MEMORIES above.
- If the same fact already exists → do NOT add a duplicate
- If an existing memory is partially outdated → output a "replace" suggestion instead
- Only add genuinely NEW information

## Task 2: Skill — compare conversation against full Skill content above
{SKILL_REVIEW_PROMPT}

## Conversation (recent turns, oldest first)

{conversation_text}

---
Return JSON only:
{{
  "memories": [...],
  "nothing_worth_saving": false,
  "skill_updates": [...],
  "nothing_to_update": false
}}
"""

    def _format_existing_memories_full(self) -> str:
        """格式化已有记忆全文，用于去重判断。"""
        if not self.memory.memories:
            return "(无已有记忆)"
        lines = []
        for i, m in enumerate(self.memory.memories, 1):
            lines.append(f"{i}. **{m.name}** — {m.description}")
            lines.append(f"   Content: {m.content[:500]}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_conversation_snapshot(snapshot: list[dict]) -> str:
        """格式化多轮对话快照为可读文本。

        策略：
        - 最多保留最近 10 轮（约 20 条消息）
        - 每条消息截断到 2000 字符
        - user 和 assistant 角色用图标区分
        """
        # 取最近的消息（最多 20 条，约 10 轮）
        recent = snapshot[-20:] if len(snapshot) > 20 else snapshot

        lines = []
        for i, msg in enumerate(recent):
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "unknown")).lower()
            content = str(msg.get("content", ""))

            # 跳过 system 消息
            if role == "system":
                continue
            # 跳过 tool 结果（太长且通常是技术细节）
            if role == "tool":
                if len(content) > 300:
                    content = content[:300] + "…[tool result truncated]"
                lines.append(f"[{i+1}] 🔧 Tool: {content}")
                continue

            # 截断过长内容
            max_len = 2000
            if len(content) > max_len:
                content = content[:max_len] + f"…[truncated, {len(content)} chars total]"

            role_label = {"user": "👤 User", "assistant": "🤖 Agent"}.get(role, f"📝 {role}")
            lines.append(f"[{i+1}] {role_label}:\n{content}\n")

        return "\n".join(lines)

    async def _call_light_model(self, prompt: str) -> str:
        """调用轻模型获取 review 结果."""
        import httpx

        api_key = self.llm.config.api_key
        base_url = self.llm.config.base_url

        if not api_key:
            raise RuntimeError("No API key configured")

        payload = {
            "model": self.light_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "temperature": 0.3,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )

            if response.status_code != 200:
                body = await response.aread()
                raise RuntimeError(f"Review API error {response.status_code}: {body.decode()[:500]}")

            data = response.json()
            return data["choices"][0]["message"]["content"]

    def _parse_and_apply(self, response: str) -> dict:
        """解析 LLM 返回的 JSON 并应用变更."""
        result = {"memories_saved": 0, "skills_updated": 0}

        # 提取 JSON（可能被 markdown 代码块包裹）
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0]

        try:
            data = json.loads(json_str.strip())
        except json.JSONDecodeError:
            logger.info("Failed to parse review JSON: %s", response[:200])
            return result

        # ── 应用记忆 ──
        memories = data.get("memories", [])
        for mem in memories:
            if not mem.get("name") or not mem.get("content"):
                continue
            try:
                from kunkun.memory.manager import Memory
                memory = Memory(
                    name=mem["name"],
                    description=mem.get("description", ""),
                    content=mem["content"],
                    metadata={"type": "project", "source": "auto_review"},
                )
                self.memory.save(memory)
                result["memories_saved"] += 1
            except Exception as e:
                logger.info("Failed to save auto memory: %s", e)

        # ── 应用 Skill 更新 ──
        skill_updates = data.get("skill_updates", [])
        for upd in skill_updates:
            if not upd.get("skill_name") or not upd.get("content"):
                continue
            try:
                self._apply_skill_update(upd)
                result["skills_updated"] += 1
            except Exception as e:
                logger.info("Failed to apply skill update: %s", e)

        return result

    def _apply_skill_update(self, update: dict) -> None:
        """应用单个 Skill 更新.

        v0.9: 章节级 upsert — 取代旧版的盲目追加。
        - patch: 按 section heading 去重 → 有则替换，无则追加
        - create: 创建新 Skill 目录和 SKILL.md
        """
        action = update.get("action", "create")
        skill_name = update["skill_name"]
        description = update.get("description", "")
        triggers = update.get("triggers", [])
        content = update["content"]

        from pathlib import Path

        skill_dir = Path(self.skills.skill_dir) / skill_name

        if action == "patch" and skill_dir.is_dir():
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                existing = skill_md.read_text(encoding="utf-8")
                updated = self._upsert_skill_section(existing, content, action)
                skill_md.write_text(updated, encoding="utf-8")
        else:
            # 创建新 Skill
            skill_dir.mkdir(parents=True, exist_ok=True)
            triggers_yaml = "\n".join(f"  - {t}" for t in triggers)
            skill_md_content = f"""---
name: {skill_name}
description: {description}
triggers:
{triggers_yaml}
---

{content}
"""
            (skill_dir / "SKILL.md").write_text(skill_md_content, encoding="utf-8")

        # 记录使用量
        if self.skill_usage:
            if action == "patch":
                self.skill_usage.bump_patch(skill_name)
            elif action == "create":
                self.skill_usage.mark_created(skill_name)
                self.skill_usage.bump_patch(skill_name)

        # 重新加载 Skill
        self.skills.reload()

    @staticmethod
    def _upsert_skill_section(existing: str, new_content: str, action: str) -> str:
        """按章节 heading 做 upsert：找到同名 heading 则替换，否则追加。

        策略：
        1. 从 new_content 中提取 ## 级别的 heading
        2. 在 existing 中查找同名 heading
        3. 找到 → 替换该 section 内容（从 heading 到下一个同级 heading 或 EOF）
        4. 没找到 → 追加到文件末尾

        这避免了旧版"每次 patch 追加一个 ## 自动更新"导致的 Skill 腐化问题。
        """
        import re

        # 提取 new_content 中的 ## heading
        new_headings = re.findall(r'^##\s+(.+)$', new_content, re.MULTILINE)
        if not new_headings:
            # 无 heading → 追加到末尾（保持向后兼容）
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            return existing.rstrip() + f"\n\n---\n\n## 补充 ({ts})\n\n{new_content}\n"

        updated = existing
        for heading in new_headings:
            # 在 existing 中查找同名 heading（从行首开始）
            heading_pattern = re.compile(
                rf'^##\s+{re.escape(heading)}\s*$', re.MULTILINE
            )
            match = heading_pattern.search(updated)

            if match:
                # 找到 → 确定 section 范围（从该 heading 到下一个 ## 或 EOF）
                start = match.start()
                # 该 heading 行尾
                line_end = updated.index('\n', match.end()) if '\n' in updated[match.end():] else len(updated)
                section_start = line_end + 1 if line_end < len(updated) else len(updated)

                # 找下一个 ## heading
                next_heading = re.search(
                    r'^##\s+', updated[section_start:], re.MULTILINE
                )
                if next_heading:
                    section_end = section_start + next_heading.start()
                else:
                    section_end = len(updated)

                # 提取对应的 new section 内容（不含 heading 行）
                # 从 new_content 中切出该 heading 对应的内容
                new_heading_match = re.search(
                    rf'^##\s+{re.escape(heading)}\s*$', new_content, re.MULTILINE
                )
                if new_heading_match:
                    new_start = new_heading_match.start()
                    new_line_end = new_content.index('\n', new_heading_match.end()) if '\n' in new_content[new_heading_match.end():] else len(new_content)
                    new_body_start = new_line_end + 1 if new_line_end < len(new_content) else len(new_content)

                    next_new_heading = re.search(
                        r'^##\s+', new_content[new_body_start:], re.MULTILINE
                    )
                    if next_new_heading:
                        new_body_end = new_body_start + next_new_heading.start()
                    else:
                        new_body_end = len(new_content)
                    new_section_body = new_content[new_body_start:new_body_end].strip()
                else:
                    new_section_body = new_content.strip()

                # 替换 section 内容，保留 heading 行
                heading_line = updated[start:section_start]
                updated = (
                    updated[:start]
                    + heading_line.rstrip() + "\n\n"
                    + new_section_body + "\n"
                    + (updated[section_end:] if section_end < len(updated) else "")
                )
            else:
                # 没找到同名 heading → 追加到末尾
                from datetime import datetime
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                # 只追加 new_content 中该 heading 所在的部分
                section_text = _extract_headed_section(new_content, heading)
                updated = updated.rstrip() + f"\n\n---\n\n## {heading} ({ts})\n\n{section_text}\n"

        return updated
