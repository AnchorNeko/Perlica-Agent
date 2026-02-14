# AGENTS.md（interaction）

## 目录职责

`interaction` 负责 pending 交互状态机：发布问题、接收回答、并发保护、回答归档与读取。

## 文档先行

若修改交互命令语义、事件名、回答解析规则，先同步 `README.md` 与架构文档。

## Debug 规则（Log-First）

1. 先看 `eventlog.db` 与 `debug.log.jsonl` 的交互链路事件。  
2. 先锁定 `run_id/trace_id/interaction_id/session_id`，再定位 `coordinator.py`。  
3. 先复现“发布 -> 待确认 -> 提交回答 -> 继续执行”最小链路，再改代码。

## 允许改动

1. `InteractionCoordinator` 的状态机与并发控制。  
2. `InteractionRequest/InteractionAnswer` 类型约束。  
3. 交互提示文本与选项建议（不改变核心合同前提下）。

## 禁止改动

1. 允许多个活动 pending 交互并存。  
2. 绕过 `interaction_id` 校验直接提交回答。  
3. 把交互回答当成第二次 provider 主调用。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_interaction_coordinator.py`
2. `PYTHONPATH=src pytest -q tests/test_acp_permission_interaction.py`
3. `PYTHONPATH=src pytest -q tests/test_tui_interaction_prompt.py`

## 完成定义（DoD）

1. pending 状态流转可观测且可复盘。  
2. 单活动交互约束稳定。  
3. 文档与行为一致。
