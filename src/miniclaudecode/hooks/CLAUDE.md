# hooks 目录约定

本目录实现 MiniClaudeCode Hook 规则系统。

## 文件职责
- `core.py`：领域模型、枚举、异常、诊断类型；不做 IO。
- `loader.py`：发现和解析项目级、用户级 YAML 规则文件。
- `validator.py`：集中校验规则结构、事件、条件、动作、拦截和异步限制。
- `registry.py`：保存当前有效规则、覆盖结果和加载诊断，支持安全重载。
- `matcher.py`：按 HookContext 执行 exact / not / regex / glob 条件匹配。
- `template.py`：渲染动作模板变量，缺失变量替换为空字符串。
- `actions.py`：执行 shell、prompt_injection、http 和预留 sub_agent 动作。
- `engine.py`：触发规则、管理 once 状态、处理拦截、隔离 Hook 执行错误。

## 约束
- Hook 核心模块不依赖 Textual。
- Hook 诊断不写入普通 session history。
- prompt injection 只生成 `DynamicInstruction`。
- 工具执行前拦截必须同步，不能后台执行。
- shell 和 HTTP 默认需要确认，`trusted: true` 只跳过 Hook 动作确认，不绕过危险命令检查。
- sub_agent 第一阶段只预留，不启动真实子 Agent。
- Hook 自身失败只产生诊断，不中断 Agent 主流程。
