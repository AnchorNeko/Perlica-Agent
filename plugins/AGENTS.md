# AGENTS.md（plugins）

## 目录职责

`plugins/` 存放插件 manifest 与入口文件（当前主要用于 PluginManager 校验与报告）。

## 文档先行

插件规范变更需文档先行（常规强制）。

## Debug 规则（Log-First Debug）

1. 插件问题先看 `doctor` 与 plugin 加载日志，再改 manifest。  
2. 使用日志中的 `plugin_id/core_api/requires/error` 关键词定位配置问题。  
3. 调整 manifest 规范时补充清晰错误日志，便于快速定位字段缺失或兼容错误。

## 允许改动

1. 新增/更新 `plugin.toml`。  
2. 修正 `entry` 目标文件。  
3. 调整 capability/requires 声明。
4. provider 插件声明 ACP 传输与适配能力字段。

## 禁止改动

1. 缺失必填字段：`id/name/version/kind/entry/core_api/capabilities/requires`。  
2. 声明与实现文件不一致。  
3. 破坏 `core_api` 兼容约束而不更新说明。
4. provider 插件缺失 ACP 约束字段却标记为主 provider。

## 改动前检查

1. `entry` 对应文件是否存在。  
2. `requires` 是否存在依赖环风险。  
3. `core_api` 是否匹配 core major=2。
4. provider manifest 是否声明：
   - `transport=acp`
   - `adapter_capabilities`
   - `schema_version`

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_plugin_loading.py`
2. `PYTHONPATH=src pytest -q tests/test_doctor.py tests/test_doctor_format.py`

## 常见陷阱

1. 重复 plugin id 被静默忽略。  
2. cycle 检测导致整环失效。  
3. 误以为插件代码会被 runtime 自动执行（当前不是）。
4. provider plugin 未声明 `transport=acp` 但被当作默认 provider 使用。

## 完成定义（DoD）

1. manifest 校验通过且语义清晰。  
2. 相关测试通过。  
3. 文档同步。
