# Kun (鲲)

> 小而能化大，终成鹏 — DeepSeek 原生编码 Agent

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 是什么

Kun 是一个从 DeepSeek 模型特性出发设计的专属编码 Agent。不做"又一个 Claude Code 复刻"——做 DS 模型特性驱动的 Harness。

| 特性 | 说明 |
|------|------|
| 🧠 **Thinking 原生** | DSv4 reasoning_content 实时渲染，思考过程可见 |
| 🇨🇳 **中文优先** | System Prompt + 交互全程中文 |
| 🔧 **工具即插即用** | `@tool` 装饰器注册，一分钟写一个新工具 |
| 🛡️ **权限管道** | Deny list 硬拒绝 + workspace 边界 + ask 模式 |
| 💾 **记忆系统** | 项目偏好持久化，跨会话自动加载 |
| 🎯 **Skill 系统** | 领域知识注入，按 prompt 自动匹配激活 |
| 💰 **成本感知** | 三层漏斗路由 (规则→Flash→Pro) + Token 预算 |
| 🪶 **轻量** | 零框架依赖，纯 Python + httpx |

## 安装

```bash
pip install kun
```

## 快速开始

```bash
# 单次执行
kun "找出项目中所有的 Python 文件，统计每个文件的代码行数"

# 交互模式
kun-interactive
```

## 从源码安装

```bash
git clone https://github.com/nobailan/kun.git
cd kun
pip install -e .
```

## 配置

```bash
cp .env.example .env
# 编辑 .env，填入 API Key
```

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `KUN_API_KEY` | DeepSeek API 密钥 | — |
| `KUN_MODEL` | 模型名称 | `deepseek-v4-pro` |
| `KUN_LIGHT_MODEL` | 轻量模型 | `deepseek-v4-flash` |
| `KUN_WORKSPACE` | 工作目录 | `.` |
| `KUN_MAX_TURNS` | 最大轮次 | `50` |

## 版本路线

| 版本 | 状态 | 交付 |
|------|------|------|
| v0.1 MVP | ✅ | Agent Loop + 4 工具 + CLI |
| v0.2 基础设施 | ✅ | 错误恢复 + 权限 + 记忆 + 成本路由 |
| v0.3 Skill 系统 | ✅ | Memory + Skill 自改进闭环，对齐 Hermes |
| v0.4 界面 | 计划中 | TUI 美化 + GUI 桌面应用 |

## 工具 (7 个)

| 工具 | 权限 | 说明 |
|------|------|------|
| `bash` | write | 执行 Shell 命令 (危险命令自动拦截) |
| `read_file` | read | 读取文件 (行号标注) |
| `write_file` | write | 写入/创建文件 |
| `glob` | read | 文件模式匹配搜索 |
| `remember` | write | 记忆/Skill 管理（单条/批量，自动区分事实与约定） |
| `recall` | read | 搜索已保存的记忆 |
| `skill_load` | read | 加载 Skill 全文 |

## Memory + Skill 系统

| | Memory | Skill |
|---|--------|-------|
| 本质 | "是什么"（事实/偏好） | "怎么做"（约定/规范） |
| 写入 | `remember(target="memory")` | `remember(target="skill")` |
| 注入 | Frozen Snapshot（会话启动时全文注入，后续复用） | 同左 |
| 进化 | background_review 每轮自动提取 | background_review 每轮自动修补 |
| 生命周期 | 手动管理 | Curator 自动管理 (active→stale→archived) |

预置 3 个中文 Skill：

| Skill | 触发词 | 说明 |
|-------|--------|------|
| `code-review` | 代码审查/review/检查代码 | 按行业规范审查代码质量 |
| `python-project` | Python/写代码/编码规范 | Python 项目开发规范 |
| `git-conventions` | git/commit/提交/发布 | Conventional Commits 规范 |

自定义 Skill: 在 `skills/` 目录下创建 `SKILL.md`，格式见预置示例。

## 许可证

MIT

---

> 北冥有鱼，其名为鲲。鲲之大，不知其几千里也。化而为鳥，其名为鹏。
