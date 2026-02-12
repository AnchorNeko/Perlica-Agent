# AGENTS.md（service/channels）

## 目录职责

渠道适配层（当前 `imessage_adapter.py`）负责外部 I/O 到统一 `ChannelInboundMessage/ChannelOutboundMessage` 契约的映射。

边界说明：channel 层不感知 ACP 细节，仅透传标准消息与标准诊断事件。

## 文档先行

渠道协议或字段语义调整属于文档先行（常规强制）。

## Debug 规则（Log-First Debug）

1. 先查看 channel telemetry/health/debug log，再定位解析或发送链路问题。  
2. 从日志中的 `event_id/contact/chat/last_error/raw_line_preview` 关键词反查适配器代码。  
3. 处理 I/O 兼容问题时补充最小必要日志，便于后续快速定位，不输出敏感内容。

## 允许改动

1. `ChannelAdapter` 协议实现细节。  
2. iMessage 解析、发送、telemetry、健康状态。  
3. bootstrap 权限检查与系统设置跳转。

## 禁止改动

1. 放宽 `is_from_me` 严格模式而不评估安全回归。  
2. 修改 contact 标准化逻辑但不更新匹配测试。  
3. 引入非幂等轮询行为导致重复投递。
4. 在 channel 层引入 provider 方言字段解析逻辑。
5. 在 channel 层新增/复活 provider prompt 超时判断逻辑。

## 改动前检查

1. 识别 inbound payload 兼容面。  
2. 校验 send fallback（chat_id -> contact）路径。  
3. 校验 telemetry 事件是否被上层消费。
4. 确认 provider 协议升级不会污染 channel 契约字段。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_imessage_adapter.py`
2. `PYTHONPATH=src pytest -q tests/test_service_bootstrap_permissions.py`

## 常见陷阱

1. timestamp 解析错误导致 poll watermark 失效。  
2. 误把 stderr JSON 事件丢弃。  
3. 未正确设置 last_error/health 状态。

## 完成定义（DoD）

1. 协议兼容、发送可靠、telemetry 可观测。  
2. 相关测试通过。  
3. 文档更新完成。
