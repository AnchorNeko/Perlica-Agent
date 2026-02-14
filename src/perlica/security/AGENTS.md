# AGENTS.md（security）

## 目录职责

`security` 负责启动权限探测（shell/AppleScript）与结果结构化输出。

## 文档先行

权限探测行为、探测命令、错误分类变更需要先更新 `README.md` 与架构文档。

## Debug 规则（Log-First）

1. 先看 doctor 的 permissions 段与 `debug.log.jsonl` 证据。  
2. 先区分“权限拒绝”与“可执行缺失/命令错误”。  
3. 再定位 `permission_probe.py` 对应分支。

## 允许改动

1. 权限探测命令与结果映射。  
2. 探测降级策略与可读错误文案。  
3. 与 doctor/运行时状态输出的字段对齐。

## 禁止改动

1. 取消权限探测失败的结构化输出。  
2. 扩大探测动作到高风险副作用命令。  
3. 用宽松断言掩盖真实权限失败。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_permission_probe.py`
2. `PYTHONPATH=src pytest -q tests/test_doctor.py tests/test_doctor_logs_section.py`

## 完成定义（DoD）

1. 探测结果结构化且可诊断。  
2. 权限异常不会误导主流程。  
3. 文档、doctor 输出、实现一致。
