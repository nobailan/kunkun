# Kunkun × Claude Code × Hermes × OpenCode × Pi 全面对比

> 2026-07-10 | v0.9.0 视角

---

## 一、一句话定位

| 系统 | 定位 | 设计哲学 |
|------|------|---------|
| **Claude Code** | Anthropic 官方编码 Agent | 全功能 + 平台化（云端 Routine/Workflow/AgentTeam） |
| **OpenCode** | 开源多模型编码 Agent（17 万 star） | 可插拔 + 分层架构（主Agent/子Agent 角色分离） |
| **Hermes** | Python 多 Provider Agent 平台 | 可插拔记忆 + 自改进闭环 |
| **Pi** | 极简终端 Agent | 最小工具集 + TypeScript 扩展 + 不替你做决定 |
| **Kunkun v0.9** | DSv4 专属中文编码 Agent | Python 原生 + Skill 自进化 + 评测内置 |

---

## 二、技术栈

| | Claude Code | OpenCode | Hermes | Pi | Kunkun |
|---|:---:|:---:|:---:|:---:|:---:|
| 语言 | TypeScript | TypeScript | Python | TypeScript | **Python** |
| 运行时 | Node.js | **Bun** | Python | Node.js | Python |
| 模型支持 | **Anthropic 独家** | **75+** providers | **25+** providers | **15+** providers | **DeepSeek 专属** |
| 默认模型 | Claude Opus 4.5 | 可配置 | 可配置 | Claude Opus 4.5 | DSv4-Pro |
| 接口 | CLI / TUI / SDK | CLI / TUI / **HTTP+SSE** | CLI / TUI / 多平台 gateway | CLI / TUI / RPC / SDK | CLI / TUI / **FlowForge JSON** |

---

## 三、Agent 编排

| | Claude Code | OpenCode | Hermes | Pi | Kunkun |
|---|:---:|:---:|:---:|:---:|:---:|
| 子 Agent | ✅ Agent 工具 | ✅ **主/子 Agent 分层** | ✅ delegation | ❌ 内置无 | ✅ Agent 工具 |
| Agent Team | ✅ experimental | ✅ **插件生态** (Blueprint/Spavn/Matrixx) | ❌ 无 | ❌ 无 | ✅ AgentTeam (4 角色) |
| 角色白名单 | ❌ | ✅ **按任务划分** | ❌ | — | ✅ Explorer/Coder/Reviewer/Planner |
| **Workflow 脚本引擎** | ✅ JS 脚本 | ✅ **DAG 任务调度** | ❌ 无 | ❌ 无 | ✅ **Python 脚本** |
| **Cron 定时调度** | ✅ 云 Routine + 本地 Loop | ❌ 社区 cron-claude | ❌ CLI 薄包装 | ❌ 无 | ✅ **CronScheduler** |
| 通信方式 | async mailbox | named messaging | memory 间接 | — | **AgentMailbox + AgentBus** |
| **并行策略** | pipeline (非屏障默认) | **DAG 依赖图** | 串行/并行工具判断 | — | **pipeline (非屏障默认)** |

---

## 四、工具系统

| | Claude Code | OpenCode | Hermes | Pi | Kunkun |
|---|:---:|:---:|:---:|:---:|:---:|
| 工具数 | **30+** | 15+ | 80+ | **4** (read/write/edit/bash) | **19** |
| 工具注册 | Zod schema | AI SDK tool() | AST 扫描 + 函数注册 | 无注册机制 | **Pydantic + @tool 装饰器** |
| 权限控制 | 三层门禁 | 细粒度 per-agent | 卫语句 + tool_guardrails | **YOLO 模式** | 三层门禁 |
| Web 搜索 | ✅ WebSearch/WebFetch | ✅ | ✅ 多后端 | ❌ | ✅ **Tavily + DDG 双后端** |
| 代码智能 | ✅ LSP | ✅ **LSP + AST-Grep** | ❌ | ❌ | ✅ findsymbol/gotodef/findrefs |
| 大文件处理 | ✅ | ✅ | ✅ | ✅ | ✅ **智能采样 (>2MB)** |

---

## 五、记忆与 Skill

| | Claude Code | OpenCode | Hermes | Pi | Kunkun |
|---|:---:|:---:|:---:|:---:|:---:|
| 记忆存储 | .memory/*.md | SQLite 会话历史 | **MEMORY.md § 分隔** | ❌ 无 | .kun/memory/*.md |
| 检索方式 | **LLM 判断** | SQLite | **FTS5 + trigram** | — | **LLM + n-gram 双重** |
| **Skill 自进化** | ❌ 静态 | ❌ 静态 | ✅ background_review | ❌ | ✅ **background_review** |
| **Frozen Snapshot** | ❌ | ❌ | ✅ | — | ✅ |
| **Curator 生命周期** | ❌ | ❌ | ✅ 完整 | — | ✅ active→stale→archived |
| Provider 插件 | ❌ | ❌ | ✅ MemoryProvider ABC | — | ❌ |

---

## 六、评测与可视化

| | Claude Code | OpenCode | Hermes | Pi | Kunkun |
|---|:---:|:---:|:---:|:---:|:---:|
| **ThinkBlock 过程评测** | ❌ 无 reasoning_content | ❌ | ❌ | ❌ | ✅ **AgentThink 三维度** |
| **AdaRubric 任务评测** | ❌ | ❌ | ❌ | ❌ | ✅ **动态维度 + 打分** |
| **GRPO 多版本择优** | ❌ 太贵 | ❌ | ❌ | ❌ | ✅ **3 路径 + Judge** |
| **HTML 仪表盘** | ❌ | ❌ | ❌ | ❌ | ✅ **单文件仪表盘** |
| FlowForge 集成 | ❌ | ❌ | ❌ | ❌ | ✅ **--flowforge JSON** |

---

## 七、Pi 的启示：Kunkun 可以"更瘦"

Pi 只有 4 个工具、~200 token 的 System Prompt，但 Terminal-Bench 成绩接近 Claude Code。这告诉我们的不是"Kunkun 该删工具"，而是：

| Pi 的正确选择 | Kunkun 可以学的 |
|-------------|---------------|
| System Prompt 极简 | 当前 Prompt 有 ~1500 tokens 的 Skill 内容，可以精简 |
| 不内置子 Agent | 我们的 Agent 工具是"有时能用有时不能"，不是必需品 |
| TypeScript 扩展 | **Python 动态加载也同样可以做**——让用户写 Python 扩展 |
| 模型可切换 | **Kunkun 绑定了 DSv4**，但如果要和其他编码 Agent 对比，多模型支持是加分项 |

## 八、OpenCode 的启示：工程架构

| OpenCode 的正确选择 | Kunkun 可以学的 |
|-------------------|---------------|
| **DAG 任务调度** | Workflow 的 pipeline 是线性的，DAG 可以表达依赖图 |
| **主/子 Agent 分层** | AgentTeam 已有，但角色间通信还不够灵活 |
| **Git Worktree 隔离** | 子 Agent 目前是线程隔离，Worktree 可以做到文件系统隔离 |
| **70+ Provider 可插拔** | Kunkun 的 AgentRuntime ABC 已就绪，但只实现了 KunkunHarness |

## 九、Kunkun 当前状态 vs 各系统最强项

| 维度 | 最强系统 | Kunkun 状态 | 差距 |
|------|---------|-----------|------|
| 工具数量 | Claude Code (30+) | 19 | 🟡 可接受（Pi 4 个照样强） |
| Sub-agent 可靠性 | Claude Code | ⚠️ 有时失败 | 🔴 **需要修** |
| Workflow 引擎 | **Kunkun** (Python) | ✅ | 🟢 **领先** |
| Cron 调度 | Claude Code (云端) | ✅ 本地 | 🟢 差异化 |
| 评测体系 | **Kunkun** | ✅ | 🟢 **独有** |
| 多模型 | OpenCode (75+) | ❌ DSv4 绑定 | 🟡 弱 |
| 记忆进化 | **Kunkun = Hermes** | ✅ | 🟢 |
| System Prompt 效率 | Pi (~200 tokens) | ~1500 tokens | 🟡 可以压缩 |
| 社区生态 | OpenCode (17 万 star) | 1 个开发者 | — |

---

## 十、后续优化方向

| 优先级 | 做什么 | 学谁 |
|--------|--------|------|
| 🔴 | **修复子 Agent 稳定性**（线程隔离 + 错误恢复） | Claude Code 的 mailbox 模式 |
| 🟡 | **精简 System Prompt**（从 1500 tokens 压到 ~800） | Pi 的极简哲学 |
| 🟡 | **Workflow 支持 DAG 依赖图** | OpenCode 的 DAG 调度 |
| 🟡 | **Git Worktree 隔离子 Agent** | OpenCode 的 Worktree 隔离 |
| 🟢 | **多 Provider 支持**（至少加一个 OpenAI 兼容接口） | OpenCode 的 Provider 抽象 |
| 🟢 | **Python 动态扩展**（类似 Pi 的 TypeScript 扩展） | Pi 的扩展机制 |

---

## 十一、总结

| | Claude Code | OpenCode | Hermes | Pi | Kunkun v0.9 |
|---|:---:|:---:|:---:|:---:|:---:|
| 定位 | 全功能平台 | 开源多模型 | Python Agent | 极简终端 | DSv4 专属中文 |
| 复杂度 | 中 | 高 | 高 | **极低** | 中 |
| Agent 编排 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐ | ⭐⭐⭐⭐ |
| 工具系统 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐⭐⭐⭐ |
| 记忆/Skill | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐⭐⭐⭐⭐ |
| 评测可视化 | ⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐⭐ |
| Workflow/Cron | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐ |
| Token 效率 | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| 中文支持 | ⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ |
