# Kunkun 核心创新 × Pi 扩展可行性分析

> 2026-07-10

---

## 一、Kunkun 核心创新

| # | 创新点 | 一句话 | Claude Code 有吗 | Hermes 有吗 | Pi 有吗 |
|---|--------|--------|:---:|:---:|:---:|
| 1 | **Memory + Skill 自进化** | background_review 每轮反思 → 自动写记忆 + 修补 Skill | ❌ | ✅ | ❌ |
| 2 | **ThinkBlock 过程评测** | 利用 DSv4 reasoning_content 做思考质量三维度打分 | ❌ | ❌ | ❌ |
| 3 | **AdaRubric 任务评测** | 动态生成评分维度 + 打分（论文落地） | ❌ | ❌ | ❌ |
| 4 | **GRPO 多版本生成** | 3 路径并行 + LLM-as-Judge 择优 | ❌ | ❌ | ❌ |
| 5 | **AgentTeam 角色化** | Planner→Explorer→Coder→Reviewer, 每种角色有工具白名单 | ✅ experimental | ❌ | ❌ |
| 6 | **Workflow + Cron** | Python 脚本引擎 + asyncio 调度器 | ✅ JS 脚本 | ❌ | ❌ |
| 7 | **评测仪表盘** | HTML 单文件, 思考质量 + 任务评分 + Cron 状态 | ❌ | ❌ | ❌ |

---

## 二、可以通过 Pi 扩展实现的有哪些

Pi 的扩展模型：TypeScript hooks + 用户自定义工具。

### 可以直接移植的（语言无关，纯设计）

| 创新 | 移植方式 | 难度 |
|------|---------|------|
| Memory 文件系统 | Pi 加 `.pi/memory/*.md` + YAML frontmatter + 检索 | ⭐ 低 |
| Skill 文件系统 | Pi 加 `skills/SKILL.md` + triggers 匹配 + System Prompt 注入 | ⭐ 低 |
| AdaRubric 评测 | Pi 加 `--eval` CLI flag, 调 flash 模型做动态打分 | ⭐⭐ 中 |
| 评测仪表盘 | Pi 加 `--dashboard` flag, 生成 HTML | ⭐⭐ 中 |
| Cron 调度器 | Pi 加 `pi cron` 子命令 + 定时任务 | ⭐⭐ 中 |

### 需要 Python 依赖的（跨语言调用）

| 创新 | 移植方式 | 难度 |
|------|---------|------|
| ThinkBlock 评测 | Kunkun 作为后端服务, Pi 通过 HTTP 调 /eval/thinking | ⭐⭐⭐ 高 |
| background_review | Kunkun 作为后端, Pi 每轮结束后 POST | ⭐⭐⭐ 高 |
| GRPO 多版本 | Kunkun 作为后端, Pi 通过 /grpo 端点调用 | ⭐⭐⭐ 高 |
| AgentTeam | 同上, /team 端点 | ⭐⭐⭐ 高 |

---

## 三、推荐方案：Kunkun 作为 Pi 的 "评测后端"

不是把 Kunkun 重写成 TypeScript，而是让 Pi 用户通过一个简单命令接入：

```bash
# Pi 用户只需一行
pi extension add kunkun-eval
```

`kunkun-eval` 做什么：
1. 在 `.pi/extensions/kunkun-eval/` 下放一个 TypeScript hook
2. Hook 在每轮对话结束后，POST 当前对话到 Kunkun 的 `/eval` 端点
3. Kunkun 跑 ThinkBlock + AdaRubric 评测，返回 JSON
4. Pi 在输出中嵌入评测摘要

```
Pi 对话结束
  → POST http://localhost:8765/eval { "prompt": "...", "trajectory": "..." }
  → Kunkun 返回 { "thinking_eval": {...}, "task_eval": {...} }
  → Pi 输出: "📊 思考质量: 2/10 | 任务评分: 2.5/3"
```

### 需要 Kunkun 端做的

1. 加一个 `kunkun serve` 命令，启动 HTTP 服务器
2. 暴露 `/eval` 端点（接受 prompt + trajectory，返回评测）
3. 暴露 `/dashboard` 端点（返回 HTML）

### 需要 Pi 端做的

1. 写一个 TypeScript extension，注册 `onTurnEnd` hook
2. Hook 中 POST 到 Kunkun
3. 格式化输出

---

## 四、这样做对开源的意义

| 贡献 | 价值 |
|------|------|
| **Pi 端** | Pi 用户获得内存评测能力（Pi 完全没有评测） |
| **Kunkun 端** | 作为 Pi 生态的"评测插件"被广泛使用，推广 DSv4 专属能力 |
| **社区** | 展现 Python+TS 跨语言协作模式（不是二选一，是互补） |

---

## 五、实现优先级

| 优先级 | 做什么 | 工作量 |
|--------|--------|--------|
| 🔴 今天 | `kunkun serve` — HTTP 服务器 + `/eval` 端点 | 1-2h |
| 🟡 明天 | Pi TypeScript extension（hook + HTTP 调用） | 1-2h |
| 🟡 明天 | Pi 端嵌入评测结果到对话输出 | 1h |

要不要现在开始做 `kunkun serve`？
