# AGENTS.md（kernel）

## 目录职责

`kernel/` 负责 Runtime、Runner、Dispatcher、SessionStore、EventLog、DebugLog 等核心执行链。

## 文档先行

kernel 行为改动属于文档先行强制范围：先改 `Perlica-Agent项目开发架构.md` 与 `README.md`。

## 单轮约束（必须遵守）

1. `Runner.run_text()` 单次 run 只能调用一次 provider。
2. 禁止在 kernel 引入 provider 重试或多轮回调。
3. provider `tool_calls` 仅记录，不执行本地 dispatch。
4. 上下文超预算时仅确定性截断，不触发摘要模型调用。
5. `session/prompt` 不做本地硬超时，等待 provider 最终响应；仅在进程退出或协议错误时失败。

## Debug 规则（Log-First）

1. 优先读取：`eventlog.db`、`debug.log.jsonl`、`doctor`。
2. 先用事件链定位：`llm.requested/llm.responded/llm.provider_error/tool.blocked/context.truncated`。
3. 修改异常路径时补充结构化事件字段，保持可追踪。

## 允许改动

1. Runner 单轮编排与错误上报字段。
2. ACPClient 生命周期与失败语义。
3. Runtime 可观测字段与 doctor 输出。
4. DebugLog 脱敏、轮转、fail-open 实现。

## 禁止改动

1. 恢复 `while response.tool_calls` 多轮流程。
2. provider `tool_calls` 恢复本地执行。
3. 将日志写失败升级为阻断错误。
4. 在无证据情况下跨模块大规模猜测式改动。

## 改动前检查

1. 确认事件消费者（CLI/TUI/service/tests）是否受影响。
2. 确认错误字段是否向后兼容（保留 `error` 文本）。
3. 确认 session/provider 锁定约束未破坏。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_runner.py`
2. `PYTHONPATH=src pytest -q tests/test_runner_session_context.py`
3. `PYTHONPATH=src pytest -q tests/test_acp_timeout_retry.py tests/test_acp_client_lifecycle.py`
4. `PYTHONPATH=src pytest -q tests/test_acp_transport_timeout_semantics.py tests/test_acp_transport_activity.py`
5. `PYTHONPATH=src pytest -q tests/test_runtime_debug_log_integration.py tests/test_doctor_logs_section.py`

## 完成定义（DoD）

1. 单轮调用行为稳定且有回归覆盖。
2. provider 失败结构化上报可在日志和用户态看到。
3. 文档与实现一致。
