---
name: python-code-style
description: Kun 项目的 Python 代码风格偏好
metadata:
  type: project
---

# Kun 项目 Python 代码风格指南

## 1. 导入规范
- 使用 `from __future__ import annotations` (所有文件第一行)
- 标准库 → 第三方库 → 本地模块，分组排列
- 导入路径使用绝对导入: `from kun.core.xxx import YYY`
- typing 导入: `from typing import AsyncGenerator` 等

## 2. 文档字符串 (docstrings)
- 模块级 docstring: 第一行一句概括，随后 blank line 接详细说明
- 类 docstring: 说明职责，标注借鉴来源
- 函数 docstring: Google 风格 (Args/Returns/Raises/Yields)
- 中文优先，技术术语保留英文
- 借鉴的项目用 `借鉴:` 标注 (如 cc-haha, Hermes)
- 版本用 `v0.1:` / `v0.2:` 标注变化

## 3. 命名风格
- 类: `PascalCase` (如 `AgentLoop`, `ToolRegistry`, `MemoryManager`)
- 函数/方法: `snake_case` (如 `run_once`, `parse_args`, `_init_tools`)
- 变量: `snake_case` (如 `tool_schemas`, `memory_context`)
- 常量: `UPPER_SNAKE_CASE` (如 `MAX_LINES`, `MAX_MEMORIES`)
- 私有方法/属性: `_` 前缀 (如 `_abort`, `_stream_with_retry`)
- Enum 成员: `UPPER_SNAKE_CASE` (如 `IDLE`, `TOOL_USE`)

## 4. 类型注解
- 全面使用类型注解 (函数参数、返回值、属性)
- 使用 `| None` 而非 `Optional[]`
- Pydantic BaseModel 用于工具输入验证
- `dataclass` 优先于手写 `__init__`
- `field(default_factory=...)` 处理可变默认值
- **函数/方法必须标注返回值类型注解**

## 5. 代码组织
- 模块内: 枚举 → 数据类 → 主逻辑类 → 工具函数
- 类内: `__init__` → `public_api` → `_internal_methods`
- 用 `# ─── 分隔线 ──────────────────────────────` 划分代码段
- 一个文件一个主要职责

## 6. 错误处理
- 工具函数返回 `ToolResult(data=..., is_error=True)` 而非抛异常
- 顶层异常捕获用 `logger.exception()`
- 用 `try/finally` 清理资源

## 7. 异步
- `async/await` 优先
- 流式输出用 `AsyncGenerator`
- 使用 `asyncio.Event` 做中断控制

## 8. 注释风格
- 中文注释，技术术语保留英文
- `# v0.2:` 标注版本迭代
- 借鉴代码标注来源: `# 借鉴 cc-haha xxx`
- 用 `# ───` 做视觉分隔

## 9. 字符串
- **使用双引号 `"string"` 而非单引号**
- f-string 优先于 `.format()` 或 `%`
- 长字符串隐式连接 (括号内自动连接)

## 10. 缩进与格式
- **缩进: 4 spaces**
- 行长度: 未强制，但倾向于 100-120 字符
- 文件末尾: 空一行
- `__slots__` 在明确需要性能优化的类中使用
- 环境变量通过 `HarnessConfig.from_env()` 加载