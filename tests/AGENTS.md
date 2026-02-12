# AGENTS.md（tests）

## 目录职责

`tests/` 是 Perlica 行为契约回归基线，优先保障单轮模式与错误可观测性。

## 文档先行

当测试反映行为变化时，先更新 `README.md` 与架构文档，再调整实现/断言。

## 单轮模式测试基线

1. 每次用户输入只触发一次 provider 调用。
2. provider 失败不重试（尤其 ACP timeout/protocol/contract）。
3. provider `tool_calls` 不在本地执行，必须断言 `tool.blocked` 与失败 `tool.result`。
4. 上下文超预算时不触发摘要模型调用。
5. `session/prompt` 在有运行迹象时不应本地超时；非 prompt 方法超时语义保持可测。

## Debug 测试规则

1. 修复 bug 前先保留失败证据（日志/事件/堆栈）。
2. 回归用例先覆盖失败现场，再修实现。
3. 模型失败用例必须校验结构化错误字段，不只校验文案。

## 允许改动

1. 新增最小回归用例锁定 bug。
2. 更新断言以匹配已确认的新行为。
3. 为真实外部副作用场景提供环境门控集成测试。

## 禁止改动

1. 删测试掩盖问题。
2. 通过放宽断言掩盖“重试/重复执行”回归。
3. 继续保留与单轮模式冲突的历史断言。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_runner.py tests/test_runner_session_context.py`
2. `PYTHONPATH=src pytest -q tests/test_acp_timeout_retry.py`
3. `PYTHONPATH=src pytest -q tests/test_acp_transport_timeout_semantics.py tests/test_acp_transport_activity.py`
4. `PYTHONPATH=src pytest -q tests/test_notes_real_write_once.py`
5. `PYTHONPATH=src pytest -q tests/test_readme_examples.py`

## 完成定义（DoD）

1. 关键行为边界有测试覆盖且通过。
2. 测试描述和文档语义一致。
3. 失败路径可通过日志与事件快速定位。
