# Kunkun × Claude Code × Hermes Agent 编排对比

> 2026-07-10 | v0.8 视角

---

## 一、定位差异

| | Claude Code | Hermes | Kunkun v0.8 |
|---|------------|--------|-----------|
| 子 Agent 定位 | 独立 worker, 完成原子任务 | 独立 worker, 通过 memory provider 协作 | 角色化 Agent, 工具白名单 + 权限隔离 |
| 多 Agent 编排 | Agent Team (experimental) | 无原生 Team, 通过 delegation 实现 | AgentTeam (Planner→Explorer→Reviewer→Coder→Leader) |
| 通信方式 | async mailbox (SDK 内部) | 无直接通信, 通过 memory 间接 | AgentMailbox (1:1) + AgentBus (pub/sub) |
| 任务拆解 | LLM 自主判断 | LLM 自主判断 | Planner 角色显式拆解 → 委派 |

---

## 二、子 Agent 实现

| | Claude Code | Hermes | Kunkun v0.8 |
|---|------------|--------|-----------|
| 隔离方式 | 独立 context window | 独立 session (parent_session_id 链) | 独立线程 + 独立 event loop |
| 工具权限 | 继承父 Agent, 可通过 canUseTool 限制 | 完整工具集, 通过 tool_guardrails 限制 | 按角色白名单 (4 种角色, 每种不同工具集) |
| 结构化结果 | 通过 SDKMessage 返回 | 通过 memory_provider.sync_turn() 持久化 | 通过 AgentMessage("result", data={tokens, turns, cost}) 返回 |
| 评测 | 无 | 无 (评测在 FlowForge 层) | ThinkBlock + AdaRubric 自动评测 |
| 并行能力 | 并发 spawn, 无上限控制 | 串行/并行由 tool_executor 判断 | 线程池控制 (max 8 workers) + Semaphore 限流 |

---

## 三、Agent Team 协作

| | Claude Code | Hermes | Kunkun v0.8 |
|---|------------|--------|-----------|
| Team 创建 | `--experimental-agent-teams` flag | 无原生支持 | `team` 工具 (一键启动) |
| 角色定义 | 无预设角色, LLM 自行分工 | 无 | 4 种预设角色: Planner/Explorer/Coder/Reviewer |
| 任务拆解 | LLM 在 ThinkBlock 中规划 | LLM 自主 | Planner 角色显式拆解 → JSON 计划 |
| 依赖管理 | LLM 自行判断 | LLM 自行 | 显式 `depends_on` 字段, 按批次执行 |
| 结果汇总 | Leader Agent 自动汇总 | 无 | Leader 汇总 (无 Leader 时自动 fallback) |
| 权限隔离 | 无 | 无 (子 Agent 共享 memory) | 按角色白名单 + 独立配置 |
| 消息总线 | async mailbox (单向) | 无 (通过 memory 间接) | AgentBus (多订阅者 + 过滤器, pub/sub) |

---

## 四、通信架构对比

```
Claude Code:
  Worker A ──mailbox──▶ Parent
  Worker B ──mailbox──▶ Parent
  (星型, 父 Agent 是唯一协调者)

Hermes:
  Worker A ──memory.write──▶ Memory Store
  Worker B ──memory.read───▶ Memory Store
  (共享内存, 无直接通信)

Kunkun v0.8:
  Worker A ──mailbox──▶ Parent ◀──mailbox── Worker B
           ──AgentBus.publish("result")──▶ Worker B (subscriber)
  (星型 + 总线, 父 Agent 协调 + Agent 间直接广播)
```

---

## 五、评测集成

| | Claude Code | Hermes | Kunkun v0.8 |
|---|:---:|:---:|:---:|
| 过程评测 (ThinkBlock) | ❌ 无 reasoning_content | ❌ 跨模型无专属解析 | ✅ AgentThink 三维度 |
| 结果评测 (AdaRubric) | ❌ | ❌ | ✅ 动态维度生成 + 打分 |
| 多版本择优 (GRPO) | ❌ 成本太高 | ❌ | ✅ 3 路径并行 → Judge |
| 评测可视化 | ❌ | ❌ | ✅ HTML 仪表盘 (单文件) |
| FlowForge 集成 | ❌ | ❌ | ✅ `--flowforge` JSON 输出 |

---

## 六、DSv4 专属能力

| 能力 | Claude Code | Hermes | Kunkun |
|------|:---:|:---:|:---:|
| ThinkBlock 可见 | ❌ | ❌ | ✅ |
| 过度思考被动检测 | ❌ | ❌ | ✅ |
| Prompt 粒度编译器 | ❌ (只有 Claude) | ❌ | ✅ (Pro/Flash/R1) |
| 低成本并行 (MLA) | ❌ | ❌ | ✅ (可放宽压缩阈值) |

---

## 七、总结

Kunkun 在 Agent 编排上的核心差异：

1. **角色化隔离** — 不是简单的"fork 一个 Agent"，而是给每个角色定义工具白名单和权限级别。Explorer 只能读，Coder 能写，Reviewer 只能分析。
2. **双通道通信** — AgentMailbox (高效 1:1) + AgentBus (灵活 pub/sub)，兼有星型和总线两种模式。
3. **评测内置** — Claude Code 和 Hermes 都没有从 Harness 层面对子 Agent 的执行质量做评测。Kunkun 每次执行自动产出 ThinkBlock + AdaRubric 评分。
4. **显式任务拆解** — 不是依赖 LLM 自觉分工，而是 Planner 显式生成 JSON 计划（含依赖关系），再按批次委派执行。
