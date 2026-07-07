"""成本感知路由 — 三层漏斗 + Token 预算管理.

借鉴:
- ecommerce-kg-chat 三层漏斗: ScriptRouter → Template → LLM
- Claude Code token budget + auto-compaction
- FlowForge 成本评估

设计:
- CostRouter: 关键词匹配 → flash → pro 三层漏斗
- BudgetTracker: 每日/每任务预算控制
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from kunkun.core.state import HarnessConfig

logger = logging.getLogger(__name__)

# ─── 模型层级 ──────────────────────────────────────


class ModelTier(str, Enum):
    RULE = "rule"  # 不需要 LLM，直接执行
    LIGHT = "light"  # deepseek-v4-flash (便宜)
    HEAVY = "heavy"  # deepseek-v4-pro (最强)


# ─── 路由规则 ──────────────────────────────────────

# 简单任务关键词 → Flash 模型
SIMPLE_TASK_KEYWORDS: list[str] = [
    # 文件查看
    "列出", "查看", "读取", "显示", "搜索", "查找",
    "list", "show", "read", "find", "search", "cat",
    "看看", "看看这个", "这是什么",
    # 简单统计
    "统计行数", "多少个", "有几", "count", "lines",
    # 简单问答
    "是什么", "什么是", "怎么用", "how to",
    "解释", "说明", "介绍",
]

# 复杂任务关键词 → Pro 模型
COMPLEX_TASK_KEYWORDS: list[str] = [
    # 代码生成
    "实现", "开发", "创建", "构建", "写一个",
    "implement", "create", "build", "develop", "write",
    # 代码修改
    "重构", "优化", "修复", "改进", "修改",
    "refactor", "optimize", "fix", "improve", "modify",
    # 架构设计
    "设计", "架构", "方案", "规划", "分析",
    "design", "architecture", "plan", "analyze",
    # 多文件操作
    "整个项目", "所有文件", "批量", "迁移",
    "project", "all files", "batch", "migrate",
    # 调试
    "调试", "debug", "排查", "定位",
    "错误", "bug", "error", "问题",
]


def classify_task(prompt: str) -> ModelTier:
    """根据 prompt 关键词分类任务复杂度.

    Args:
        prompt: 用户输入

    Returns:
        推荐的模型层级
    """
    prompt_lower = prompt.lower()

    # 先检查复杂关键词
    complex_score = 0
    for kw in COMPLEX_TASK_KEYWORDS:
        if kw in prompt_lower:
            complex_score += 1

    # 再检查简单关键词
    simple_score = 0
    for kw in SIMPLE_TASK_KEYWORDS:
        if kw in prompt_lower:
            simple_score += 1

    # 如果任务很长 (≥200 字符)，倾向于 Pro
    if len(prompt) >= 200:
        complex_score += 2

    # 决策
    if complex_score >= 2:
        return ModelTier.HEAVY
    elif simple_score >= 2 and complex_score == 0:
        return ModelTier.LIGHT
    elif complex_score > 0:
        return ModelTier.HEAVY

    # 默认: 短任务用 flash (>30 字符)，长任务用 pro
    if len(prompt) <= 30:
        return ModelTier.LIGHT
    return ModelTier.HEAVY


# ─── 预算跟踪 ──────────────────────────────────────


# 模型价格 (USD per 1M tokens)
PRICING = {
    "deepseek-v4-pro": {"input": 0.55, "output": 2.19, "thinking": 1.10},
    "deepseek-v4-flash": {"input": 0.14, "output": 0.55, "thinking": 0.28},
}


@dataclass
class BudgetTracker:
    """Token 预算管理器.

    借鉴 Claude Code budget system:
    - daily_budget: 每日总预算 (USD)
    - task_budget: 每任务预算 (USD)
    - spent_today: 今日已花费 (USD)
    """

    daily_budget: float = 20.0
    task_budget: float = 5.0
    spent_today: float = 0.0
    spent_task: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_thinking_tokens: int = 0

    @classmethod
    def from_config(cls, config: HarnessConfig) -> "BudgetTracker":
        return cls(
            daily_budget=config.daily_budget_usd,
            task_budget=config.max_budget_usd,
        )

    def deduct(self, input_tokens: int, output_tokens: int, model: str, thinking_tokens: int = 0) -> float:
        """扣除 token 消耗.

        Args:
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数
            model: 模型名
            thinking_tokens: thinking token 数

        Returns:
            本次消耗 (USD)
        """
        price = PRICING.get(model, PRICING["deepseek-v4-pro"])
        cost = (
            input_tokens / 1_000_000 * price["input"]
            + output_tokens / 1_000_000 * price["output"]
            + thinking_tokens / 1_000_000 * price["thinking"]
        )

        self.spent_today += cost
        self.spent_task += cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_thinking_tokens += thinking_tokens

        return cost

    def check_daily(self) -> bool:
        """检查是否超过每日预算."""
        return self.spent_today < self.daily_budget

    def check_task(self) -> bool:
        """检查是否超过任务预算."""
        return self.spent_task < self.task_budget

    def can_use_pro(self) -> bool:
        """是否可以继续使用 Pro 模型."""
        # 超过每日预算 80% → 降级
        if self.daily_budget > 0 and self.spent_today > self.daily_budget * 0.8:
            logger.info("Budget >80%% used, downgrading to flash")
            return False
        # 超过任务预算 → 降级
        if self.task_budget > 0 and self.spent_task > self.task_budget:
            logger.info("Task budget exhausted, downgrading to flash")
            return False
        return True

    def reset_task(self) -> None:
        """重置任务级预算."""
        self.spent_task = 0.0

    def reset_daily(self) -> None:
        """重置每日预算."""
        self.spent_today = 0.0

    @property
    def summary(self) -> dict:
        return {
            "daily_budget": self.daily_budget,
            "spent_today": round(self.spent_today, 4),
            "task_budget": self.task_budget,
            "spent_task": round(self.spent_task, 4),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_thinking_tokens": self.total_thinking_tokens,
        }


# ─── 成本路由器 ────────────────────────────────────


@dataclass
class CostRouter:
    """三层漏斗成本路由器.

    借鉴 ecommerce-kg-chat 三层漏斗:
    1. 关键词匹配 → 不需要 LLM (v0.2 保留，后续完善)
    2. 轻模型 (deepseek-v4-flash) → 简单任务
    3. 重模型 (deepseek-v4-pro) → 复杂任务

    Attributes:
        config: Harness 配置
        budget: 预算跟踪器
    """

    config: HarnessConfig
    budget: BudgetTracker = field(default_factory=BudgetTracker)

    def __post_init__(self):
        if not self.budget.daily_budget:
            self.budget = BudgetTracker.from_config(self.config)

    def route(self, prompt: str) -> str:
        """路由到合适的模型.

        Args:
            prompt: 用户输入

        Returns:
            模型名称
        """
        # 检查预算
        if not self.budget.can_use_pro():
            logger.info("Budget constraint → flash")
            return self.config.light_model

        tier = classify_task(prompt)

        if tier == ModelTier.LIGHT:
            logger.debug("Simple task → %s", self.config.light_model)
            return self.config.light_model
        elif tier == ModelTier.HEAVY:
            logger.debug("Complex task → %s", self.config.model)
            return self.config.model
        else:
            # RULE 层: v0.2 保留，后续实现直接执行
            logger.debug("Rule match → fallback to %s", self.config.light_model)
            return self.config.light_model

    def record_usage(self, input_tokens: int, output_tokens: int, model: str, thinking_tokens: int = 0) -> float:
        """记录 token 使用."""
        return self.budget.deduct(input_tokens, output_tokens, model, thinking_tokens)

    @property
    def summary(self) -> dict:
        return self.budget.summary
