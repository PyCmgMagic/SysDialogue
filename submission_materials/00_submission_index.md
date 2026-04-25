# SysDialogue 提交材料索引

本文档用于索引 `submission_materials/` 目录中的书面材料。项目介绍、安装、启动和现场演示流程见根目录 `README.md`。

## 1. 材料目录

| 文件 | 对应要求 | 内容 |
| --- | --- | --- |
| `01_agent_configuration.md` | Agent 配置说明 | 运行入口、模型配置、远程目标机、持久化状态、安全档位、审批与审计配置。 |
| `02_core_prompt.md` | 核心 Prompt 文本 | 系统提示词结构、固定约束、ReAct 协议、执行模式、安全摘要。 |
| `03_tools_and_capabilities.md` | 工具及能力定义文档 | 静态工具、元工具、workflow、DynTool、Skills、Hooks、Role Handoff。 |
| `04_decision_paths.md` | 关键场景决策逻辑 | 自然语言到工具执行的路径、风险审批、回滚与验证链路。 |
| `05_self_test_and_validation.md` | 自测与验证材料 | 评测指令、演示场景、可观测输出、视频录制脚本。 |
| `06_design_explanation.md` | 设计说明文档 | 总体架构、模块边界、实现进度、技术选型与设计取舍。 |
| `07_review_findings_closure.md` | 审查问题闭环说明 | 已知审查问题的处理口径、代码位置和验证方式。 |
| `evidence/verification_log_2026-04-25.md` | 验证日志 | 预检、编译、测试、自检命令及结果摘要。 |

## 2. 阅读顺序

1. `01_agent_configuration.md`：了解系统配置、入口和运行模式。
2. `04_decision_paths.md`：查看自然语言请求如何转化为工具执行。
3. `03_tools_and_capabilities.md`：核对工具和 workflow 覆盖范围。
4. `05_self_test_and_validation.md`：按评测指令复现实测场景。
5. `evidence/verification_log_2026-04-25.md`：查看本地验证结果。
6. `06_design_explanation.md`：查看架构设计、实现深度和取舍。
7. `07_review_findings_closure.md`：查看审查问题闭环。

## 3. 配套材料

- 项目运行说明：根目录 `README.md`、`RUNNING.md`
- 验证日志：`evidence/verification_log_2026-04-25.md`
- 演示视频：`video/演示视频.mp4`
