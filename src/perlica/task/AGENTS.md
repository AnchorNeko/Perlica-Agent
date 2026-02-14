# AGENTS.md（task）

## 目录职责

`task` 负责单活动任务状态机：`IDLE/RUNNING/AWAITING_INTERACTION/COMPLETED/FAILED` 及其事件输出。

## 文档先行

若修改任务状态、拒绝策略、事件字段，必须先更新 `README.md` 与架构文档。

## Debug 规则（Log-First）

1. 先核对 `task.started -> task.state.changed -> task.command.rejected|deferred` 事件序列。  
2. 以 `run_id/conversation_id/session_id` 反查 `task/coordinator.py`。  
3. 先做串行与并发最小复现，再修状态机。

## 允许改动

1. `TaskCoordinator` 状态迁移与边界校验。  
2. `TaskSnapshot/TaskState` 字段补充。  
3. 与 runtime/service 的事件桥接。

## 禁止改动

1. 允许同一时刻存在多个活动任务。  
2. 在 `RUNNING` 态放行普通新指令抢占当前任务。  
3. 跳过失败态直接回到空闲态且无可观测事件。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_service_pending_answer_routing.py`
2. `PYTHONPATH=src pytest -q tests/test_service_interaction_flow.py`
3. `PYTHONPATH=src pytest -q tests/test_tui_interaction_prompt.py`

## 完成定义（DoD）

1. 串行任务约束稳定，无竞态回归。  
2. 状态机事件完整可追踪。  
3. 文档、实现、测试一致。
