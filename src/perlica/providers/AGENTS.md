# AGENTS.md（providers）

## 目录职责

Provider 适配层将外部 provider 交互（默认 ACP）规范化为 `LLMResponse`。

## 文档先行

provider 合同和错误语义变更属于文档先行（常规强制）。

## Debug 规则（Log-First Debug）

1. provider debug 先看失败日志与 `ProviderError/ProviderContractError` 证据，再改解析逻辑。  
2. 使用日志中的 `provider_id/finish_reason/tool_calls/error` 关键词快速定位到具体 adapter 文件。  
3. 解析/协议改动时补关键日志点，保证 CLI 输出与内部诊断可对齐。

## 允许改动

1. ACP client 请求构建与响应映射。  
2. schema 校验与 usage 归一化。  
3. `ProviderTransportError` / `ProviderProtocolError` / `ProviderContractError` 判定。  
4. 受控降级结构（`ProviderDegradedResponse`）与事件对齐。
5. ACP `session/prompt` 长推理等待策略（不做本地硬超时）。

## 禁止改动

1. 允许 provider 直接执行命令（越权）。  
2. 返回非结构化结果破坏 runner 期望。  
3. 修改 tool_calls 结构而不更新类型与测试。
4. 以“自由文本盲解析”替代 ACP 合同校验主路径。  
5. 不经 break-glass 开关引入 legacy CLI 直连常态路径。  
6. 在 contract 失败时静默吞错（必须事件可观测）。
7. 将 `session/prompt` 改回本地硬超时，导致思考中调用被误判 timeout。

## 改动前检查

1. 合同是否仍满足 `LLMResponse`。  
2. tool call 安全边界是否保持。  
3. usage 字段是否完整。
4. ACP 生命周期是否完整（`initialize/session/new/session/prompt/session/close`）。  
5. `request_id/run_id/trace_id` 是否具备幂等与追踪语义。
6. `session/prompt` 是否保持“等待最终结果”，超时是否仅用于非 prompt 方法。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_provider_contract.py`
2. `PYTHONPATH=src pytest -q tests/test_provider_usage_parse.py`
3. 如改 live 行为，补充 `PERLICA_LIVE_PROVIDER=...` 手工验证说明。
4. 补充/执行 ACP 协议专项回归（超时重试、重复响应去重、合同降级事件）。

## 常见陷阱

1. JSONL 解析遗漏最后 agent_message。  
2. Claude structured_output schema 不完整。  
3. usage 映射字段名不一致。
4. 混淆 ACP 协议错误与业务合同错误，导致告警分类失真。  
5. 降级回复返回了用户文本但遗漏 `llm.contract_degraded` 事件。
6. 看到 progress notification 持续增长却仍触发本地 prompt timeout。

## 完成定义（DoD）

1. provider contract 测试通过。  
2. 错误语义清晰可诊断。  
3. 文档已更新。
4. ACP-first 主路径稳定，break-glass 路径仅应急可审计。
