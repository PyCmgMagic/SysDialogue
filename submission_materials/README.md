# SysDialogue 提交材料总览

> 适用要求：截图中的 4.2.2「Agent 形态」、4.3「自测与验证材料」、4.4「设计说明文档」。
> 当前代码基线：`framework/claudeplan9.md`，提交基线：`main @ b2a85cc` 之后的文档材料。

## 材料目录

| 文件 | 对应要求 | 用途 |
| --- | --- | --- |
| `01_agent_configuration.md` | Agent 配置说明 | 说明模型/API、运行入口、远程目标机、持久化状态、安全门、审批与审计配置。 |
| `02_core_prompt.md` | 核心 Prompt 文本 | 给出当前核心系统提示词结构、固定约束、ReAct 协议、执行模式与安全摘要。 |
| `03_tools_and_capabilities.md` | 工具及能力定义文档 | 列出 37 个静态工具、6 个元工具、10 个 workflow、DynTool、Skills、Hooks、Role Handoff。 |
| `04_decision_paths.md` | 关键业务场景决策逻辑 | 说明意图识别、工具选择依据、执行路径、风险审批、回滚与验证链路。 |
| `05_self_test_and_validation.md` | 自测与验证材料 | 提供演示场景、自然语言输入示例、可观测输出、日志路径、视频录制脚本和预测关注点。 |
| `06_design_explanation.md` | 设计说明文档 | 说明整体架构、模块边界、实现进度、技术选型与设计取舍。 |
| `07_review_findings_closure.md` | 审查问题闭环说明 | 对用户列出的 review findings 给出对应代码位置、验证方式和提交材料说明口径。 |
| `evidence/verification_log_2026-04-25.md` | 观测输出/验证日志 | 记录本地预检、自检、测试、编译验证的命令与结果。 |

## 推荐提交方式

1. 将整个 `submission_materials/` 目录作为书面材料提交。
2. 视频材料按 `05_self_test_and_validation.md` 的「视频录制脚本」录制。
3. 若需要压缩包，可包含：
   - `submission_materials/`
   - `RUNNING.md`
   - `CLAUDE.md`
   - `framework/claudeplan9.md`
   - 关键代码目录：`sysdialogue/agent/`、`sysdialogue/tools/`、`sysdialogue/workflows/`、`sysdialogue/ui/`

## 评审阅读顺序

1. 先读 `01_agent_configuration.md` 了解系统如何配置和运行。
2. 再读 `04_decision_paths.md` 理解 Agent 如何从自然语言走到工具执行。
3. 查 `03_tools_and_capabilities.md` 对照工具覆盖范围。
4. 看 `05_self_test_and_validation.md` 和 `evidence/verification_log_2026-04-25.md` 复现自测。
5. 最后读 `06_design_explanation.md` 和 `07_review_findings_closure.md` 看架构取舍与审查闭环。

