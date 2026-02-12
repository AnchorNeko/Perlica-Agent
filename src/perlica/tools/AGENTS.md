# AGENTS.md（tools）

## 目录职责

`tools` 提供可调度工具实现（当前 `shell.exec` 与 runtime 映射的 `mcp.*` 工具桥）。

## 文档先行

工具行为、参数、错误语义变更需文档先行（常规强制）。

## Debug 规则（Log-First Debug）

1. debug tool 行为先看 dispatcher/tool 错误日志，再排查执行实现。  
2. 从日志中的 `tool_name/risk_tier/cmd/error` 关键词定位 tool 与 policy 交界处代码。  
3. 修改执行路径时补关键日志，保证审批、阻断、超时都可追踪。

## 允许改动

1. 工具参数解析与结果封装。  
2. 安全执行环境（cwd/env/timeout）细化。  
3. MCP tool runtime bridge 错误映射。

## 禁止改动

1. 删除 `DISPATCH_ACTIVE` 防护。  
2. 放宽 shell 高风险限制到 tool 内绕过 policy。  
3. 直接依赖 UI 或 service 层实现细节。
4. 在 tool 层新增 provider 协议补丁逻辑（tool 层与 ACP 无耦合）。

## 改动前检查

1. 是否保持 `ToolResult` 结构稳定。  
2. 是否影响 dispatcher blocking 逻辑。  
3. 是否影响审批策略行为。
4. 确认 provider 协议升级不改变 tool 调度接口。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_dispatcher_blocking.py`
2. `PYTHONPATH=src pytest -q tests/test_mcp_tool_dispatch.py`

## 常见陷阱

1. shell env 过宽引入安全风险。  
2. timeout 处理不一致。  
3. 错误码/错误文本与上层预期不一致。
4. 把 provider 合同修复放到 tool 层实现，导致职责混乱。

## 完成定义（DoD）

1. 工具只能被 dispatcher 调用。  
2. 风险控制不回退。  
3. 文档同步。
