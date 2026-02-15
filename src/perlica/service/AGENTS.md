# AGENTS.md（service）

## 目录职责

`service` 负责手机桥接编排：配对、联系人授权、ACK/回复顺序、状态展示、工具策略命令。

## 文档先行

service 行为变更必须文档先行（常规强制）。

## Debug 规则（Log-First Debug）

1. service debug 优先查看 `debug.log.jsonl` 中 `service_event/telemetry`，并结合 TUI status 字段（`queue/busy/listen`）。  
2. 以日志里的 `contact/chat/session/event_id/event_type` 为依据定位 `orchestrator` 与 `channel` 路径。  
3. 改动 ACK/队列/绑定逻辑时，必须补可观测日志，确保后续可复盘“先 ACK、后串行回复”的时序。
4. 若用户反馈“Claude 卡住/超时”，先查同 run 是否持续出现 `provider.acp.notification.received(stage=session/prompt)`；有则视为仍在运行，不应被本地 prompt timeout 误判。

## 允许改动

1. `ServiceOrchestrator` 配对与消息处理。  
2. `ServiceStore` 配对码/绑定/cursor 持久化。  
3. `ServiceController` 频道激活与命令钩子。  
4. `tool_policies.py` 策略变更入口。
5. `ServiceOrchestrator._emit()` 到 runtime 调试日志的事件桥接。
6. 快速 ACK（fast-ack）与串行队列状态可观测字段维护。
7. service 模式下 provider ACP 诊断事件透传与展示。

## 禁止改动

1. 改成 chat_id-only 授权（当前是 contact-only）。  
2. 改变 ACK 在回复前发送的顺序而无明确需求。  
3. 未评估重复消息去重就改 event_id 逻辑。
4. 将 service 诊断日志写失败升级成用户可见阻断。
5. 破坏“ACK 可提前、最终回复仍串行有序”的约束。
6. provider 异常处理绕过 ACP 标准错误事件（如 `llm.invalid_response` / `llm.contract_degraded`）。

## 改动前检查

1. 是否影响 `ingest=poll` 语义。  
2. 是否影响 `/service channel ...` 与 `/service tools ...`。  
3. 是否影响状态栏字段与 telemetry 展示。
4. 是否保持 service 事件在无 event sink 时仍可进入 debug logs。
5. 是否保持 ACK 去重（避免 fast-ack 与主处理 ACK 重复发送）。
6. 若涉及 provider 路由，确认绑定会话与启动 `--provider` 冲突时有自动迁移/清晰策略。
7. 若涉及 provider 失败路径，确认不会破坏“先 ACK、后串行回复”时序。
8. 若涉及 timeout 文案/状态展示，确认与“prompt 持续等待最终结果”语义一致。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_service_orchestrator.py`
2. `PYTHONPATH=src pytest -q tests/test_service_channel_selection.py`
3. `PYTHONPATH=src pytest -q tests/test_service_channel_cli_commands.py`
4. `PYTHONPATH=src pytest -q tests/test_service_contact_only_match.py tests/test_service_store.py`
5. `PYTHONPATH=src pytest -q tests/test_runtime_debug_log_integration.py`

## 常见陷阱

1. pairing 重绑死锁（需关注可重入锁）。  
2. poll watermark 回放重复消息。  
3. 忽略 from_me 消息过滤。
4. `_emit` 早返回导致调试日志缺失。
5. 队列深度统计与实际处理状态不一致，导致“卡顿”排障信息失真。
6. 复用历史绑定会话但未对齐 `provider_locked`，导致显式 provider 启动后仍混用模型。
7. provider 受控降级未发标准事件，TUI/日志无法定位根因。

## 完成定义（DoD）

1. 配对、ACK、回复、授权链路测试通过。  
2. 状态文本与 README/架构文档一致。  
3. 文档已同步。
4. service 关键事件可在 `.perlica_config/contexts/<ctx>/logs/debug.log.jsonl` 观测。
5. provider 异常在 service 模式下仍保持 ACP 诊断可观测且不破坏 ACK/串行约束。
