# SysDialogue 提交材料总览

本文档汇总本次作品提交所需材料，并给出阅读顺序、打包范围和复现入口。

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

## 2. 提交包范围

书面材料：

- `submission_materials/`
- `RUNNING.md`
- `framework/claudeplan9.md`

工程材料：

- `sysdialogue/`
- `framework/`
- `scripts/`
- `tests/`
- `pyproject.toml`
- `requirements.txt`
- `requirements-dev.txt`

视频材料：

- `video/演示视频.mp4`

提交前需排除真实密钥、私钥、服务器密码、生产 IP、未脱敏日志和本机 `.env`。

## 3. 阅读顺序

1. `01_agent_configuration.md`：了解系统配置、入口和运行模式。
2. `04_decision_paths.md`：查看自然语言请求如何转化为工具执行。
3. `03_tools_and_capabilities.md`：核对工具和 workflow 覆盖范围。
4. `05_self_test_and_validation.md`：按评测指令复现实测场景。
5. `evidence/verification_log_2026-04-25.md`：查看本地验证结果。
6. `06_design_explanation.md`：查看架构设计、实现深度和取舍。
7. `07_review_findings_closure.md`：查看审查问题闭环。

## 4. 复现入口

```powershell
python -m sysdialogue.app.cli --verify
python -m sysdialogue.app.cli --demo
python -m sysdialogue.app.cli
python -m sysdialogue.app.cli --simple
```

远程 Linux 目标机：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519
```

高能力模式：

```powershell
python -m sysdialogue.app.cli --break-glass
```

## 5. 现场启动说明

向评委演示时，可按以下顺序说明：

1. 进入项目根目录并激活 Python 环境。

```powershell
cd D:\项目\Nexus
.\.venv\Scripts\Activate.ps1
```

2. 确认依赖已安装。

```powershell
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

3. 配置模型接口。提交材料不包含真实密钥，现场通过环境变量或 `.env` 注入。

```powershell
$env:OPENAI_API_KEY="<现场密钥>"
$env:OPENAI_BASE_URL="<兼容接口地址，可为空>"
$env:OPENAI_MODEL="<模型名称>"
```

4. 先执行自检，不调用模型接口，用于证明工程、工具注册和安全规则可正常加载。

```powershell
python -m sysdialogue.app.cli --verify
```

自检重点看四项：

- 注册工具：`37 static + 6 meta`
- 内置 workflow：`10`
- 安全规则：`RiskClassifier`、`CommandSafetyChecker`、`RemoteLockoutChecker`
- 配置状态：模型、接口、动态工具、部署模式

5. 启动交互入口。

```powershell
python -m sysdialogue.app.cli
```

启动后进入 TUI。评委可直接输入自然语言任务，例如：

```text
检查系统版本、负载、磁盘和端口。
```

系统会展示任务卡片、工具调用、风险审批、执行结果和验证证据。

6. 需要远程 Linux 目标机时，通过 SSH 模式启动。

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519
```

此时控制端仍在本机，工具执行目标切换为远程服务器。远程模式会启用防锁门规则，停止 `sshd`、阻断当前 SSH 端口等操作会被拒绝。

7. 需要展示高能力动态命令场景时，显式启用 break-glass。

```powershell
python -m sysdialogue.app.cli --break-glass
```

该模式允许 DynTool 使用 shell 字符串、管道、重定向和复合命令；凭证泄露式提权、毁盘命令、远程 SSH 自锁等 HARD-BLOCK 仍会被拒绝。

8. 运行结束后，可向评委展示本地证据目录。

```powershell
Get-ChildItem $env:USERPROFILE\.sysdialogue\sessions
Get-ChildItem $env:USERPROFILE\.sysdialogue\tasks
Get-ChildItem $env:USERPROFILE\.sysdialogue\audit
Get-ChildItem $env:USERPROFILE\.sysdialogue\traces
```

这些文件用于证明任务过程、工具执行、安全判断、审批和最终结果均有记录。
