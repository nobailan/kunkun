"""Prompt 粒度编译器 — 根据目标 DS 模型自动适配 System Prompt 粒度.

DSv4 专属创新: 不同 DS 模型有不同的指令遵循特性,
不能用同一套 prompt 糊弄。

- Pro: 推理能力强, 给方向就行, 过度约束反而限制发挥
- Flash: 轻量模型, 需要把边界列清楚
- R1: 擅长拆解复杂问题, 把任务分解给它

借鉴: FlowForge 的中文 prompt 测试经验,
      同一任务不同模型 → 不同 prompt 稳定性差异显著
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class ModelProfile(str, Enum):
    PRO = "pro"          # deepseek-v4-pro
    FLASH = "flash"      # deepseek-v4-flash
    R1 = "r1"            # deepseek-reasoner / deepseek-r1


# ─── Profile 检测 ───────────────────────────────────────


def detect_profile(model_name: str) -> ModelProfile:
    """根据模型名检测 profile."""
    name = model_name.lower()
    if "r1" in name or "reasoner" in name:
        return ModelProfile.R1
    if "flash" in name:
        return ModelProfile.FLASH
    return ModelProfile.PRO


# ─── Prompt 编译器 ───────────────────────────────────────


class PromptCompiler:
    """根据模型 profile 编译不同粒度的 System Prompt.

    Attributes:
        profile: 目标模型 profile
        base_prompt: 基础 System Prompt (会被修改)
    """

    # ─── Pro 版本 — 简洁, 给方向, 不约束 ───
    _PRO_ADDITIONS = """
## 工作方式
你是专家级编码助手。理解任务意图后直接执行，无需反复确认。
遇到不确定的情况，优先尝试而非询问。保持高效。
"""

    # ─── Flash 版本 — 详尽, 列边界, 防跑偏 ───
    _FLASH_ADDITIONS = """
## 工作方式
你是编码助手。请严格遵守以下规则：

### 操作前
1. 先理解当前文件内容再修改，不要猜测
2. 不确定文件是否存在时，先用 glob 搜索
3. 修改前先 read_file 确认当前内容

### 操作中
4. 每次只改一个文件，改完确认无误再改下一个
5. 使用 edit 工具做精确替换，不要用 write_file 覆盖整个文件
6. 替换文本必须包含足够的上下文行来唯一定位

### 操作后
7. 改完代码后自己检查一遍是否正确
8. 如果修改涉及 imports，确认新 import 的模块确实存在

### 禁止
- 不要删除你没有完全理解的代码
- 不要假设文件路径，始终用 glob/grep 确认
- 不要在不确定时继续操作，停下来思考
"""

    # ─── R1 版本 — 步骤化, 适合推理链路 ───
    _R1_ADDITIONS = """
## 工作方式
你是推理型编码助手。处理任务时分步进行：

### 第一步: 理解
- 分析任务需求，明确输入和期望输出
- 识别涉及的代码范围和依赖关系

### 第二步: 规划
- 制定执行计划，列出具体步骤
- 预估每步的风险和回滚方案

### 第三步: 执行
- 严格按计划逐步执行
- 每步完成后验证结果是否符合预期
- 如结果偏离预期，回到第二步重新规划

### 第四步: 验证
- 检查所有修改是否一致
- 确认没有引入副作用
"""

    def __init__(self, model_name: str = "deepseek-v4-pro"):
        self.profile = detect_profile(model_name)

    def compile(self, base_prompt: str) -> str:
        """编译 System Prompt.

        Args:
            base_prompt: 基础 System Prompt

        Returns:
            适配当前 profile 的 System Prompt
        """
        addition = {
            ModelProfile.PRO: self._PRO_ADDITIONS,
            ModelProfile.FLASH: self._FLASH_ADDITIONS,
            ModelProfile.R1: self._R1_ADDITIONS,
        }.get(self.profile, "")

        header = {
            ModelProfile.PRO: "\n## 运行模式: Pro (简洁版)\n",
            ModelProfile.FLASH: "\n## 运行模式: Flash (详尽版)\n",
            ModelProfile.R1: "\n## 运行模式: R1 (步骤版)\n",
        }.get(self.profile, "")
        return base_prompt + header + addition

    def compile_agent_prompt(self, task_prompt: str) -> str:
        """编译 Agent 任务 prompt (用于子 Agent).

        Args:
            task_prompt: 原始任务描述

        Returns:
            适配当前 profile 的任务 prompt
        """
        if self.profile == ModelProfile.FLASH:
            return (
                f"{task_prompt}\n\n"
                f"注意: 请一步步执行, 每步确认后再继续。不要跳过验证步骤。"
            )
        elif self.profile == ModelProfile.R1:
            return (
                f"任务: {task_prompt}\n\n"
                f"请按以下步骤处理:\n"
                f"1. 理解任务需求\n"
                f"2. 制定执行计划\n"
                f"3. 逐步执行\n"
                f"4. 验证结果"
            )
        return task_prompt  # Pro: 不需要额外包装

    def get_tool_usage_hint(self, tool_name: str) -> str:
        """获取工具使用提示 (Flash 专属).

        Flash 模型需要更详细的工具说明, Pro 不需要.
        """
        if self.profile != ModelProfile.FLASH:
            return ""

        hints = {
            "edit": "old_string 必须包含足够的上下文来唯一定位。至少包含前后各 2 行。",
            "grep": "搜索范围尽量小。先用 glob 了解目录结构, 再对特定目录 grep。",
            "bash": "先在脑海验证命令不会造成破坏。不确定时先用 echo/type 测试。",
            "write_file": "非必要不使用。优先用 edit 做增量修改。",
        }
        return hints.get(tool_name, "")


# ─── 全局单例 ──────────────────────────────────────────

_compiler: PromptCompiler | None = None


def get_compiler(model_name: str = "deepseek-v4-pro") -> PromptCompiler:
    """获取全局 PromptCompiler."""
    global _compiler
    if _compiler is None or _compiler.profile != detect_profile(model_name):
        _compiler = PromptCompiler(model_name)
    return _compiler
