# Perlica（As-Built 交互优先 CLI Agent）

Perlica 是一个面向终端的本地 Agent Runtime，默认以交互会话为主入口。  
Perlica is a local terminal-first agent runtime, with interactive chat as the default entrypoint.

当前文档以仓库最新实现为准（As-Built），命令与行为和 `src/perlica` 代码一致。  
This README is As-Built and aligned with the current implementation in `src/perlica`.

## 快速开始（Quick Start）

1. 安装当前项目（Install from current workspace）

```bash
python3 -m pip install -e /Users/anchorcat/Desktop/Perlica-Agent
```

2. 确认 Claude CLI 可用并已登录（Check Claude CLI is available and authenticated）

```bash
claude --version
claude -p "你好" --output-format json --max-turns 1
```

3. 确认 OpenCode ACP 可用（Check OpenCode ACP is available）

```bash
opencode --version
opencode acp --help
```

4. 在项目目录初始化配置（Initialize project config）

```bash
perlica init
```

5. 启动交互会话（Start interactive chat）

```bash
perlica
```

6. 启动前台手机桥接服务（Start foreground mobile bridge）

```bash
perlica --service
```

## 入口与运行模式（Entrypoints & Modes）

- `perlica [--provider claude|opencode]`：进入交互聊天模式。  
  Default interactive chat mode.
- `perlica chat [--provider claude|opencode]`：显式进入交互聊天模式。  
  Explicit interactive chat mode.
- `perlica run "..." [--provider claude|opencode]`：单轮执行后退出。  
  Run one turn and exit.
- `perlica --service [--provider claude|opencode]`：进入服务模式（手机桥接 TUI）。  
  Service bridge TUI mode.
- `perlica --help`：仅显示帮助，不进入聊天。  
  Show help only.
- 非 TTY 且无子命令时：读取 stdin 执行单轮后退出。  
  In non-TTY without subcommand, reads stdin for one-shot execution.

首次 provider 选择（First Provider Selection）：

1. 首次在 TTY 启动时会提示选择默认 provider（`claude` 或 `opencode`），并写入配置。  
   First TTY startup asks you to choose default provider and persists it.
2. 首次在非 TTY 启动时，如果未显式传 `--provider`，会直接报错并退出。  
   On first non-TTY startup, `--provider` is required.

## 交互模式（Interactive Chat）

`perlica` / `perlica chat` 使用 Textual 三段式界面：  
`perlica` / `perlica chat` runs a three-panel Textual UI.

- 顶栏（Status Bar）：`model | session | context | phase`
- 中间（Chat Log）：用户、助手、系统消息面板
- 底部（Input）：自然语言输入与 Slash 命令共用

### 快捷键（Hotkeys）

- `Enter` / `Ctrl+S`：发送（Submit）
- `Shift+Enter` / `Ctrl+J` / `Ctrl+N` / `Alt+Enter` / `Ctrl+Enter`：换行（Newline）
- `Ctrl+C`：请求取消当前生成（Cancel current generation display）
- `Ctrl+D`：退出（Exit）
- `Ctrl+L`：清屏（Clear chat log）

## Slash 命令（Slash Commands）

交互和 service 模式共用同一命令层（`repl_commands.py`）。  
Interactive and service mode share the same slash command layer.

核心命令（Core commands）：

- `/help`
- `/clear`
- `/pending`
- `/choose <index|text...>`
- `/exit` 或 `/quit`
- `/save [name]`
- `/discard`
- `/session list`
- `/session list --all`
- `/session new --name demo`
- `/session use <session_ref>`
- `/session current`
- `/session delete <session_ref>`
- `/doctor --format text`
- `/mcp list`
- `/mcp reload`
- `/mcp status`
- `/skill list`
- `/skill reload`
- `/policy approvals list`

说明（Notes）：

- 未识别的 `/xxx` 会回退为普通消息发送给模型。  
  Unknown slash commands fall back to model input.
- `/clear` 只清空当前会话消息与摘要，不删除会话本身。  
  `/clear` clears messages/summaries only, keeping the session record.
- `/session delete <session_ref>` 仅允许删除“非当前会话”；当前会话会被拒绝删除。
  `/session delete <session_ref>` only deletes non-current sessions; deleting current session is rejected.
- 当模型发起交互确认时，`/pending` 可查看当前待确认问题。  
  When model asks for interaction confirmation, `/pending` shows the active pending request.
- `/choose 1` 选择第 1 个选项，`/choose 任意文本` 提交自定义回答。  
  `/choose 1` selects option 1, and `/choose <free text>` submits custom input.

## 交互确认/选项选择（Interaction Confirmation & Choices）

Perlica 支持 ACP 交互确认子协议，模型可在同一轮中请求用户决策。  
Perlica supports ACP interaction confirmation so the model can request user decisions in the same run.

行为规则（Behavior）：

1. 模型发起交互请求后，界面会显示问题与选项（编号）。  
   UI shows pending question and numbered options.
2. 你可以直接输入编号（如 `1`）选择，也可以输入自定义文本。  
   You can enter a number (`1`) or free-form text.
3. 在 pending 存在时，非 slash 输入默认作为本次交互回答。  
   While pending exists, non-slash input is treated as interaction answer by default.
4. service 模式支持远端（手机）回答，采用“先到先得”。  
   Service mode supports remote (phone) answers with first-valid-answer-wins.
5. 回答提交后会继续等待同一轮模型最终响应，不会启动第二次 provider 主调用。  
   After reply submission, Perlica continues waiting for final output in the same provider call.

### 串行任务模型（Single Active Task）

1. 每条用户输入都是一个任务（task），同一时刻只允许一个活动任务。  
   Each user input is one task; only one active task is allowed at a time.
2. provider 在任务内发起的多轮确认属于同一任务，回答不算新指令。  
   Multi-round provider confirmations stay in the same task, not new commands.
3. 上一任务未完成时：聊天模式会拒绝新普通输入；service 模式会将新消息排队。  
   While a task is active: chat rejects new normal input, service defers new messages.
4. 可观测事件：`task.started`、`task.state.changed`、`task.command.deferred`、`task.command.rejected`。  
   Check these events in debug logs for task-level diagnosis.

Claude Code 兼容说明（Claude AskUserQuestion compatibility）：

1. 当 `claude -p` 返回 `permission_denials.tool_name=AskUserQuestion` 时，Perlica 会把问题映射为 pending 交互并展示选项。  
   When Claude returns `permission_denials.tool_name=AskUserQuestion`, Perlica maps it to pending interaction options.
2. 你可直接输入编号或自由文本回答，Perlica 会把回答加入后续轮次上下文并继续执行。  
   You can answer with an index or free text; Perlica appends answers to follow-up context and continues.
3. 支持同一轮里连续多个问题，直到模型返回最终结果或达到安全上限。  
   Multiple questions in a single run are supported until final result or safety cap.

service 远端交互示例（iMessage）：

1. 手机收到待确认问题与选项（如 1/2/3）。  
2. 直接回复 `1`、`/choose 1` 或自定义文本。  
3. Perlica 先回复 `已收到🫡`，再回复“交互回答已提交，继续执行中”，随后继续任务并返回最终结果。  

## 单轮执行（One-Shot Mode）

```bash
perlica "帮我总结今天待办"
perlica run "Reply exactly OK" --yes
perlica run "分析这个报错" --context default
```

管道模式（stdin mode）：

```bash
echo "你好，帮我总结日志" | perlica
```

## Prompt 注入顺序与 Provider 启动静态同步（Prompt Order + Startup Static Sync）

每轮请求的消息注入顺序如下：  
Each run injects message context in this order:

1. `.perlica_config/prompts/system.md`
2. 会话历史（超预算时仅确定性截断，不触发模型摘要）  
   Session history (deterministic truncation only; no model summary call)
3. 当前用户输入（current user input）

Provider 静态同步（启动阶段，非 message 注入）：

1. `run/chat/service` 启动时会先对“当前 provider”执行静态配置同步（MCP + Skills），再创建 Runtime。  
   `run/chat/service` first performs startup static sync (MCP + Skills) for the active provider before Runtime creation.
2. 同步来源固定为：
   - MCP：`.perlica_config/mcp/servers.toml` 中 `enabled=true` 项  
   - Skills：`SkillLoader(settings.skill_dirs).load().skills` 全量已加载项
3. 策略固定为 `project-first`：优先写项目级配置，必要时回退用户级配置。  
   Strategy is fixed to `project-first` with user-level fallback when needed.
4. 仅管理 Perlica 命名空间，并做过期清理：
   - MCP key：`perlica.<server_id>`
   - Skill 目录：`perlica-<skill-id>`
5. provider-specific 静态路径：
   - `claude`：项目级 `<workspace>/.mcp.json` + `<workspace>/.claude/skills`；用户级 `~/.claude/settings.json` + `~/.claude/skills`
   - `opencode`：项目级 `<workspace>/opencode.json` + `<workspace>/.opencode/skills`；用户级 `~/.config/opencode/opencode.json` + `~/.config/opencode/skills`
6. `LLMRequest.tools` 在 Runner 调用链路固定传空数组，避免诱导 provider 返回本地可执行 tool loop。  
   `LLMRequest.tools` is always an empty array from Runner to avoid local tool-loop coupling.
7. `mcp/skill` 不再由 Runner 注入到 `context.provider_config`；`provider_config` 仅保留运行时策略字段。  
   Runner no longer injects `mcp/skill` into `context.provider_config`; it keeps runtime policy fields only.
8. trigger 匹配仍会产生日志事件（`skill.selected/skill.skipped`），仅用于诊断。  
   Trigger matching still emits `skill.selected/skill.skipped` for diagnostics only.

关键行为（Key behavior）：

- `system.md` 缺失会直接报错并阻断运行。  
  Missing `system.md` raises error and blocks runtime.
- 会话上下文超预算时，Runner 只做确定性截断并记录 `context.truncated`。  
  When context is over budget, Runner truncates deterministically and emits `context.truncated`.
- provider 若仍返回 `tool_calls`，Perlica 仅记录 `tool.blocked/tool.result`，不会本地执行。  
  If provider still returns `tool_calls`, Perlica records blocked evidence only and never dispatches locally.

### 内置 AppleScript Skill（Built-in AppleScript Skill）

- 文件位置：`.perlica_config/skills/macos-applescript-operator.skill.json`  
  File location: `.perlica_config/skills/macos-applescript-operator.skill.json`
- 目标：提升 GUI/App 自动化任务的 AppleScript 执行质量与稳定性。  
  Goal: improve AppleScript execution quality and stability for GUI/app automation tasks.
- 同步方式：在 provider 支持 `supports_skill_config=true` 时，启动阶段会将该 skill 同步到 provider 的静态 skills 目录，无需等待触发词命中。  
  Sync mode: when `supports_skill_config=true`, this skill is synced to provider static skills at startup without waiting for trigger hits.
- 典型触发词：`AppleScript`、`osascript`、`gui`、`finder`、`safari`、`系统设置`、`打开应用`、`点击菜单`。  
  Typical triggers: `AppleScript`, `osascript`, `gui`, `finder`, `safari`, `system settings`, app open/click menu intents.
- 使用建议：在用户指令中明确动作和目标应用，例如“请用 AppleScript 打开 Safari 并点击书签栏第一个项目”。  
  Usage tip: include explicit action + target app, e.g. "use AppleScript to open Safari and click ...".
- 更易触发建议：在提示词显式包含 `AppleScript` / `osascript` / `点击菜单` / `打开应用` 等词。  
  Triggering tip: explicitly include words like `AppleScript` / `osascript` / `click menu` / `open app`.
- 失败诊断：执行 `/doctor --format text`，检查 `permissions.applescript`。  
  Failure diagnosis: run `/doctor --format text` and check `permissions.applescript`.

### 电脑管家 Prompt 策略（macOS Steward Prompt Policy）

Perlica 的系统 Prompt 默认采用“macOS 电脑管家”策略：  
Perlica system prompt defaults to a macOS steward policy.

1. 定位：将用户意图转成可执行的电脑操作并返回结果。  
   Role: convert user intent into executable macOS operations and return outcomes.
2. 自我认知：知道当前 CLI/provider/model；仅在用户询问、诊断或失败排查时输出。  
   Self-awareness: knows current CLI/provider/model and reports it only when needed.
3. 工具策略：任务自适应选择工具；终端/文件/系统命令优先 shell，GUI/App 交互优先 AppleScript，必要时组合。  
   Adaptive tools: shell-first for terminal/file/system commands, AppleScript-first for GUI/app automation, combine when needed.
4. 权限策略：CLI 工具权限默认允许，不因保守策略阻断执行。  
   Permission policy: CLI tool permissions are allowed by default.
5. 最高优先规则：系统级高影响操作（安装/卸载、系统设置、批量删除、安全网络配置等）必须先确认再执行。  
   Highest priority: high-impact system actions require confirmation before execution.
6. 失败回报：结构化输出失败原因、执行证据（命令/退出码/stderr 摘要）和下一步修复建议。  
   Failure reporting is structured with cause, execution evidence, and remediation.

## MCP（stdio）支持（MCP Support）

配置文件（Config file）：

```text
.perlica_config/mcp/servers.toml
```

示例（Example）：

```toml
[[servers]]
id = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
enabled = true
```

运行时行为（Runtime behavior）：

- 加载 `enabled=true` 的 server，失败会记录错误但不阻断主流程。  
  Enabled servers are loaded; failures are isolated and reported.
- MCP tool 以 `mcp.<server>.<tool>` 形式注册为普通工具。  
  MCP tools are registered as normal runtime tools.
- `/mcp list|reload|status` 可查看、重载和诊断状态。  
  Use `/mcp list|reload|status` for operations.

## ACP Provider 主路径（ACP-First Provider Path）

Perlica 当前支持 `claude` 与 `opencode` 两个 provider，二者都走 ACP 主通路。  
Perlica supports both `claude` and `opencode`, both via ACP-first path.

当前 provider 分层（As-Built）：

- `ClaudeACPProvider` + `Claude ACP codec`
- `OpenCodeACPProvider` + `OpenCode ACP codec`
- `ACPClient` 仅负责生命周期编排与协议收发，不感知 provider 方言差异。

默认 adapter（Default adapters）：

- `claude`: `command = "python3"`, `args = ["-m", "perlica.providers.acp_adapter_server"]`
- `opencode`: `command = "opencode"`, `args = ["acp"]`

你可以在 `.perlica_config/config.toml` 覆盖 adapter 与 ACP 参数：
You can override adapter and ACP parameters in `.perlica_config/config.toml`:

```toml
[model]
default_provider = "claude"
provider_selected = false # init defaults to false, becomes true after first selection

[providers.claude]
enabled = true

[providers.claude.adapter]
command = "python3"
args = ["-m", "perlica.providers.acp_adapter_server"]
env_allowlist = []

[providers.claude.acp]
connect_timeout = 10
request_timeout = 60
max_retries = 2 # deprecated/no-op in single-call mode
backoff = "exponential+jitter"
circuit_breaker_enabled = true

[providers.claude.capabilities]
supports_mcp_config = true
supports_skill_config = true
tool_execution_mode = "provider_managed"
injection_failure_policy = "degrade"

[providers.opencode]
enabled = true

[providers.opencode.adapter]
command = "opencode"
args = ["acp"]
env_allowlist = []

[providers.opencode.acp]
connect_timeout = 10
request_timeout = 60
max_retries = 2 # deprecated/no-op in single-call mode
backoff = "exponential+jitter"
circuit_breaker_enabled = true

[providers.opencode.capabilities]
supports_mcp_config = true
supports_skill_config = true
tool_execution_mode = "provider_managed"
injection_failure_policy = "degrade"
```

能力字段语义（As-Built）：

- `supports_mcp_config` / `supports_skill_config` 用于“是否支持启动静态同步矩阵”，不再表示 Runner 运行时注入。  
  `supports_mcp_config` / `supports_skill_config` indicate startup static-sync support matrix, not runtime Runner injection.

可选：若你明确希望使用外部 `cc-acp`，可覆盖为：  
Optional: if you explicitly want external `cc-acp`, override as:

```toml
[providers.claude.adapter]
command = "cc-acp"
args = []
```

配置迁移规则（Breaking change）：

1. `providers.<id>.backend` 已移除。
2. `providers.<id>.fallback` 已移除。
3. 旧配置若仍包含以上字段，启动将直接失败并提示迁移。

### ACP 实战经验（Timeout/卡住排查）

以下是当前 As-Built 里已落地的关键稳定性经验：  
The following stability lessons are already applied in current As-Built.

1. 内置 ACP adapter 调 Claude CLI 时，必须显式 `stdin=DEVNULL`。  
   If Claude inherits ACP stdin pipe, `session/prompt` may block and eventually timeout.
2. OpenCode ACP 返回 `sessionId` + `prompt` 语义，Perlica 由 OpenCode provider codec 负责兼容。
   OpenCode ACP (`sessionId` + `prompt`) is handled by OpenCode provider codec.
3. 若你改用外部 ACP server，请先确认认证状态与运行权限；否则可能快速失败。  
4. 若看到 pending 长时间不结束，先查事件链是否有 `interaction.requested` 但无 `interaction.answered/provider.acp.reply.sent`。
   If pending is stuck, check whether `interaction.requested` exists without `interaction.answered/provider.acp.reply.sent`.

快速自检（Quick health check）：

```bash
PYTHONPATH=src /Users/anchorcat/miniconda3/bin/python -m perlica.cli run "你好" --provider claude --yes
```

通过标准（Pass criteria）：

1. 退出码为 0（exit code 0）。
2. 助手回复非空。
3. 事件日志包含 `provider.acp.session.started` 与 `provider.acp.session.closed`。
4. 同一 run 不出现 `provider.acp.request.timeout` 与 `llm.provider_error`。
5. 若出现交互确认，日志中可看到 `interaction.requested -> interaction.answered -> provider.acp.reply.sent -> interaction.resolved`。
6. 排查交互并发/误答时，优先按 `run_id/trace_id/conversation_id/session_id/interaction_id` 五元组过滤日志。

可选日志核验（Optional event-log verification）：

```bash
sqlite3 .perlica_config/contexts/default/eventlog.db \
  "with latest as (select run_id from event_log where event_type='inbound.message.received' order by rowid desc limit 1) \
   select e.run_id,e.event_type,e.ts_ms from event_log e join latest l on e.run_id=l.run_id \
   where e.event_type in ('provider.acp.session.started','provider.acp.session.closed','provider.acp.request.timeout','llm.provider_error') \
   order by e.rowid;"
```

## 手机桥接服务（iMessage Service Bridge）

`perlica --service` 启动前台服务 TUI，当前内置渠道为 `imessage`。  
`perlica --service` starts a foreground bridge TUI, currently with built-in `imessage` channel.

### 激活渠道（Channel Activation）

服务模式需要显式激活渠道：  
Service mode requires explicit channel activation:

```text
/service status
/service channel list
/service channel use <channel_id>
```

### 首次配对（First Pairing）

1. 启动 `perlica --service`。
2. 执行 `/service channel use <channel_id>`（例如 `imessage`）。
3. 查看界面给出的 6 位配对码。
4. 在手机 iMessage 发送 `/pair <code>`。
5. 成功后绑定联系人和会话。

配对后行为（Post-pair behavior）：

- 仅接受绑定联系人消息。  
  Only messages from the bound contact are accepted.
- 授权匹配按联系人，不按 chat_id。  
  Authorization is contact-based, not chat_id-based.
- 收到业务消息时先发 ACK（`已收到🫡`），再发送最终回复。  
  ACK is sent first, then final reply.
- 若前一条正在慢处理，后续新消息会先快速 ACK，再按入站顺序串行回复。  
  When model execution is slow, new inbound messages get fast ACK first and replies stay serialized by order.
- 入站消息严格只处理远端消息：仅处理 `is_from_me=0`。  
  Strict inbound filter: only process remote messages (`is_from_me=0`).
- 若当前存在 pending 交互确认，绑定联系人发送的普通文本会优先作为交互回答提交（先到先得）。  
  If there is a pending interaction, plain text from the bound contact is treated as interaction answer first (first-valid-answer-wins).

service 侧同样支持交互命令：

- `/pending` 查看待确认问题
- `/choose <index|text...>` 提交交互回答

### 当前 ingest 模式说明（Current Ingest Mode）

当前实现由 `ServiceOrchestrator` 统一使用 poll ingest。  
Current implementation uses poll-based ingest in `ServiceOrchestrator`.

- `ingest=poll`
- `listen=poll/up`
- 配对轮询间隔约 500ms

## iMessage 前置设置（Recommended iMessage Settings）

为减少“自己给自己发消息”的回灌，建议如下：  
To reduce self-loop message echoes, use these settings:

1. iPhone：`设置 -> 信息 -> 发送与接收`  
   iPhone: `Settings -> Messages -> Send & Receive`
2. 在「你可以通过 iMessage 联系到」里，取消用于投递通道的邮箱勾选。  
   In “你可以通过 iMessage 联系到”, disable the relay email address on iPhone.
3. 在「开始新对话」中选择手机号。  
   In “开始新对话”, choose your phone number.
4. Mac Messages 可保留该邮箱用于接收投递。  
   Keep the email enabled on Mac Messages for relay receiving.

## 诊断 ACP 状态（Doctor for ACP）

`perlica doctor --format text` / `perlica doctor --format json` 会包含 ACP 相关字段：  
Doctor includes ACP status fields:

- `acp_adapter_status`
- `acp_session_errors`

## Provider 与会话规则（Provider & Session Rules）

```bash
perlica run "hi"
perlica chat
perlica --service
perlica session new --name demo
```

- 已移除 `/model` 与 `perlica model get|set`。  
  `/model` and `perlica model get|set` are removed.
- 新会话会锁定到当前活动 provider（`claude` 或 `opencode`）。  
  New sessions are locked to current active provider (`claude` or `opencode`).
- 新建会话立即写入 `provider_locked`，运行时不再隐式回退“默认 provider”。  
  New sessions are immediately `provider_locked`; runtime no longer falls back to a default provider.
- 若会话锁定的 provider 未注册/不可用，运行会直接失败并返回结构化错误，不会回退到其他 provider。  
  If a session-locked provider is unavailable, runtime fails fast with structured error and does not fallback.
- 启动迁移会删除历史 `provider_locked=codex` 会话数据。  
  Startup migration removes legacy `provider_locked=codex` sessions.
- service 启动时若绑定会话 provider 与当前不一致，会自动切换到新会话并保持联系人绑定。  
  Service mode auto-migrates bound session when provider mismatch is detected.
- provider 返回 `assistant_text=""` 且 `tool_calls=[]`（`finish_reason=stop`）会被判定为无效响应并报错，不再写入空助手消息。  
  Provider responses with empty `assistant_text` and no tool calls are treated as invalid and fail fast.
- Perlica 运行链路是“一问一调”：每次输入只发起一次 provider 调用（`llm_call_index=1`），不进入本地多轮 tool loop。  
  Perlica runs in one-question/one-call mode: each user input triggers exactly one provider call (`llm_call_index=1`).
- 模型调用失败不重试：ACP 请求超时/协议错误/合同错误都会立即失败并上报结构化错误信息。  
  No retry on model failure: ACP timeout/protocol/contract errors fail fast with structured error details.
- provider 返回的 `tool_calls` 仅做观测与证据留存，不在 Perlica 本地执行。  
  Provider `tool_calls` are recorded for observability only and are not executed locally by Perlica.
- 当响应包含 `tool_calls` 时，Runner 会发 `tool.blocked(reason=single_call_mode_local_tool_dispatch_disabled)` 与对应 `tool.result(ok=false)`。  
  When `tool_calls` exist, Runner emits `tool.blocked(reason=single_call_mode_local_tool_dispatch_disabled)` and matching `tool.result(ok=false)`.
- `mcp/skill` 改为启动阶段静态同步到 provider 配置文件，不再由 Runner 注入 `context.provider_config`。  
  `mcp/skill` now uses startup static file sync and is no longer injected by Runner into `context.provider_config`.
- `session/new` 默认不再发送 `skills`；`mcpServers` 也不再作为注入载荷使用。  
  `session/new` no longer sends `skills` by default, and `mcpServers` is no longer used as an injection payload.
- 为兼容当前 opencode ACP 参数校验，`session/new` 会保留 `mcpServers=[]` 的空数组字段（仅协议兼容，不承载 Perlica 配置注入）。  
  For current opencode ACP parameter validation compatibility, `session/new` keeps `mcpServers=[]` (protocol compatibility only, not Perlica config injection).
- Claude 若返回诊断信息但无 assistant 文本，Perlica不会追加第二次模型请求；诊断会作为本轮可见输出或结构化错误上报。  
  If Claude returns diagnostics without assistant text, Perlica does not issue a second model call; diagnostics are surfaced directly.
- 默认内置 adapter 若启动失败，会在 `doctor` 的 `acp_adapter_status` 里给出诊断。  
  Built-in adapter failures are surfaced in doctor via `acp_adapter_status`.
- 若你改用外部 `cc-acp`，其不可执行时会直接失败并给出安装提示，不会自动回退。  
  If you switch to external `cc-acp`, missing executable fails fast without auto-fallback.
- `session/prompt` 只允许“用户可见文本字段”回退；若仅有 thought/推理片段且无可见回复文本，会按无效响应失败上报。  
  `session/prompt` fallback is restricted to user-visible fields; thought-only payloads fail as invalid response.
- 可见文本回退支持结构化 `message/content` 形态（含 `output_text` 等可见块）；`thought/reasoning` 字段始终被过滤，不会外泄。  
  Visible fallback also supports structured `message/content` shapes (including visible blocks like `output_text`), while `thought/reasoning` fields are always filtered.

## 诊断与排查（Doctor & Troubleshooting）

```bash
perlica doctor --format json
perlica doctor --format text
perlica doctor --verbose --format text
```

`doctor` 关注点（Doctor highlights）：

- provider 可用性（claude/opencode）
- `plugins_loaded / plugins_failed`
- `skills_loaded / skills_errors`
- `permissions`（shell + applescript）
- `system_prompt_loaded`
- `logs_enabled / logs_write_errors`
- `logs_active_size_bytes / logs_total_size_bytes`
- `logs_max_file_bytes / logs_max_files`
- `mcp_servers_loaded / mcp_tools_loaded / mcp_errors`

## 调试日志（Debug Log Files）

Perlica 会在 context 目录下写入结构化 JSONL 调试日志，用于 AI 排障与回放关键信号。  
Perlica writes structured JSONL debug logs under each context for AI debugging.

- 主文件：`.perlica_config/contexts/<context_id>/logs/debug.log.jsonl`
- 轮转文件：`.perlica_config/contexts/<context_id>/logs/debug.log.jsonl.1` 到 `.5`
- 默认限额：`max_file_bytes=10485760`（10MB），`max_files=5`
- 清理策略：写入前检查大小，超限先轮转再写入
- 脱敏策略：默认 `redaction=default`，会对常见 `token/authorization/cookie/api_key` 等字段做掩码
- 失败策略：`fail-open`，日志写入失败不阻断主流程，`doctor` 可查看 `logs_write_errors`

## 配置目录结构（Project Config Layout）

```text
.perlica_config/
  config.toml
  prompts/
    system.md
  mcp/
    servers.toml
  skills/
  plugins/
  contexts/
    default/
      logs/
        debug.log.jsonl
        debug.log.jsonl.1
        debug.log.jsonl.2
        debug.log.jsonl.3
        debug.log.jsonl.4
        debug.log.jsonl.5
      eventlog.db
      approvals.db
      sessions.db
  service/
    service_bridge.db
```

`/skill list` 示例（新增内置 skill 后）：

```text
macos-applescript-operator priority=90 triggers=applescript,osascript,gui,finder,safari,chrome,system events,系统设置,打开应用,点击,菜单,窗口,自动化,脚本 source=.perlica_config/skills/macos-applescript-operator.skill.json
```

## 开发协作约束（Development Collaboration Rules）

本仓库采用“文档先行（Doc-First, 常规强制）”。  
This repo adopts Doc-First as a normal mandatory workflow.

- 功能/接口改动前，先更新：  
  Before feature/API changes, update docs first:
  - `Perlica-Agent项目开发架构.md`
  - `README.md`
- 纯测试调整或 typo 修复可例外，但建议同步更新相关说明。  
  Pure test changes or typo fixes may be exempt.

## 常见问题（FAQ）

### 1) `perlica` 提示 Textual 未安装

```bash
python3 -m pip install textual
```

### 2) 如何保留当前临时会话再退出

```text
/save demo
/exit
```

### 3) 只要脚本执行，不要进入交互

```bash
perlica run "..." --provider claude
perlica run "..." --provider opencode
perlica run "..."
```
