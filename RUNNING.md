# SysDialogue 部署运行说明

## 1. 说明

本文档用于说明 SysDialogue 的安装、配置、启动、远程连接、权限模式和验证方法。

实现以 `framework/claudeplan9.md` 为设计基线。`framework/claudeplan6.md`、`framework/claudeplan7.md`、`framework/claudeplan8.md` 仅作为历史参考。

## 2. 环境要求

- Python `>= 3.11`
- 运行环境：Linux 优先
- Windows 可作为控制端运行 TUI 和 `--verify`
- 远程目标主机需支持 SSH 访问，优先使用 Linux 服务器

## 3. 安装

进入项目根目录：

```powershell
cd D:\项目\Nexus
```

### 3.1 Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

如 PowerShell 限制脚本执行，可在当前进程临时放开：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 3.2 Linux / macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

## 4. 接口配置

交互式入口需要以下配置：

- `OPENAI_API_KEY`
- `OPENAI_MODEL`

可选配置：

- `OPENAI_BASE_URL`

### 4.1 环境变量

```powershell
$env:OPENAI_API_KEY="your_api_key"
$env:OPENAI_BASE_URL="https://your-openai-compatible-endpoint/v1"
$env:OPENAI_MODEL="your-model-name"
```

### 4.2 `.env` 文件

在项目根目录创建 `.env`：

```dotenv
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_MODEL=your-model-name
SYSDIALOGUE_MAX_ITER=160
```

`SYSDIALOGUE_MAX_ITER` 用于限制单个任务的最大迭代次数。任务预算会按场景动态调整：

- 问答、说明类任务：约 `20`
- 常规运维任务：约 `80`
- 复杂变更、工作流、DynTool 任务：约 `140`

最终取值限制在 `20..300`。

## 5. 扩展配置

SysDialogue 支持项目级和用户级扩展：

- 项目 Skill：`.sysdialogue/skills/<name>/SKILL.md`
- 用户 Skill：`~/.sysdialogue/skills/<name>/SKILL.md`
- 项目 Hook：`.sysdialogue/hooks.json`
- 用户 Hook：`~/.sysdialogue/hooks.json`
- 目标机器配置：`~/.sysdialogue/targets/`

Skill 用于补充操作流程和领域规则。Hook 可用于通知、上下文注入或受控命令执行。

## 6. 安全配置档

安全配置由 `SYSDIALOGUE_SAFETY_PROFILE` 控制：

```powershell
$env:SYSDIALOGUE_SAFETY_PROFILE="standard"
```

可选值：

- `standard`：默认配置，执行限制最严格。
- `operator`：面向受控运维场景，适度放宽操作能力。
- `break_glass`：应急高能力模式，允许 DynTool 执行 shell 字符串、管道、重定向和复合命令，并默认跳过动态命令确认。

也可通过命令行启用 break-glass：

```powershell
python -m sysdialogue.app.cli --break-glass
```

兼容旧配置：

```powershell
$env:SYSDIALOGUE_OPERATOR_MODE="1"
```

该配置等价于 `operator`。

以下操作始终拦截：

- 凭证、密码泄露式提权
- 交互式 `su` / `runuser`
- 明显毁盘命令
- 删除根目录或核心系统目录
- 远端 SSH 自锁操作
- 远端防火墙断开 SSH
- 关闭审计或绕过审计链路

## 7. Git 预检

开发或演示前可执行：

```powershell
python scripts\git_preflight.py
```

检查内容：

- 当前分支
- 工作区状态
- 远端同步状态
- 条件允许时执行安全的 `git pull --rebase`

## 8. 启动方式

### 8.1 安装检查

不调用模型接口，可直接用于环境验证：

```powershell
python -m sysdialogue.app.cli --verify
```

### 8.2 内置演示

运行内置 `security_audit` 工作流：

```powershell
python -m sysdialogue.app.cli --demo
```

本地 demo 面向 Linux。Windows 环境下会返回不支持提示。

### 8.3 TUI

```powershell
python -m sysdialogue.app.cli
```

### 8.4 定时任务回调

```powershell
python -m sysdialogue.app.cli --run-scheduled-job <job_id>
```

## 9. 远程 SSH 模式

控制端在本机运行，命令执行目标切换到远程 Linux 主机：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519
```

密码认证：

```powershell
$env:SYSDIALOGUE_SSH_PASSWORD="your_ssh_password"
python -m sysdialogue.app.cli --remote user@example.com:22
```

本地临时测试也可直接传入密码：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-password your_ssh_password
```

说明：

- `--remote` 只改变工具执行目标。
- 首次连接的 SSH 主机会写入 `known_hosts`。
- 主机密钥发生变化时，连接会被拒绝。

## 10. TUI 快捷键

- `F2`：会话历史
- `F3`：审计面板
- `F4`：环境面板
- `Ctrl+C`：取消当前任务或工作流
- `Ctrl+D`：退出

历史会话只恢复上下文，不重放历史命令。

## 11. 持久化数据

运行状态保存在 `~/.sysdialogue/`：

- `sessions/`
- `tasks/`
- `locks/`
- `policy.json`
- `memory/`
- `traces/`
- `commands/`
- `targets/`

这些数据在 TUI 与定时任务之间共享。重启后，已过期的活跃任务会标记为 `interrupted`；待确认和待输入内容不会自动重放。

## 12. 控制命令

交互式入口支持以下命令：

```text
/status
/resume
/locks
/plan
/audit
/memory
/tools
/permissions
/compact
/skills
/skill <name> [json args]
/skill-reload
/hooks
/forget <memory_id>
/target
/why [tool]
```

示例：

```text
/status
/memory Prefer nginx changes during maintenance windows
/compact nginx service is the current troubleshooting target
/skills
/skill service-triage {"service":"nginx"}
/target set maintenance_window=Sunday 02:00
```

## 13. 执行规则

每个任务均通过 ReAct runtime 执行。核心规则如下：

- 任务完成必须调用 `finish_task`。
- 运维任务完成前必须读取环境状态。
- 变更任务完成前必须执行后置验证。
- 失败的变更不会记录为成功变更。

## 14. 权限、记忆与 Trace

- 权限策略文件：`~/.sysdialogue/policy.json`
- 记忆文件目录：`~/.sysdialogue/memory/`
- Trace 文件目录：`~/.sysdialogue/traces/`

`PermissionPolicy` 支持 `allow / ask / deny`。静态工具保留默认风险分类行为，除非命中更严格的策略规则。记忆写入前会对明显敏感信息做脱敏处理。Trace 以 JSONL 形式记录执行过程，便于审计和复盘。

## 15. DynTool

DynTool 支持两种执行模式：

- `argv`：参数数组模式，适合结构化命令。
- `shell`：shell 字符串模式，适合复合命令、管道和重定向。

执行顺序：

1. 静态工具
2. 内置 workflow
3. inline `execute_dynamic_tool`
4. `propose_dynamic_tool`

不同安全配置档的 DynTool 行为：

- `standard`：默认确认，高风险命令严格限制。
- `operator`：允许更多受控运维操作，保留较强确认和阻断。
- `break_glass`：允许 shell 字符串、管道、重定向、复合命令和受控 sudo；多数语义阻断降级为高风险告警。

所有模式均保留：

- 命令安全判断
- 语义风险映射
- 审计记录
- 完成门禁
- 变更后验证

## 16. TUI

- 终端交互界面
- 提供任务卡片、审计面板、环境面板
- 支持折叠技术细节

## 17. 验证命令

回归命令：

```powershell
python -m pytest -q
python -m compileall -q sysdialogue tests
python -m sysdialogue.app.cli --verify
```

Break-glass 回归：

```powershell
$env:SYSDIALOGUE_SAFETY_PROFILE="break_glass"
python -m pytest -q
python -m sysdialogue.app.cli --verify
```

真实 Linux 主机补充验证：

- `safe_config_patch`
- 真实服务回滚链路
- 远程变更与后置验证
- 系统 cron 执行
- TUI / scheduled job 锁竞争
- shell DynTool、远程 shell DynTool、受控 sudo shell

## 18. 故障排查

### 18.1 缺少接口配置

检查：

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_BASE_URL`

### 18.2 工具调用失败

检查：

- 当前模型是否支持 `tool_calls`
- 任务描述是否过于宽泛
- 是否命中权限策略或安全拦截

### 18.3 本地 demo 不支持

Windows 本地运行 `--demo` 返回不支持提示属于预期行为。请在 Linux 上运行，或连接远程 Linux 目标机。

### 18.4 远程 SSH 无法连接

检查：

- host / port / user
- SSH 私钥路径
- `known_hosts`
- 密码或密钥权限
- 目标机 SSH 服务状态

## 19. 快速开始

### 19.1 本地验证

```powershell
python scripts\git_preflight.py
python -m sysdialogue.app.cli --verify
```

### 19.2 本地 TUI

```powershell
python -m sysdialogue.app.cli
```

### 19.3 本地 Break-glass TUI

```powershell
python -m sysdialogue.app.cli --break-glass
```
