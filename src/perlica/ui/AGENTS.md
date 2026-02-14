# AGENTS.md（ui）

## 目录职责

`ui` 负责 CLI/TUI 文本渲染：notice、banner、help、doctor、运行元信息等。

## 文档先行

用户可见文案、命令帮助、状态字段变更前先更新 `README.md` 与架构文档。

## Debug 规则（Log-First）

1. 先复现输入与输出文本，再映射到 `ui/render.py`。  
2. 先确认数据来源字段（runtime/session/service），再改渲染模板。  
3. 需要双语文案时保持中英一致，不遗漏关键字段。

## 允许改动

1. 文本渲染函数与格式化细节。  
2. doctor/repl 帮助信息结构。  
3. 与 service/interaction 的提示文案对齐。

## 禁止改动

1. 输出 thought/推理片段到用户界面。  
2. 删除关键可观测字段（provider_id/run_id/状态摘要）。  
3. 在未同步测试与文档时改动帮助菜单语义。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_cli_rendering.py`
2. `PYTHONPATH=src pytest -q tests/test_tui_input_submit.py tests/test_tui_slash_commands.py`
3. `PYTHONPATH=src pytest -q tests/test_readme_examples.py`

## 完成定义（DoD）

1. 关键用户提示准确、双语一致。  
2. 输出不泄露 thought，且保留排障关键信息。  
3. 文档与渲染结果一致。
