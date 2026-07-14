# DS-Native Harness 项目计划书

> 定位：面向 DeepSeek 模型的专属编码 Agent 基础设施。不做"又一个 Claude Code 复刻"——做 DS 模型特性驱动的 Harness。

---

## 一、为什么做

Claude Code 是好产品，但它的每一层设计——System Prompt 措辞、tool_use 协议、权限判断阈值、压缩激进度——都是为 Claude 的指令遵循模式调优的。换 DSv4 来跑，像用 Mac 的键盘驱动插到 Windows 上——能用，但处处别扭。

DS 要解放 DSv4 的能力，需要一个从模型特性出发设计的 Harness。中文优先、ThinkBlock 原生支持、成本感知调度、过程评测内置——这些不是"差异化 feature"，是专属 Harness 的物理必然。

---

## 二、核心设计原则（和 Claude Code 的根本差异）

| 原则 | Claude Code | 本项目的选择 | 原因 |
|------|------------|-----------|------|
| **模型耦合** | 紧密耦合 Claude 的指令遵循模式 | 适配 DSv4 的显式约束 + ThinkBlock | DSv4 需要更直白的边界定义 |
| **Prompt 语言** | 英文优先 | 中文优先 + 中英灵活切换 | 实测中文 prompt 在 OpenCode 上稳定性差 |
| **评测** | 无内置 | 每次执行完自动出过程报告 | 没有评测的 Harness 是盲的 |
| **成本模型** | 不透明 | Token 预算管理器 + 三层漏斗路由 | DSv4 便宜但不是免费，该省得省 |
| **数据飞轮** | 遥测归 Anthropic | 用户数据回流 → Harness 自优化 | 专属 Harness 的价值在于飞轮能转 |
| **记忆** | 文件级 .memory/*.md | 文件级 + 可选 SQLite FTS5（借鉴 Hermes） | 中文对话的语义检索比英文更需要全文搜索 |

---

## 三、版本规划

> **2026-07-06 修订**: v0.3 评测内置取消（由 FlowForge 完成），v0.2-v0.5 基础能力合并为一个 v0.2。v0.4 新增 TUI + GUI 界面设计。

### v0.1 MVP ✅ — 最小可用的编码 Agent（已完成）

**目标**：能在命令行里接任务、调工具、完成任务。跑通整个 Loop。

```
用户输入: "在 ~/project 目录下找所有 Python 文件，统计每个文件的代码行数"

Agent:
  1. Plan: 需要先 glob 找文件，再逐个 read 统计行数
  2. Act: glob("~/project/*.py") → 拿到文件列表
  3. Observe: 3 个文件，共 450 行
  4. Act: 输出统计结果 → 退出
```

**模块清单**：

| 模块 | 功能 | 技术选型 |
|------|------|---------|
| Agent Loop | while True: LLM → tool_use? → execute → feed back | 自研，不依赖 LangChain/LangGraph |
| LLM 适配层 | DSv4 API 调用 + ThinkBlock 解析 + 流式输出 | Anthropic 兼容接口，复用已有 `llm_utils.py` |
| 工具层 | bash / read_file / write_file / glob（4 个够用） | 装饰器注册（复用 FlowForge 的注册模式） |
| 上下文管理 | 滑动窗口裁剪 + 首条 system 和首条 user 永保留 | 复用 20 题库中的 SlidingWindowManager |
| CLI 交互 | 用户输入 → Agent → 实时输出 thinking + tool_call | 终端富文本（工具调用高亮、ThinkBlock 灰色） |

---

### v0.2 — 基础设施一步到位（2 周）🚧 进行中

**目标**：合并原 v0.2-v0.5，一次性补齐所有基础能力。不让 Kun 停留在"又一个平庸的 Harness"。

| 模块 | 功能 | 状态 |
|------|------|------|
| 错误恢复 | 区分 429/5xx→重试，4xx→报错。指数退避 + jitter | ✅ |
| 权限管道 | Deny list 硬拒绝 + Rule match（workspace 边界）+ Ask user | ✅ |
| 超时控制 | 总超时 300s + 单步超时 60s | ✅ |
| 执行日志 | EventBus → JSON 持久化，完整事件时间线 | ✅ |
| 记忆系统 | 文件级 `.kun/memory/*.md`（YAML frontmatter）+ FTS5 可选 | ✅ |
| 成本感知路由 | 三层漏斗：关键词匹配 → V4-Flash → V4-Pro + Token 预算管理 | ✅ |

### v0.3 — FlowForge 整合（计划中）

**目标**：Kun 成为 FlowForge 的 Harness 内核，替换 OpenCode。中文生态深度整合。

| 模块 | 功能 |
|------|------|
| FlowForge 内核替换 | Kun 作为 Agent 引擎驱动 FlowForge 工作流 |
| Skill 市场 | `skills/` 目录，中文优先的 Skill 生态 |
| 中文 Prompt 模板库 | 面向中文开发场景的预设模板 |
| DS 模型特性挖掘 | ThinkBlock 驱动的工作流、成本自适应调度 |

### v0.4 — TUI + GUI 界面（计划中）

**目标**：把 TUI 界面做好看，并开发 GUI 界面。

| 模块 | 功能 |
|------|------|
| TUI 美化 | prompt_toolkit 全功能 TUI，Rich 主题定制，ThinkBlock 动画效果 |
| GUI 界面 | 桌面 GUI 应用（Electron/PyQt/Web UI 待定） |
| 设计系统 | 统一的设计风格，先调研 Skill 市场有无合适方案，否则自行设计 |

---

## 四、技术架构全景

```
┌─────────────────────────────────────────────────┐
│                  CLI 交互层                       │
│   think 实时展示 | tool_call 高亮 | 结果流式输出    │
├─────────────────────────────────────────────────┤
│                  Harness 内核                     │
│                                                  │
│  before LLM: 记忆加载 → 上下文裁剪 → Prompt 组装   │
│       LLM: DSv4 API (ThinkBlock 解析 + 流式)      │
│  after LLM: 权限管道 → 工具执行 → 结果回传          │
│                                                  │
│  循环不变: while True → LLM → tool? → execute →   │
│            observe → 更新状态 → 继续/退出           │
├─────────────────────────────────────────────────┤
│                  评测层（内置）                     │
│   每次执行: 过程报告 + 指标统计 + 历史对比           │
├─────────────────────────────────────────────────┤
│                  记忆层                           │
│   文件级 .ds-harness/memory/ + FTS5 全文检索      │
├─────────────────────────────────────────────────┤
│                  工具层                           │
│   装饰器注册 | 权限标签 | 角色白名单 | MCP 扩展     │
├─────────────────────────────────────────────────┤
│                  Skill 层                         │
│   skills/ 目录 | 中文优先 | 启动扫描 | 按需加载    │
└─────────────────────────────────────────────────┘
```

---

## 五、技术选型

| 层 | 选型 | 为什么不选替代方案 |
|------|------|-----------------|
| Agent Loop | 自研 while True | LangChain AgentExecutor 黑盒不可控 |
| LLM API | Anthropic 兼容接口 | DSv4 原生支持，无需 adapter |
| 状态管理 | TypedDict + dataclass | LangGraph StateGraph 太重，单 Agent 不需要 |
| 记忆 | 文件级 MD + SQLite FTS5 | Claude Code 已验证的文件级方案 + Hermes 的 FTS5 |
| CLI | Python prompt_toolkit | 轻量、终端富文本支持好 |
| 评测 | 自研 EventBus + JSON 持久化 | 不需要 LangSmith 等外部依赖 |

---

## 六、和现有项目的对照

| 本项目的模块 | 来源 |
|------------|------|
| Agent Loop 骨架 | learn-claude-code s01-s02 的 while 循环 + tool dispatch |
| 工具注册 | FlowForge 的装饰器注册 + 角色白名单 |
| 上下文管理 | 20 题库 SlidingWindowManager |
| 权限管道 | learn-claude-code s03 三层门禁 |
| 错误恢复 | AgenticRAG 的区分错误类型 + 指数退避 |
| 三层漏斗 | ecommerce-kg-chat 的 ScriptRouter → Template → LLM |
| 过程评测 | FlowForge 评估引擎 + GEMMAS 论文 IDS/UPR |
| 记忆系统 | learn-claude-code s09 + Hermes FTS5 |
| ThinkBlock 处理 | InsightAgent 的 llm_utils.py |

---

## 七、面试时的讲法

> "我最近在做一个 DS 专属的编码 Harness。Claude Code 每一层都是为 Claude 调优的——换 DSv4 跑会水土不服。所以我想从 DS 的模型特性出发自己做一套——中文优先、ThinkBlock 原生支持、成本感知路由、最关键是内置了过程评测，每次执行完自动出报告。
>
> 为什么内置评测？因为我在 FlowForge 里验证过一个假设——没有评测的 Harness 是盲的。专属 Harness 的价值不只是今天比 Claude Code 好，而是用了之后数据能回流、模型和 Harness 能一起变好。Claude Code + DSv4 的飞轮被 Anthropic 截断了，我做的这个飞轮是完整的。"

---

## 八、预计里程碑

| 版本 | 预计时间 | 关键交付 |
|------|---------|---------|
| v0.1 MVP | ✅ 完成 | 命令行里能跑通 Coding Agent Loop |
| v0.2 基础 | 2 周 | 错误恢复 + 权限 + 记忆 + 成本路由 |
| v0.3 FlowForge | 待定 | Kun 成为 FlowForge 内核 |
| v0.4 界面 | 待定 | TUI 美化 + GUI 桌面应用 |
