# Workflow × Cron 技术对比：Claude Code vs Hermes vs Kunkun

> 2026-07-10 | 技术调研 + Kunkun 方案设计

---

## 一、Claude Code 的 Workflow + Cron 体系

### 1.1 Workflow 工具

Claude Code 的 Workflow 是一个**脚本驱动的 Agent 编排引擎**。不是图形化工作流，是一段 JS 脚本定义 fan-out/fan-in 逻辑：

```javascript
// Workflow 脚本示例
export const meta = { name: 'review-changes', phases: [{title: 'Review'}, {title: 'Verify'}] }

phase('Review')
const findings = await pipeline(   // pipeline = 流水线并行
    DIMENSIONS,
    d => agent(d.prompt, {schema: SCHEMA}),   // 每个维度一个 Agent
    review => parallel(review.map(f =>        // 验证阶段
        () => agent(verifyPrompt(f))
    ))
)
return { confirmed: findings.flat().filter(Boolean) }
```

**核心模式**：

| 原语 | 作用 |
|------|------|
| `agent(prompt, opt)` | 启动子 Agent，可带 schema 返回结构化数据 |
| `parallel(thunks)` | **屏障** — 所有任务并行跑完才继续 |
| `pipeline(items, ...stages)` | **非屏障** — Item A 进 stage 2 时 Item B 还在 stage 1 |
| `phase(title)` | 进度分组标记 |
| `budget` | Token 预算控制 |

**关键设计**：`pipeline` 是默认选项。它比 `parallel` 效率高——不需要等最慢的任务。

### 1.2 Cron/Scheduling 体系

Claude Code 有三层调度：

| 层级 | 工具 | 运行位置 |
|------|------|---------|
| **L1 云 Routine** | RemoteTrigger (cron/API/webhook) | Anthropic 云端 |
| **L2 本地 Loop** | `/loop 5m /code-review` | 本地进程内 |
| **L3 目标驱动** | `/goal "所有测试通过"` | 本地，检查条件→重试 |

**L1 云 Routine 的能力**：
- 触发方式：cron 表达式、HTTP POST、GitHub webhook
- 独立运行在云端，不需要本地开机
- 有权限管控（只能推 `claude/*` 分支）
- 有用量配额（Pro 5 次/天，Team 25 次/天）

**L2 本地 Loop**：
- `ScheduleWakeup` 工具动态调整下次唤醒时间
- 对话缓存保持（5 分钟内 cache 热）
- 社区有 `cron-claude` 等 MCP 工具补充

---

## 二、Hermes 的 Workflow + Cron 体系

Hermes **没有原生的 Workflow 工具和 Cron 系统**。

### 2.1 Hermes 的替代方案

| Claude Code 能力 | Hermes 如何实现 |
|-----------------|---------------|
| Workflow 脚本编排 | 无直接等价物。可通过 `/delegate` 手动串行调用子 Agent |
| Cron 定时任务 | CLI 的 `hermes cron` 命令，但只是外部 crontab 的薄包装 |
| 云端 Routine | 无。Hermes 必须本地运行 |
| 目标驱动循环 | 无。Agent Loop 不支持条件重试循环 |

### 2.2 Hermes 的相关基础设施

虽然 Hermes 没有 workflow/cron，但它有一些可复用的底层：

- **`auxiliary_client.py`** — 辅助模型客户端（用于后台任务）
- **`background_review.py`** — 后台 fork Agent 做反思（异步模式）
- **`daemon_pool.py`** — 守护线程池（用于后台任务）
- **`agent/conversation_compression.py`** — 上下文压缩触发循环

**本质**：Hermes 的设计哲学是"Agent 是对话驱动的"，不是"任务驱动的"。它假设 Agent 始终在和人交互，所以不需要 workflow/cron。

---

## 三、Kunkun 当前状态

| 能力 | 状态 |
|------|------|
| 子 Agent 线程隔离 | ✅ v0.7.1 |
| Agent Team 多角色协作 | ✅ v0.7.1 |
| 异步信箱 + 总线 | ✅ v0.8 |
| 任务拆解（Planner→委派） | ✅ v0.7.1 |
| 评测仪表盘 | ✅ v0.8 |
| **Workflow 脚本引擎** | ❌ |
| **Cron 定时调度** | ❌ |
| **目标驱动循环** | ❌ |
| **云端 Routine** | ❌（长期） |

---

## 四、方案设计：Kunkun Workflow + Cron

### 4.1 设计原则

1. **不做云端 Routine**（v0.8 阶段不需要，本地够用）
2. **Workflow 借鉴 Claude Code 的脚本模式**（不是图形化，不是 YAML）
3. **Cron 用 asyncio 原生实现**（零外部依赖，借鉴 `cron-claude` 的思路）
4. **DSv4 适配**：ThinkBlock 先规划 → Workflow 拆解 → Cron 定时执行 → 评测仪表盘追踪

### 4.2 Workflow 引擎设计

```python
# Kunkun Workflow 脚本 (Python, 不是 JS)
from kunkun.workflow import workflow, agent, parallel, pipeline, phase

@workflow(name="nightly-code-review")
async def nightly():
    phase("扫描变更")
    changed = await agent("grep 最近 24 小时修改的文件", agent_type="explorer")

    phase("审查")
    findings = await pipeline(
        changed,                          # 每个文件启动一个审查 Agent
        lambda f: agent(f"审查 {f}", agent_type="reviewer"),
    )

    phase("汇总")
    report = await agent(f"汇总审查结果: {findings}", agent_type="planner")
    return report
```

**借鉴 Claude Code 的原语**：

| 原语 | 作用 | 和 Claude Code 的区别 |
|------|------|---------------------|
| `agent(prompt, agent_type)` | 启动角色化子 Agent | Kunkun 有角色白名单，CC 没有 |
| `parallel(tasks)` | 屏障并行 | 同 CC |
| `pipeline(items, ...stages)` | 流水线并行 | 同 CC |
| `phase(title)` | 进度标记 | 同 CC |

### 4.3 Cron 引擎设计

```python
# Kunkun Cron: 纯 Python, 零外部依赖
from kunkun.cron import CronScheduler, Task

scheduler = CronScheduler()

@scheduler.task("0 9 * * 1-5", name="daily-standup")
async def standup():
    result = await agent("汇总昨日所有项目的变更")
    # 保存结果到 .kun/reports/

@scheduler.task("*/30 * * * *", name="health-check")
async def health():
    # 监控 Agent 运行状态
    pass

scheduler.start()  # 后台异步事件循环
```

**核心能力**：

| 功能 | 实现 |
|------|------|
| cron 表达式解析 | 纯 Python 实现（5 字段标准 cron） |
| 任务持久化 | `.kun/cron/tasks.json` |
| 失败重试 | 指数退避，最多 3 次 |
| 并发控制 | Semaphore 限制同时运行的 cron 任务 |
| 状态追踪 | 集成评测仪表盘（上次执行时间/成功率/耗时） |
| 跳过策略 | 上次还在运行 → 跳过本次 |

### 4.4 目录结构

```
src/kunkun/
├── workflow/
│   ├── __init__.py
│   ├── engine.py          # Workflow 引擎 (agent/parallel/pipeline/phase)
│   └── registry.py        # Workflow 注册中心
├── cron/
│   ├── __init__.py
│   ├── scheduler.py       # Cron 调度器 (asyncio)
│   └── parser.py          # Cron 表达式解析
```

### 4.5 实现路线

| 阶段 | 内容 | 预计 |
|------|------|------|
| Phase 1 | Cron 调度器（解析 + 执行 + 重试） | 先做 |
| Phase 2 | Workflow 引擎（agent/parallel/pipeline/phase 原语） | 后做 |
| Phase 3 | 仪表盘集成（cron 任务状态可视化） | 收尾 |

---

## 五、总结

| 维度 | Claude Code | Hermes | Kunkun 计划 |
|------|:---:|:---:|:---:|
| Workflow | ✅ JS 脚本引擎 | ❌ 无 | ✅ Python 脚本引擎 |
| Cron | ✅ 云端 + 本地 | ❌ CLI 薄包装 | ✅ asyncio 原生 |
| Agent 角色 | ❌ 无预设 | ❌ 无 | ✅ 4 种角色白名单 |
| 评测集成 | ❌ | ❌ | ✅ 仪表盘追踪 cron 状态 |
| 外部依赖 | ✅ (云端) | ❌ 需外部 crontab | ✅ 零依赖 |

**Kunkun 的核心差异**：不是写 JS/YAML workflow 文件，而是写 Python 脚本。Python 是 Kunkun 的母语——Workflow 脚本可以直接 import Kunkun 的任何模块，没有语言边界。
