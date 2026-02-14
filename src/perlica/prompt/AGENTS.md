# AGENTS.md（prompt）

## 目录职责

`prompt` 负责系统提示词文件加载与错误语义（`PromptLoadError`）。

## 文档先行

若修改系统提示词路径、加载时机、错误文案，先更新 `README.md` 与架构文档。

## Debug 规则（Log-First）

1. 先看 doctor 输出与启动日志中的 prompt 字段。  
2. 先确认 `system_prompt_file` 配置值与文件可读性。  
3. 再定位 `system_prompt.py` 的加载分支。

## 允许改动

1. 提示词加载路径与编码处理。  
2. 异常语义与错误信息增强。  
3. 与配置层的契约对齐。

## 禁止改动

1. 静默吞掉提示词加载失败。  
2. 在无文档同步情况下改动提示词来源约束。  
3. 将运行时错误退化为不可诊断的通用异常。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_system_prompt_loading.py`
2. `PYTHONPATH=src pytest -q tests/test_doctor.py tests/test_doctor_format.py`

## 完成定义（DoD）

1. 提示词加载行为可预测。  
2. 失败可观测、可定位。  
3. 文档与配置说明一致。
