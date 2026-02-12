# AGENTS.md（仓库总控）

## 目标

Perlica 当前按“单轮代理器（One Question, One Provider Call）”运行。
本规则文件约束整个仓库的 AI 改动流程。

## 文档先行（常规强制）

1. 功能/API/行为改动前，必须先更新：
   - `Perlica-Agent项目开发架构.md`
   - `README.md`
2. 纯测试与 typo 可例外。

## 单轮模式硬约束

1. 每次用户输入只允许一次 provider 调用。
2. 模型失败不得重试；必须快速、完整、结构化上报错误。
3. provider 返回的 `tool_calls` 不在 Perlica 本地执行。
4. 禁止引入“工具后再调模型”的本地循环。
5. 禁止引入隐式摘要模型调用。
6. `session/prompt` 不做 Perlica 本地硬超时；只要 Claude/ACP 进程仍在运行，就持续等待最终结果。
7. 非 `session/prompt` 方法允许超时保护（如 `initialize/session/new/session/close`）。

## Debug 规则（Log-First）

1. debug 先看证据：`eventlog.db`、`debug.log.jsonl`、失败测试输出。
2. 先锁定 `run_id/trace_id/session_id/event_type`，再 `rg` 反查代码。
3. 先做最小复现，再修代码；禁止无证据盲改。
4. 涉及模型失败时，先核对最近链路：
   `inbound -> llm.requested -> llm.responded | llm.provider_error -> tool.blocked/tool.result`。

## 允许改动

1. 按目录职责修改源码、测试、文档。
2. 在关键路径增加结构化日志（含脱敏）。
3. 新增回归测试固定行为边界。

## 禁止改动

1. 未更新文档直接提交行为变更。
2. 恢复模型重试或多轮 tool loop。
3. 在 provider `tool_calls` 路径恢复本地 dispatch。
4. 通过放宽测试断言掩盖真实回归。
5. 将 `session/prompt` 改回本地硬超时（误判“仍在思考”的正常调用）。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_readme_examples.py`
2. `PYTHONPATH=src pytest -q`

## 完成定义（DoD）

1. 代码、测试、README、架构文档一致。
2. 单轮约束有测试覆盖并通过。
3. 失败场景具备结构化错误与可观测证据。
