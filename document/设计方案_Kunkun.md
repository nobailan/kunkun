# Kunkun 设计方案

> 基于 Claude Code、OpenCode、Hermes Agent 三大源码的深度分析
> DeepSeek v4 原生编码 Agent 基础设施
> 2026-07-07 | v0.3.2 更新

---

## 一、项目定位

Kunkun 是一个从 DeepSeek v4 模型特性出发设计的编码 Agent。不做"又一个 Claude Code 复刻"——利用 DSv4 的 ThinkBlock 可见、长上下文低成本、中文原生三个特性，构建差异化的 Harness。

| 维度 | Kunkun 的选择 | 原因 |
|------|-----------|------|
| 语言 | Python | 生态丰富，DS API Python SDK 成熟 |
| Agent Loop | while + AsyncGenerator | 借鉴 Claude Code，简洁可控 |
| 模型 | DSv4 Anthropic 兼容接口 | /v1/chat/completions，reasoning_content 解析 |
| 工具注册 | @tool 装饰器 + Pydantic | 借鉴 FlowForge 注册模式 |
| 记忆系统 | 文件级 .kun/memory/*.md + Frozen Snapshot | 借鉴 Claude Code 格式 + Hermes 生命周期 |
| Skill 系统 | skills/ 目录 + 自改进闭环 | 借鉴 Hermes background_review + curator |
| CLI | prompt_toolkit / Rich | 轻量终端渲染 |

---

## 二、当前架构 (v0.3.2)

### 2.1 模块全景

```
src/kunkun/
├── main.py                     # CLI 入口
├── cli/
│   └── tui.py                  # Rich 终端渲染
├── core/
│   ├── agent_loop.py           # Agent 主循环 (while True + Frozen Snapshot)
│   ├── llm_client.py           # DSv4 OpenAI 兼容 API + ThinkBlock 解析
│   ├── state.py                # AgentState + HarnessConfig
│   ├── events.py               # Event 类型 + EventBus
│   ├── context.py              # 按轮次滑动窗口 + System Prompt 组装
│   ├── error_recovery.py       # 错误分类 + 指数退避重试
│   ├── permission.py           # 权限管道 (Deny list + workspace + ask)
│   ├── execution_log.py        # 事件日志 JSON 持久化
│   └── background_review.py    # 每轮自动反思 (记忆提取 + Skill 进化)
├── tools/
│   ├── decorators.py           # @tool 装饰器 + ToolRegistry
│   ├── registry.py             # 全局注册中心单例
│   ├── bash_tool.py            # Shell 执行
│   ├── read_file.py            # 文件读取
│   ├── write_file.py           # 文件写入
│   ├── glob_tool.py            # 文件搜索
│   └── remember_tool.py        # 记忆/Skill 管理 + recall + skill_load
├── memory/
│   └── manager.py              # 文件级记忆管理 + n-gram 搜索
├── routing/
│   └── cost_router.py          # 三层漏斗路由 + BudgetTracker
└── skills/
    ├── loader.py               # Skill 扫描 + 匹配 + 注入
    ├── usage.py                # Skill 使用量追踪 (.usage.json)
    └── curator.py              # Skill 生命周期管理 (active→stale→archived)
```

### 2.2 Agent Loop 数据流 (v0.3.2)

```
会话首次 run():
  ├── 加载全部 Memory 全文 → 注入 System Prompt
  ├── 加载全部 Skill 全文 → 注入 System Prompt
  └── 冻结 (_frozen_prompt) → 后续轮次复用

每轮:
  用户输入 → [Frozen System Prompt] → [DSv4 API Stream]
    → ThinkBlock 渲染 → text 输出
    → tool_use? → 权限检查 → 执行 → 结果回传
    → end_turn? → schedule background_review → 返回

会话结束:
  → await wait_pending() → 反思完成 → 执行日志 flush
  → 下次会话启动 → 加载更新后的 Memory + Skill
```

### 2.3 Frozen Snapshot 机制

借鉴 Hermes 的 Frozen Snapshot 设计。会话首次调用时，加载全部 Memory 和 Skill 全文注入 System Prompt 并冻结。后续轮次复用冻结快照，保护 LLM prefix cache。中途写入只落盘，下次会话生效。

### 2.4 自改进闭环

```
对话 → background_review (flash 模型, async)
  → "有什么值得记住的？" → 自动写入 Memory
  → "Skill 需要改进吗？" → 自动创建/修补 Skill
  → SkillUsageStore 记录 patch_count
  → Curator.scan() → 30天未用 → stale → 90天 → archived
```

---

## 三、工具系统 (7 个)

| 工具 | 权限 | 说明 |
|------|------|------|
| `bash` | write | 执行 Shell 命令 (危险命令 Deny list) |
| `read_file` | read | 读取文件 (行号标注 + 编码检测) |
| `write_file` | write | 写入/创建文件 |
| `glob` | read | 文件模式匹配搜索 |
| `remember` | write | 记忆/Skill 管理 (target 区分 memory/skill, 批量 operations) |
| `recall` | read | 搜索记忆 (n-gram 匹配 + fallback 全量) |
| `skill_load` | read | 获取 Skill 全文 |

**v0.4 计划新增**：Grep (内容搜索)、Edit (精确替换)、WebSearch、WebFetch、Agent (子 Agent)、TodoWrite (任务跟踪)

---

## 四、Memory + Skill 系统

### 4.1 统一模型

Memory 和 Skill 底层是同一类东西——可注入 System Prompt 的 `.md` + YAML frontmatter 文件。区别在于使用模式：

| | Memory | Skill |
|---|--------|-------|
| 本质 | "是什么" (事实/偏好) | "怎么做" (约定/规范) |
| 存储 | `.kun/memory/` | `skills/` |
| 写入 | `remember(target="memory")` | `remember(target="skill")` |
| 注入 | Frozen Snapshot 全文 | Frozen Snapshot 全文 |
| 进化 | background_review 自动提取 | background_review 自动修补 |
| 生命周期 | 手动管理 | Curator (active→stale→archived) |

### 4.2 和 Claude Code / Hermes 的对比

| | Claude Code | Hermes | Kunkun v0.3.2 |
|---|------------|--------|-----------|
| 存储格式 | .md + YAML | MEMORY.md / USER.md (§ 分隔) | .md + YAML |
| Skill 自进化 | ❌ | ✅ background_review | ✅ background_review |
| Curator 生命周期 | ❌ | ✅ | ✅ |
| Frozen Snapshot | ❌ | ✅ | ✅ |
| 中文语义检索 | LLM 判断 | FTS5 + trigram | LLM 判断 + n-gram |
| Provider 插件 | ❌ | ✅ | ❌ |

---

## 五、权限管道

三层门禁 (借鉴 Claude Code)：

1. **Deny List**：15 条危险模式 (rm -rf /, sudo, curl|bash, fork bomb...)
2. **Workspace 边界**：路径须在 workspace 内
3. **Permission Mode**：default / accept_edits / bypass

---

## 六、成本感知路由

三层漏斗 (借鉴 ecommerce-kg-chat)：

1. **关键词匹配**：列出/查看/统计 → flash
2. **轻模型**：简单问答 → deepseek-v4-flash
3. **重模型**：重构/设计/调试 → deepseek-v4-pro

BudgetTracker：每日 $20 / 任务 $5，超 80% 自动降级。

---

## 七、当前版本状态

| 版本 | 状态 | 交付 |
|------|------|------|
| v0.1 MVP | ✅ | Agent Loop + 4 工具 + CLI |
| v0.2 基础设施 | ✅ | 错误恢复 + 权限 + 记忆 + 成本路由 |
| v0.3.2 Skill 系统 | ✅ | Frozen Snapshot + background_review + Curator + 批量写入 |
| v0.4.0 工具补全 | 计划中 | Grep + Edit |
| v0.4.1 Web 能力 | 计划中 | WebSearch + WebFetch |
| v0.4.2 Agent 编排 | 计划中 | Agent + TodoWrite (ThinkBlock 驱动任务拆解) |
| v0.4.3 代码智能 | 计划中 | LSP + Schedule + Workflow |

---

## 八、DS 专属创新方向

Kunkun 的核心竞争力不在于工具数量（追平 Claude Code 只是"站在巨人肩膀上"），而在于利用 DSv4 的独有特性做 Claude Code 做不到的事。

### 8.1 ThinkBlock 过程评测

Claude Code / Hermes 看不到模型推理过程。DSv4 的 `reasoning_content` 暴露了"内心独白"——自我纠错、工具调用前的推理、犹豫信号。量化这些信号 → Thinking Quality Score。

### 8.2 Prompt 粒度编译器

同一任务根据目标模型自动选择 Prompt 粒度：
- V4-Pro：给方向，不过度约束
- V4-Flash：详尽边界定义
- R1：任务分解 + 步骤化

### 8.3 GRPO 式多版本生成

利用 DSv4 低成本生成 3 条执行路径 → LLM-as-Judge 择优 → 返回最佳方案 + 理由。

### 8.4 MLA 感知上下文

DSv4 的 MLA 让 KV Cache 显存大幅降低，长上下文不贵 → 压缩策略可以更宽容 → 对话连贯性更好。

### 8.5 中文优先 Skill 生态

中文编码场景的审查标准、Git 规范、API 文档格式与英文不同。直接翻译会产生水土不服。Kunkun 从中文场景出发设计 Skill。

---

## 九、目录结构

```
kunkun/
├── pyproject.toml
├── README.md
├── .env.example
├── .kun/                        # 运行时数据
│   ├── memory/                  # 记忆文件 (.md)
│   └── reports/                 # 执行日志 (.json)
├── skills/                      # Skill 目录
│   ├── .usage.json              # 使用量追踪
│   ├── code-review/SKILL.md
│   ├── python-project/SKILL.md
│   └── git-conventions/SKILL.md
├── document/                    # 设计文档
│   ├── 计划书_DS-Harness.md
│   ├── 设计方案_Kunkun.md
│   ├── 记忆系统对比报告.md
│   └── v0.*更新公告.md
├── tests/
└── src/kunkun/                     # 源码
    ├── main.py
    ├── cli/
    ├── core/
    ├── tools/
    ├── memory/
    ├── routing/
    └── skills/
```
