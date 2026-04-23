# SysDialogue — 模块设计文档 v4.1

> AI Hackathon 2026 · 操作系统智能代理  
> v4.1 修订重点：结构化冻结计划、CapabilityProbe 环境探测、审计/复现导出、
> RemoteExecutor 主机指纹校验、OutputSanitizer 脱敏、Workflow 样例修正、
> 稳定性验证矩阵、提交物清单与评分映射补齐

---

## 零、v4 → v4.1 缺陷清单与修订摘要

| 编号 | 严重度 | v4 缺口 | v4.1 修订 |
|---|---|---|---|
| D111 | P1 | `plan_steps` 仅是 `string[]`，展示计划与实际执行计划可能漂移 | 将 `plan_steps` 升级为结构化 `PlanStep[]`，用户输入 `go` 后按冻结计划执行 |
| D112 | P1 | 审计日志未覆盖“实际执行指令、执行路径、可复现、可审计” | 扩展 AuditLog schema，增加 `command_trace`、`decision_trace`、`env_profile`、`prompt_version` |
| D113 | P1 | `RemoteExecutor` 默认信任未知主机 | 改为 `known_hosts` + 指纹确认 + `RejectPolicy()` |
| D114 | P1 | 未显式处理 Linux 发行版和工具差异 | 新增 `CapabilityProbe / EnvProfile`，统一探测发行版、init、命令能力、权限级别 |
| D115 | P2 | Workflow 样例存在参数未声明、展示结果聚合丢失、可能双确认 | 修正模板参数声明与展示方式，增加确认去重策略 |
| D116 | P2 | 自检仅覆盖通路，不足以证明稳定性、异常恢复、性能与复现能力 | 新增 `--verify-demo`、故障注入矩阵、复现包导出 |

---

## 一、整体架构概览（v4.1）

```text
┌────────────────────────────────────────────────────────────────────┐
│                           界面层 (UI Layer)                        │
│   TUI App (Textual)   │   Simple CLI (--simple)   │   Voice       │
└──────────────┬─────────────────────────────────────────────────────┘
               │ 用户输入（自然语言 / 语音转写文本）
┌──────────────▼─────────────────────────────────────────────────────┐
│                     对话引擎层 (Dialogue Layer)                     │
│  ConversationManager  ↔  PlanningEngine  ↔  WorkflowEngine         │
│  SystemPromptBuilder（注入实时 OS 快照 / EnvProfile / Prompt 版本）│
└──────────────┬─────────────────────────────────────────────────────┘
               │ messages[] + tool_definitions[]（含元工具）
┌──────────────▼─────────────────────────────────────────────────────┐
│                       AI 调用层 (Claude Layer)                      │
│  ClaudeClient（agentic loop + 流式）                                │
│  检测 set_execution_mode → 路由到 Planning / Workflow              │
└──────────────┬─────────────────────────────────────────────────────┘
               │ ToolCall（结构化）
┌──────────────▼─────────────────────────────────────────────────────┐
│                       安全门层 (Security Gate)                      │
│  RiskClassifier（规则 / 路径 / 远程上下文）                         │
│  UserConfirmation（WARN 弹窗）                                      │
│  AuditLog（JSONL + command_trace + decision_trace）                 │
└──────────────┬─────────────────────────────────────────────────────┘
               │ 已批准的 ToolCall
┌──────────────▼─────────────────────────────────────────────────────┐
│                     执行适配层 (Executor Adapter)                   │
│  CapabilityProbe（EnvProfile 构建）                                 │
│  LocalExecutor（subprocess list, shell=False）                      │
│  RemoteExecutor（known_hosts 校验 + 单命令 SSH exec）              │
└──────────────┬─────────────────────────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────────────────┐
│                        工具执行层 (Tool Layer)                      │
│  Disk / File / Process / Port / User / Service / Network / Log     │
│  SafeExecutor（超时 + 截断 + 统一异常）                              │
│  OutputSanitizer（脱敏）                                            │
└────────────────────────────────────────────────────────────────────┘
```

**核心约束：**

- Claude **永远不在自然语言回复中输出裸 shell 命令字符串**
- 安全门强制拦截所有 OS 工具调用，不可绕过
- 所有操作写入审计日志，包括 `SAFE`、`WARN`、`BLOCK`、`user_cancelled`
- 底层执行命令只进入 `AuditLog / F3 面板 / 导出复现包`，用于比赛验证，不作为用户侧命令行建议展示

---

## 二、统一工具面（12 个 OS 工具 + 1 个元工具）

### 2.1 OS 工具表

| # | 工具名 | 能力域 | 参数摘要 | 最高风险 |
|---|---|---|---|---|
| 1 | `get_disk_usage` | 磁盘 | `path`, `recursive` | WARN-LOW |
| 2 | `find_files` | 文件 | `search_path`, `pattern`, `min_size_mb`, `max_depth` | WARN-LOW |
| 3 | `list_processes` | 进程 | `top_n`, `sort_by`, `filter_user` | SAFE |
| 4 | `kill_process` | 进程 | `pid`, `signal` | WARN-HIGH / BLOCK |
| 5 | `get_port_status` | 网络 | `port`, `protocol(tcp\|udp\|all)` | SAFE |
| 6 | `create_user` | 用户 | `username`, `groups[]`, `shell`, `create_home` | WARN-HIGH |
| 7 | `delete_user` | 用户 | `username`, `remove_home` | WARN-HIGH |
| 8 | `modify_user_groups` | 用户 | `username`, `groups[]`, `action(add\|remove)` | WARN-HIGH |
| 9 | `get_system_info` | 系统 | 无参数 | SAFE |
| 10 | `manage_service` | 服务 | `name`, `action(start\|stop\|restart\|status\|enable\|disable)` | SAFE / WARN-LOW / WARN-HIGH |
| 11 | `get_network_info` | 网络 | `interface`（可选） | SAFE |
| 12 | `read_log` | 日志 | `unit`（可选）, `lines`, `since`（可选） | SAFE / WARN-LOW |

**工具面约束：**

- `delete_file` 和 `write_file` **不注册**
- 文件删除属于高风险扩展能力，不在基础工具面中提供
- 当用户提出“清理文件”类诉求时，系统只输出分析和解释，不在自然语言中给出 shell 命令

### 2.2 元工具（Meta Tool）

| 工具名 | 用途 | 是否经过 RiskClassifier |
|---|---|---|
| `set_execution_mode` | 声明 direct / plan / workflow 执行模式，并在 plan 模式下提交冻结候选计划 | 否 |

---

## 三、模块详细设计

### 模块 1：ClaudeClient（AI 调用层）

向 Claude 传入两组工具：OS 工具 + 元工具 `set_execution_mode`。

System prompt 中包含以下约束：

```text
在调用任何 OS 工具之前，如果满足以下任一条件，必须先调用 set_execution_mode：
  - 用户请求需要 3 步或以上操作：mode="plan"
  - 用户请求匹配某个 Workflow 模板：mode="workflow"
  - 用户请求单步直接执行：mode="direct" 或不调用

不确定是否需要规划时，倾向于调用。
```

ClaudeClient 在处理第一个 `tool_call` 时：

- 若为 `set_execution_mode`：路由到 PlanningEngine 或 WorkflowEngine，不走安全门
- 若直接是 OS 工具：按 direct 模式正常进入安全门

**可靠性说明：**

- 元工具让 plan / workflow 触发不再依赖文本前缀
- 即使模型跳过元工具，系统仍可 direct 执行，只是失去“计划展示 + 冻结执行 + 回放”能力

**API Key 管理：**

```text
读取优先级：
  1. 环境变量 ANTHROPIC_API_KEY（推荐）
  2. ~/.sysdialogue/.env（权限必须为 600）
  3. config.toml 中的 api.key（允许，但启动时打印 WARNING）
```

---

### 模块 2：ConversationManager

与 v4 思路一致：

- Turn Bundle 组织多轮上下文
- 中文修正 token 估算
- 100K 总预算
- 16K system prompt 预留

新增要求：

- 记录 `plan_id` / `workflow_name` / `env_profile_id`
- 支持从审计日志重放一个 turn 的决策路径

---

### 模块 3：SystemPromptBuilder

v4.1 明确区分“对话回复”和“审计导出”：

```text
- 永远不在自然语言回复中输出 shell 命令字符串。
- 若用户请求的操作超出工具边界，仅描述“需要做什么”，不附命令。
- 对外回复只使用自然语言。
- 对内审计可记录结构化 command trace，用于比赛复现与取证。
```

此外，SystemPromptBuilder 会注入实时环境快照：

- 发行版与版本
- 是否远程模式
- 是否容器环境
- init system
- 当前用户
- 关键命令可用性矩阵

---

### 模块 4：ToolRegistry

`set_execution_mode` 定义如下：

```json
{
  "name": "set_execution_mode",
  "description": "在调用 OS 工具之前声明执行模式，用于触发 plan 或 workflow。",
  "input_schema": {
    "type": "object",
    "properties": {
      "mode": {
        "type": "string",
        "enum": ["plan", "workflow", "direct"]
      },
      "plan_steps": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "step_id": {"type": "string"},
            "tool": {"type": "string"},
            "args": {"type": "object"},
            "purpose": {"type": "string"},
            "expected_risk": {
              "type": "string",
              "enum": ["SAFE", "WARN-LOW", "WARN-HIGH", "BLOCK", "UNKNOWN"]
            },
            "confirm_required": {"type": "boolean"}
          },
          "required": ["step_id", "tool", "args", "purpose"]
        }
      },
      "workflow_name": {"type": "string"},
      "workflow_params": {"type": "object"}
    },
    "required": ["mode"]
  }
}
```

---

### 模块 5：RiskClassifier

#### 5.1 PATH_PARAMETERS 注册表

```python
PATH_PARAMETERS: dict[str, list[str]] = {
    "get_disk_usage": ["path"],
    "find_files": ["search_path"],
    "list_processes": [],
    "kill_process": [],
    "get_port_status": [],
    "create_user": [],
    "delete_user": [],
    "modify_user_groups": [],
    "get_system_info": [],
    "manage_service": [],
    "get_network_info": [],
    "read_log": [],
}

def get_path_args(tool_name: str, args: dict) -> list[str]:
    return [args[p] for p in PATH_PARAMETERS.get(tool_name, []) if p in args]
```

#### 5.2 B005 路径穿越检测

```python
def has_path_traversal(path: str) -> bool:
    normalized = os.path.normpath(path)
    return ".." in Path(normalized).parts
```

#### 5.3 BLOCK 规则（共 9 条）

| ID | 触发条件 | 原因 |
|---|---|---|
| B001 | 访问 `/etc/passwd`、`/etc/shadow`、`/boot/*`、`/lib/systemd/*` | 系统完整性保护 |
| B002 | `kill_process(pid=1)` | 防止杀死 systemd |
| B004 | `delete_user(root)` 或 `modify_user_groups(root)` | 保护超级管理员 |
| B005 | 任一路径参数含 `..` 组件 | 防路径穿越 |
| B006 | `find_files(search_path="/", max_depth>5)` | 防资源耗尽 |
| B007 | 路径匹配 `/proc/kcore`、`/dev/mem`、`/proc/sys/kernel/` | 内核内存访问 |
| B008 | 路径匹配 `/etc/sudoers` 或 `/etc/sudoers.d/*` | sudo 配置提权 |
| B009 | 路径匹配 `/etc/ssh/sshd_config` | 可能切断远程访问 |
| B010 | 远程模式下 `manage_service(name in ["ssh","sshd","systemd"], action in ["stop","disable"])` | 防止切断当前 SSH |

#### 5.4 WARN-HIGH 规则

| ID | 触发条件 | 原因 |
|---|---|---|
| WH001 | 终止非当前用户进程 | 影响不可预测 |
| WH002 | 删除任意用户 | 永久删除账号 |
| WH003 | 创建用户 | 获得系统登录权限 |
| WH004 | 修改用户组 | 权限变更立即生效 |
| WH005 | 停止或禁用服务 | 服务中断 |
| WH006 | 启动/重启/启用关键服务 | 影响业务连续性 |

```python
CRITICAL_SERVICES = {
    "mysql", "mysqld", "mariadb", "postgresql", "postgres",
    "nginx", "httpd", "apache2",
    "redis", "redis-server",
    "mongodb", "mongod",
    "elasticsearch",
    "rabbitmq", "rabbitmq-server",
    "docker", "containerd",
}
```

#### 5.5 WARN-LOW 规则

| ID | 触发条件 | 原因 |
|---|---|---|
| WL001 | `get_disk_usage(recursive=true)` | 大量 I/O |
| WL002 | `find_files(search_path="/", max_depth<=5)` | 全盘搜索耗时 |
| WL003 | `kill_process` 未命中更高规则 | 终止进程不可逆 |
| WL004 | 非关键服务的 `start/restart` | 低风险服务扰动 |
| WL005 | `read_log(unit=None)` | 全局日志可能含敏感信息 |

#### 5.6 规则优先级

```text
BLOCK > WARN-HIGH > WARN-LOW > SAFE
```

---

### 模块 6：AuditLog

#### 6.1 设计目标

AuditLog 在 v4.1 中承担三类职责：

- 安全追责
- 比赛取证
- 线上复现

#### 6.2 JSON Lines 结构

```json
{
  "timestamp": "2026-04-22T19:20:31Z",
  "session_id": "sess_01",
  "request_id": "req_014",
  "prompt_version": "sysdialogue-v4.1",
  "mode": "workflow",
  "plan_id": "plan_004",
  "workflow_name": "service_restart",
  "user_input": "帮我重启一下 nginx",
  "tool_name": "manage_service",
  "tool_args": {"name": "nginx", "action": "restart"},
  "risk_level": "WARN-HIGH",
  "rule_id": "WH006",
  "decision": "user_confirmed",
  "env_profile_id": "env_remote_01",
  "remote_host": "10.0.0.8",
  "host_fingerprint": "SHA256:xxxx",
  "command_trace": [
    {"executor": "ssh", "argv": ["systemctl", "restart", "nginx"]},
    {"executor": "ssh", "rendered": "systemctl restart nginx"}
  ],
  "decision_trace": [
    "workflow_confirm(service_restart.s2)",
    "risk_classifier(WH006)",
    "user_confirmed(final)"
  ],
  "result_preview": "nginx active (running)",
  "output_redacted": true,
  "duration_ms": 842,
  "exit_code": 0
}
```

#### 6.3 关键说明

- `command_trace` 只用于审计面板和复现包，不进入对话回复
- `decision_trace` 明确展示“为什么执行 / 为什么拦截 / 为什么确认”
- `prompt_version`、`env_profile_id`、`host_fingerprint` 用于复现和排障
- `result_preview` 在写入前统一经过脱敏

#### 6.4 导出能力

- `--export-audit <session_id>`：导出完整审计 JSONL
- `--export-repro-pack <session_id>`：导出复现包

复现包包含：

- `audit.jsonl`
- `env_profile.json`
- `prompt_version.txt`
- `demo_script.md`
- `screenshots_index.json`

---

### 模块 7：CapabilityProbe + ExecutorAdapter + SafeExecutor

#### 7.1 CapabilityProbe / EnvProfile

题目背景强调 Linux 发行版和工具链差异，因此所有执行前先构建环境画像。

```python
class EnvProfile(TypedDict):
    os_release: str
    distro_family: str
    init_system: str
    package_manager: str
    available_cmds: dict[str, bool]
    is_container: bool
    remote_mode: bool
    sudo_available: bool
    current_user: str
```

探测项：

- `systemctl` / `service`
- `journalctl` / `/var/log/*`
- `ss` / `netstat`
- `ip` / `ifconfig`
- `sudo` 是否存在且可用
- 是否容器环境
- 当前用户与权限级别

#### 7.2 Executor 接口

```python
class ExecutorAdapter(Protocol):
    def run(self, cmd: list[str], timeout: int) -> tuple[str, int]:
        ...
```

约束：

- `cmd` 始终是一条命令的参数列表
- 禁止 `&&`、`|`、`;`
- 工具层需要多步信息时，分多次调用

#### 7.3 LocalExecutor

```python
class LocalExecutor:
    def run(self, cmd: list[str], timeout: int) -> tuple[str, int]:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return result.stdout + result.stderr, result.returncode
```

#### 7.4 RemoteExecutor

```python
class RemoteExecutor:
    def __init__(self, host: str, user: str, port: int = 22,
                 key_path: str | None = None,
                 known_hosts_path: str | None = None):
        self.ssh = paramiko.SSHClient()
        self.ssh.load_system_host_keys()
        if known_hosts_path:
            self.ssh.load_host_keys(known_hosts_path)
        self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        self.remote_mode = True

    def run(self, cmd: list[str], timeout: int) -> tuple[str, int]:
        cmd_str = " ".join(shlex.quote(arg) for arg in cmd)
        stdin, stdout, stderr = self.ssh.exec_command(cmd_str, timeout=timeout)
        output = stdout.read().decode("utf-8", errors="replace")
        output += stderr.read().decode("utf-8", errors="replace")
        return output, stdout.channel.recv_exit_status()
```

#### 7.5 首次连接安全策略

- 默认严格模式：仅接受 `known_hosts` 中已有主机
- 遇到未知主机时：先获取并展示 SHA256 指纹
- 用户键入 `trust` 后写入专用 `known_hosts`
- 之后重新以 `RejectPolicy()` 建立连接

#### 7.6 CapabilityProbe 驱动的适配矩阵

| 能力域 | 优先路径 | fallback 路径 |
|---|---|---|
| 服务状态 | `systemctl status` | `service <name> status` |
| 服务控制 | `systemctl start/stop/restart` | `service <name> start/stop/restart` |
| 日志读取 | `journalctl` | `/var/log/*` + `tail` |
| 端口查看 | `ss` | `netstat` |
| 网卡信息 | `ip addr` | `ifconfig` |

#### 7.7 SafeExecutor

统一处理：

- timeout
- stdout / stderr 截断
- exit code 归一化
- 权限不足异常
- SSH 断连与重试策略

---

### 模块 8：工具集与 OutputSanitizer

#### 8.1 LogTool

| 条件 | 风险级别 | 原因 |
|---|---|---|
| `read_log(unit=<service>)` | SAFE | 范围明确 |
| `read_log(unit=None)` | WARN-LOW | 可能泄漏认证信息、内部地址 |

工具参数约束：

```json
{
  "name": "read_log",
  "parameters": {
    "unit": {"type": "string"},
    "lines": {"type": "integer", "minimum": 10, "maximum": 500, "default": 50},
    "since": {"type": "string"}
  }
}
```

#### 8.2 OutputSanitizer（新增）

所有工具输出在进入 UI 与 AuditLog 前统一脱敏：

- `Bearer <token>`
- `Authorization:`
- `password=`
- `--password`
- 数据库连接串
- `sk-...`、AK/SK、密钥片段

原则：

- 尽量保留语义
- 不保留敏感值原文
- UI、日志、导出包统一使用脱敏后的结果

---

### 模块 9：PlanningEngine（计划引擎）

#### 9.1 触发机制

```python
first_call = tool_calls[0]
if first_call.name == "set_execution_mode":
    mode = first_call.input["mode"]
    if mode == "plan":
        frozen_plan = planning_engine.freeze(first_call.input["plan_steps"])
        planning_engine.show_plan(frozen_plan)
    elif mode == "workflow":
        workflow_engine.run(...)
```

#### 9.2 冻结计划结构

```python
class PlanStep(TypedDict):
    step_id: str
    tool: str
    args: dict
    purpose: str
    expected_risk: str
    confirm_required: bool
```

#### 9.3 执行流程

1. 校验 `PlanStep[]` 是否为合法工具调用
2. 对每个步骤重新运行 `RiskClassifier.classify(tool, args)`
3. 生成 `plan_id`
4. 将冻结后的计划写入 session store
5. 用户输入 `go` 后按冻结计划直接执行
6. 若用户修改计划，原 `plan_id` 作废，要求模型重新生成

#### 9.4 展示格式

```text
我将执行以下计划，请确认后输入 'go' 开始执行：

计划ID: plan_004

  步骤 1 [SAFE]       get_system_info()                               → 确认当前用户状态
  步骤 2 [WARN-HIGH]  create_user(username="alice")                   → 新账号获得系统登录权限
  步骤 3 [WARN-HIGH]  modify_user_groups(username="alice", groups=["sudo"])
                                                                  → 权限变更立即生效
  步骤 4 [SAFE]       get_system_info()                               → 验证账号创建结果

输入 'go' 开始执行，或描述修改意见 >
```

这使“展示计划”和“实际执行计划”完全一致。

---

### 模块 10：WorkflowEngine（工作流引擎）

#### 10.1 触发方式

Workflow 仅通过 `set_execution_mode(mode="workflow")` 触发，不依赖关键词或文本前缀。

#### 10.2 Workflow YAML Schema

```yaml
name: 模板名称
description: 一句话描述
parameters:
  - name: 参数名
    type: text | enum | text_list
    description: 参数说明
    required: true/false
    default: 默认值
    enum_values: [可选值列表]

steps:
  - id: 步骤ID
    type: tool_call | confirm | display
    depends_on: [依赖步骤ID]

    # type=tool_call
    tool: 工具名
    args: {参数: "{{变量}}"}
    description: 步骤描述

    # type=confirm
    message: "确认提示文字"

    # type=display
    template: "展示文案，可引用 {{step_id.result}}"
    source_step: 依赖的步骤ID（可选）
```

#### 10.3 三种 step type

| type | 行为 | 示例 |
|---|---|---|
| `tool_call` | 调用 OS 工具，走安全门，记录审计日志 | `create_user` |
| `confirm` | 向用户展示确认信息 | 服务重启确认 |
| `display` | 渲染展示文案，不执行操作 | 磁盘分析结果 |

#### 10.4 确认去重策略

- 若 `confirm` 步骤已覆盖紧随其后的 WARN 风险语义，则复用该确认
- 若 RiskClassifier 判定的风险高于 workflow 预期，仍追加安全确认
- 所有确认来源写入 `decision_trace`

#### 10.5 内置模板（v4.1）

**`new_user.yaml`**

```yaml
name: 创建开发者账号
parameters:
  - {name: username, type: text, required: true}
  - {name: groups, type: text_list, required: false, default: ["sudo"]}
steps:
  - {id: s1, type: tool_call, tool: get_system_info, description: 确认当前用户状态}
  - {id: s2, type: tool_call, tool: create_user, args: {username: "{{username}}", create_home: true}, depends_on: [s1]}
  - {id: s3, type: tool_call, tool: modify_user_groups, args: {username: "{{username}}", groups: "{{groups}}", action: add}, depends_on: [s2]}
  - {id: s4, type: tool_call, tool: get_system_info, description: 验证账号创建结果, depends_on: [s3]}
```

**`disk_cleanup.yaml`**

```yaml
name: 磁盘空间分析
description: 扫描大文件，生成分析报告（不执行删除）
steps:
  - {id: s1, type: tool_call, tool: get_disk_usage, args: {path: "/", recursive: false}, description: 概览各分区使用情况}
  - {id: s2, type: tool_call, tool: find_files, args: {search_path: "/var", pattern: "*", min_size_mb: 100, max_depth: 5}, description: 扫描 /var 下大文件}
  - {id: s3, type: tool_call, tool: find_files, args: {search_path: "/home", pattern: "*", min_size_mb: 100, max_depth: 4}, description: 扫描 /home 下大文件, depends_on: [s2]}
  - id: s4
    type: display
    depends_on: [s2, s3]
    template: |
      磁盘分析完成。

      /var 扫描结果：
      {{s2.result}}

      /home 扫描结果：
      {{s3.result}}

      如需释放空间，请在终端中手动审核并删除不需要的文件。
      我可以继续帮助分析这些文件的用途或优先级。
```

**`security_audit.yaml`**

```yaml
steps:
  - {id: s1, type: tool_call, tool: get_system_info}
  - {id: s2, type: tool_call, tool: list_processes, args: {top_n: 20, sort_by: cpu}}
  - {id: s3, type: tool_call, tool: get_port_status, args: {protocol: all}}
  - {id: s4, type: tool_call, tool: get_network_info}
```

**`port_scan.yaml`**

```yaml
steps:
  - {id: s1, type: tool_call, tool: get_port_status, args: {protocol: tcp}}
  - {id: s2, type: tool_call, tool: get_port_status, args: {protocol: udp}}
```

**`service_restart.yaml`**

```yaml
name: 安全重启服务
parameters:
  - {name: service_name, type: text, required: true}
steps:
  - {id: s1, type: tool_call, tool: manage_service, args: {name: "{{service_name}}", action: status}, description: 查看当前状态}
  - id: s2
    type: confirm
    message: "即将重启 {{service_name}}，这可能中断正在处理的请求。确认继续？"
    depends_on: [s1]
  - {id: s3, type: tool_call, tool: manage_service, args: {name: "{{service_name}}", action: restart}, depends_on: [s2]}
  - {id: s4, type: tool_call, tool: manage_service, args: {name: "{{service_name}}", action: status}, depends_on: [s3]}
  - id: s5
    type: display
    source_step: s4
    template: "{{service_name}} 重启完成。当前状态：{{s4.result}}"
```

---

### 模块 11：AppConfig 与验证

#### 11.1 API key 策略

| 来源 | 优先级 | 行为 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 最高 | 静默使用 |
| `~/.sysdialogue/.env`（权限 600） | 中 | 使用，打印 INFO |
| `~/.sysdialogue/.env`（权限非 600） | — | 拒绝读取并报错 |
| `config.toml.api.key` | 最低 | 允许，打印 WARNING |

#### 11.2 通路自检

```text
sysdialogue --test-tools

[1/7] API 连接
[2/7] 磁盘工具
[3/7] 进程工具
[4/7] 端口工具
[5/7] 系统信息
[6/7] PATH_PARAMETERS
[7/7] 安全规则（9/9 BLOCK + WL003/WL005）
```

#### 11.3 扩展验证

```text
sysdialogue --verify-demo

[1/6] Workflow 冻结计划
[2/6] 远程主机校验
[3/6] 环境探测适配
[4/6] 输出脱敏
[5/6] 故障恢复
[6/6] 复现包导出
```

#### 11.4 会话管理

- `--list-sessions`
- `--delete-session`
- 30 天保留
- 20 个会话上限

---

### 模块 12：TUI App

保留 v4 的 `BlockWidget / StatusPanel / ConfirmModal / CommandInput / SSH 状态显示`。

v4.1 新增：

- F3 审计日志面板：展示 `decision_trace` 与 `command_trace`
- F4 环境画像面板：展示 `EnvProfile`
- 未知主机首次连接时展示 SHA256 指纹，要求键入 `trust`

---

### 模块 13：VoiceInput

保持双方案：

- 本地 Whisper
- 联网 SpeechRecognition

约束：

- 高风险操作不接受语音确认
- 语音输入只负责输入方式切换，不改变风险分级规则

---

## 四、模块间数据流（v4.1 workflow 场景）

```text
用户输入："帮我重启一下 nginx"
    │
    ▼
[ClaudeClient] 第一个 tool_call:
  set_execution_mode(mode="workflow",
                     workflow_name="service_restart",
                     workflow_params={"service_name": "nginx"})
    │
    ▼
[WorkflowEngine] 加载 service_restart.yaml
    │
    ▼ 步骤 s1：manage_service(nginx, status)
[RiskClassifier] SAFE
[ExecutorAdapter] 按 EnvProfile 选择 systemctl 或 service
[AuditLog] auto_executed
    │
    ▼ 步骤 s2：confirm
[TUI ConfirmModal] "即将重启 nginx，这可能中断正在处理的请求。确认继续？"
用户键入 "confirm"
    │
    ▼ 步骤 s3：manage_service(nginx, restart)
[RiskClassifier] WH006 或 WL004
[WorkflowEngine] 若 s2 已覆盖相同风险语义，则复用确认，不重复弹窗
[AuditLog] user_confirmed, decision_trace += workflow_confirm
    │
    ▼ 步骤 s4：manage_service(nginx, status)
    │
    ▼ 步骤 s5：display
[TUI] 展示最终状态
```

---

## 五、关键设计变更记录（v4 → v4.1）

| 问题 | v4 | v4.1 | 原因 |
|---|---|---|---|
| 计划表示 | `string[]` | `PlanStep[]` + `plan_id` | 消除展示与执行漂移 |
| 审计输出 | 仅结果摘要 | 增加命令、决策、环境、Prompt 版本 | 满足赛题可审计与可复现 |
| SSH 主机信任 | 自动接受未知主机 | 指纹确认 + known_hosts | 提升远程安全可信度 |
| 环境感知 | 工具级零散 fallback | CapabilityProbe 统一探测 | 更贴合不同 Linux 环境 |
| 输出安全 | 仅风险分级 | 统一脱敏层 | 防止日志和验证材料泄密 |
| Workflow 示例 | 有实现歧义 | 参数完整、展示完整、确认去重 | 提高可落地性 |
| 自测 | 通路级 | 增加稳定性、恢复、复现验证 | 对应评分项更完整 |

---

## 六、关键设计决策总结

| 决策 | 方案 | 原因 |
|---|---|---|
| 不输出裸 shell | 用户侧只给自然语言解释 | 保持去命令行体验与安全边界 |
| 审计导出保留命令 | `command_trace` 仅进入审计与复现包 | 满足比赛对“实际执行指令”的要求 |
| 冻结计划执行 | `go` 后直接执行冻结的 `PlanStep[]` | 保持计划与执行一致 |
| CapabilityProbe 先行 | 先探测能力，再选执行路径 | 应对发行版与工具差异 |
| Host key 校验 | unknown host 必须显式信任 | 避免静默信任远端 |
| 确认去重 | workflow confirm 与风控 confirm 可复用 | 在安全前提下改善体验 |
| OutputSanitizer | UI、日志、导出统一脱敏 | 防止敏感信息泄漏 |

---

## 七、开发优先级（v4.1）

```text
P0（核心通路）
  ClaudeClient（agentic loop + set_execution_mode）
  PlanningEngine（PlanStep 冻结 / plan_id / go 执行）
  ToolRegistry（OS 工具 + 元工具定义）
  RiskClassifier（B001-B010 / WH / WL）
  CapabilityProbe / EnvProfile
  LocalExecutor / RemoteExecutor（host key 校验）
  AuditLog（command_trace / decision_trace）
  Simple CLI
  --test-tools

P1（完整功能）
  UserTool / ServiceTool / LogTool
  WorkflowEngine（5 个模板）
  ConfirmModal（确认去重）
  ConversationManager
  OutputSanitizer
  TUI 审计面板 / 环境画像面板
  --verify-demo
  复现包导出

P2（加分项）
  RollbackAdvisor
  故障注入测试
  性能面板
  AppConfig 增强

P3（可选增强）
  VoiceInput
  F4 会话管理面板
```

---

## 八、演示场景设计（评分维度覆盖）

| 场景 | 覆盖评分维度 | 关键特性 |
|---|---|---|
| 基础四项：磁盘 / 进程 / 端口 / 用户 | 基础需求执行 | 4 类 OS 工具直接调用 |
| 风险拦截：PID 1、root、路径穿越 | 高风险识别与处置 | BLOCK / WARN 闭环 |
| 冻结计划：创建用户 → 展示计划 → `go` 执行 | 复杂连续任务、闭环稳定性 | `PlanStep[]` + `plan_id` |
| 关键服务重启 | 风险提示清晰度、交互自然度 | workflow confirm 去重 |
| 多轮上下文追问 | 持续状态更新、反馈连贯性 | ConversationManager |
| SSH 首次连接指纹确认 | 环境感知、安全可解释 | known_hosts + fingerprint |
| openEuler / Ubuntu 双环境演示 | 环境感知与决策 | CapabilityProbe 适配 |
| 语音输入查日志 | 多模态无劣变 | VoiceInput + SAFE 工具 |
| 审计日志 F3 | 行为可解释 | `command_trace` + `decision_trace` |
| 故障恢复：断连 / 超时 / 取消 / 权限不足 | 稳定性与一致性 | SafeExecutor + verify-demo |

---

## 九、提交物与复现材料清单

### 9.1 源代码与工程文件

- 完整源代码
- `README.md`
- 依赖清单
- 示例配置
- 环境搭建说明

### 9.2 Agent 材料

- Agent 配置说明
- 核心 Prompt 文本
- 工具与能力定义文档
- 决策边界说明（何时 direct / plan / workflow，何时 BLOCK / WARN）

### 9.3 复现与验证材料

- 审计日志导出
- 复现包
- 演示脚本
- 截图索引
- 环境画像
- Prompt 版本文件

### 9.4 合规材料

- 第三方开源组件清单
- 许可证说明
- 引用来源标注

---

## 十、验证矩阵与评分映射

| 评分项 | 方案抓手 | 验证方式 |
|---|---|---|
| 基础需求执行 | 12 个 OS 工具 | 基础四项演示 |
| 高风险识别与处置 | RiskClassifier + ConfirmModal | PID 1 / root / 穿越 / 关键服务 |
| 复杂连续任务 | PlanningEngine + WorkflowEngine | 冻结计划 / workflow |
| 环境感知与决策 | CapabilityProbe / EnvProfile | 双发行版演示 |
| 结果反馈与执行说明 | 自然语言反馈 + decision trace | 视频 + 审计面板 |
| 操作闭环完整性 | 计划、确认、执行、状态校验、落盘 | 单轮 / 风险 / 连续任务闭环 |
| 性能表现 | `duration_ms` + 验证脚本 | 首响应 / 总耗时 |
| 稳定性与一致性 | timeout / 断连 / 权限不足 / 取消 | 故障注入矩阵 |
| 用户体验 | 去命令行回复 + 风险提示 + 确认去重 | TUI / Voice |
| 工程质量 | 模块边界、导出能力、复现包 | 代码结构 + 文档 |
| 创新性 | AI 冻结计划 + 环境探测 + 可回放审计 | 视频与说明文档 |

---

## 十一、结论

v4.1 的目标不是再堆功能，而是把方案从“结构上合理”推进到“可比赛提交、可真实复现、可被评委验证”。  
核心升级点有五个：

- 计划可冻结
- 环境可探测
- 执行可回放
- 风险可解释
- 方案可复现

这五点对应赛题里最关键的客观分与主观分项，能显著提高整套方案的说服力和落地感。
