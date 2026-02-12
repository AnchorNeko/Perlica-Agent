# AGENTS.md（skills）

## 目录职责

`skills` 负责 skill schema、文件加载、触发匹配、优先级排序与 prompt context 拼装。

## 文档先行

skill 触发与优先级语义变化需文档先行（常规强制）。

## Debug 规则（Log-First Debug）

1. debug skill 问题先查看加载/匹配错误日志，再调整规则。  
2. 依据日志中的 `skill_id/trigger/priority/error` 关键词定位加载器和匹配逻辑。  
3. 修改匹配与排序时补充可读日志，便于回归定位顺序问题。

## 允许改动

1. `SkillSpec` 字段校验。  
2. 多路径加载优先级。  
3. 触发匹配与稳定排序。

## 禁止改动

1. 打破“先路径优先、再优先级排序”的确定性。  
2. 允许非对象 skill JSON 且无错误上报。  
3. 改动系统 prompt 组装格式而不验证 runner 注入。
4. 让 Skill 依赖 provider 方言输出字段（必须只依赖标准 `LLMResponse`）。

## 改动前检查

1. 明确是否影响 trigger 匹配逻辑。  
2. 明确是否影响 `list_errors()` 行为。  
3. 确认与 README 中 Skill 注入说明一致。
4. 确认对 provider 协议升级保持无感（通过标准字段消费）。

## 改动后必跑

1. `PYTHONPATH=src pytest -q tests/test_skill_resolution.py`

## 常见陷阱

1. 路径优先级被反转。  
2. 相同 priority 时排序不稳定。  
3. 错误吞掉导致诊断缺失。
4. 依赖特定 provider 文案模式，导致协议切换后选择异常。

## 完成定义（DoD）

1. 选择结果稳定可预测。  
2. 报错可定位。  
3. 文档同步。
