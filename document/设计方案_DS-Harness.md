# DS-Harness 详尽设计方案

> 基于 Claude Code (cc-haha)、OpenCode、Hermes Agent 三大源码的深度分析
> 面向 DeepSeek v4 模型的专属编码 Agent 基础设施
> 2026-07-06

---

## 目录

1. [源码分析综述](#一源码分析综述)
2. [总体架构设计](#二总体架构设计)
3. [v0.1 MVP — Agent Loop 核心](#三v01-mvp--agent-loop-核心)
4. [v0.2 — 基础设施一步到位](#四v02--基础设施)
5. [v0.3 — FlowForge 整合](#五v03--原创方向)
6. [v0.4 — TUI + GUI 界面](#六v04--界面)
9. [技术选型详解](#九技术选型详解)
10. [目录结构规范](#十目录结构规范)
11. [关键接口定义](#十一关键接口定义)
12. [风险与对策](#十二风险与对策)

---

## 一、源码分析综述

### 1.1 Claude Code (cc-haha) 架构分析

**技术栈**: TypeScript + Bun + React/Ink (TUI) + Anthropic SDK

**核心架构**:

```
┌──────────────────────────────────────────────────┐
│                  CLI / TUI (Ink)                   │
│  src/cli/  src/components/  src/screens/          │
├──────────────────────────────────────────────────┤
│              QueryEngine (核心引擎)                │
│  src/QueryEngine.ts: submitMessage() → query()    │
│  - 单次会话引擎，支持多轮 submitMessage            │
│  - AsyncGenerator<SDKMessage> 流式输出             │
├──────────────────────────────────────────────────┤
│                 query.ts (Agent Loop)              │
│  while(true):                                     │
│    1. LLM call (claude.ts)                        │
│    2. yield stream events                         │
│    3. tool_use? → runTools() → feed back          │
│    4. compaction / budget / retry 检查            │
├──────────────────────────────────────────────────┤
│                   工具层                           │
│  src/tools/ (40+ tools)                           │
│  Tool<Input, Output, Progress> 泛型接口            │
│  buildTool() 工厂函数 + 默认值注入                  │
├──────────────────────────────────────────────────┤
│                  服务层                            │
│  src/services/api/claude.ts — LLM API             │
│  src/services/compact/ — 上下文压缩                │
│  src/services/mcp/ — MCP 协议                     │
├──────────────────────────────────────────────────┤
│                  基础设施                          │
│  - hooks system (Pre/PostToolUse, SessionStart...) │
│  - permission pipeline (三层门禁)                  │
│  - session persistence (transcript)               │
│  - plugin system                                  │
│  - skill system                                   │
│  - memory system (文件级 .memory/*.md)            │
│  - multi-agent (subagent / team / workflow)       │
└──────────────────────────────────────────────────┘
```

**关键设计模式总结**:

| 模式 | Claude Code 实现 | 本项目借鉴 |
|------|-----------------|-----------|
| Agent Loop | `while(true)` + AsyncGenerator，State 对象跨迭代 | **核心借鉴** — 简洁的 while 循环 + 状态管理 |
| 工具系统 | `Tool<Input,Output,Progress>` 泛型 + `buildTool()` 工厂 | **借鉴** — 简化为 Python 装饰器注册 |
| LLM 调用 | Anthropic SDK streaming + message_start/delta/stop 事件 | **适配** — DSv4 Anthropic 兼容接口 |
| 上下文管理 | autoCompact + reactiveCompact + snipCompact 多策略 | **简化** — v0.1 滑动窗口，v0.5 做 FTS5 |
| 权限系统 | canUseTool 回调 + deny list + rule match + ask user | **完整借鉴** — 三层门禁 |
| 记忆系统 | 文件级 `.memory/*.md` + YAML frontmatter + MEMORY.md 索引 | **核心借鉴** + Hermes FTS5 增强 |
| 状态管理 | AppState (React state) + getAppState/setAppState | **简化** — TypedDict + dataclass |
| 流式输出 | AsyncGenerator<SDKMessage> 统一消息协议 | **借鉴** — AsyncGenerator + 统一事件类型 |

**Claude Code 核心接口 (TypeScript → Python 映射)**:

```typescript
// Tool 接口 (TypeScript)
type Tool<Input, Output, P> = {
  name: string
  call(args, context, canUseTool, parentMessage, onProgress): Promise<ToolResult<Output>>
  description(input, options): Promise<string>
  inputSchema: Input  // Zod schema
  isEnabled(): boolean
  isReadOnly(input): boolean
  isConcurrencySafe(input): boolean
  checkPermissions(input, context): Promise<PermissionResult>
  // ... 渲染、进度、摘要等 UI 方法
}

// → Python 映射:
# @tool 装饰器生成 ToolInstance
# call() 异步方法
# description() 返回 prompt 描述
# input_schema 使用 Pydantic 或 JSON Schema
```

```typescript
// QueryEngine 接口 (TypeScript)
class QueryEngine {
  constructor(config: QueryEngineConfig)
  async *submitMessage(prompt, options?): AsyncGenerator<SDKMessage>
  interrupt(): void
}

// → Python 映射:
# class AgentLoop:
#     async def run(self, prompt: str) -> AsyncGenerator[Event]
#     def interrupt(self) -> None
```

### 1.2 OpenCode 架构分析

**技术栈**: TypeScript + Effect-TS (函数式) + AI SDK (Vercel) + Bun

**核心架构**:

```
┌──────────────────────────────────────────────────┐
│               Session (会话层)                     │
│  packages/opencode/src/session/                   │
│  - processor.ts: SessionProcessor                 │
│  - prompt.ts: 核心执行引擎 (1200+ 行)              │
│  - llm.ts: LLM 流式调用                           │
│  - compaction.ts: 上下文压缩                       │
│  - message-v2.ts: 消息模型                        │
├──────────────────────────────────────────────────┤
│               Agent (智能体层)                     │
│  packages/opencode/src/agent/                     │
│  - agent.ts: Agent 定义与服务                      │
│  - subagent-permissions.ts: 子 Agent 权限          │
├──────────────────────────────────────────────────┤
│               Tool (工具层)                        │
│  packages/opencode/src/tool/                      │
│  - 使用 Vercel AI SDK 的 tool() 函数               │
│  - ToolRegistry 管理工具注册                       │
│  - 支持 MCP / LSP / Shell / Task 等               │
├──────────────────────────────────────────────────┤
│               Provider (模型适配层)                │
│  packages/opencode/src/provider/                  │
│  - 多模型支持 (Anthropic, OpenAI, Google, ...)     │
│  - ProviderTransform: 输出 token 限制              │
├──────────────────────────────────────────────────┤
│           基础设施 (Effect-TS DI)                  │
│  - Layer-based 依赖注入                            │
│  - Effect 错误处理                                 │
│  - Stream 流式处理                                 │
│  - SQLite/Drizzle 持久化                           │
└──────────────────────────────────────────────────┘
```

**关键设计模式**:

| 模式 | OpenCode 实现 | 本项目借鉴 |
|------|-------------|-----------|
| 依赖注入 | Effect-TS `Layer` + `Context.Service` | **不借鉴** — 太重，本项目用简单的函数参数传递 |
| 多 Provider | `Provider` 抽象层 + `ProviderTransform` | **借鉴思路** — DSv4 的 Anthropic 兼容接口天然支持 |
| Session 管理 | `SessionPrompt` + `SessionProcessor` | **借鉴** — 会话生命周期管理 |
| 工具定义 | AI SDK `tool()` + `jsonSchema()` | **不借鉴** — 自研装饰器更灵活 |
| 消息模型 | `MessageV2` 带完整元数据 | **借鉴** — 设计统一的消息类型 |

**OpenCode 的亮点**:
- 多 provider 无缝切换（Anthropic / OpenAI / Google）
- SQLite 持久化会话历史（Drizzle ORM）
- Effect-TS 函数式错误处理
- Session 级别的 compaction 和 summary

**OpenCode 的不足**:
- Effect-TS 学习曲线极陡，不适合快速迭代
- AI SDK 抽象泄漏（provider 差异仍需大量适配代码）
- 不内置评测系统
- 记忆系统较弱（主要依赖会话历史）

### 1.3 Hermes Agent 架构分析 (基于源码深度阅读)

> 源码路径: `C:/Windows/TEMP/hermes-agent/` (NousResearch/hermes-agent, 6099 文件)
> 核心文件: `run_agent.py` (275KB), `hermes_state.py` (269KB), `agent/conversation_loop.py`, `agent/memory_manager.py`

**技术栈**: Python 3 + uv + SQLite + Rich TUI + 多 Provider (25+)

**核心架构**:

```
┌──────────────────────────────────────────────────────┐
│                CLI / TUI (Rich + KawaiiSpinner)       │
│  cli.py (756KB) / ui-tui/                            │
├──────────────────────────────────────────────────────┤
│              AIAgent (run_agent.py, 275KB)            │
│  - run_conversation(): 每 turn 的核心驱动               │
│  - _build_system_prompt(): 组装 system prompt          │
│  - _execute_tool_calls_sequential/concurrent()         │
│  - _compress_context(): 触发上下文压缩                  │
│  - memory_manager.sync_all(): 记忆同步                 │
├──────────────────────────────────────────────────────┤
│    agent/ 子包 (模块化抽取, 60+ 文件)                   │
│                                                       │
│  conversation_loop.py — 主循环 (~3900行原始逻辑)       │
│  context_engine.py — 可插拔上下文引擎 (ABC)             │
│  context_compressor.py — LLM 摘要压缩                   │
│  conversation_compression.py — 压缩编排 (lock/split)    │
│  memory_manager.py — MemoryManager 编排器               │
│  memory_provider.py — MemoryProvider 抽象基类 (ABC)    │
│  tool_executor.py — 串行/并行工具执行                   │
│  tool_dispatch_helpers.py — 并行安全/路径冲突检测       │
│  tool_guardrails.py — 工具护栏 (ToolGuardrailDecision)  │
│  tool_result_classification.py — 工具结果分类           │
│  prompt_builder.py — System Prompt 组装                │
│  prompt_caching.py — Anthropic cache_control           │
│  model_metadata.py — Token 估算 + context window        │
│  retry_utils.py — 指数退避 + jitter                     │
│  error_classifier.py — FailoverReason 分类              │
│  message_sanitization.py — 消息清洗/修复                │
│  turn_context.py — Turn 上下文构建                       │
│  trajectory.py — 轨迹保存                               │
│  agent_init.py — Agent 初始化                           │
│  display.py — KawaiiSpinner + tool label/emoji          │
├──────────────────────────────────────────────────────┤
│              工具系统 (tools/, 80+ 文件)               │
│                                                       │
│  registry.py — 中央注册中心 (AST 扫描 + ToolEntry)      │
│  model_tools.py (64KB) — 工具定义 + 分发               │
│  toolset_distributions.py — 工具集分发                  │
│  toolsets.py (35KB) — 工具集定义                        │
│  ***_tool.py — 各工具实现 (terminal, file, browser...) │
├──────────────────────────────────────────────────────┤
│              状态存储 (hermes_state.py, 269KB)          │
│                                                       │
│  SQLite + FTS5 全文搜索                                │
│  - WAL 模式 (并发读 + 单写)                             │
│  - 压缩触发的 session splitting (parent_session_id)     │
│  - 跨平台 session 追踪 (cli/telegram/discord/cron)     │
│  - Delegate subagent cascade 管理                      │
├──────────────────────────────────────────────────────┤
│              Provider 层 (providers/ + agent/*adapter*) │
│                                                       │
│  providers/base.py — ProviderProfile dataclass (声明式) │
│  agent/anthropic_adapter.py — Anthropic Messages API   │
│  agent/bedrock_adapter.py — AWS Bedrock                │
│  agent/vertex_adapter.py — Google Vertex               │
│  agent/gemini_native_adapter.py — Gemini 原生           │
│  agent/codex_responses_adapter.py — OpenAI Responses    │
│  agent/openrouter_client.py — OpenRouter 客户端         │
└──────────────────────────────────────────────────────┘
```

**关键设计模式详解**:

#### 1. Agent Loop（conversation_loop.py）

```python
# AIAgent.run_conversation() 的核心流程 (conversation_loop.py):
def run_conversation(agent, user_message: str) -> str:
    # 1. 记忆预取 (background)
    agent._memory_manager.prefetch_all(user_message)
    
    # 2. 构建 turn context
    turn_context = build_turn_context(agent, user_message)
    
    # 3. 构建 System Prompt (多来源拼装)
    system_prompt = agent._build_system_prompt()
    
    # 4. 进入 while True 循环
    while iteration < max_iterations:
        # 4a. 构建 API messages
        messages = [system_msg] + history + [user_msg]
        
        # 4b. 上下文压缩检查
        if agent.context_compressor.should_compress():
            messages = compress_context(agent, messages)
            agent.session_id = new_session_id  # session 分裂
        
        # 4c. LLM 调用 (带重试 + 降级)
        response = call_model_with_retry(agent, messages, tools)
        
        # 4d. 解析 tool_calls
        if response.tool_calls:
            # 判别: 串行 vs 并行执行
            if _should_parallelize_tool_batch(tool_calls):
                results = execute_concurrent(agent, tool_calls)
            else:
                results = execute_sequential(agent, tool_calls)
            messages.extend(tool_results)
            continue  # 回到 LLM
        
        # 4e. 无 tool_calls → 结束
        break
    
    # 5. Post-turn: 记忆同步 + 轨迹保存
    agent._memory_manager.sync_all(user_msg, assistant_response)
    save_trajectory(agent, messages)
```

#### 2. 记忆系统 — MemoryManager + MemoryProvider（可插拔架构）

这是 Hermes 最适合本项目借鉴的模块。

```python
# MemoryProvider 抽象基类 (agent/memory_provider.py)
class MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...
    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None: ...
    
    # 核心方法
    def system_prompt_block(self) -> str:  # 静态说明文本
    def prefetch(self, query: str, *, session_id: str = "") -> str:  # 召回
    def queue_prefetch(self, query: str, *, session_id: str = "")  # 后台预取
    def sync_turn(self, user_content, assistant_content, **kwargs):  # 持久化
    def get_tool_schemas(self) -> List[Dict]:  # 暴露给 LLM 的工具
    def handle_tool_call(self, tool_name, args, **kwargs) -> str:  # 工具执行
    
    # 可选钩子
    def on_turn_start(self, turn, message, **kwargs): ...
    def on_session_end(self, messages): ...
    def on_session_switch(self, new_session_id, **kwargs): ...
    def on_pre_compress(self, messages) -> str: ...  # 压缩前提取关键信息!
    def on_memory_write(self, action, target, content, metadata=None): ...
    def on_delegation(self, task, result, **kwargs): ...
    def backup_paths(self) -> list[str]: ...

# MemoryManager 编排器 (agent/memory_manager.py)
class MemoryManager:
    def add_provider(self, provider: MemoryProvider): ...  # 只允许一个外部 provider
    def build_system_prompt(self) -> str: ...  # 拼装所有 provider 的 system_prompt_block
    def prefetch_all(self, user_message: str) -> str: ...  # 汇总所有 provider 的 prefetch
    def sync_all(self, user_msg, assistant_response): ...  # 同步到所有 provider
    def queue_prefetch_all(self, user_msg): ...  # 为下一 turn 后台预取
    def get_all_tool_schemas(self) -> List[Dict]: ...  # 收集所有 provider 的工具
```

#### 3. 工具注册中心（AST 扫描 + 模块导入）

这是 Hermes 最精巧的设计之一：

```python
# tools/registry.py — 中央注册中心
class ToolEntry:
    __slots__ = (
        "name", "toolset", "schema", "handler", "check_fn",
        "requires_env", "is_async", "description", "emoji",
        "max_result_size_chars", "dynamic_schema_overrides",
    )

# 自动发现: 扫描 tools/*.py 中顶层 registry.register(...) 调用的 AST
def discover_builtin_tools(tools_dir) -> List[str]:
    for path in tools_dir.glob("*.py"):
        if _module_registers_tools(path):  # AST 解析，检查顶层是否有 registry.register()
            importlib.import_module(f"tools.{path.stem}")
```

#### 4. 上下文引擎 — ContextEngine（可插拔 ABC）

```python
# agent/context_engine.py
class ContextEngine(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    # Token 状态追踪 (由 run_agent 读取)
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0

    # 压缩参数
    threshold_percent: float = 0.75    # 触发阈值 (占 context 比例)
    protect_first_n: int = 3           # 保护前 N 条消息
    protect_last_n: int = 6            # 保护后 N 条消息

    # 核心接口
    @abstractmethod
    def update_from_response(self, usage: Dict) -> None: ...
    @abstractmethod
    def should_compress(self, prompt_tokens: int = None) -> bool: ...
    @abstractmethod
    def compress(self, messages, current_tokens=None, focus_topic=None) -> List[Dict]: ...
```

#### 5. 工具并行执行策略（tool_dispatch_helpers.py）

```python
# 按安全性分三级:
_NEVER_PARALLEL_TOOLS = frozenset({"clarify"})  # 交互式工具，绝不并行
_PARALLEL_SAFE_TOOLS = frozenset({  # 纯读操作，安全并行
    "read_file", "search_files", "session_search",
    "web_extract", "web_search", "vision_analyze", ...
})
_PATH_SCOPED_TOOLS = frozenset({"read_file", "write_file", "patch"})
# → 当多个 path-scoped 工具的目标路径无重叠时，可以并行

# 破坏性命令检测（阻止并行）
_DESTRUCTIVE_PATTERNS = re.compile(r"(?:rm\s|rmdir\s|cp\s|mv\s|sed\s+-i|dd\s|...)")
```

#### 6. Provider 声明式配置（providers/base.py）

```python
@dataclass
class ProviderProfile:
    name: str
    api_mode: str = "chat_completions"
    aliases: tuple = ()
    display_name: str = ""
    description: str = ""
    signup_url: str = ""          # 注册链接
    env_vars: tuple = ()          # 环境变量名
    base_url: str = ""
    auth_type: str = "api_key"    # api_key | oauth_device_code | aws_sdk
    supports_vision: bool = False
    supports_vision_tool_messages: bool = True
    fallback_models: tuple = ()   # /model picker 的离线后备
    default_headers: dict = field(default_factory=dict)
    fixed_temperature: Any = None
    default_max_tokens: int | None = None
    default_aux_model: str = ""   # 辅助任务模型 (压缩/视觉摘要)
```

#### 7. 状态存储 — SQLite + FTS5（hermes_state.py, 269KB）

```sql
-- FTS5 全文搜索 (中文分词需 jieba 插件)
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    tokenize='unicode61'
);

-- Session 分裂 (context compression 触发)
-- parent_session_id 链支持跨 session 追踪
-- branch vs compression continuation vs delegate subagent 三种子 session 类型
```

**Hermes 的亮点（修正版）**:
- **Python 生态**，开发效率高，大量模块化抽取 (`agent/` 子包 60+ 文件)
- **记忆系统可插拔**，MemoryProvider ABC + MemoryManager 编排器，设计成熟
- **工具注册精巧**，AST 扫描自发现 + ToolEntry slots 轻量设计
- **上下文引擎可插拔**，ContextEngine ABC，支持 LCM 等第三方引擎
- **多 Provider**，25+ ProviderProfile，声明式配置
- **并行工具执行**，三级安全分类 + 路径冲突检测
- **SQLite + FTS5** 全文搜索，session splitting 机制

**Hermes 的不足（修正版）**:
- **主文件过大**，run_agent.py 275KB / cli.py 756KB（虽然已部分抽取到 agent/ 子包）
- **无内置评测框架**，无基线、无过程报告
- **无成本感知路由**，所有请求走同一模型
- **记忆默认较弱**，纯 FTS5 无中文分词（需 holographic-zh 等插件增强）
- **工具护栏基础**，主要依赖正则模式匹配
- **无 Skill 系统**（仅有 skills 目录 + skill_utils，不同于 Claude Code 的 Skill 系统）

### 1.4 三大项目的对比总结

| 维度 | Claude Code | OpenCode | Hermes | **本项目定位** |
|------|------------|----------|--------|-------------|
| 语言 | TypeScript | TypeScript | Python | **Python** |
| 模型耦合 | 紧密耦合 Claude | 多 Provider | 多 Provider | **DSv4 专属优化** |
| Agent Loop | while + Generator | Effect Stream | while + async | **while + AsyncGenerator** |
| 工具系统 | 泛型 Tool< I,O,P > | AI SDK tool() | 函数注册 | **装饰器注册 (FlowForge 模式)** |
| 上下文管理 | 多策略压缩 | Session compaction | Context compressor | **滑动窗口 + FTS5** |
| 记忆系统 | 文件级 MD | 会话历史 SQLite | Memory Provider | **文件级 MD + 可选 FTS5** |
| 评测 | 无内置 | 无内置 | 无内置 | **内置过程评测** |
| 成本 | API 计费追踪 | 无 | 无 | **三层漏斗路由** |
| TUI | Ink/React | Ink/React | Rich | **prompt_toolkit** |
| 中文支持 | 弱 | 弱 | 插件中文分词 | **中文优先** |
| ThinkBlock | 仅 Claude thinking | 无 | 无 | **原生 ThinkBlock 解析** |

---

## 二、总体架构设计

### 2.1 架构全景

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI 交互层                                │
│                                                                  │
│  prompt_toolkit TUI                                              │
│  - ThinkBlock 灰色斜体实时渲染                                    │
│  - Tool Call 高亮 (青色背景 + 工具名)                             │
│  - 流式输出 Markdown 渲染                                        │
│  - 权限问询 inline 弹窗                                          │
├─────────────────────────────────────────────────────────────────┤
│                      Harness 内核                                │
│                                                                  │
│  ┌─────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐      │
│  │ 记忆加载 │ → │ 上下文裁剪│ → │Prompt 组装│ → │   LLM    │      │
│  │ (Pre)   │   │ (Sliding) │   │ (Sys+User)│   │  (DSv4)  │      │
│  └─────────┘   └──────────┘   └──────────┘   └────┬─────┘      │
│                                                     │            │
│  ┌─────────────────────────────────────────────────┘            │
│  │  ThinkBlock 解析 + Streaming                                 │
│  ▼                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐    │
│  │ 权限管道  │ → │ 工具执行  │ → │ 结果回传  │ → │ 状态更新  │    │
│  │ (3层)    │   │ (并行)   │   │ (format) │   │ (State)  │    │
│  └──────────┘   └──────────┘   └──────────┘   └────┬─────┘    │
│                                                     │            │
│                         循环 ←──────────────────────┘            │
│                                                                  │
│  Agent Loop:                                                     │
│    while True:                                                   │
│      event = await llm_stream(messages)                          │
│      if event.type == "tool_use":                                │
│        result = await permission_check(tool, input)              │
│        if result == "allow":                                     │
│          output = await execute_tool(tool, input)                │
│          messages.append(tool_result(output))                    │
│      elif event.type == "text":                                  │
│        yield event  # 流式输出到 TUI                             │
│      elif event.type == "stop":                                  │
│        break                                                     │
├─────────────────────────────────────────────────────────────────┤
│                      评测层 (内置)                                │
│                                                                  │
│  EventBus 收集 → 过程报告生成 → 指标统计 → 历史对比 → JSON 持久化 │
├─────────────────────────────────────────────────────────────────┤
│                      记忆层                                      │
│                                                                  │
│  .ds-harness/memory/*.md (YAML frontmatter + Markdown)          │
│  MEMORY.md 索引文件                                              │
│  可选: SQLite FTS5 全文检索 (jieba 中文分词)                     │
├─────────────────────────────────────────────────────────────────┤
│                      工具层                                      │
│                                                                  │
│  @tool 装饰器注册 | 权限标签 | 角色白名单 | MCP 扩展              │
├─────────────────────────────────────────────────────────────────┤
│                      Skill 层                                    │
│                                                                  │
│  skills/ 目录 | SKILL.md (YAML + Markdown) | 启动扫描 | 按需加载 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流设计

```
用户输入 "在 ~/project 下找所有 Python 文件并统计行数"
    │
    ▼
[Pre-LLM Pipeline]
    │
    ├─→ 1. Memory Loader: 从 .ds-harness/memory/ 加载相关记忆
    │       - 扫描 MEMORY.md 索引
    │       - 按 name + description 匹配 ≤5 条
    │       - 注入 System Prompt
    │
    ├─→ 2. Context Trimmer: 滑动窗口裁剪历史消息
    │       - 首条 system + 首条 user 永保留
    │       - 后续: 总 token ≤ limit (默认 64K, 可配置)
    │       - 裁剪策略: FIFO 删除中间的 user/assistant 轮次
    │
    └─→ 3. Prompt Assembler: 组装完整 messages
            system_prompt = [
              Harness System Prompt,
              Memory Context (≤5 条),
              Project Context (CLAUDE.md/.ds-harness/config.yaml),
            ]
            messages = [system, user_context, ...history, user_input]
    │
    ▼
[LLM Stream (DSv4 via Anthropic Compatible API)]
    │
    ├─→ Stream 事件类型:
    │     - message_start: 初始化，记录 usage
    │     - content_block_start: text / thinking / tool_use
    │     - content_block_delta: text_delta / thinking_delta / input_json_delta
    │     - content_block_stop
    │     - message_delta: stop_reason, usage
    │     - message_stop
    │
    ├─→ ThinkBlock 处理:
    │     - 解析 thinking 类型 content_block
    │     - 实时渲染为灰色斜体 (TUI)
    │     - JSON 日志中标记为 thinking 类型
    │
    └─→ Tool Use 检测:
          如果 stop_reason == "tool_use":
            yield tool_use 事件 → 进入后处理
    │
    ▼
[Post-LLM Pipeline]
    │
    ├─→ 1. Permission Check (三层门禁)
    │     Layer 1 - Deny List: rm -rf /, sudo, curl | sh → 硬拒绝
    │     Layer 2 - Rule Match: 路径须在 workspace 内 → 匹配放行/拒绝
    │     Layer 3 - Ask User: 不在规则内的操作 → TUI 弹窗确认
    │
    ├─→ 2. Tool Execution
    │     - bash: 异步子进程 (asyncio.create_subprocess_exec)
    │     - read_file: 同步读 (aiofiles)
    │     - write_file: 同步写 + 备份
    │     - glob: fast glob (库)
    │
    └─→ 3. Result Feed Back
          messages.append(ToolResult(content, tool_use_id))
          回到 Agent Loop
    │
    ▼
[Evaluation Pipeline (每次执行完自动触发)]
    │
    ├─→ EventBus.drain() → 收集所有事件
    ├─→ MetricsCalculator:
    │     - 成功率
    │     - 总 Token (input + output + thinking)
    │     - 工具调用次数 (按类型)
    │     - 端到端延迟
    │     - 每步时间线
    │     - 冗余检测 (tool result 是否被后续引用)
    ├─→ ReportGenerator:
    │     - 终端摘要 (彩色表格)
    │     - JSON 完整报告 → .ds-harness/reports/{timestamp}.json
    └─→ BaselineComparer (v0.3+):
          - 与历史基线对比
          - 标注退化/改进
```

### 2.3 核心类型定义 (Python)

```python
from dataclasses import dataclass, field
from typing import TypedDict, Literal, Any, AsyncGenerator
from enum import Enum
from datetime import datetime
import uuid

# ─── 消息类型 ──────────────────────────────

class MessageRole(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

class ContentType(Enum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"

@dataclass
class ContentBlock:
    type: ContentType
    content: str | dict  # text → str, tool_use → dict, tool_result → dict

@dataclass
class Message:
    role: MessageRole
    content: list[ContentBlock] | str
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    # 元数据
    usage: dict | None = None        # {"input_tokens": N, "output_tokens": N}
    stop_reason: str | None = None   # "end_turn" | "tool_use" | "max_tokens" | "stop_sequence"
    thinking_content: str | None = None  # DSv4 ThinkBlock 内容
    tool_use_id: str | None = None   # 工具调用 ID
    is_error: bool = False

# ─── 工具类型 ──────────────────────────────

class PermissionResult(TypedDict):
    behavior: Literal["allow", "deny", "ask"]
    message: str | None
    updated_input: dict | None

class ToolResult(TypedDict):
    data: Any
    is_error: bool
    new_messages: list[Message] | None

class ToolInstance:
    name: str
    description_text: str
    input_schema: dict  # JSON Schema
    permission: Literal["read", "write", "destroy"]
    is_concurrency_safe: bool

    async def call(self, args: dict, context: "ToolUseContext") -> ToolResult: ...
    async def check_permissions(self, args: dict, context: "ToolUseContext") -> PermissionResult: ...
    def is_enabled(self) -> bool: ...

# ─── 事件类型 ──────────────────────────────

class EventType(Enum):
    # LLM Stream
    MESSAGE_START = "message_start"
    CONTENT_BLOCK_START = "content_block_start"
    CONTENT_BLOCK_DELTA = "content_block_delta"
    CONTENT_BLOCK_STOP = "content_block_stop"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_STOP = "message_stop"
    # Tool
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    # System
    PERMISSION_DENIED = "permission_denied"
    ERROR = "error"
    RETRY = "retry"
    # Lifecycle
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    SESSION_START = "session_start"
    SESSION_END = "session_end"

@dataclass
class Event:
    type: EventType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    session_id: str = ""
    turn_number: int = 0

# ─── Agent 状态 ─────────────────────────────

@dataclass
class AgentState:
    messages: list[Message] = field(default_factory=list)
    current_turn: int = 0
    total_tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0, "thinking": 0})
    tool_calls: list[dict] = field(default_factory=list)
    start_time: float = field(default_factory=lambda: datetime.now().timestamp())
    model: str = "deepseek-v4-pro"
    budget_remaining: float | None = None
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))

# ─── 配置 ─────────────────────────────────

@dataclass
class HarnessConfig:
    model: str = "deepseek-v4-pro"              # 默认模型
    light_model: str = "deepseek-v4-flash"      # 轻量模型
    max_turns: int = 50                         # 每任务最大轮次
    max_tokens_per_turn: int = 64000            # 每轮上下文窗口
    max_budget_usd: float = 5.0                 # 每任务最大花费
    daily_budget_usd: float = 20.0              # 每日最大花费
    workspace: str = "."                         # 工作目录
    permission_mode: Literal["default", "accept_edits", "bypass"] = "default"
    memory_dir: str = ".ds-harness/memory"
    report_dir: str = ".ds-harness/reports"
    skill_dir: str = "skills"
    locale: Literal["zh", "en", "auto"] = "auto"
    think_visibility: Literal["show", "hide"] = "show"
```

---

## 三、v0.1 MVP — Agent Loop 核心

### 3.1 设计目标

跑通完整 Agent Loop：CLI 输入任务 → LLM 思考 → 调用工具 → 观察结果 → 继续/结束

### 3.2 模块详设

#### 3.2.1 Agent Loop 引擎

**文件**: `ds_harness/core/agent_loop.py`

**设计来源**: 
- cc-haha `src/query.ts` — `while(true)` + AsyncGenerator + State 跨迭代
- cc-haha `src/QueryEngine.ts` — `submitMessage()` + `interrupt()`

**设计方案**:

```python
from typing import AsyncGenerator
import asyncio
from ds_harness.core.state import AgentState
from ds_harness.core.events import Event, EventType
from ds_harness.core.llm_client import LLMClient
from ds_harness.tools.registry import ToolRegistry
from ds_harness.core.permissions import PermissionPipeline
from ds_harness.core.context import ContextManager

class AgentLoop:
    """DS-Native Harness 核心 Agent Loop.
    
    借鉴 Claude Code query.ts 的 while(true) + State 模式:
    - AsyncGenerator 流式输出事件到 CLI
    - State 对象跨迭代保持状态
    - 支持 interrupt() 中断
    """
    
    def __init__(self, config: HarnessConfig):
        self.config = config
        self.state = AgentState()
        self.llm = LLMClient(config)
        self.tools = ToolRegistry()
        self.permissions = PermissionPipeline(config)
        self.context_mgr = ContextManager(config)
        self._abort = asyncio.Event()
    
    async def run(self, prompt: str) -> AsyncGenerator[Event, None]:
        """主循环入口。每个用户输入调用一次。"""
        self.state.start_time = datetime.now().timestamp()
        
        # Step 1: 处理用户输入 (借鉴 cc-haha processUserInput)
        user_msg = Message(role=MessageRole.USER, content=prompt)
        self.state.messages.append(user_msg)
        yield Event(type=EventType.TURN_START, data={"prompt": prompt})
        
        # Step 2: 进入 Agent Loop
        while self.state.current_turn < self.config.max_turns:
            if self._abort.is_set():
                yield Event(type=EventType.SESSION_END, data={"reason": "aborted"})
                return
            
            self.state.current_turn += 1
            
            # 2a. Pre-LLM: 上下文裁剪
            messages = self.context_mgr.trim(self.state.messages)
            
            # 2b. LLM 流式调用
            try:
                async for event in self.llm.stream(messages, self.tools.schemas()):
                    if event.type == EventType.CONTENT_BLOCK_DELTA:
                        yield event  # 实时输出到 CLI
                    elif event.type == EventType.TOOL_USE:
                        # 2c. Post-LLM: 权限检查 + 工具执行
                        tool_result = await self._handle_tool_use(event)
                        self.state.messages.append(tool_result)
                        yield Event(type=EventType.TOOL_RESULT, data=tool_result)
                    elif event.type == EventType.MESSAGE_STOP:
                        stop_reason = event.data.get("stop_reason")
                        if stop_reason == "end_turn":
                            yield Event(type=EventType.TURN_END)
                            return  # 任务完成
                        # stop_reason == "tool_use" → 继续循环
                        break
            except Exception as e:
                yield Event(type=EventType.ERROR, data={"error": str(e)})
                # v0.1: 简单错误处理，v0.2 做完整恢复
                break
        
        yield Event(type=EventType.TURN_END, 
                     data={"reason": "max_turns", "turns": self.state.current_turn})
    
    async def _handle_tool_use(self, event: Event) -> Message:
        """处理工具调用: 权限检查 → 执行 → 格式化结果"""
        tool_name = event.data["name"]
        tool_input = event.data["input"]
        tool_use_id = event.data["id"]
        
        # 权限检查 (三层门禁，借鉴 cc-haha canUseTool)
        tool = self.tools.get(tool_name)
        if tool is None:
            return self._error_result(tool_use_id, f"Unknown tool: {tool_name}")
        
        perm_result = await self.permissions.check(tool, tool_input)
        if perm_result["behavior"] == "deny":
            return self._error_result(tool_use_id, perm_result["message"])
        
        # 执行工具
        try:
            result = await tool.call(tool_input, self._tool_context())
            return Message(
                role=MessageRole.USER,
                content=[ContentBlock(
                    type=ContentType.TOOL_RESULT,
                    content={"tool_use_id": tool_use_id, "content": result["data"]}
                )],
                tool_use_id=tool_use_id,
                is_error=result["is_error"]
            )
        except Exception as e:
            return self._error_result(tool_use_id, str(e), is_error=True)
    
    def interrupt(self):
        """中断当前执行 (借鉴 cc-haha QueryEngine.interrupt)"""
        self._abort.set()
```

#### 3.2.2 LLM 适配层 — DSv4 ThinkBlock

**文件**: `ds_harness/core/llm_client.py`

**设计来源**: 
- cc-haha `src/services/api/claude.ts` — Anthropic SDK streaming
- InsightAgent `llm_utils.py` — ThinkBlock 解析

**关键设计 — ThinkBlock 解析**:

DSv4 的 thinking 内容通过 Anthropic 兼容接口以特定格式返回。需要：

1. **解析 thinking content block**: 在 stream 中检测 `type: "thinking"` 的 content_block
2. **ThinkBlock 与 text 分离**: thinking 内容存入 `thinking_content` 字段，不进入 text 上下文
3. **TUI 渲染**: thinking 灰色斜体实时输出，正常 text 白色输出

```python
class LLMClient:
    """DSv4 LLM 客户端 (Anthropic 兼容接口).
    
    借鉴 cc-haha claude.ts 的 streaming 架构:
    - message_start → message_delta → message_stop 事件流
    - usage 追踪
    - stop_reason 检测
    """
    
    async def stream(self, messages: list[Message], tools: list[dict]) -> AsyncGenerator[Event, None]:
        """流式调用 DSv4 API.
        
        DSv4 Anthropic 兼容接口预期行为:
        - 使用 messages API (POST /v1/messages)
        - stream: true
        - 兼容 Anthropic SDK 格式的消息和工具定义
        """
        # 构造请求
        system_prompt = self._build_system_prompt()
        api_messages = self._to_api_messages(messages)
        api_tools = self._to_api_tools(tools)
        
        # 发起流式请求
        async with self._stream_request(
            model=self.config.model,
            system=system_prompt,
            messages=api_messages,
            tools=api_tools,
            max_tokens=8192,
            stream=True,
        ) as response:
            current_block_type = None
            current_tool_input = {}
            current_tool_name = ""
            current_tool_id = ""
            thinking_buffer = ""
            
            async for chunk in response:
                # message_start
                if chunk.type == "message_start":
                    yield Event(EventType.MESSAGE_START, 
                                data={"usage": chunk.message.usage})
                
                # content_block_start
                elif chunk.type == "content_block_start":
                    block = chunk.content_block
                    if block.type == "thinking":
                        current_block_type = "thinking"
                        thinking_buffer = ""
                        yield Event(EventType.CONTENT_BLOCK_START,
                                    data={"type": "thinking"})
                    elif block.type == "text":
                        current_block_type = "text"
                        yield Event(EventType.CONTENT_BLOCK_START,
                                    data={"type": "text"})
                    elif block.type == "tool_use":
                        current_block_type = "tool_use"
                        current_tool_name = block.name
                        current_tool_id = block.id
                        current_tool_input = {}
                
                # content_block_delta
                elif chunk.type == "content_block_delta":
                    delta = chunk.delta
                    if delta.type == "thinking_delta":
                        thinking_buffer += delta.thinking
                        yield Event(EventType.CONTENT_BLOCK_DELTA,
                                    data={"type": "thinking", "text": delta.thinking})
                    elif delta.type == "text_delta":
                        yield Event(EventType.CONTENT_BLOCK_DELTA,
                                    data={"type": "text", "text": delta.text})
                    elif delta.type == "input_json_delta":
                        current_tool_input = self._merge_json(
                            current_tool_input, delta.partial_json
                        )
                
                # content_block_stop
                elif chunk.type == "content_block_stop":
                    if current_block_type == "tool_use":
                        yield Event(EventType.TOOL_USE, data={
                            "name": current_tool_name,
                            "id": current_tool_id,
                            "input": current_tool_input
                        })
                    current_block_type = None
                
                # message_delta
                elif chunk.type == "message_delta":
                    yield Event(EventType.MESSAGE_DELTA, data={
                        "stop_reason": chunk.delta.stop_reason,
                        "usage": chunk.usage
                    })
                
                # message_stop
                elif chunk.type == "message_stop":
                    yield Event(EventType.MESSAGE_STOP, data={
                        "stop_reason": chunk.delta.stop_reason if hasattr(chunk, 'delta') else "end_turn"
                    })
```

#### 3.2.3 工具系统 — 装饰器注册

**文件**: `ds_harness/tools/registry.py`, `ds_harness/tools/decorators.py`

**设计来源**: 
- cc-haha `src/Tool.ts` — `Tool<Input, Output, Progress>` 泛型接口 + `buildTool()` 工厂
- FlowForge 装饰器注册模式

**关键设计**: 

```python
import functools
from typing import Callable, Any
from pydantic import BaseModel

class ToolRegistry:
    """工具注册中心.
    
    借鉴 cc-haha Tool 接口 + FlowForge 装饰器模式:
    - @tool 装饰器自动注册
    - Pydantic 模型 → JSON Schema (替代 cc-haha 的 Zod)
    - 权限标签声明式
    """
    
    def __init__(self):
        self._tools: dict[str, ToolInstance] = {}
    
    def register(self, tool: ToolInstance):
        self._tools[tool.name] = tool
    
    def get(self, name: str) -> ToolInstance | None:
        return self._tools.get(name)
    
    def schemas(self) -> list[dict]:
        """返回所有工具的 JSON Schema (用于 LLM API)"""
        return [t.to_api_schema() for t in self._tools.values() if t.is_enabled()]


# ─── 装饰器 ──────────────────────────────

def tool(
    name: str,
    description: str,
    permission: Literal["read", "write", "destroy"] = "read",
    is_concurrency_safe: bool = False,
    input_model: type[BaseModel] | None = None,
):
    """工具装饰器.
    
    示例:
        @tool(
            name="bash",
            description="Execute a bash command",
            permission="write",
            input_model=BashInput
        )
        async def bash(args: BashInput, context: ToolUseContext) -> ToolResult:
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(args: dict, context: ToolUseContext):
            if input_model:
                validated = input_model(**args)
            else:
                validated = args
            return await func(validated, context)
        
        wrapper._tool_meta = {
            "name": name,
            "description": description,
            "permission": permission,
            "is_concurrency_safe": is_concurrency_safe,
            "input_model": input_model,
        }
        return wrapper
    return decorator
```

**v0.1 工具清单**:

```python
# 1. bash — 执行 Shell 命令
@tool(name="bash", description="Execute a bash command in the workspace", permission="write")
async def bash_tool(args: BashInput, ctx: ToolUseContext) -> ToolResult:
    """借鉴 cc-haha BashTool: 子进程执行 + 超时控制 + 输出截断"""
    ...

# 2. read_file — 读取文件
@tool(name="read_file", description="Read a file from the filesystem", permission="read")
async def read_file_tool(args: ReadFileInput, ctx: ToolUseContext) -> ToolResult:
    """借鉴 cc-haha FileReadTool: 行号范围 + 编码检测"""
    ...

# 3. write_file — 写入文件
@tool(name="write_file", description="Write or overwrite a file", permission="write")
async def write_file_tool(args: WriteFileInput, ctx: ToolUseContext) -> ToolResult:
    """借鉴 cc-haha FileWriteTool: 内容 + 路径"""
    ...

# 4. glob — 文件模式匹配
@tool(name="glob", description="Find files matching a glob pattern", permission="read")
async def glob_tool(args: GlobInput, ctx: ToolUseContext) -> ToolResult:
    """借鉴 cc-haha GlobTool: pattern + path"""
    ...
```

#### 3.2.4 上下文管理 — 滑动窗口

**文件**: `ds_harness/core/context.py`

**设计来源**:
- cc-haha `src/services/compact/` — autoCompact 多策略压缩
- 20 题库 SlidingWindowManager — 首条永保留

**v0.1 策略 (简化版)**:

```python
class ContextManager:
    """上下文管理器.
    
    v0.1: 简单滑动窗口裁剪
    - 首条 system prompt 永保留
    - 首条 user 输入永保留
    - 后续消息: 总 token ≤ max_tokens (默认 64K)
    - 裁剪策略: FIFO 删除最旧的中间轮次
    """
    
    def trim(self, messages: list[Message], max_tokens: int = 64000) -> list[Message]:
        if not messages:
            return messages
        
        # 保留首条 system 和首条 user
        preserved_head = []
        preserved_tail = list(messages)
        
        # 计算总 token，超出则从中间裁剪
        total = sum(self._estimate_tokens(m) for m in messages)
        if total <= max_tokens:
            return messages
        
        # 从最旧的中间消息开始移除 (保留头和尾)
        while total > max_tokens and len(preserved_tail) > 3:
            removed = preserved_tail.pop(2)  # 跳过 sys[0] + user[1], 删最旧的
            total -= self._estimate_tokens(removed)
        
        return preserved_tail
    
    def _estimate_tokens(self, msg: Message) -> int:
        """简单 token 估算: 1 token ≈ 3 chars (中文) 或 ≈ 4 chars (英文)"""
        text = str(msg.content)
        return len(text) // 3  # 保守估计
```

**v0.5 升级路径**: 
- 引入智能压缩 (借鉴 cc-haha autoCompact)
- 基于 DSv4 的摘要能力做 conversation summary
- FTS5 检索历史关键信息

#### 3.2.5 CLI 交互 — prompt_toolkit TUI

**文件**: `ds_harness/cli/tui.py`

**设计来源**: 
- cc-haha Ink/React TUI — 组件化渲染
- Hermes Rich TUI — Python 原生终端渲染

**设计**:

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText

class HarnessTUI:
    """终端交互界面.
    
    功能:
    - 用户输入区域 (底部)
    - ThinkBlock 灰色斜体实时输出
    - Tool Call 高亮显示
    - 权限问询 inline 弹窗
    - Markdown 渲染 (粗体/代码块/列表)
    """
    
    style = Style.from_dict({
        "thinking": "italic #888888",       # 思考过程灰色斜体
        "tool-call": "bg:#005f87 fg:white", # 工具调用高亮
        "tool-result": "fg:#00af5f",        # 工具结果绿色
        "error": "fg:#af0000 bold",         # 错误红色粗体
        "permission": "bg:#ffff00 fg:black", # 权限弹窗
    })
    
    async def render_stream(self, events: AsyncGenerator[Event, None]):
        """实时渲染事件流"""
        async for event in events:
            if event.type == EventType.CONTENT_BLOCK_DELTA:
                if event.data["type"] == "thinking":
                    self._print_thinking(event.data["text"])
                elif event.data["type"] == "text":
                    self._print_markdown(event.data["text"])
            elif event.type == EventType.TOOL_USE:
                self._print_tool_call(event.data)
            elif event.type == EventType.TOOL_RESULT:
                self._print_tool_result(event.data)
```

---

## 四、v0.2 — 基础设施一步到位

> **战略调整 (2026-07-06)**：原计划分四版（v0.2 可靠性 → v0.3 评测 → v0.4 成本 → v0.5 记忆），现合并为一个版本。
>
> **原因**：
> 1. 评测内置交给 FlowForge 做（v0.3 取消），Kun 不重复造轮子
> 2. 可靠性 + 权限 + 成本路由 + 记忆系统，这些是"站在前人肩膀上"的基础设施
> 3. 即使做到 v0.5，也不过是 Claude Code + Hermes 的缝合版——真正的差异化在 v0.3+ 开始
> 4. 合并后一步到位，腾出精力聚焦 Kun 的原创方向
>
> **定位**：v0.1 是 MVP（跑通循环），v0.2 是"能用"（生产级可靠性），v0.3+ 是"不一样"（FlowForge 的 Harness 内核 + 中文生态原创）

### 4.1 错误恢复模块

**文件**: `ds_harness/core/error_recovery.py`

**设计来源**: 
- cc-haha `src/services/api/errors.ts` — `categorizeRetryableAPIError()`
- cc-haha `src/services/api/withRetry.ts` — 指数退避重试

```python
import asyncio
from enum import Enum

class ErrorCategory(Enum):
    RETRYABLE = "retryable"     # 429, 5xx, connection timeout
    NON_RETRYABLE = "non_retryable"  # 4xx (except 429)
    FATAL = "fatal"             # auth error, workspace not found

class ErrorRecovery:
    """错误恢复策略.
    
    借鉴 cc-haha 的错误分类:
    - 429 Rate Limit → 指数退避 + jitter
    - 5xx Server Error → 指数退避
    - Connection Timeout → 有限重试
    - 4xx → 直接报错
    """
    
    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # 秒
    MAX_DELAY = 30.0   # 秒
    DELAY_MULTIPLIER = 2.0
    
    @classmethod
    def categorize(cls, error: Exception) -> ErrorCategory:
        if isinstance(error, asyncio.TimeoutError):
            return ErrorCategory.RETRYABLE
        status = getattr(error, 'status_code', None)
        if status == 429:
            return ErrorCategory.RETRYABLE
        if status and 500 <= status < 600:
            return ErrorCategory.RETRYABLE
        if status and 400 <= status < 500:
            return ErrorCategory.NON_RETRYABLE
        return ErrorCategory.FATAL
    
    @classmethod
    async def with_retry(cls, func, *args, **kwargs):
        """带重试的异步函数包装器"""
        last_error = None
        for attempt in range(cls.MAX_RETRIES + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_error = e
                cat = cls.categorize(e)
                if cat == ErrorCategory.FATAL:
                    raise
                if cat == ErrorCategory.NON_RETRYABLE:
                    raise
                if attempt < cls.MAX_RETRIES:
                    delay = min(
                        cls.BASE_DELAY * (cls.DELAY_MULTIPLIER ** attempt),
                        cls.MAX_DELAY
                    )
                    # 添加 jitter (±25%)
                    import random
                    delay *= 0.75 + random.random() * 0.5
                    await asyncio.sleep(delay)
        raise last_error
```

### 4.2 权限管道

**文件**: `ds_harness/core/permissions.py`

**设计来源**: cc-haha 三层门禁 (Deny List → Rule Match → Ask User)

```python
class PermissionPipeline:
    """三层权限管道.
    
    借鉴 cc-haha 的 canUseTool → checkPermissions 链路:
    Layer 1 - DenyList: 硬拒绝危险操作
    Layer 2 - RuleMatch: workspace 路径检查 + 自定义规则
    Layer 3 - AskUser: TUI 弹窗确认
    """
    
    # 借鉴 cc-haha 的危险命令模式
    DENY_PATTERNS = [
        r"rm\s+-rf\s+/",        # 递归删除根目录
        r"sudo\s+",              # 提权
        r"curl\s+.*\|\s*(ba)?sh", # curl pipe shell
        r">\s*/dev/[sh]d[a-z]",  # 覆写磁盘
        r"mkfs\.",               # 格式化
        r"dd\s+if=",             # 磁盘直接操作
        r"chmod\s+777\s+/",      # 权限全开
        r":(){ :|:& };:",        # fork bomb
    ]
    
    async def check(self, tool: ToolInstance, input: dict) -> PermissionResult:
        # Layer 1: Deny List
        deny_result = self._check_deny_list(tool, input)
        if deny_result:
            return deny_result
        
        # Layer 2: Rule Match
        rule_result = await self._check_rules(tool, input)
        if rule_result and rule_result["behavior"] != "ask":
            return rule_result
        
        # Layer 3: Ask User (TUI 弹窗)
        return await self._ask_user(tool, input)
    
    def _check_deny_list(self, tool: ToolInstance, input: dict) -> PermissionResult | None:
        if tool.name == "bash":
            command = input.get("command", "")
            for pattern in self.DENY_PATTERNS:
                if re.search(pattern, command):
                    return PermissionResult(
                        behavior="deny",
                        message=f"Command rejected by safety policy: matches dangerous pattern '{pattern}'"
                    )
        return None
    
    def _check_rules(self, tool: ToolInstance, input: dict) -> PermissionResult | None:
        # 检查路径是否在 workspace 内
        if "path" in input:
            path = Path(input["path"]).resolve()
            workspace = Path(self.config.workspace).resolve()
            if not str(path).startswith(str(workspace)):
                return PermissionResult(
                    behavior="ask",
                    message=f"Path '{path}' is outside workspace '{workspace}'"
                )
        return None
    
    async def _ask_user(self, tool: ToolInstance, input: dict) -> PermissionResult:
        """v0.2: TUI 弹窗确认"""
        # 使用 prompt_toolkit 的 dialog 模式
        # 返回 PermissionResult(behavior="allow"|"deny")
        ...
```

### 4.3 执行日志

**文件**: `ds_harness/core/execution_logger.py`

```python
class ExecutionLogger:
    """执行日志系统.
    
    每次执行完整事件时间线 → JSON 持久化
    借鉴 cc-haha 的 transcript 机制
    """
    
    def __init__(self, report_dir: str):
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []
        self.start_time: float = 0
    
    def record(self, event: Event):
        self.events.append({
            "type": event.type.value,
            "data": event.data,
            "timestamp": event.timestamp,
            "session_id": event.session_id,
            "turn": event.turn_number,
        })
    
    async def flush(self, session_id: str):
        """持久化到 JSON 文件"""
        report = {
            "session_id": session_id,
            "start_time": self.start_time,
            "end_time": datetime.now().timestamp(),
            "duration_ms": (datetime.now().timestamp() - self.start_time) * 1000,
            "event_count": len(self.events),
            "events": self.events,
        }
        filepath = self.report_dir / f"{session_id}.json"
        async with aiofiles.open(filepath, "w") as f:
            await f.write(json.dumps(report, indent=2, ensure_ascii=False))
```

---

### 4.9 评测内置 (已取消 — 转交 FlowForge)

> **2026-07-06 决策**：评测内置功能不再在 Kun 中实现，交由 FlowForge 负责。
> FlowForge 已有成熟的评估引擎（成功率/延迟/成本/工具有效率四维评分），
> Kun 聚焦 Agent 运行时，FlowForge 聚焦评估——各司其职。

### 5.1 设计目标

这是本项目最核心的差异化：**每次执行完自动出过程报告**。

### 5.2 评测引擎

**文件**: `ds_harness/evaluation/metrics.py`, `ds_harness/evaluation/reporter.py`

**设计来源**:
- FlowForge 评估引擎: 成功率 / 延迟 / 成本 / 工具效率 四维评分
- GEMMAS 论文: IDS (Intervention Detection Score) / UPR (Unnecessary Permission Request)
- SWE-bench: 真实任务而非选择题

```python
@dataclass
class ExecutionMetrics:
    """执行指标"""
    # 基础指标
    success: bool
    total_tokens: dict  # {"input": N, "output": N, "thinking": N}
    tool_calls: int
    end_to_end_latency_ms: float
    
    # 过程指标 (v0.3)
    step_timeline: list[StepMetric]        # 每步时间线
    tool_efficiency: float                  # 工具调用有效率 (被后续引用/总调用)
    think_ratio: float                      # thinking tokens / total tokens
    permission_requests: int                # 权限请求次数
    user_interactions: int                  # 用户交互次数
    
    # 成本指标 (v0.4)
    cost_usd: float                         # 总花费
    cost_per_task: float                    # 每任务花费

@dataclass
class StepMetric:
    """每步指标"""
    step_number: int
    event_type: str                         # llm_call | tool_exec | permission_check
    duration_ms: float
    tokens: dict | None
    tool_name: str | None
    tool_was_referenced: bool               # 结果是否被后续引用
    is_redundant: bool                      # 是否冗余调用

class MetricsCalculator:
    """指标计算器.
    
    借鉴 FlowForge 四维评分:
    1. 成功率: 任务是否完成
    2. 延迟: 端到端 + 每步时间
    3. 成本: 总 Token + 费用
    4. 工具效率: 有效调用 / 总调用
    """
    
    def calculate(self, events: list[dict], messages: list[Message]) -> ExecutionMetrics:
        # 1. 成功率: 最后一个 assistant message 是否为正常结束
        success = self._check_success(messages)
        
        # 2. Token 统计
        total_tokens = self._count_tokens(events)
        
        # 3. 工具调用次数
        tool_calls = sum(1 for e in events if e["type"] == "tool_use")
        
        # 4. 端到端延迟
        latency = (events[-1]["timestamp"] - events[0]["timestamp"]) * 1000
        
        # 5. 工具效率 (冗余检测)
        tool_efficiency = self._calculate_tool_efficiency(events, messages)
        
        # 6. ThinkBlock 占比
        think_ratio = self._calculate_think_ratio(events)
        
        # 7. 每步时间线
        timeline = self._build_timeline(events)
        
        return ExecutionMetrics(
            success=success,
            total_tokens=total_tokens,
            tool_calls=tool_calls,
            end_to_end_latency_ms=latency,
            step_timeline=timeline,
            tool_efficiency=tool_efficiency,
            think_ratio=think_ratio,
            permission_requests=sum(1 for e in events if e["type"] == "permission_denied"),
            user_interactions=sum(1 for e in events if e["type"] == "turn_start"),
            cost_usd=self._estimate_cost(total_tokens),
            cost_per_task=0.0,
        )
    
    def _calculate_tool_efficiency(self, events: list[dict], messages: list[Message]) -> float:
        """计算工具调用有效率 = 被后续引用的调用数 / 总调用数.
        
        借鉴 GEMMAS IDS 思路: 如果 tool_use 的结果在后续消息中被引用，
        则该工具调用是有效的。
        """
        tool_ids = [e["data"]["id"] for e in events if e["type"] == "tool_use"]
        referenced_ids = set()
        
        for msg in messages:
            content_str = str(msg.content)
            for tid in tool_ids:
                if tid in content_str:
                    referenced_ids.add(tid)
        
        if not tool_ids:
            return 1.0
        return len(referenced_ids) / len(tool_ids)
```

### 5.3 评测报告生成

```python
class ReportGenerator:
    """评测报告生成器.
    
    每次执行完自动:
    1. 终端打印摘要 (彩色表格)
    2. JSON 完整报告持久化
    """
    
    def generate_terminal(self, metrics: ExecutionMetrics) -> str:
        """终端彩色摘要表格"""
        from rich.table import Table
        from rich.console import Console
        
        table = Table(title="📊 DS-Harness 执行报告")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        
        table.add_row("成功", "✅" if metrics.success else "❌")
        table.add_row("总 Token", f"{metrics.total_tokens['input']:,} in + {metrics.total_tokens['output']:,} out")
        table.add_row("Think Token", f"{metrics.total_tokens.get('thinking', 0):,}")
        table.add_row("工具调用", str(metrics.tool_calls))
        table.add_row("工具有效率", f"{metrics.tool_efficiency:.0%}")
        table.add_row("端到端延迟", f"{metrics.end_to_end_latency_ms:.0f}ms")
        table.add_row("费用", f"${metrics.cost_usd:.4f}")
        
        return table
    
    async def save_json(self, metrics: ExecutionMetrics, session_id: str):
        """持久化完整 JSON 报告"""
        report = {
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "metrics": dataclasses.asdict(metrics),
        }
        path = Path(self.config.report_dir) / f"{session_id}_report.json"
        async with aiofiles.open(path, "w") as f:
            await f.write(json.dumps(report, indent=2, ensure_ascii=False))
```

### 5.4 测试集设计

**文件**: `ds_harness/evaluation/test_suite.py`

```python
# 20 道编码任务
TEST_SUITE = {
    "file_operations": [
        {"task": "在 ~/test_project 下找到所有 Python 文件，统计每个文件的代码行数", "type": "glob+read"},
        # ... 5 题
    ],
    "code_generation": [
        {"task": "写一个 Python 函数，接收 URL 列表，并发下载所有文件", "type": "write"},
        # ... 5 题
    ],
    "code_modification": [
        {"task": "重构 src/utils.py 里的 validate_email 函数，使其支持中文邮箱", "type": "edit"},
        # ... 5 题
    ],
    "debug_fix": [
        {"task": "test_parser.py 的第 42 行测试失败，找出并修复 bug", "type": "read+debug+edit"},
        # ... 5 题
    ],
}
```

---

### 4.10 成本感知路由

### 6.1 三层漏斗路由

**文件**: `ds_harness/routing/funnel.py`

**设计来源**: ecommerce-kg-chat 三层漏斗 (ScriptRouter → Template → LLM)

```python
class CostAwareRouter:
    """成本感知路由.
    
    三层漏斗:
    ① 规则引擎 (ScriptRouter) — 关键词匹配，直接执行，不调 LLM
    ② 轻模型 (V4-Flash) — 简单编码任务
    ③ 重模型 (V4-Pro) — 复杂/需要推理的任务
    """
    
    # 关键词 → 直接执行模板
    DIRECT_PATTERNS = [
        (r"列出.*文件", "glob"),
        (r"统计.*行数", "bash_wc"),
        (r"显示.*内容", "read_file"),
        (r"当前.*时间", "bash_date"),
        (r"查找.*文件", "glob"),
    ]
    
    # 任务复杂度判断规则
    SIMPLE_TASK_PATTERNS = [
        r"^(查看|读取|列出|显示|找到|搜索)",   # 读操作
        r"^(解释|什么是|为什么|怎么样)",         # 问答
        r"^(生成|创建).{1,20}(文件|目录)",        # 简单创建
    ]
    
    COMPLEX_TASK_PATTERNS = [
        r"(重构|优化|修复|调试|修复bug)",        # 需要推理
        r"(设计|架构|实现).{1,30}(系统|模块|API)", # 架构设计
        r"(多.*文件|批量|递归)",                   # 多文件操作
        r"(分析|审查|审查代码)",                   # 代码分析
    ]
    
    async def route(self, prompt: str) -> RouteDecision:
        """决定使用哪个模型/策略处理请求"""
        
        # Layer 1: 规则引擎 — 简单操作直接执行
        for pattern, action in self.DIRECT_PATTERNS:
            if re.search(pattern, prompt):
                return RouteDecision(
                    handler="direct",
                    action=action,
                    model="none",
                    estimated_cost=0.0
                )
        
        # Layer 2 vs 3: 轻模型 vs 重模型
        for pattern in self.COMPLEX_TASK_PATTERNS:
            if re.search(pattern, prompt):
                return RouteDecision(
                    handler="llm",
                    model="deepseek-v4-pro",
                    estimated_cost=self._estimate_cost(prompt, "pro")
                )
        
        return RouteDecision(
            handler="llm",
            model="deepseek-v4-flash",
            estimated_cost=self._estimate_cost(prompt, "flash")
        )
```

### 6.2 Token 预算管理器

**文件**: `ds_harness/routing/budget.py`

```python
class TokenBudgetManager:
    """Token 预算管理器.
    
    借鉴 cc-haha 的 maxBudgetUsd + taskBudget:
    - 每日限额 (daily_budget)
    - 每任务预算 (task_budget)
    - 超出预算自动降级到轻模型
    """
    
    def __init__(self, daily_budget_usd: float = 20.0, task_budget_usd: float = 5.0):
        self.daily_budget = daily_budget_usd
        self.task_budget = task_budget_usd
        self.daily_spent = 0.0
        self.task_spent = 0.0
    
    def can_use_model(self, model: str) -> bool:
        """检查是否还有预算使用指定模型"""
        if model == "deepseek-v4-pro":
            return self.task_spent < self.task_budget and self.daily_spent < self.daily_budget
        # flash 模型始终可用
        return True
    
    def track_usage(self, tokens: dict, model: str):
        """追踪 Token 使用"""
        cost = self._calculate_cost(tokens, model)
        self.task_spent += cost
        self.daily_spent += cost
    
    def should_downgrade(self) -> bool:
        """是否应该降级到轻模型"""
        return self.task_spent > self.task_budget * 0.8  # 用了 80% 预算
```

### 6.3 工具结果缓存

**文件**: `ds_harness/routing/cache.py`

```python
class ToolResultCache:
    """工具结果缓存.
    
    - 相同工具 + 相同参数 → 复用结果
    - TTL 按工具类型区分:
      - glob: 60s (文件列表变化慢)
      - read_file: 30s (内容可能变化)
      - bash: 0s (不缓存，副作用不可预测)
    """
    
    TTL_MAP = {
        "glob": 60,
        "read_file": 30,
        "bash": 0,
    }
    
    def __init__(self):
        self._cache: dict[str, tuple[Any, float]] = {}
    
    def get(self, tool_name: str, args: dict) -> Any | None:
        key = self._make_key(tool_name, args)
        if key in self._cache:
            result, timestamp = self._cache[key]
            ttl = self.TTL_MAP.get(tool_name, 30)
            if time.time() - timestamp < ttl:
                return result
            else:
                del self._cache[key]
        return None
    
    def set(self, tool_name: str, args: dict, result: Any):
        key = self._make_key(tool_name, args)
        self._cache[key] = (result, time.time())
```

---

### 4.11 记忆系统

### 7.1 设计目标

Agent 记住你的项目结构、编码偏好、常用命令。

### 7.2 文件级记忆 + FTS5

**文件**: `ds_harness/memory/manager.py`, `ds_harness/memory/storage.py`

**设计来源**:
- **cc-haha memory system** (`src/memdir/`): 文件级 `.memory/*.md` + YAML frontmatter + MEMORY.md 索引
- **Hermes Memory Provider**: 可插拔接口 + FTS5 全文搜索

**架构**:

```
.ds-harness/
├── memory/
│   ├── MEMORY.md              # 索引文件 (借鉴 cc-haha ENTRYPOINT_NAME)
│   ├── user-prefers-pytest.md # 用户偏好记忆
│   ├── project-structure.md   # 项目结构记忆
│   └── common-commands.md     # 常用命令记忆
├── memory.db                  # SQLite FTS5 (可选，借鉴 Hermes)
└── config.yaml
```

```python
# ─── 记忆文件格式 (借鉴 cc-haha) ──────────

# 每个记忆文件 (MEMORY_ENTRY_FORMAT)
"""
---
name: user-prefers-pytest
description: User prefers pytest for testing Python projects
metadata:
  type: user | project | reference | feedback
  importance: high | medium | low
  created: 2026-07-06T10:00:00Z
  updated: 2026-07-06T10:00:00Z
---

The user prefers pytest over unittest for all Python projects.
Use `pytest` command for running tests.

**Why:** User's coding preference, discovered from conversation.
**How to apply:** Always suggest pytest commands instead of unittest.
"""

# MEMORY.md 索引文件 (借鉴 cc-haha)
"""
# Project Memory Index

- [User Prefers Pytest](user-prefers-pytest.md) — coding preference
- [Project Structure](project-structure.md) — project layout reference
- [Common Commands](common-commands.md) — frequently used shell commands
"""
```

```python
class MemoryManager:
    """记忆管理器.
    
    借鉴 cc-haha memdir 的文件级架构:
    - MEMORY.md 索引文件
    - 每个记忆一个 MD 文件 (YAML frontmatter)
    - LLM 根据 name+description 选 ≤5 条注入
    
    借鉴 Hermes 的 FTS5 扩展:
    - 可选 SQLite FTS5 全文搜索
    - jieba 中文分词
    - 混合排序 (BM25 + 时间衰减)
    """
    
    def __init__(self, memory_dir: str, use_fts5: bool = False):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.use_fts5 = use_fts5
        if use_fts5:
            self.fts5 = FTS5Index(self.memory_dir / "memory.db")
    
    def load_relevant(self, prompt: str, max_count: int = 5) -> list[Memory]:
        """加载相关记忆 (注入 System Prompt)"""
        if self.use_fts5:
            # FTS5 语义搜索
            return self.fts5.search(prompt, limit=max_count)
        else:
            # 基于 MEMORY.md 索引的关键词匹配
            return self._keyword_match(prompt, max_count)
    
    async def extract_memories(self, conversation: list[Message]):
        """从对话中提取新记忆 (对话结束后异步执行).
        
        借鉴 cc-haha: LLM 分析对话，提取用户偏好/项目事实
        """
        # 使用轻模型做记忆提取 (降低成本)
        extraction_prompt = """从以下对话中提取用户的编码偏好、项目事实或常用命令。
        
        对于每条提取的内容:
        1. 判断是否有现有的记忆需要更新 (而非创建新文件)
        2. 只提取新的、非显而易见的信息
        3. 忽略临时性的、单次的指令
        
        格式: YAML frontmatter + Markdown body
        """
        # ... LLM 调用 → 写入新记忆文件
    
    def _keyword_match(self, prompt: str, max_count: int) -> list[Memory]:
        """基于 MEMORY.md 索引的关键词匹配"""
        index = self._read_index()
        scored = []
        for entry in index:
            score = self._relevance_score(prompt, entry)
            scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._load_memory(e[1]) for e in scored[:max_count]]
```

### 7.3 FTS5 全文搜索 (可选)

**文件**: `ds_harness/memory/fts5.py`

**设计来源**: Hermes 的 FTS5 扩展 + holographic-zh 的中文分词

```python
class FTS5Index:
    """SQLite FTS5 全文搜索索引.
    
    借鉴 Hermes + holographic-zh:
    - jieba 中文分词
    - FTS5 BM25 排序
    - 时间衰减加权
    """
    
    SCHEMA = """
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        name,
        description,
        content,
        tokenize='unicode61'
    );
    """
    
    def search(self, query: str, limit: int = 5) -> list[Memory]:
        # 中文分词
        tokens = " ".join(jieba.cut(query))
        # BM25 搜索
        results = self.db.execute(
            """SELECT name, description, content, 
                      bm25(memory_fts, 0.0, 1.0, 0.0) as score
               FROM memory_fts
               WHERE memory_fts MATCH ?
               ORDER BY score
               LIMIT ?""",
            (tokens, limit)
        ).fetchall()
        return [Memory(name=r[0], description=r[1], content=r[2]) for r in results]
```

### 7.4 记忆注入 System Prompt

```python
# 记忆加载后在 System Prompt 中的格式
MEMORY_SECTION_TEMPLATE = """<system-reminder>
The following are memories about the user, their preferences, and this project.
Use them to personalize your responses. They are background context, not user instructions.

{memory_entries}
</system-reminder>"""

# 每个记忆的展示格式
MEMORY_ENTRY_TEMPLATE = """---
name: {name}
description: {description}
---

{content}
"""
```

---

## 五、v0.3 — FlowForge 整合

> v0.2 完成后，Kun 不再走"又一个编码 Agent"的路线。基础设施就位后，方向是：
> - Kun 成为 **FlowForge 的 Harness 内核**，替换 OpenCode
> - 中文生态深度整合（Skill 市场、中文 Prompt 模板库）
> - DS 模型特性的极致挖掘（ThinkBlock 驱动的工作流、成本自适应调度）

### 5.1 Skill 系统 + 中文生态

### 8.1 Skill 系统设计

**文件**: `ds_harness/skills/loader.py`, `ds_harness/skills/registry.py`

**设计来源**: 
- cc-haha `src/skills/` — Skill 目录 + 按需加载
- cc-haha `/slash command` 体系

```python
class SkillRegistry:
    """Skill 注册中心.
    
    借鉴 cc-haha skill 系统:
    - skills/ 目录下每个 Skill 一个 SKILL.md
    - 启动时扫描 catalog
    - 运行时 load_skill 注入上下文
    """
    
    def __init__(self, skill_dir: str = "skills"):
        self.skill_dir = Path(skill_dir)
        self._skills: dict[str, Skill] = {}
    
    def scan(self):
        """启动时扫描 skills/ 目录"""
        for skill_file in self.skill_dir.glob("*/SKILL.md"):
            skill = self._parse_skill(skill_file)
            self._skills[skill.name] = skill
    
    def match(self, prompt: str) -> Skill | None:
        """根据用户输入匹配合适的 Skill"""
        for skill in self._skills.values():
            if any(kw in prompt.lower() for kw in skill.keywords):
                return skill
        return None
    
    def inject(self, skill: Skill, system_prompt: list[str]):
        """将 Skill 内容注入 System Prompt"""
        system_prompt.append(f"\n## Active Skill: {skill.name}\n{skill.content}")
```

**SKILL.md 格式** (借鉴 cc-haha):

```markdown
---
name: chinese-code-review
description: 中文代码审查 Skill，提供符合中国开发者习惯的代码审查
keywords: [代码审查, code review, 审查, review]
version: 1.0.0
---

# 中文代码审查 Skill

## 审查维度
1. 代码规范: 是否符合 PEP8 / ESLint 规范
2. 逻辑正确性: 边界条件、异常处理
3. 性能: 算法复杂度、数据库查询效率
4. 安全性: SQL 注入、XSS、敏感信息泄露

## 审查报告格式
### 发现 {N} 个问题
- 🔴 严重: ...
- 🟡 建议: ...
- 🟢 优化: ...
```

### 8.2 中文优先生态

```python
# 中文 System Prompt 模板
ZH_SYSTEM_PROMPT = """你是 DS-Harness，一个面向 DeepSeek 模型的编码 Agent。

## 你的能力
- 读写文件、执行 Shell 命令、搜索代码
- 理解项目结构，提供符合项目规范的代码

## 工作原则
1. **先理解再动手**：修改代码前先阅读相关文件
2. **最小改动**：只改需要改的，不动无关代码
3. **主动验证**：改完代码后运行测试确认
4. **中文回复**：始终用中文回复用户，代码和命令除外

## ThinkBlock 使用
- 在 `thinking` 块中用中文思考复杂问题
- 思考内容不会被计入对话历史
- 工具调用前说明原因
"""
```

---

## 九、技术选型详解

### 9.1 对比表

| 层 | 本项目选型 | Claude Code (cc-haha) | OpenCode | Hermes |
|------|-----------|----------------------|----------|--------|
| **语言** | **Python 3.12+** | TypeScript + Bun | TypeScript + Bun | Python |
| **Agent Loop** | **自研 while + AsyncGenerator** | while + AsyncGenerator | Effect Stream | while + async |
| **LLM API** | **httpx + Anthropic SDK** | Anthropic SDK | AI SDK (Vercel) | 多 Adapter |
| **状态管理** | **dataclass + TypedDict** | React AppState | Effect Context | 字典 |
| **工具注册** | **装饰器 + Pydantic** | Zod + generics | AI SDK tool() | 函数注册 |
| **记忆** | **文件级 MD + 可选 FTS5** | 文件级 MD + MEMORY.md | SQLite 会话 | Memory Provider |
| **CLI/TUI** | **prompt_toolkit** | Ink/React | Ink/React | Rich |
| **评测** | **自研 EventBus** | 无内置 | 无内置 | 无内置 |
| **流式输出** | **AsyncGenerator[Event]** | AsyncGenerator[SDKMessage] | Stream[LLMEvent] | AsyncIterator |
| **错误处理** | **try/except + 分类重试** | categorizeRetryableAPIError | Effect.retry | try/except |
| **配置** | **YAML + env** | JSON + env | Effect Config | YAML + env |

### 9.2 为什么不选替代方案

| 替代方案 | 不选的原因 |
|---------|-----------|
| LangChain / LangGraph | AgentExecutor 黑盒不可控，LangGraph StateGraph 对单 Agent 太重 |
| TypeScript (学 cc-haha) | Python 生态更适合快速迭代，DS API 的 Python SDK 更成熟 |
| Effect-TS (学 OpenCode) | 学习曲线极陡，本项目不需要函数式 DI |
| AI SDK / Vercel | 与 DSv4 的 Anthropic 兼容接口重复，增加依赖 |
| Qdrant / Pinecone 向量库 | v0.5 之前的记忆系统不需要，SQLite FTS5 足够 |
| 外部评测服务 (LangSmith) | 需要外部依赖，自研更灵活且成本可控 |

---

## 十、目录结构规范

```
ds-harness/
├── pyproject.toml              # 项目配置 + 依赖
├── README.md
├── .env.example                # 环境变量模板
├── .ds-harness/                # 项目级配置 (类似 .claude/)
│   ├── config.yaml             # 项目配置
│   ├── memory/                 # 记忆目录
│   │   ├── MEMORY.md           # 索引文件
│   │   └── *.md                # 记忆文件
│   └── reports/                # 评测报告
│       └── *.json
├── docs/
│   └── 设计方案_DS-Harness.md
├── skills/                     # Skill 目录
│   ├── chinese-code-review/
│   │   └── SKILL.md
│   ├── python-project/
│   │   └── SKILL.md
│   └── git-conventions/
│       └── SKILL.md
├── tests/                      # 测试
│   ├── test_agent_loop.py
│   ├── test_tools.py
│   ├── test_permissions.py
│   └── evaluation/             # 评测测试集
│       └── test_suite.py
└── src/ds_harness/             # 主包
    ├── __init__.py
    ├── main.py                 # 入口: CLI 启动
    ├── cli/                    # CLI/TUI 层
    │   ├── __init__.py
    │   ├── tui.py              # prompt_toolkit TUI
    │   ├── styles.py           # 终端样式
    │   └── input.py            # 输入处理
    ├── core/                   # Harness 内核
    │   ├── __init__.py
    │   ├── agent_loop.py       # Agent Loop 主循环
    │   ├── llm_client.py       # DSv4 LLM 客户端
    │   ├── state.py            # AgentState 定义
    │   ├── events.py           # Event 类型定义
    │   ├── context.py          # 上下文管理器
    │   ├── permissions.py      # 权限管道
    │   ├── error_recovery.py   # 错误恢复
    │   └── execution_logger.py # 执行日志
    ├── tools/                  # 工具层
    │   ├── __init__.py
    │   ├── registry.py         # 工具注册中心
    │   ├── decorators.py       # @tool 装饰器
    │   ├── bash_tool.py        # Shell 命令执行
    │   ├── read_file.py        # 文件读取
    │   ├── write_file.py       # 文件写入
    │   ├── glob_tool.py        # 文件搜索
    │   ├── edit_tool.py        # 文件编辑 (v0.2+)
    │   └── mcp/                # MCP 扩展 (v0.4+)
    │       └── mcp_client.py
    ├── routing/                # 路由层 (v0.4)
    │   ├── __init__.py
    │   ├── funnel.py           # 三层漏斗
    │   ├── budget.py           # Token 预算
    │   └── cache.py            # 结果缓存
    ├── memory/                 # 记忆层 (v0.5)
    │   ├── __init__.py
    │   ├── manager.py          # 记忆管理器
    │   ├── storage.py          # 文件存储
    │   ├── fts5.py             # FTS5 索引
    │   └── extractor.py        # 记忆提取
    ├── skills/                 # Skill 系统 (v0.6)
    │   ├── __init__.py
    │   ├── loader.py           # Skill 加载
    │   └── registry.py         # Skill 注册
    └── evaluation/             # 评测层 (v0.3)
        ├── __init__.py
        ├── metrics.py          # 指标计算
        ├── reporter.py         # 报告生成
        ├── event_bus.py        # 事件总线
        └── test_suite.py       # 测试集
```

---

## 十一、关键接口定义

### 11.1 Agent Loop 对外接口

```python
# 使用示例
async def main():
    config = HarnessConfig(
        model="deepseek-v4-pro",
        workspace="~/my-project",
        max_turns=50,
    )
    
    agent = AgentLoop(config)
    
    async for event in agent.run("找到所有 Python 文件并统计代码行数"):
        match event.type:
            case EventType.CONTENT_BLOCK_DELTA:
                print(event.data["text"], end="", flush=True)
            case EventType.TOOL_USE:
                print(f"\n🔧 {event.data['name']}: {event.data['input']}")
            case EventType.TOOL_RESULT:
                print(f"📋 Result: {event.data['data'][:100]}...")
            case EventType.ERROR:
                print(f"❌ Error: {event.data['error']}")
```

### 11.2 工具定义接口

```python
# 定义输入模型
class ReadFileInput(BaseModel):
    path: str = Field(description="Absolute path to the file")
    offset: int = Field(default=0, description="Line number to start reading from")
    limit: int = Field(default=2000, description="Maximum lines to read")

# 定义工具
@tool(
    name="read_file",
    description="Read a file from the local filesystem. Returns line-numbered content.",
    permission="read",
    input_model=ReadFileInput
)
async def read_file(args: ReadFileInput, ctx: ToolUseContext) -> ToolResult:
    file_path = Path(args.path)
    if not file_path.exists():
        return ToolResult(data=f"File not found: {args.path}", is_error=True)
    
    async with aiofiles.open(file_path, 'r') as f:
        content = await f.read()
    
    lines = content.split('\n')
    start = args.offset
    end = min(start + args.limit, len(lines))
    result = '\n'.join(f"{i+1}\t{line}" for i, line in enumerate(lines[start:end], start))
    
    return ToolResult(data=result)
```

### 11.3 评测接口

```python
# 使用 EventBus 收集
event_bus = EventBus()
agent = AgentLoop(config, event_bus=event_bus)

# 执行任务
async for event in agent.run(task):
    pass  # CLI 渲染

# 自动出报告
metrics = MetricsCalculator().calculate(event_bus.drain(), agent.state.messages)
reporter = ReportGenerator(config)
print(reporter.generate_terminal(metrics))
await reporter.save_json(metrics, agent.state.session_id)
```

---

## 十二、风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| DSv4 Anthropic 兼容接口不完全 | 中 | 高 | 抽象 Provider 层，可切换原生 API |
| ThinkBlock 格式变化 | 中 | 中 | 解析层独立，易于适配 |
| prompt_toolkit 性能 (大量输出) | 低 | 中 | 虚拟滚动 + 输出节流 |
| 中文 System Prompt 效果差 | 中 | 高 | A/B 测试中英 Prompt，选择更优方案 |
| FTS5 中文分词不准 | 中 | 中 | jieba 分词 + 用户反馈优化词典 |
| 成本估算不准 | 高 | 低 | 持续校准模型 (以实际 API 返回的 usage 为准) |

---

## 附录 A: 各版本文件清单

### v0.1 MVP (最小 15 个源文件)

```
src/ds_harness/
├── main.py                    # CLI 入口
├── cli/tui.py                 # 终端 UI
├── core/
│   ├── agent_loop.py          # 主循环 ⭐
│   ├── llm_client.py          # LLM 客户端 ⭐
│   ├── state.py               # 状态定义
│   ├── events.py              # 事件类型
│   └── context.py             # 上下文管理
├── tools/
│   ├── registry.py            # 工具注册
│   ├── decorators.py          # @tool 装饰器
│   ├── bash_tool.py           # bash
│   ├── read_file.py           # read_file
│   ├── write_file.py          # write_file
│   └── glob_tool.py           # glob
```

### v0.2 基础设施 (新增 6 个文件 — 合并原 v0.2~v0.5) ✅

```
src/kun/core/
├── error_recovery.py          # 错误分类 + 指数退避重试
├── permission.py              # 权限管道 (Deny list + workspace + ask)
├── execution_log.py           # 事件日志 JSON 持久化
src/kun/routing/
├── __init__.py
└── cost_router.py             # 三层漏斗路由 + Token 预算
src/kun/memory/
├── __init__.py
└── manager.py                 # 文件级记忆管理 + 关键词检索
```

### v0.3 FlowForge 整合 (计划中)

```
src/kun/skills/
├── loader.py                  # Skill 加载
└── registry.py                # Skill 注册
skills/                        # Skill 目录
├── chinese-code-review/SKILL.md
├── python-project/SKILL.md
└── git-conventions/SKILL.md
```

### v0.4 TUI + GUI 界面 (计划中)

**目标**：把 TUI 界面做好看，开发 GUI 桌面应用。

| 子版本 | 内容 |
|--------|------|
| v0.4.1 | prompt_toolkit 全功能 TUI：语法高亮输入、自动补全、多行编辑、ThinkBlock 动画 |
| v0.4.2 | Rich 主题系统：暗色/亮色切换、自定义配色、状态栏组件 |
| v0.4.3 | GUI 桌面应用：技术选型待调研（Electron/PyQt/Web UI），设计风格先查 Skill 市场 |

**设计原则**：
- 先做 TUI，再做 GUI
- 设计风格统一，形成 Kun 的品牌识别
- 优先调研现有 Skill/模板，避免从零设计

---

---

## DS 专属创新路线图

> v0.1-v0.32 完成了跨框架架构抽象（对齐 Claude Code/Hermes/OpenCode 的 memory + skill 系统），v0.4 起聚焦 DS 模型特性驱动的原创设计。

### 创新总览

```
                    ┌─────────────────────────────────────┐
                    │    DS 模型独有特性 → Kun 专属能力      │
                    └─────────────────────────────────────┘

DSv4 ThinkBlock  ──→  思考质量分析 (Thinking Quality Score)
DSv4 Prompt 特性  ──→  Prompt 粒度编译器 (Pro vs Flash vs R1)
GRPO 训练范式    ──→  GRPO 式多版本生成 → LLM-as-Judge 择优
MLA KV Cache     ──→  MLA 感知上下文管理 (宽松压缩策略)
中文生态需求     ──→  中文优先 Skill 生态
```

---

### 创新 1: ThinkBlock 过程评测（最独特）

**为什么 Claude Code/Hermes 做不了**：Claude API 不返回内部推理过程，Hermes 跨模型适配没有专属 ThinkBlock 解析。只有 DSv4 的 `reasoning_content` 字段暴露了模型的"内心独白"。

**Kun 的利用方式**：

```
每次 Agent 执行结束:
  1. 提取所有 ThinkBlock 内容 (已在 llm_client.py 中解析)
  2. 分析维度:
     - 自我纠错行为: "wait, let me reconsider" → 模型在修正自己
     - 工具调用推理: 调工具前的思考 vs 工具结果 → 是否匹配
     - 犹豫信号: "maybe..." / "不确定" → 推理质量预警
     - 推理深度: thinking 字数 / 总输出字数的比例
  3. 产出 Thinking Quality Score (0-100)
  4. 写入执行报告 → 每次执行完自动展示
```

**面试话术**：

> "Claude Code 和 Hermes 都看不到模型的思考过程。但 DSv4 有 ThinkBlock——我利用它在 Kun 里做了一套思考质量分析。Agent 调用工具前的推理是否充分、有没有自我纠错行为、有没有反复犹豫——这些在最终输出里看不到的信号，在 thinking 里全部可见。我把它们量化成了过程评测指标。"

---

### 创新 2: Prompt 粒度编译器

**为什么需要**：FlowForge 的实战经验——同一个任务，中文 prompt 比英文 prompt 稳定性差，因为 DSv4 需要更直白的边界定义。不同 DS 模型（Pro/Flash/R1）有不同的指令遵循特性，不能用同一套 prompt 糊弄。

**Kun 的实现**：

```
同一任务 → 根据目标模型自动选择 Prompt 粒度:

  V4-Pro → 简洁版 System Prompt
    原因: 推理能力强，给方向就行，过度约束反而限制发挥
    示例: "翻译游戏文本，注意保留代码标识符"

  V4-Flash → 详尽版 System Prompt
    原因: 轻量模型，需要把边界列清楚
    示例: "翻译规则: 1) 只翻译UI文本 2) 不动变量名 3) 不动JSON key
           4) 不动文件路径 5) 不确定时保持原文"

  R1 → 推理版 System Prompt
    原因: R1 擅长拆解复杂问题，把任务分解给它
    示例: "第一步: 扫描所有文本文件 第二步: 区分UI文本和代码标识符
           第三步: 只翻译UI文本 第四步: 验证没有改动代码"
```

**对用户透明**：一个配置项 `prompt_granularity: "auto" | "concise" | "detailed" | "reasoning"`。

---

### 创新 3: GRPO 式多版本生成

**为什么和 GRPO 对应**：GRPO 的训练范式是"生成多个回答 → 组内比较 → 择优学习"。Kun 在推理阶段复刻这个循环——不是训练模型，而是用同样的"多版本择优"思路提高单次推理质量。

**Kun 的实现**：

```
用户任务
  ↓
同时生成 3 条执行路径 (利用 DSv4 的低成本):
  路径A: 直接执行
  路径B: 先搜索 → 再执行
  路径C: 先分析 → 再执行
  ↓
LLM-as-Judge 评审三条结果:
  评分维度: 正确性 / 效率 / 安全性 / 可维护性
  ↓
返回最佳方案 + "为什么选这个"的理由
被淘汰方案的思路也可供用户参考
```

**成本控制**：只在复杂任务时触发（关键词检测到"实现""设计""重构"等），简单查询不触发。

---

### 创新 4: MLA 感知上下文管理

**为什么不同**：Claude Code 的上下文压缩是四层激进策略（budget→snip→micro→LLM summary），因为 Claude API 贵。DSv4 的 MLA 让 KV Cache 显存降低 93.3%，长上下文成本大幅低于 Claude——所以压缩策略可以更宽容。

**Kun 的实现**：

```
根据模型自适应压缩阈值:

  Claude 模式 (默认):
    窗口 > 80% 满 → L1 snip → L2 micro → L3 budget → L4 LLM summary

  DSv4 模式 (MLA 感知):
    窗口 > 95% 满 → 才触发 L1 snip
    L2-L4 阈值同步上调
    原因: MLA 让长上下文不贵，宁可多保留一些对话连贯性
```

**效果**：同样 64K 上下文，DSv4 模式下的对话连贯性更好——因为更少触发压缩。

---

### 创新 5: 中文优先 Skill 生态

**为什么不是翻译英文 Skill**：中文编码场景的代码审查标准、Git 提交规范、API 文档格式与英文不同。直接翻译 Claude Code 的 Skill 会产生水土不服——就像 FlowForge 的中文 prompt 测试中，节点协作稳定性远差于英文一样。

**Kun 的首批中文 Skill**：

| Skill | 中文场景独特性 |
|-------|-------------|
| `zh-code-review` | 中文变量命名规范、注释审查、国内常见的代码风格问题 |
| `zh-api-docs` | 中文 API 文档生成标准、中英文混排格式 |
| `zh-git-commit` | 中文提交信息规范、中文 changelog 生成 |
| `zh-poetry-translate` | 代码中的中文文案翻译（不误改代码标识符——源自游戏汉化教训） |

---

### 创新对照表

| 创新 | 依赖的 DS 特性 | Claude Code 能做吗 | Hermes 能做吗 |
|------|-------------|:--:|:--:|
| ThinkBlock 过程评测 | DSv4 reasoning_content | ❌ 无此字段 | ❌ 跨模型无专属解析 |
| Prompt 粒度编译器 | DS Pro/Flash/R1 指令遵循差异 | ❌ 只有 Claude 一家 | ❌ 跨模型但不精细 |
| GRPO 式多版本生成 | DSv4 低成本 + GRPO 训练范式 | ❌ API 成本太高 | ❌ 无 GRPO 概念 |
| MLA 感知上下文 | MLA KV Cache 压缩 | ❌ 不是 MLA | ❌ 无 MLA 感知 |
| 中文 Skill 生态 | 中文场景需求 | ❌ 英文优先 | ❌ 英文为主 |

---

## 附录 B: 参考资料

- **Claude Code (cc-haha)**: [e:\agentProject\DS-harness\cc-haha\](e:\agentProject\DS-harness\cc-haha\)
  - 核心文件: `src/QueryEngine.ts`, `src/query.ts`, `src/Tool.ts`, `src/services/api/claude.ts`, `src/memdir/`
- **OpenCode**: [E:\agentProject\opencode\](E:\agentProject\opencode\)
  - 核心文件: `packages/opencode/src/session/prompt.ts`, `packages/opencode/src/session/processor.ts`, `packages/opencode/src/agent/agent.ts`
- **Hermes Agent**: [E:\agentProject\hermes-agent\](E:\agentProject\hermes-agent\)
  - 核心文件: `run_agent.py`, `agent/conversation_loop.py`, `agent/memory_manager.py`, `agent/memory_provider.py`, `agent/context_engine.py`, `agent/tool_executor.py`, `agent/tool_dispatch_helpers.py`, `tools/registry.py`, `providers/base.py`, `hermes_state.py`
- **InsightAgent**: [e:\agentProject\InsightAgent\src\](e:\agentProject\InsightAgent\src\)
  - `llm_utils.py`: ThinkBlock 解析参考
- **FlowForge**: 工具装饰器注册 + 评估引擎设计
