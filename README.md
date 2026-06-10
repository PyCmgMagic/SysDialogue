# SysDialogue

SysDialogue 是面向 Linux 服务器运维场景的自然语言操作代理。用户用中文或英文描述目标，系统通过受控工具、内置 workflow 或 DynTool 执行操作，并在执行前后完成环境观察、风险判断、审批、审计和结果验证。

项目重点解决三类问题：

- 把自然语言运维需求转化为可审计的工具执行流程。
- 在服务、文件、用户、容器、计划任务、防火墙等场景中完成观察、变更和验证闭环。
- 对高风险操作提供阻断、审批、回滚和远程 SSH 防锁门保护。

## 1. 核心能力

- TUI 交互入口。
- 本机模式与远程 SSH 模式。
- 37 个静态工具、6 个元工具、10 个内置 workflow。
- ReAct 任务闭环：观察、计划、执行、验证、收口。
- 持久化会话、任务、锁、审计、Trace 和 Memory。
- `standard`、`operator`、`break_glass` 三档安全配置。
- DynTool 支持 `argv` 与 `shell` 两种执行模式。
- 高风险操作审批、HARD-BLOCK 硬拦截、变更后验证。

## 2. 环境要求

- Python `>= 3.11`
- 运行环境：Linux 优先
- Windows 可作为控制端运行 TUI 和自检
- 远程目标机需支持 SSH，优先使用 Linux 服务器

## 3. 安装

进入项目根目录：

```powershell
cd <project_root>
```

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

Linux / macOS：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

## 4. 配置

交互式入口需要模型接口配置。交付包不包含真实密钥，运行时通过环境变量或 `.env` 注入。

PowerShell：

```powershell
$env:OPENAI_API_KEY="<api_key>"
$env:OPENAI_BASE_URL="<兼容接口地址，可为空>"
$env:OPENAI_MODEL="<模型名称>"
```

`.env` 示例：

```dotenv
OPENAI_API_KEY=<redacted>
OPENAI_BASE_URL=
OPENAI_MODEL=
SYSDIALOGUE_MAX_ITER=160
SYSDIALOGUE_SAFETY_PROFILE=standard
```

安全配置档：

- `standard`：默认模式，动态命令确认和阻断策略最保守。
- `operator`：受控运维模式，放宽部分低风险动态命令。
- `break_glass`：应急高能力模式，允许 DynTool shell 字符串、管道、重定向和复合命令；HARD-BLOCK 仍直接拒绝。

## 5. 启动

自检，不调用模型接口：

```powershell
python -m sysdialogue.app.cli --verify
```

内置安全巡检演示，不调用模型接口：

```powershell
python -m sysdialogue.app.cli --demo
```

Linux 服务器本机 TUI：

```bash
python -m sysdialogue.app.cli
```

Windows 控制端连接远程 Linux：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key <ssh_key_path>
```

Web 控制台后端桥接服务：

```powershell
python -m sysdialogue.app.web_api
```

如 8000 端口已被占用：

```powershell
$env:SYSDIALOGUE_WEB_PORT="8010"
python -m sysdialogue.app.web_api
```

然后进入 `web` 目录启动前端，默认连接 `http://127.0.0.1:8000/api`：

```powershell
cd web
npm run dev
```

Break-glass 模式：

```powershell
python -m sysdialogue.app.cli --break-glass
```

## 6. 启动与演示流程

演示流程如下：

1. 进入项目根目录并激活虚拟环境。
2. 执行 `python -m sysdialogue.app.cli --verify`，展示工具注册、workflow、安全规则和配置状态。
3. 执行 `python -m sysdialogue.app.cli` 进入 TUI。
4. 输入只读巡检指令：

```text
检查系统版本、负载、磁盘和端口。
```

5. 展示任务卡片、工具调用、结果摘要和证据。
6. 输入高风险或变更类指令，展示审批、执行、验证和审计。
7. 使用 `--remote` 展示远程执行与 SSH 防锁门。
8. 使用 `--break-glass` 展示复杂动态命令能力。

运行证据目录：

```powershell
Get-ChildItem $env:USERPROFILE\.sysdialogue\sessions
Get-ChildItem $env:USERPROFILE\.sysdialogue\tasks
Get-ChildItem $env:USERPROFILE\.sysdialogue\audit
Get-ChildItem $env:USERPROFILE\.sysdialogue\traces
```

这些文件记录任务过程、工具执行、安全判断、审批和最终结果。
