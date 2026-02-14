# AGENTS.md（src/perlica）

## 目录职责

`src/perlica` 是运行时主包，包含 CLI、kernel、provider、service、tui、mcp、skills、tools。

## 文档先行

继承仓库总控：文档先行（常规强制）。  
涉及用户可见行为时，先更新 `README.md` 与架构文档。
涉及 ACP 配置项、ACP 事件名、provider 错误语义变化时，也必须先更新文档。

## Debug 规则（Log-First Debug）

1. debug 任务先看日志证据，再改代码：优先使用 `debug.log.jsonl`、`doctor` 输出、失败测试栈。  
2. 依据日志中的 `component/event_type/session_id/run_id/trace_id/error` 关键词，用 `rg` 精确定位到模块文件。  
3. 开发改动要补关键结构化日志（异常路径、状态切换、跨模块边界），避免只靠猜测排障。
4. ACP 超时排障时，先区分 `session/prompt` 与其他方法：`session/prompt` 允许长时等待，不应因本地硬超时失败。
5. `opencode` 空回复排障时，先核对 `session/update` 是否有 `agent_message*` 文本，再判断合同失败是否误报。

## 允许改动

1. 包级组织与模块拆分。  
2. 非行为变更的重构（保持对外行为一致）。  
3. 跨子模块集成（需同步测试）。

## 禁止改动

1. 跨子目录修改但不更新对应目录 `AGENTS.md` 约束。  
2. 删除入口模块（`cli.py`/`repl.py`）而不迁移测试。  
3. 无测试覆盖的行为变更。
4. 在包级层面重新引入 provider 方言耦合，绕开 ACP 统一抽象。
5. 把 `session/prompt` 恢复为本地硬超时策略。

## 改动前检查

1. 明确目标子模块边界（kernel/service/mcp/...）。  
2. 定位现有测试文件与断言。  
3. 判断是否是“行为变更”。
4. 若是 debug，先确认可复现日志与定位路径。

## 改动后必跑

1. `PYTHONPATH=src pytest -q`
2. 若改 README，同步跑 `tests/test_readme_examples.py`。
3. 若改 provider 交互语义，补跑 ACP 协议相关回归（包含 contract/degrade 事件断言）。
4. 若改 `opencode` 解析，补跑“主解析缺失但可回退文本存在”的专项测试。

## 常见陷阱

1. 忽略 session/provider 锁定语义。  
2. 忽略服务模式与交互模式差异。  
3. 文档更新滞后于实现。

## 完成定义（DoD）

1. 变更与子目录规范一致。  
2. 文档与代码一致。  
3. 关键回归通过。
4. ACP-first 约束未被破坏（含 `provider_locked` 与降级可观测性）。
5. provider 文本回退策略不泄露 thought 内容，仅输出用户可见回复字段。
