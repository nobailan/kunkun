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

# ─── Review Engine ───────────────────────────────────────


class BackgroundReviewer:
    """自动反思引擎.

    借鉴 Hermes background_review:
    - review_memory(): 从对话中提取值得记住的事实
    - review_skills(): 从对话中评估 Skill 是否需要进化
    - 使用轻模型（flash）以降低成本
    """

    def __init__(self, memory_manager, skill_loader, llm_client, skill_usage=None, light_model: str = "deepseek-v4-flash"):
        self.memory = memory_manager
        self.skills = skill_loader
        self.llm = llm_client
        self.skill_usage = skill_usage  # SkillUsageStore
        self.light_model = light_model
        self._task: asyncio.Task | None = None

    async def review_turn(self, user_msg: str, assistant_msg: str) -> dict:
        """反思一轮对话，提取记忆 + 评估 Skill 更新.

        异步执行，不阻塞主对话。

        Args:
            user_msg: 用户输入
            assistant_msg: Agent 回复

        Returns:
            {"memories_saved": N, "skills_updated": N}
        """
        # 合并为一个 prompt：一次 LLM 调用完成两项评估
        prompt = self._build_combined_prompt(user_msg, assistant_msg)

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
            logger.debug("Background review failed (non-fatal): %s", e)
            return {"memories_saved": 0, "skills_updated": 0, "error": str(e)}

    def schedule_review(self, user_msg: str, assistant_msg: str) -> None:
        """调度后台反思任务（fire-and-forget）.

        Args:
            user_msg: 用户输入
            assistant_msg: Agent 回复
        """
        self._task = asyncio.create_task(self.review_turn(user_msg, assistant_msg))

    async def wait_pending(self) -> None:
        """等待 pending 的 review 完成."""
        if self._task and not self._task.done():
            try:
                await self._task
            except Exception:
                pass

    # ─── Internal ────────────────────────────────────

    def _build_combined_prompt(self, user_msg: str, assistant_msg: str) -> str:
        """构建合并的 review prompt."""
        # 加载当前记忆和 Skill 列表作为上下文
        self.memory.load()
        memory_names = [m.name for m in self.memory.memories]

        self.skills.load()
        skill_names = self.skills.list_names()

        # v0.3.1: inject full skill content for accurate evolution
        matched = self.skills.match(user_msg)
        skill_full = ""
        if matched:
            parts = []
            for s in matched:
                parts.append(f"### Skill: {s.name}\nDescription: {s.description}\n\nFull content:\n{s.content[:3000]}")
            skill_full = "\n\n---\n\n".join(parts)

        return f"""You are Kun's review assistant. Analyze this conversation:

## Memory names already saved
{memory_names if memory_names else '(none)'}

## Existing Skills (FULL content for precise comparison)
{skill_full if skill_full else '(none matched)'}
All skill names: {skill_names if skill_names else '(none)'}

## Task 1: Memory
{MEMORY_REVIEW_PROMPT}

## Task 2: Skill — compare conversation against full Skill content above
{SKILL_REVIEW_PROMPT}

## Conversation

User: {user_msg[:2000]}

Agent: {assistant_msg[:3000]}

---
Return JSON only:
{{
  "memories": [...],
  "nothing_worth_saving": false,
  "skill_updates": [...],
  "nothing_to_update": false
}}
"""

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
            logger.debug("Failed to parse review JSON: %s", response[:200])
            return result

        # ── 应用记忆 ──
        memories = data.get("memories", [])
        for mem in memories:
            if not mem.get("name") or not mem.get("content"):
                continue
            try:
                from kun.memory.manager import Memory
                memory = Memory(
                    name=mem["name"],
                    description=mem.get("description", ""),
                    content=mem["content"],
                    metadata={"type": "project", "source": "auto_review"},
                )
                self.memory.save(memory)
                result["memories_saved"] += 1
            except Exception as e:
                logger.debug("Failed to save auto memory: %s", e)

        # ── 应用 Skill 更新 ──
        skill_updates = data.get("skill_updates", [])
        for upd in skill_updates:
            if not upd.get("skill_name") or not upd.get("content"):
                continue
            try:
                self._apply_skill_update(upd)
                result["skills_updated"] += 1
            except Exception as e:
                logger.debug("Failed to apply skill update: %s", e)

        return result

    def _apply_skill_update(self, update: dict) -> None:
        """应用单个 Skill 更新."""
        action = update.get("action", "create")
        skill_name = update["skill_name"]
        description = update.get("description", "")
        triggers = update.get("triggers", [])
        content = update["content"]

        import os
        from pathlib import Path

        skill_dir = Path(self.skills.skill_dir) / skill_name

        if action == "patch" and skill_dir.is_dir():
            # 修补已有 Skill：追加内容
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                existing = skill_md.read_text(encoding="utf-8")
                # 在已有内容后追加新内容，用分隔线隔开
                updated = existing.rstrip() + f"\n\n---\n\n## 自动更新 ({action})\n\n{content}\n"
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
