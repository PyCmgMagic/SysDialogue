# SysDialogue v6 运行手册

本文档说明如何从零开始安装、配置、运行和验证 SysDialogue v6。命令示例默认在仓库根目录执行：

```powershell
cd D:\项目\Nexus
```

SysDialogue 是面向 Linux 服务器运维场景的智能代理。它通过 OpenAI-compatible Chat Completions API 调用模型，可从 TUI、轻量 CLI、Web 控制台或计划任务入口运行，并通过任务级 ReAct runtime、固定工具集、风险分级、人工确认、备份回滚和审计日志来约束操作。

## 1. 运行前提

### 1.1 系统要求

- Python 版本：`>= 3.11`
- 推荐运行环境：Linux
- Windows 支持：可用于开发、自检、Web/TUI/CLI 入口调试；本地 `--demo` 会提示不支持 Linux 巡检演示，这是预期行为
- 远程运维目标机：Linux，需 SSH 可达

检查 Python：

```powershell
python --version
```

如果 Windows 上 `python` 不可用，可尝试：

```powershell
py -3.11 --version
```

### 1.2 Git 预检

开发或修改前建议先运行仓库内置预检脚本：

```powershell
python scripts\git_preflight.py
```

该脚本会检查当前分支、工作区状态，执行 `git fetch --all --prune`，并且只在工作区干净时同步最新代码。

只查看状态：

```powershell
git status --short --branch
```

## 2. 安装依赖

### 2.1 Windows PowerShell

创建虚拟环境：

```powershell
py -3.11 -m venv .venv
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止脚本执行，可只在当前窗口放开策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

升级 pip 并安装项目：

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

### 2.2 Linux / macOS

创建并激活虚拟环境：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

升级 pip 并安装项目：

```bash
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

### 2.3 依赖说明

主要运行依赖来自 `requirements.txt` / `pyproject.toml`：

- `openai`：OpenAI-compatible Chat Completions 客户端
- `paramiko`：远程 SSH 执行
- `textual`：TUI 界面
- `fastapi`、`uvicorn`：Web 控制台
- `click`：CLI 入口
- `pyyaml`、`jinja2`：工作流 YAML 与模板渲染
- `python-dotenv`：读取 `.env`
- `pytest`：开发测试

## 3. 配置 API Key

`--verify` 和 `--demo` 不调用 LLM API，可以不配置 Key。TUI、Simple CLI、Web 控制台需要 `OPENAI_API_KEY` 和模型名。

### 3.1 使用环境变量

Windows PowerShell：

```powershell
$env:OPENAI_API_KEY="你的_api_key"
$env:OPENAI_BASE_URL="你的_openai_compatible_base_url"
$env:OPENAI_MODEL="你的模型名"
```

Linux / macOS：

```bash
export OPENAI_API_KEY="你的_api_key"
export OPENAI_BASE_URL="你的_openai_compatible_base_url"
export OPENAI_MODEL="你的模型名"
```

### 3.2 使用 `.env`

在仓库根目录创建 `.env`：

```dotenv
OPENAI_API_KEY=你的_api_key
OPENAI_BASE_URL=你的_openai_compatible_base_url
OPENAI_MODEL=你的模型名
SYSDIALOGUE_COMPETITION_MODE=true
SYSDIALOGUE_MAX_ITER=25
```

运行时显式指定：

```powershell
python -m sysdialogue.app.cli --env-file .env --simple
```

如果不指定 `--env-file`，程序也会尝试读取当前目录下的 `.env`。

## 4. 命令入口总览

安装为可编辑包后，可使用两种入口。

推荐的模块入口：

```powershell
python -m sysdialogue.app.cli --help
```

如果 console script 已正确安装，也可以使用：

```powershell
sysdialogue --help
```

当前主要选项：

```text
--verify                   系统自检，不调 API
--demo                     演示 security_audit 工作流，不调 API
--remote USER@HOST[:PORT]  远程 SSH 模式
--ssh-key PATH             SSH 私钥文件路径
--dev                      关闭竞赛模式，开启 DynTool
--model TEXT               覆盖默认模型
--env-file PATH            指定 .env 文件
--workflows-dir PATH       指定工作流 YAML 目录
--run-scheduled-job TEXT   执行已注册的计划任务
--simple                   启动轻量命令行模式
--web                      启动 Web 控制台
--host TEXT                Web 监听地址，默认 127.0.0.1
--port INTEGER             Web 监听端口，默认 8000
```

## 5. 运行方式

### 5.1 自检模式：`--verify`

用途：

- 探测当前环境能力
- 列出已注册工具和工作流
- 检查安全规则、配置状态
- 不调用 LLM API

运行：

```powershell
python -m sysdialogue.app.cli --verify
```

常见结果：

- 配置了 `OPENAI_API_KEY` 和模型：通常返回 `0`
- 未配置 `OPENAI_API_KEY` 或模型：会列出 warning，返回 `1`
- Windows 本地：Linux 能力项可能显示 `unknown`，这是正常开发环境现象

### 5.2 演示模式：`--demo`

用途：

- 不调用 LLM API
- 直接跑内置 `security_audit` 工作流
- 用于验证工作流引擎、工具注册、安全链路

Linux 本地运行：

```bash
python -m sysdialogue.app.cli --demo
```

Windows 本地运行会得到类似提示：

```text
[UNSUPPORTED] Local demo requires a Linux host.
```

这是预期行为，因为 `security_audit` 是 Linux 巡检演示。

远程 Linux 演示：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519 --demo
```

### 5.3 TUI 模式：默认入口

用途：

- 主要交互入口
- 使用 Textual TUI
- 支持对话、计划预览、风险确认、审计查看、环境查看

运行：

```powershell
python -m sysdialogue.app.cli
```

指定 `.env`：

```powershell
python -m sysdialogue.app.cli --env-file .env
```

远程目标机：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519
```

TUI 快捷键：

- `Ctrl+C`：取消当前执行中的任务或 workflow，并触发可用的回滚链路
- `Ctrl+D`：退出应用
- `F3`：查看审计面板
- `F4`：查看环境画像面板

### 5.4 Simple CLI：`--simple`

用途：

- 轻量 stdin/stdout 对话
- 适合终端快速测试
- 需要 `OPENAI_API_KEY` 和模型名

运行：

```powershell
python -m sysdialogue.app.cli --simple
```

远程目标机：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519 --simple
```

交互命令：

- 输入自然语言运维需求开始对话
- 输入 `cancel` 请求取消当前执行
- 输入 `quit` 或 `exit` 退出

示例输入：

```text
帮我检查磁盘空间和占用最大的目录
```

```text
检查 nginx 服务状态和最近 50 行日志
```

### 5.5 Web 控制台：`--web`

用途：

- 浏览器界面
- 提供对话区、计划区、风险确认区、执行时间线、结果总结
- 需要 `OPENAI_API_KEY` 和模型名

启动：

```powershell
python -m sysdialogue.app.cli --web --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

连接远程目标机时，Web 服务仍然启动在当前控制端本机；`--remote` 只表示后端工具通过 SSH/SFTP 作用于目标 Linux 主机，不需要在目标机上单独启动 Web 服务。

本机 Web 控制台连接远程目标机：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519 --web --host 127.0.0.1 --port 8000
```

Web API：

```text
GET  /
GET  /api/session/{id}/state
POST /api/session/{id}/turn
POST /api/session/{id}/confirm
POST /api/session/{id}/cancel
```

## 6. 远程 SSH 模式

### 6.1 基本命令

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519 --simple
```

参数说明：

- `--remote user@host:port`：目标 Linux 主机
- `--ssh-key PATH`：私钥路径
- 未写端口时默认 `22`

### 6.2 known_hosts 要求

远程模式使用 Paramiko，并启用 `RejectPolicy`。这意味着目标主机必须已经在本机 `known_hosts` 中，否则连接会被拒绝。

首次连接前，先用系统 SSH 建立一次信任：

```powershell
ssh -p 22 user@example.com
```

确认 fingerprint 后退出，再运行 SysDialogue。

### 6.3 远程模式作用范围

远程模式下，工具应作用于目标机：

- 文件读写通过目标机文件访问层
- 备份索引写入目标机 `~/.sysdialogue/backups/`
- cron 索引写入目标机 `~/.sysdialogue/cron_index.json`
- 配置校验命令在目标机执行
- hosts、cron、服务、包管理、容器等操作针对目标机

## 7. 计划任务与 cron

### 7.1 创建计划任务

计划任务通常通过对话或 workflow 创建，例如：

```text
每 5 分钟检查 10.0.0.10:443 的 TCP 连通性
```

内部会调用 `manage_cron`，并写入：

- 用户 cron：`crontab`
- 系统 cron：`/etc/cron.d/sysdialogue-<job_id>`
- 元数据索引：`~/.sysdialogue/cron_index.json`

调度命令统一为：

```bash
sysdialogue --run-scheduled-job <job_id>
```

### 7.2 手动执行计划任务

本地任务：

```powershell
python -m sysdialogue.app.cli --run-scheduled-job job_xxxxxxxx
```

远程索引中的任务：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519 --run-scheduled-job job_xxxxxxxx
```

### 7.3 非交互安全规则

计划任务是非交互入口，因此不会弹出人工确认：

- `SAFE` / `WARN-LOW`：允许执行并写审计
- `WARN-HIGH` / `BLOCK`：入口拒绝执行并写审计
- workflow 中如果包含 `confirm`、`approval`、`input`，也会被入口拒绝

## 8. 工作流

内置工作流位于：

```text
sysdialogue/workflows/
```

当前内置 10 个：

```text
container_rollout.yaml
disk_cleanup.yaml
file_edit.yaml
new_user.yaml
port_scan.yaml
rollback_config.yaml
safe_config_patch.yaml
scheduled_health_check.yaml
security_audit.yaml
service_restart.yaml
```

指定自定义工作流目录：

```powershell
python -m sysdialogue.app.cli --workflows-dir .\my_workflows --simple
```

工作流支持的 step 类型：

- `tool_call`：调用静态工具
- `confirm`：人工确认
- `approval`：人工审批
- `display`：展示信息
- `input`：收集用户输入

## 9. 安全模型

SysDialogue 的执行链路大致为：

```text
用户自然语言
  -> ReActRunner 创建任务并记录事件时间线
  -> LLM 生成 tool_use / workflow 路由 / finish_task
  -> RiskClassifier 风险分级
  -> RemoteLockout / CommandSafety 追加检查
  -> 必要时人工确认
  -> SafeExecutor 执行
  -> tool_result 反馈给模型继续观察、修正或验证
  -> finish_task 通过完成门校验
  -> AuditLog 记录
  -> 返回结果或触发回滚
```

风险等级：

- `SAFE`：直接执行并审计
- `WARN-LOW`：允许执行，但记录低风险提示
- `WARN-HIGH`：需要用户确认；计划任务中直接拒绝
- `BLOCK`：禁止执行

重要保护：

- 禁止读取私钥、token、`.env`、云凭据等敏感凭证
- 禁止自动覆盖关键系统文件，如 `/etc/passwd`、`/etc/shadow`、`/etc/ssh/sshd_config`
- 禁止危险远程锁门操作，如断开 SSH 端口、停止远程 SSH 服务
- 禁止 privileged / host network 容器
- 私网批量探测会升级风险

## 10. 审计与数据目录

默认数据目录：

```text
~/.sysdialogue/
```

常见文件：

```text
~/.sysdialogue/audit/<session_id>.jsonl
~/.sysdialogue/backups/
~/.sysdialogue/backups/index.json
~/.sysdialogue/cron_index.json
```

远程模式下，目标机文件访问相关数据写在目标机用户的 `~/.sysdialogue/` 下；本机审计日志仍由当前运行进程写入本机用户目录。

审计日志是 JSONL，每行一条记录，包含：

- 环境画像
- 风险判定
- 工具调用
- 命令轨迹
- workflow step
- 最终状态

## 11. 开发验证

安装开发依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

运行测试：

```powershell
python -m pytest -q
```

编译检查：

```powershell
python -m compileall -q sysdialogue tests
```

推荐完整本地检查：

```powershell
python scripts\git_preflight.py
python -m pytest -q
python -m compileall -q sysdialogue tests
python -m sysdialogue.app.cli --verify
python -m sysdialogue.app.cli --demo
```

注意：

- Windows 上 `--demo` 返回 unsupported 是预期，不代表测试失败
- 未配置 `OPENAI_API_KEY` 或模型时 `--verify` 会返回 warning

## 12. 常见问题

### 12.1 `OPENAI_API_KEY` 或模型缺失

原因：TUI、Simple CLI、Web 需要 API Key。

解决：

```powershell
$env:OPENAI_API_KEY="你的_api_key"
$env:OPENAI_BASE_URL="你的_openai_compatible_base_url"
$env:OPENAI_MODEL="你的模型名"
python -m sysdialogue.app.cli --simple
```

或使用 `.env`：

```powershell
python -m sysdialogue.app.cli --env-file .env --simple
```

### 12.2 `sysdialogue` 命令不存在

原因：console script 没有安装到当前虚拟环境，或虚拟环境没有激活。

解决：

```powershell
python -m pip install -e .
python -m sysdialogue.app.cli --help
```

模块入口始终可用，优先使用：

```powershell
python -m sysdialogue.app.cli
```

### 12.3 Windows 本地 `--demo` 不支持

这是预期行为。`--demo` 默认跑 Linux 巡检工作流，需要 Linux 主机。

可选方案：

- 在 Linux 上运行
- 使用 `--remote` 指向 Linux 目标机

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519 --demo
```

### 12.4 远程 SSH 被拒绝

常见原因：

- 目标主机不在 `known_hosts`
- 私钥路径错误
- 用户名或端口错误
- 目标机 SSH 服务不可达

先用系统 SSH 验证：

```powershell
ssh -p 22 user@example.com
```

成功后再运行 SysDialogue。

### 12.5 PowerShell 激活虚拟环境失败

临时放开当前窗口执行策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 12.6 `--verify` 返回 1

如果唯一问题是：

```text
OPENAI_API_KEY is not configured
OPENAI_MODEL or --model is not configured
```

这是配置警告，不代表程序损坏。配置 API Key 后再运行即可。

### 12.7 计划任务执行失败

检查：

```powershell
python -m sysdialogue.app.cli --run-scheduled-job job_xxxxxxxx
```

再查看：

```text
~/.sysdialogue/cron_index.json
~/.sysdialogue/audit/
```

远程任务需要在同一个目标机上下文中执行；如果索引在远程目标机，就要加 `--remote` 参数手动验证。

## 13. 推荐首次运行流程

Windows 开发机：

```powershell
cd D:\项目\Nexus
python scripts\git_preflight.py
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m sysdialogue.app.cli --verify
```

配置 API Key 后启动 Simple CLI：

```powershell
$env:OPENAI_API_KEY="你的_api_key"
$env:OPENAI_BASE_URL="你的_openai_compatible_base_url"
$env:OPENAI_MODEL="你的模型名"
python -m sysdialogue.app.cli --simple
```

Linux 目标机完整体验：

```bash
cd /path/to/Nexus
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
export OPENAI_API_KEY="你的_api_key"
export OPENAI_BASE_URL="你的_openai_compatible_base_url"
export OPENAI_MODEL="你的模型名"
python -m sysdialogue.app.cli --verify
python -m sysdialogue.app.cli --demo
python -m sysdialogue.app.cli
```

远程 Linux 运维：

```powershell
cd D:\项目\Nexus
.\.venv\Scripts\Activate.ps1
$env:OPENAI_API_KEY="你的_api_key"
$env:OPENAI_BASE_URL="你的_openai_compatible_base_url"
$env:OPENAI_MODEL="你的模型名"
ssh -p 22 user@example.com
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519 --simple
```

## 14. 当前已知限制

- Linux 真机端到端场景仍建议继续做最终验收，尤其是远程 SSH、system cron、容器 rollout、配置回滚
- Windows 本地只作为开发与入口 smoke 环境，不等价于 Linux 运维目标机
- Web 控制台是轻量最小可用版本，当前 session 存储在进程内存中
- 计划任务入口是非交互模式，高风险任务会被拒绝，不会等待人工确认
- 真实 OpenAI-compatible 服务需要支持 Chat Completions `tool_calls`；不支持时 ReAct runtime 会明确报错，不会退化成裸聊天
