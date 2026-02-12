# AGENTS.md（tui）

## 目录职责

`tui` 负责 Textual 交互层：聊天 UI、服务 UI、输入路由、状态渲染、线程回调。

## 文档先行

用户可见交互行为变更需文档先行（常规强制）。

## Debug 规则（Log-First Debug）

1. TUI debug 优先看状态栏与事件卡片日志，再定位 controller 与 UI 渲染路径。  
2. 从日志中的 `phase/session/model/queue/listen/error` 关键词反查对应刷新与回调代码。  
3. 涉及状态同步问题时补充轻量日志，避免只靠肉眼复现。
4. 超时展示必须区分：`session/prompt` 长推理期间应展示“仍在运行/思考中”，不要误报 timeout。

## 允许改动

1. 输入键位映射与 slash hint 展示。  
2. 状态栏 phase 更新与日志卡片呈现。  
3. chat/service controller 状态同步。
4. 标准诊断事件展示（含 `llm.invalid_response`、`llm.contract_degraded`）。

## 禁止改动

1. 在非主线程直接更新 Textual 组件。  
2. 更改快捷键语义而不更新 README。  
3. 破坏 `/` 命令 fallback 到普通消息逻辑。
4. 在 UI 层直接渲染 provider 方言原始 payload 作为主诊断来源。

## 改动前检查

1. 确认行为在 chat 还是 service 模式。  
2. 确认是否涉及 session 保存/丢弃弹窗。  
3. 确认状态文本是否依赖后端字段。
4. 确认 ACP/contract 事件展示使用标准事件名，而非 provider-specific 细节。
5. 若展示超时/运行状态，必须参考 `acp.notification.received` 与 `acp.request.timeout` 的组合证据。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_tui_input_bindings.py`
2. `PYTHONPATH=src pytest -q tests/test_tui_input_submit.py`
3. `PYTHONPATH=src pytest -q tests/test_tui_slash_commands.py tests/test_tui_entry.py`
4. `PYTHONPATH=src pytest -q tests/test_service_tui_event_render.py`

## 常见陷阱

1. 取消生成只取消展示，不中断后端执行。  
2. slash hint 精度回退导致命令提示噪声。  
3. 状态栏 phase 与后台实际状态不一致。
4. provider 降级时 UI 只显示“失败”，但缺少 `llm.contract_degraded` 线索。

## 完成定义（DoD）

1. UI 交互可用、线程安全、提示准确。  
2. 相关测试通过。  
3. 文档同步。
