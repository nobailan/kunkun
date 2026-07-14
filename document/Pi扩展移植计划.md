# Kunkun → Pi 扩展移植计划

> 2026-07-11

---

## 一、Pi 扩展模型

Pi 的扩展方式：
- **Hook**: 注册到生命周期事件（onTurnStart, onTurnEnd, onToolUse 等）
- **Custom Tool**: 注册新工具到 Pi 的工具集
- **Custom Command**: 在 `.pi/scripts/` 下放可执行脚本

一个扩展 = 一个 TypeScript/JavaScript 文件，放在 `.pi/extensions/` 下。

---

## 二、各创新点的移植方案

### 2.1 Memory 系统 → Pi Extension

**方式**: Hook + 文件系统

```
.pi/memory/
├── MEMORY.md
├── python-style.md
└── work-principles.md
```

**实现**：
- `onSessionStart` hook → 扫描 `.pi/memory/*.md` → 提取 name+description → 拼接到 System Prompt
- `onTurnEnd` hook → 检查对话中是否有"记住 X"指令 → 自动写入新 `.md`
- 注册 `recall` 工具 → Agent 可主动搜索记忆

**难度**: ⭐ 低（纯文件操作）
**依赖**: 无外部依赖

### 2.2 Skill 系统 → Pi Extension

**方式**: Hook + 文件系统

```
.pi/skills/
├── code-review/SKILL.md
├── python-project/SKILL.md
└── git-conventions/SKILL.md
```

**实现**：
- `onSessionStart` hook → 扫描 `skills/` → 按 triggers 匹配 System Prompt 注入
- 注册 `skill_load` 工具 → Agent 可获取 Skill 全文

**难度**: ⭐ 低（和 Memory 共用同一套文件读取逻辑）
**依赖**: 无

### 2.3 background_review → Pi Post-Turn Hook

**方式**: `onTurnEnd` hook + flash API 调用

```
onTurnEnd(conversation):
    prompt = build_review_prompt(conversation)
    result = call_flash_api(prompt)
    if result.memories: write_to_memory(result.memories)
    if result.skill_updates: patch_skill(result.skill_updates)
```

**实现**：
- Hook 中收集当前轮的用户输入 + Agent 回复
- 调用 DeepSeek flash API → JSON 解析
- 写入 Memory / Skill 文件

**难度**: ⭐⭐ 中（需要 API 调用 + JSON 解析）
**依赖**: DeepSeek API key

### 2.4 AdaRubric 评测 → Pi Post-Turn Hook

**方式**: `onTurnEnd` hook + flash API

**实现**：
- 同上，但换一个 prompt（任务动态维度生成 + 打分）
- 结果输出到终端（或保存 JSON 供 dashboard 读取）

**难度**: ⭐⭐ 中
**依赖**: DeepSeek API key

### 2.5 ThinkBlock 评测 → Pi Post-Turn Hook

**方式**: `onTurnEnd` hook + reasoning_content 提取

**实现**：
- Pi 使用 DeepSeek API 时可获取 `reasoning_content`
- Hook 收集 thinking 轨迹 → 调 flash 做三维度打分

**难度**: ⭐⭐ 中
**依赖**: DeepSeek API（只有 DSv4 返回 reasoning_content）

### 2.6 评测仪表盘 → Pi Custom Command

**方式**: `pi dashboard` 命令

**实现**：
- 独立的 `pi-dashboard` 脚本
- 读取 `.pi/extensions/kunkun/evaluations.jsonl`
- 生成 HTML（复用 Kunkun 的 HTML 模板，翻译成 TS 字符串）

**难度**: ⭐⭐ 中
**依赖**: 评测数据来自 2.4/2.5 hooks

### 2.7 Workflow → Pi Custom Command

**方式**: `.pi/scripts/workflow.js` 或 Python 脚本

**实现**：
- 复用 Kunkun 的 workflow 引擎（Python）
- Pi 通过 `pi exec workflow.py` 调用
- 或者用 TypeScript 重写四个原语

**难度**: ⭐⭐⭐ 高（跨语言调用）
**依赖**: Python 环境（如果复用 Kunkun 引擎）

### 2.8 Cron → Pi Custom Command

**方式**: `pi cron` 命令

**实现**：
- 独立的 `pi-cron` 脚本
- asyncio 或 cron 库实现调度
- 注册定时任务到系统 crontab

**难度**: ⭐⭐ 中
**依赖**: 无

### 2.9 FTS5 跨会话搜索 → Pi Tool

**方式**: 注册 `session_search` 工具

**实现**：
- TypeScript 版 SQLite + FTS5（better-sqlite3 库）
- 或直接用 JSONL 文件 + 全文搜索

**难度**: ⭐⭐ 中
**依赖**: better-sqlite3 (npm)

---

## 三、推荐分批实现

### 第一批：零依赖（今天可交付）

| 扩展 | 方式 | 文件 |
|------|------|------|
| Memory 系统 | Hook + .md | `memory.ts` |
| Skill 系统 | Hook + .md | `skill.ts` |
| 评测仪表盘 | HTML 生成 | `dashboard.ts` |

这三个**不依赖任何 API**，纯文件操作 + prompt 拼接。

### 第二批：依赖 API（需要 DeepSeek key）

| 扩展 | 方式 | 文件 |
|------|------|------|
| background_review | onTurnEnd hook + flash API | `review.ts` |
| AdaRubric 评测 | onTurnEnd hook + flash API | `eval.ts` |
| ThinkBlock 评测 | onTurnEnd hook + DSv4 | `think_eval.ts` |

### 第三批：跨进程（需要 Kunkun 后端）

| 扩展 | 方式 | 文件 |
|------|------|------|
| Workflow 引擎 | Python 脚本 → Pi exec | `workflow.py` |
| Cron 调度器 | 独立脚本 | `cron.py` |
| FTS5 搜索 | SQLite 工具注册 | `search.ts` |

---

## 四、目录结构

```
.pi/extensions/kunkun/
├── README.md                 # 安装说明
├── memory.ts                 # Memory hook
├── skill.ts                  # Skill hook
├── review.ts                 # background_review hook
├── eval.ts                   # AdaRubric hook
├── dashboard.ts              # 仪表盘生成
├── tools.ts                  # recall + session_search 工具注册
├── prompts/                  # 评测 prompt 模板（从 Kunkun 翻译）
│   ├── memory_review.md
│   ├── skill_review.md
│   └── rubric_eval.md
└── templates/                # HTML 仪表盘模板
    └── dashboard.html
```

---

## 五、对 Pi 社区的价值

| 价值 | 说明 |
|------|------|
| **零到一** | Pi 完全没有 Memory/Skill/评测，Kunkun 填补了这三个空白 |
| **轻量** | 第一批扩展无需 API key，零配置可用 |
| **可插拔** | 每个扩展独立，用户可以选择性安装 |
| **DSv4 优化** | 评测 prompt 经过了 DSv4 实战验证 |

---

## 六、开始做第一批

```
1. memory.ts  — 60 行，读取 .pi/memory/ 注入 System Prompt
2. skill.ts   — 40 行，读取 skills/SKILL.md 按 triggers 匹配
3. dashboard.ts — 150 行，读 evaluations.jsonl 生成 HTML
```

总共 ~250 行 TS 代码 + 3 个 prompt 模板。现在开始写吗？
