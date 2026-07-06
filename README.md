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
| 📊 **评测内置** | 每次执行完自动出过程报告 (v0.3) |
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
git clone https://github.com/<your-username>/kun.git
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
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | — |
| `KUN_MODEL` | 模型名称 | `deepseek-chat` |
| `KUN_WORKSPACE` | 工作目录 | `.` |
| `KUN_MAX_TURNS` | 最大轮次 | `50` |

## 版本路线

| 版本 | 状态 | 交付 |
|------|------|------|
| v0.1 MVP | ✅ | Agent Loop + 4 工具 + CLI |
| v0.2 | 计划中 | 可靠性 + 权限 + 成本路由 + 记忆系统 — 基础设施一步到位 |
| v0.3+ | 计划中 | Kun 差异化特性 — 融合中文生态 + FlowForge 的原创方向 |

> v0.2 之后的路线不追求"又一个 Claude Code"，那只是站在前人肩膀上复制。评测交给 FlowForge，Kun 聚焦 DS 模型特性的深度挖掘。

## 工具

| 工具 | 权限 | 说明 |
|------|------|------|
| `bash` | write | 执行 Shell 命令 |
| `read_file` | read | 读取文件 (行号标注) |
| `write_file` | write | 写入/创建文件 |
| `glob` | read | 文件模式匹配搜索 |

## 许可证

MIT

---

> 北冥有鱼，其名为鲲。鲲之大，不知其几千里也。化而为鸟，其名为鹏。
