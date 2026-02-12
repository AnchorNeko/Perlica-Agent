# AGENTS.md（mcp）

## 目录职责

`mcp` 负责配置解析、stdio 客户端生命周期、tool/resource/prompt registry 与 runtime 注入。

边界说明：MCP 用于工具/资源接入，不用于 provider 会话协议（provider 会话由 ACP 负责）。

## 文档先行

MCP 加载与注入语义变更需文档先行（常规强制）。

## Debug 规则（Log-First Debug）

1. debug MCP 问题时优先读取 `doctor` 的 MCP 字段与 debug log 的 `mcp.*` 事件。  
2. 基于日志中的 `server_id/tool_name/error` 关键词，用 `rg` 定位到配置解析、client 或 manager 代码。  
3. 新增容错逻辑时同步补日志，确保 server load/reload 失败可快速回溯。

## 允许改动

1. `servers.toml` 解析与校验。  
2. `MCPManager` load/reload/status 行为。  
3. `StdioMCPClient` JSON-RPC 通信与容错。

## 禁止改动

1. 修改 qualified tool naming（`mcp.<server>.<tool>`）而不全链路更新。  
2. 更改 prompt/resource 注入格式但不更新 Runner 相关断言。  
3. 无超时或错误处理的阻塞请求改动。
4. 将 MCP 协议层误用为 provider ACP 会话层。

## 改动前检查

1. 明确兼容的 config 错误处理策略。  
2. 明确 reload 对 runtime registry 的影响。  
3. 检查 prompt context 大小截断策略。
4. 确认 MCP 变更不会侵入 provider ACP 事件与错误语义。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_mcp_config_and_registry.py`
2. `PYTHONPATH=src pytest -q tests/test_mcp_context_injection.py`
3. `PYTHONPATH=src pytest -q tests/test_mcp_tool_dispatch.py`

## 常见陷阱

1. content-length framing 读取边界错误。  
2. 未关闭旧 client 导致资源泄漏。  
3. 配置错误未出现在 `status().errors`。
4. 把 provider 协议错误错误归类成 MCP 错误，导致排障方向偏离。

## 完成定义（DoD）

1. MCP status、reload、tool 调用行为正确。  
2. 上下文注入与测试一致。  
3. 文档同步。
