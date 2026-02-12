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

3. 在项目目录初始化配置（Initialize project config）

```bash
perlica init
```

4. 启动交互会话（Start interactive chat）

```bash
perlica
```

5. 启动前台手机桥接服务（Start foreground mobile bridge）

```bash
perlica --service
```

## 入口与运行模式（Entrypoints & Modes）

- `perlica [--provider claude]`：进入交互聊天模式（默认 `claude`）。  
  Default interactive chat mode.
- `perlica chat [--provider claude]`：显式进入交互聊天模式。  
  Explicit interactive chat mode.
- `perlica run "..." [--provider claude]`：单轮执行后退出。  
  Run one turn and exit.
- `perlica --service [--provider claude]`：进入服务模式（手机桥接 TUI）。  
  Service bridge TUI mode.
- `perlica --help`：仅显示帮助，不进入聊天。  
  Show help only.
- 非 TTY 且无子命令时：读取 stdin 执行单轮后退出。  
  In non-TTY without subcommand, reads stdin for one-shot execution.
- 当前版本仅支持 `claude`，`--provider` 可省略。若传入非 `claude` 会报错。  
  Current version supports `claude` only; non-claude provider is rejected.

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

Claude Code 兼容说明（Claude AskUserQuestion compatibility）：

1. 当 `claude -p` 返回 `permission_denials.tool_name=AskUserQuestion` 时，Perlica 会把问题映射为 pending 交互并展示选项。  
   When Claude returns `permission_denials.tool_name=AskUserQuestion`, Perlica maps it to pending interaction options.
2. 你可直接输入编号或自由文本回答，Perlica 会把回答加入后续轮次上下文并继续执行。  
   You can answer with an index or free text; Perlica appends answers to follow-up context and continues.
3. 支持同一轮里连续多个问题，直到模型返回最终结果或达到安全上限。  
   Multiple questions in a single run are supported until final result or safety cap.

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

## Prompt / Skill / MCP 注入顺序（Prompt Injection Order）

每轮请求按以下顺序注入：  
Each run injects context in this order:

1. `.perlica_config/prompts/system.md`
2. 匹配的 Skill system prompt 块（selected skill blocks）
3. MCP resources/prompts 上下文块（MCP context blocks）
4. 会话历史（超预算时仅确定性截断，不触发模型摘要）  
   Session history (deterministic truncation only; no model summary call)
5. 当前用户输入（current user input）

关键行为（Key behavior）：

- `system.md` 缺失会直接报错并阻断运行。  
  Missing `system.md` raises error and blocks runtime.
- 会话上下文超预算时，Runner 只做确定性截断并记录 `context.truncated`。  
  When context is over budget, Runner truncates deterministically and emits `context.truncated`.

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

Perlica 当前默认 provider 是 `claude`，并通过内置 ACP adapter
（桥接官方 `claude` CLI）走 ACP 主通路。  
Perlica uses `claude` by default and talks through ACP via the built-in
adapter, which bridges the official `claude` CLI.

默认 adapter（Default adapter）：

- `command = "python3"`
- `args = ["-m", "perlica.providers.acp_adapter_server"]`

你可以在 `.perlica_config/config.toml` 覆盖 adapter 与 ACP 参数：
You can override adapter and ACP parameters in `.perlica_config/config.toml`:

```toml
[model]
default_provider = "claude"

[providers.claude]
enabled = true
backend = "acp" # acp | legacy_cli

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

[providers.claude.fallback]
enabled = false
```

可选：若你明确希望使用外部 `cc-acp`，可覆盖为：  
Optional: if you explicitly want external `cc-acp`, override as:

```toml
[providers.claude.adapter]
command = "cc-acp"
args = []
```

### Break-Glass（紧急降级到 legacy_cli）

默认情况下，ACP transport/protocol 失败不会自动回退。  
By default, ACP transport/protocol failures do not auto-fallback.

只有显式设置以下环境变量时，才允许临时启用回退：
Fallback is allowed only when this env var is explicitly enabled:

```bash
PERLICA_PROVIDER_BREAK_GLASS=1 perlica run "..."
```

触发回退会写审计事件：`provider.fallback_activated`。  
Fallback activation emits audit event `provider.fallback_activated`.

### Claude ACP 实战经验（Timeout/卡住排查）

以下是当前 As-Built 里已落地的关键稳定性经验：  
The following stability lessons are already applied in current As-Built.

1. 内置 ACP adapter 调 Claude CLI 时，必须显式 `stdin=DEVNULL`。  
   If Claude inherits ACP stdin pipe, `session/prompt` may block and eventually timeout.
2. 内置 ACP adapter 的 `session/prompt` 采用同步执行并直接回包。  
   Prompt execution is synchronous to avoid heartbeat/notification interfering with RPC response delivery.
3. 若你改用外部 `cc-acp`，请先确认 CLI 登录态与运行权限；否则可能出现快速返回错误文本（例如 `Claude Code process exited with code 1`）。
4. 若看到 pending 长时间不结束，先查事件链是否有 `interaction.requested` 但无 `interaction.answered/acp.reply.sent`。  
   If pending is stuck, check whether `interaction.requested` exists without `interaction.answered/acp.reply.sent`.

快速自检（Quick health check）：

```bash
PYTHONPATH=src /Users/anchorcat/miniconda3/bin/python -m perlica.cli run "你好" --yes
```

通过标准（Pass criteria）：

1. 退出码为 0（exit code 0）。
2. 助手回复非空。
3. 事件日志包含 `acp.session.started` 与 `acp.session.closed`。
4. 同一 run 不出现 `acp.request.timeout` 与 `llm.provider_error`。
5. 若出现交互确认，日志中可看到 `interaction.requested -> interaction.answered -> acp.reply.sent -> interaction.resolved`。
6. 排查交互并发/误答时，优先按 `run_id/trace_id/conversation_id/session_id/interaction_id` 五元组过滤日志。

可选日志核验（Optional event-log verification）：

```bash
sqlite3 .perlica_config/contexts/default/eventlog.db \
  "with latest as (select run_id from event_log where event_type='inbound.message.received' order by rowid desc limit 1) \
   select e.run_id,e.event_type,e.ts_ms from event_log e join latest l on e.run_id=l.run_id \
   where e.event_type in ('acp.session.started','acp.session.closed','acp.request.timeout','llm.provider_error') \
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
/service channel use imessage
```

### 首次配对（First Pairing）

1. 启动 `perlica --service`。
2. 执行 `/service channel use imessage`。
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

- `provider_backend`
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
- 当前版本新会话默认锁定 `claude`（无需显式 `--provider`）。  
  New sessions are locked to `claude` by default.
- 新建会话立即写入 `provider_locked`，运行时不再隐式回退“默认 provider”。  
  New sessions are immediately `provider_locked`; runtime no longer falls back to a default provider.
- 启动迁移会删除历史 `provider_locked=codex` 会话数据。  
  Startup migration removes legacy `provider_locked=codex` sessions.
- service 启动时若绑定会话不是 `claude`，会自动迁移到新的 `claude` 会话并保持联系人绑定。  
  Service mode auto-migrates non-claude bound sessions to claude.
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
- Claude 若返回诊断信息但无 assistant 文本，Perlica不会追加第二次模型请求；诊断会作为本轮可见输出或结构化错误上报。  
  If Claude returns diagnostics without assistant text, Perlica does not issue a second model call; diagnostics are surfaced directly.
- 默认内置 adapter 若启动失败，会在 `doctor` 的 `acp_adapter_status` 里给出诊断。  
  Built-in adapter failures are surfaced in doctor via `acp_adapter_status`.
- 若你改用外部 `cc-acp`，其不可执行时会直接失败并给出安装提示，不会自动回退。  
  If you switch to external `cc-acp`, missing executable fails fast without auto-fallback.

## 诊断与排查（Doctor & Troubleshooting）

```bash
perlica doctor --format json
perlica doctor --format text
perlica doctor --verbose --format text
```

`doctor` 关注点（Doctor highlights）：

- provider 可用性（claude）
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
perlica run "..."
```
