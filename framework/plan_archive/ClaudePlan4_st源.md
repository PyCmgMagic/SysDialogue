# SysDialogue — 模块设计文档 v4

> AI Hackathon 2026 · 操作系统智能代理  
> v4 修订：disk_cleanup 架构矛盾修复、RemoteExecutor 接口收敛、路径参数注册表、
> read_log 风险修正、Planning/Workflow 触发协议重构、Workflow step 类型化、
> API key 策略统一、自检 BLOCK 规则数修正、B003 移除、manage_service 关键服务分级

---

## 零、v3 → v4 缺陷清单与修订摘要

| 编号 | 严重度 | 缺陷描述 | v4 修订方案 |
|---|---|---|---|
| D101 | P1 | disk_cleanup 输出原始 shell 命令，违反"不生成裸 shell"核心约束 | 将 disk_cleanup 改为纯分析工作流；系统提示明确禁止输出 shell 命令建议 |
| D102 | P1 | RemoteExecutor 接口 `run(list[str])` 与远程示例中的 `"uname && free"` 复合命令字符串不一致 | 工具层每次仅构造单条命令列表；RemoteExecutor 安全拼接后执行，无需 shell 语义 |
| D103 | P1 | B001/B007 只检查名为 `path` 的参数，漏掉 `find_files.search_path`；B005 字符串匹配 `..` 过宽，会误伤合法文件名 | 引入 PATH_PARAMS 注册表；B005 改用 normpath 组件检测 |
| D104 | P1 | `read_log` 定义为始终 SAFE，但 `unit=None` 时读全局系统日志，可能含认证信息、内部地址 | 新增 WL005：`unit` 为空时升为 WARN-LOW |
| D105 | P2 | PLAN_REQUIRED 和 `WORKFLOW:<name>:<json>` 依赖模型输出文本前缀，模型换格式即失效 | 引入 `set_execution_mode` 元工具；Claude 通过结构化 tool_call 触发编排，而非文本前缀 |
| D106 | P2 | Workflow YAML 中 `[确认]`、`[展示列表]` 是伪步骤，与 `tool_call` 混用，schema 未闭合 | 新增 `type` 字段（`tool_call` / `confirm` / `display`），schema 完全类型化 |
| D107 | P2 | API key 策略前后矛盾：摘要写"拒绝 config 存 key"，正文又允许并只警告 | 统一为"允许但警告"，删除"拒绝"措辞 |
| D108 | P2 | `--test-tools` 自检输出写"7/7 条 BLOCK 规则"，但 v3 已有 B001-B010 共 10 条 | 修正为"10/10 条 BLOCK 规则" |
| D109 | P3 | B003 检查 `create_user.uid == 0`，但统一工具面的 `create_user` 根本没有 `uid` 参数 | 删除 B003，此场景不可能发生 |
| D110 | P3 | `manage_service` restart 统一为 WARN-LOW，但重启 nginx/mysql/redis 等对业务有明显影响 | 引入 CRITICAL_SERVICES 名单；关键服务 restart = WARN-HIGH |

---

## 一、整体架构概览（v4，不变）

```
┌──────────────────────────────────────────────────────────────────┐
│                         界面层 (UI Layer)                        │
│  TUI App (Textual)  │  Simple CLI (--simple)  │  Voice (--voice) │
└──────────────┬───────────────────────────────────────────────────┘
               │ 用户输入（自然语言 / 语音转写文本）
┌──────────────▼───────────────────────────────────────────────────┐
│                     对话引擎层 (Dialogue Layer)                   │
│        ConversationManager  ←→  PlanningEngine                   │
│              ↕ 构建 messages[]                                    │
│          SystemPromptBuilder（注入实时 OS 快照）                  │
└──────────────┬───────────────────────────────────────────────────┘
               │ messages[] + tool_definitions[]（含元工具）
┌──────────────▼───────────────────────────────────────────────────┐
│                    AI 调用层 (Claude Layer)                       │
│   ClaudeClient（agentic loop + 流式）                             │
│   检测 set_execution_mode 元工具 → 路由到 PlanningEngine/Workflow │
└──────────────┬───────────────────────────────────────────────────┘
               │ ToolCall（结构化）
┌──────────────▼───────────────────────────────────────────────────┐
│                    安全门层 (Security Gate)                       │
│    RiskClassifier（PATH_PARAMS 注册表 + normpath B005）           │
│    UserConfirmation（WARN 弹窗）  AuditLog（追加式日志）          │
└──────────────┬───────────────────────────────────────────────────┘
               │ 已批准的 ToolCall
┌──────────────▼───────────────────────────────────────────────────┐
│                    执行适配层 (Executor Adapter)                   │
│     LocalExecutor（subprocess list, shell=False）                 │
│     RemoteExecutor（paramiko, 单条命令 shlex.quote 拼接）         │
└──────────────┬───────────────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────────────┐
│                     工具执行层 (Tool Layer)                       │
│  DiskTool │ FileTool │ ProcessTool │ PortTool │ UserTool         │
│  ServiceTool │ NetworkTool │ LogTool                              │
│              SafeExecutor（超时 + 截断 + 日志）                   │
└──────────────────────────────────────────────────────────────────┘
```

**不变的核心约束：**
- Claude **永远不生成裸 shell 字符串**（含在自然语言回复中向用户建议 shell 命令）
- 安全门强制拦截所有工具调用，不可绕过
- 所有操作追加到审计日志，包括 BLOCK 和用户取消

---

## 二、统一工具面（v4，共 12 个 OS 工具 + 1 个元工具）

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
| 11 | `get_network_info` | 网络 | `interface`（可选）| SAFE |
| 12 | `read_log` | 日志 | `unit`（可选）, `lines`, `since`（可选）| SAFE / WARN-LOW |

**工具面约束（v4 不变）：**
- `delete_file` 和 `write_file` **不注册**：文件写/删操作不在基础能力范围，风险高，不提供此工具
- Claude 在自然语言回复中若涉及文件删除，应告知用户"此操作需在终端手动执行，我无法代为删除文件"，**严禁附上任何 shell 命令字符串**（见 SystemPromptBuilder 约束）

### 2.2 元工具（Meta Tool）

| 工具名 | 用途 | 被 RiskClassifier 检查 |
|---|---|---|
| `set_execution_mode` | Claude 主动触发计划/工作流编排，返回结构化意图 | 否（元工具不走安全门）|

元工具不注册为 OS 工具，不出现在工具面表中，单独传入 ClaudeClient。

---

## 三、模块详细设计

---

### 模块 1：ClaudeClient（AI 调用层）

**元工具触发机制（替换 PLAN_REQUIRED 前缀，修订 D105）：**

向 Claude 传入两组工具：OS 工具 + 元工具 `set_execution_mode`。  
System prompt 中包含：

```
在调用任何 OS 工具之前，如果满足以下任一条件，必须先调用 set_execution_mode：
  - 用户请求需要 3 步或以上操作（参数 mode="plan"，列出 plan_steps）
  - 用户请求匹配某个 Workflow 模板（参数 mode="workflow"，填入 workflow_name 和 workflow_params）
  - 用户请求单步直接执行（参数 mode="direct" 或不调用此工具）

不确定是否需要规划时，倾向于调用（宁可多规划，不要漏计划）。
```

ClaudeClient 的 agentic loop 在处理第一个 tool_call 时检查：
- 若 `tool_name == "set_execution_mode"` → 路由到 PlanningEngine 或 WorkflowEngine，**不走安全门**
- 若直接是 OS 工具 → 正常走安全门处理

**可靠性说明：** 即使 Claude 未调用 `set_execution_mode`（如直接调用 OS 工具），系统依然正确运行（直接执行模式），只是没有计划展示。这是有损但无害的降级，不导致错误。

**API Key 管理（修订 D107，统一口径）：**

```
读取优先级：
  1. 环境变量 ANTHROPIC_API_KEY（推荐）
  2. ~/.sysdialogue/.env 文件（文件权限必须为 600，否则拒绝读取并报错）
  3. config.toml 中的 api.key 字段（允许，但启动时打印 WARNING 并建议迁移）

说明：三种方式均被支持。选项 3 存在 key 泄露风险（如 config 文件被提交到 git），
强烈推荐使用选项 1 或 2。
```

> "拒绝"语义仅适用于：密钥来源文件权限过宽（如 ~/.sysdialogue/.env 权限为 644），此时拒绝读取文件并提示用户修正权限。不因"存在 config 文件中的 key"本身拒绝启动。

---

### 模块 2：ConversationManager

与 v3 相同（Turn Bundle + 中文修正 token 估算 + 100K 总预算 + 16K system prompt 预留）。

---

### 模块 3：SystemPromptBuilder

**新增约束条目（修订 D101）：**

在注入的行为约束中，v4 明确增加：

```
- 永远不在自然语言回复中输出 shell 命令字符串（无论是建议还是示例）。
  若用户需要执行无法通过工具完成的操作，仅描述"需要做什么"，
  例如："此目录中的大文件需要手动删除，建议您在终端中处理。"
  不要附上 rm / truncate / apt clean 等任何命令。
```

---

### 模块 4：ToolRegistry

新增 `set_execution_mode` 元工具定义：

```json
{
  "name": "set_execution_mode",
  "description": "在调用 OS 工具之前，声明执行模式。用于触发计划编排或 Workflow 模板。",
  "input_schema": {
    "type": "object",
    "properties": {
      "mode": {
        "type": "string",
        "enum": ["plan", "workflow", "direct"],
        "description": "plan=多步骤计划; workflow=匹配到模板; direct=直接执行"
      },
      "plan_steps": {
        "type": "array",
        "items": {"type": "string"},
        "description": "mode=plan 时，列出每步的意图描述（供用户确认）"
      },
      "workflow_name": {
        "type": "string",
        "description": "mode=workflow 时，匹配的模板名称"
      },
      "workflow_params": {
        "type": "object",
        "description": "mode=workflow 时，从用户输入中提取的模板参数"
      }
    },
    "required": ["mode"]
  }
}
```

---

### 模块 5：RiskClassifier

#### 5.1 PATH_PARAMS 注册表（修订 D103）

v3 的 B001/B007 只检查参数名为 `path` 的字段，漏掉了 `find_files.search_path` 等同语义参数。

v4 引入集中注册表：

```python
# risk_rules.py 中统一定义
PATH_PARAMETERS: dict[str, list[str]] = {
    "get_disk_usage":    ["path"],
    "find_files":        ["search_path"],   # 注意：不是 "path"
    "get_system_info":   [],
    "list_processes":    [],
    "kill_process":      [],
    "get_port_status":   [],
    "create_user":       [],
    "delete_user":       [],
    "modify_user_groups":[],
    "manage_service":    [],               # name 是服务名，不是路径
    "get_network_info":  [],
    "read_log":          [],
}

def get_path_args(tool_name: str, args: dict) -> list[str]:
    """返回该工具调用中所有路径类型参数的值（用于 B001/B007/B005 检查）"""
    return [args[p] for p in PATH_PARAMETERS.get(tool_name, []) if p in args]
```

所有涉及路径检查的规则均调用 `get_path_args()`，不直接读 `args.get("path")`。

#### 5.2 B005 路径穿越检测修订（修订 D103）

v3 问题：`任意字符串参数含 ".."` → 过宽，会误伤合法场景（如搜索含 `..` 的文件名）。

v4 修订：仅对路径类型参数做 normpath 组件级检测：

```python
import os
from pathlib import Path

def has_path_traversal(path: str) -> bool:
    """
    检测路径穿越，使用 normpath 分析组件，而非字符串匹配。
    防止 "a/../../etc/passwd" 绕过；不误伤 "find -name '..hidden'" 这类场景。
    """
    try:
        normalized = os.path.normpath(path)
        parts = Path(normalized).parts
        return ".." in parts
    except (ValueError, TypeError):
        return True  # 解析失败时保守地 BLOCK

# B005 实现：
def check_b005(tool_name: str, args: dict) -> bool:
    for path_val in get_path_args(tool_name, args):
        if has_path_traversal(path_val):
            return True  # 触发 BLOCK
    return False
```

B005 只作用于路径类型参数，不再扫描所有字符串参数。

#### 5.3 BLOCK 规则清单（v4，共 9 条）

> **B003 已删除（修订 D109）**：`create_user` 工具无 `uid` 参数，该规则不可能触发。

| ID | 触发条件 | 原因 |
|---|---|---|
| B001 | `get_path_args()` 返回值匹配 `/etc/passwd`、`/etc/shadow`、`/boot/*`、`/lib/systemd/*` | 系统完整性保护 |
| B002 | `kill_process` 且 `pid == 1` | 杀死 systemd 导致系统崩溃 |
| B004 | `delete_user` 或 `modify_user_groups` 且 `username == "root"` | 保护超级管理员 |
| B005 | `get_path_args()` 中任意路径经 normpath 分析含 `..` 组件 | 防路径穿越 |
| B006 | `find_files` 且 `search_path == "/"` 且 `max_depth > 5` | 防资源耗尽 |
| B007 | `get_path_args()` 返回值匹配 `/proc/kcore`、`/dev/mem`、`/proc/sys/kernel/` 前缀 | 内核内存访问 |
| B008 | `get_path_args()` 匹配 `/etc/sudoers` 或 `/etc/sudoers.d/*` | 修改 sudo 权限可提权 |
| B009 | `get_path_args()` 匹配 `/etc/ssh/sshd_config` | 修改 SSH 配置可能永久断开远程访问 |
| B010 | `manage_service` 且 `name in ["sshd","ssh","systemd"]` 且 `action in ["stop","disable"]` 且 `remote_mode=True` | 远程模式下停止 SSH 会切断当前连接 |

#### 5.4 WARN-HIGH 规则清单（v4，共 6 条）

| ID | 触发条件 | 警告原因 |
|---|---|---|
| WH001 | `kill_process` 且目标进程非当前用户所有 | 终止他人进程，影响不可预测 |
| WH002 | `delete_user` 任意用户名 | 永久删除账号，不可恢复 |
| WH003 | `create_user` 任意请求 | 新账号获得系统登录权限 |
| WH004 | `modify_user_groups` 任意请求 | 权限变更立即生效 |
| WH005 | `manage_service` 且 `action in ["stop","disable"]` | 服务停止影响依赖方 |
| WH006 | `manage_service` 且 `action in ["start","restart","enable"]` 且 `name in CRITICAL_SERVICES` | 关键服务重启影响业务连续性 |

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

#### 5.5 WARN-LOW 规则清单（v4，共 5 条）

| ID | 触发条件 | 警告原因 |
|---|---|---|
| WL001 | `get_disk_usage` 且 `recursive == true` | 可能产生大量 I/O |
| WL002 | `find_files` 且 `search_path == "/"` 且 `max_depth <= 5` | 全盘搜索耗时 |
| WL003 | `kill_process` 且未匹配任何 BLOCK/WARN-HIGH 规则（fallback）| 终止进程不可逆 |
| WL004 | `manage_service` 且 `action in ["start","restart"]` 且 `name NOT in CRITICAL_SERVICES` | 非关键服务重启，低风险 |
| WL005 | `read_log` 且 `unit` 为空或未提供 | 全局系统日志可能含认证信息、内部地址 |

**规则优先级（修订 D103，明确）：**
```
B001-B010（BLOCK）> WH001-WH006（WARN-HIGH）> WL001-WL005（WARN-LOW）> 默认 SAFE
每个规则均调用 get_path_args() 取路径参数，不直接硬编码参数名
```

---

### 模块 6：AuditLog

与 v3 相同（追加式 JSON Lines，含 result_preview，decision 字段分 5 种）。

---

### 模块 7：SafeExecutor + ExecutorAdapter

#### 7.1 接口定义（修订 D102）

```python
from typing import Protocol

class ExecutorAdapter(Protocol):
    def run(self, cmd: list[str], timeout: int) -> tuple[str, int]:
        """
        执行单条命令。
        cmd: 参数列表（不含 shell 特殊字符，不使用 &&/|/; 拼接）
        返回: (stdout+stderr 合并文本, exit_code)
        """
        ...
```

**关键约束：**
- `cmd` 始终是单条命令的参数列表（如 `["journalctl", "-u", "nginx", "-n", "50"]`）
- 工具层若需要多步信息，调用 `run()` 多次，每次一条命令
- 适配器不负责解释 shell 语义，不支持 `&&`、`|`、`;`

#### 7.2 LocalExecutor

```python
class LocalExecutor:
    def run(self, cmd: list[str], timeout: int) -> tuple[str, int]:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            shell=False,           # 严禁 shell=True
        )
        return result.stdout + result.stderr, result.returncode
```

#### 7.3 RemoteExecutor（修订 D102）

```python
import shlex
import paramiko

class RemoteExecutor:
    def __init__(self, host: str, user: str, port: int = 22, key_path: str = None):
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.remote_mode = True   # 供 RiskClassifier B010 判断

    def run(self, cmd: list[str], timeout: int) -> tuple[str, int]:
        """
        将参数列表安全拼接为单条 SSH exec_command。
        shlex.quote 对每个参数转义，防止注入；
        不支持复合命令（&&/|），工具层须分拆为多次调用。
        """
        cmd_str = " ".join(shlex.quote(arg) for arg in cmd)
        stdin, stdout, stderr = self.ssh.exec_command(cmd_str, timeout=timeout)
        output = stdout.read().decode("utf-8", errors="replace")
        output += stderr.read().decode("utf-8", errors="replace")
        return output, stdout.channel.recv_exit_status()
```

**工具层在远程模式下的多次调用示例（替换 v3 的复合命令字符串，修订 D102）：**

`get_system_info` 远程实现：

```python
def get_system_info_remote(executor: RemoteExecutor) -> str:
    results = {}
    # 每条命令独立调用，不使用 &&
    results["kernel"],  _ = executor.run(["uname", "-r"], timeout=5)
    results["hostname"], _ = executor.run(["hostname"], timeout=5)
    results["memory"],  _ = executor.run(["free", "-h"], timeout=5)
    results["disk"],    _ = executor.run(["df", "-h", "/"], timeout=5)
    results["uptime"],  _ = executor.run(["uptime"], timeout=5)
    return format_system_info(results)
```

**psutil 在远程模式下的完整替换策略：**

| 本地（psutil）| 远程 SSH 替代 | 命令列表形式 |
|---|---|---|
| `psutil.process_iter()` | `["ps", "aux"]` | 单次调用 |
| `psutil.virtual_memory()` | `["free", "-h"]` | 单次调用 |
| `psutil.disk_usage(path)` | `["df", "-h", path]` | 单次调用 |
| `psutil.net_connections()` | `["ss", "-tlnp"]` → fallback `["netstat", "-tlnp"]` | 单次调用 |
| `psutil.net_if_addrs()` | `["ip", "addr"]` | 单次调用 |
| `psutil.getloadavg()` | `["uptime"]` | 单次调用 |

---

### 模块 8：工具集

#### LogTool（修订 D104）

`read_log` 风险分级：

| 调用条件 | 风险级别 | 原因 |
|---|---|---|
| `unit` 已指定（如 `nginx`、`sshd`）| SAFE | 范围明确，输出可预期 |
| `unit` 为空或未提供 | WARN-LOW（WL005）| 全局系统日志可能含密码、token、内部 IP 等敏感信息 |

工具参数约束更新：

```json
{
  "name": "read_log",
  "parameters": {
    "unit": {
      "type": "string",
      "description": "systemd 服务单元名（如 nginx、sshd）。留空读取全局系统日志（需用户确认）。"
    },
    "lines": {"type": "integer", "minimum": 10, "maximum": 500, "default": 50},
    "since": {"type": "string", "description": "时间范围，如 '1 hour ago'"}
  }
}
```

#### ServiceTool（不变，补充 CRITICAL_SERVICES 说明）

`manage_service` 工具本身不感知服务重要性；风险分级由 RiskClassifier 的 WH006/WL004 通过 CRITICAL_SERVICES 名单统一控制。工具层只负责执行。

---

### 模块 9：PlanningEngine（计划引擎）

**触发机制（修订 D105，替换 PLAN_REQUIRED 文本前缀）：**

ClaudeClient 在 agentic loop 的第一个 tool_call 处：

```python
first_call = tool_calls[0]
if first_call.name == "set_execution_mode":
    mode = first_call.input["mode"]
    if mode == "plan":
        planning_engine.show_plan(first_call.input["plan_steps"])
        # 等待用户输入 "go" 后继续
    elif mode == "workflow":
        workflow_engine.run(first_call.input["workflow_name"],
                            first_call.input["workflow_params"])
    # direct 或未调用：继续正常 agentic loop
```

**触发可靠性：** 元工具是结构化 tool_call，JSON 字段经过 schema 验证，不依赖模型输出文本格式。即使模型跳过 `set_execution_mode` 直接调用 OS 工具，系统正常执行（直接模式），不产生错误。

**计划展示格式（不变）：**

```
我将执行以下计划，请确认后输入 'go' 开始执行：

  步骤 1 [安全]     get_system_info()         → 确认当前用户状态
  步骤 2 [⚠需确认]  create_user(alice)         → 新账号获得系统登录权限
  步骤 3 [⚠需确认]  modify_user_groups(alice)  → 权限变更立即生效
  步骤 4 [安全]     get_system_info()          → 验证账号创建结果

输入 'go' 开始执行，或描述修改意见 >
```

步骤风险标注：PlanningEngine 对 `plan_steps` 中的每个意图描述再次调用 RiskClassifier 的 `classify(tool_name, args)` 填入风险级别，不依赖 Claude 在 `plan_steps` 中自报风险。

---

### 模块 10：WorkflowEngine（工作流引擎）

#### 10.1 触发方式（修订 D105）

WorkflowEngine 由 ClaudeClient 通过 `set_execution_mode(mode="workflow", ...)` 触发，不再依赖关键词列表或 `WORKFLOW:` 文本前缀。

#### 10.2 Workflow YAML Schema（修订 D106，引入 step type）

```yaml
name: 模板名称
description: 一句话描述
parameters:
  - name: 参数名
    type: text | enum
    description: 参数说明
    required: true/false
    default: 默认值
    enum_values: [可选值列表]

steps:
  - id: 步骤ID
    type: tool_call | confirm | display   # 必填字段，明确步骤类型
    depends_on: [依赖步骤ID]              # 可选，声明顺序依赖

    # type=tool_call 时：
    tool: 工具名                           # 必须在统一工具面中存在
    args: {参数: "{{变量}}"}
    description: 步骤描述                  # 用于 Planning 展示

    # type=confirm 时：
    message: "确认提示文字（显示给用户）"

    # type=display 时：
    template: "展示文案，可引用 {{step_id.result}}"
    source_step: 依赖的步骤ID
```

**三种 step type 语义：**

| type | 执行行为 | 举例 |
|---|---|---|
| `tool_call` | 调用 OS 工具，走安全门，记录审计日志 | `create_user`, `manage_service` |
| `confirm` | 向用户展示 message，要求键入 `confirm` 才继续 | 展示将要操作的文件列表后的二次确认 |
| `display` | 根据前步骤结果渲染展示文案，不执行操作，不走安全门 | 展示扫描到的大文件列表 |

#### 10.3 内置模板（v4，5个）

**`new_user.yaml`：**
```yaml
name: 创建开发者账号
steps:
  - {id: s1, type: tool_call, tool: get_system_info, description: 确认当前用户状态}
  - {id: s2, type: tool_call, tool: create_user, args: {username: "{{username}}", create_home: true}, depends_on: [s1]}
  - {id: s3, type: tool_call, tool: modify_user_groups, args: {username: "{{username}}", groups: ["{{groups}}"], action: add}, depends_on: [s2]}
  - {id: s4, type: tool_call, tool: get_system_info, description: 验证账号创建结果, depends_on: [s3]}
```

**`disk_cleanup.yaml`（修订 D101，纯分析，不输出 shell 命令）：**
```yaml
name: 磁盘空间分析
description: 扫描大文件，生成分析报告（不执行删除）
steps:
  - {id: s1, type: tool_call, tool: get_disk_usage, args: {path: "/", recursive: false}, description: 概览各分区使用情况}
  - {id: s2, type: tool_call, tool: find_files, args: {search_path: "/var", pattern: "*", min_size_mb: 100, max_depth: 5}, description: 扫描 /var 下大文件}
  - {id: s3, type: tool_call, tool: find_files, args: {search_path: "/home", pattern: "*", min_size_mb: 100, max_depth: 4}, description: 扫描 /home 下大文件, depends_on: [s2]}
  - id: s4
    type: display
    template: |
      磁盘分析完成。以下文件占用空间较大（{{count}} 个，合计 {{total_size}}）：
      {{file_list}}
      
      如需释放空间，请在终端中手动审核并删除不需要的文件。
      我可以进一步分析任意文件的用途或为您提供清理建议。
    source_step: s2
```

> **disk_cleanup 设计说明**：工作流以 `display` 步骤结束，Claude 呈现的是结构化分析报告，**不包含任何 shell 命令**。这与"永远不生成裸 shell 字符串"的核心约束完全一致。Claude 在后续对话中可帮助分析具体文件，但所有删除操作须由用户在终端手动完成。

**`security_audit.yaml`：**
```yaml
steps:
  - {id: s1, type: tool_call, tool: get_system_info}
  - {id: s2, type: tool_call, tool: list_processes, args: {top_n: 20, sort_by: cpu}}
  - {id: s3, type: tool_call, tool: get_port_status, args: {protocol: all}}
  - {id: s4, type: tool_call, tool: get_network_info}
```

**`port_scan.yaml`：**
```yaml
steps:
  - {id: s1, type: tool_call, tool: get_port_status, args: {protocol: tcp}}
  - {id: s2, type: tool_call, tool: get_port_status, args: {protocol: udp}}
```

**`service_restart.yaml`（带 confirm 步骤）：**
```yaml
name: 安全重启服务
parameters:
  - {name: service_name, type: text, required: true}
steps:
  - {id: s1, type: tool_call, tool: manage_service, args: {name: "{{service_name}}", action: status}, description: 查看当前状态}
  - id: s2
    type: confirm
    message: "即将重启 {{service_name}}，这将中断正在处理的请求。确认继续？"
    depends_on: [s1]
  - {id: s3, type: tool_call, tool: manage_service, args: {name: "{{service_name}}", action: restart}, depends_on: [s2]}
  - {id: s4, type: tool_call, tool: manage_service, args: {name: "{{service_name}}", action: status}, depends_on: [s3]}
  - id: s5
    type: display
    template: "{{service_name}} 重启完成。当前状态：{{s4.result}}"
    source_step: s4
```

---

### 模块 11：AppConfig

**API key 策略（修订 D107，与模块 1 一致）：**

| 来源 | 优先级 | 行为 |
|---|---|---|
| 环境变量 `ANTHROPIC_API_KEY` | 最高 | 静默使用 |
| `~/.sysdialogue/.env`（权限 600）| 中 | 使用，打印 INFO |
| `~/.sysdialogue/.env`（权限非 600）| — | 拒绝读取，报错并提示修正权限 |
| `config.toml` 的 `api.key` 字段 | 最低 | 使用，打印 WARNING（建议迁移到 env var）|

**自检模式（修订 D108，更新 BLOCK 规则数）：**

```
sysdialogue --test-tools

[1/7] API 连接       → 发送轻量测试请求验证 API key 和网络
[2/7] 磁盘工具       → get_disk_usage(path="/tmp", recursive=false)
[3/7] 进程工具       → list_processes(top_n=5, sort_by="cpu")
[4/7] 端口工具       → get_port_status(port=22, protocol="tcp")
[5/7] 系统信息       → get_system_info()
[6/7] PATH_PARAMS    → 验证注册表：find_files.search_path ≠ path（D103 修复验证）
[7/7] 安全规则       → 验证 9 条 BLOCK 规则（B001-B010，B003 已移除）+
                        WL003 fallback kill + WL005 read_log 无 unit

示例输出：
  ✓ API 连接正常（延迟 320ms）
  ✓ 磁盘工具正常
  ✓ 进程工具正常
  ✓ 端口工具正常（:22 LISTEN）
  ✓ 系统信息正常（ubuntu 22.04）
  ✓ PATH_PARAMS 注册表验证通过
  ✓ 安全规则：9/9 BLOCK + 5/5 WARN-LOW（含 WL003/WL005）全部通过
  ⚠ UserTool：sudo 不可用，用户管理功能已禁用
```

**会话管理（与 v3 相同）：** `--list-sessions`、`--delete-session`、30天/20个上限。

---

### 模块 12：TUI App

与 v3 相同（BlockWidget / StatusPanel / ConfirmModal / CommandInput / SSH 状态显示）。

`ConfirmModal` 处理 `confirm` 类型 Workflow step：
- 显示 YAML 中定义的 `message` 字段（动态替换变量）
- 要求用户键入 `confirm` 才继续
- 取消后向 WorkflowEngine 返回中断信号

---

### 模块 13：VoiceInput（与 v3 相同）

双方案（本地 Whisper / 联网 SpeechRecognition），录音触发、转写确认、高风险操作不接受语音确认。

---

## 四、模块间数据流（v4 workflow 场景）

```
用户输入："帮我重启一下 nginx"
    │
    ▼
[ClaudeClient] 第一个 tool_call:
  set_execution_mode(mode="workflow",
                     workflow_name="service_restart",
                     workflow_params={"service_name": "nginx"})
    │  ← 不走安全门，元工具直接路由
    ▼
[WorkflowEngine] 加载 service_restart.yaml，展开参数
    │
    ▼  步骤 s1 (tool_call): manage_service(nginx, status)
[RiskClassifier] → SAFE
[ExecutorAdapter] → LocalExecutor 或 RemoteExecutor
[AuditLog] auto_executed
    │
    ▼  步骤 s2 (confirm):
[TUI ConfirmModal] 显示 "即将重启 nginx，这将中断正在处理的请求。确认继续？"
用户键入 "confirm"
    │
    ▼  步骤 s3 (tool_call): manage_service(nginx, restart)
[RiskClassifier] → WARN-LOW（WL004，nginx 非 CRITICAL_SERVICES 成员时）
                   或 WARN-HIGH（WH006，若 nginx 在 CRITICAL_SERVICES 中）
[TUI ConfirmModal] WARN-HIGH 时再次弹窗确认
用户键入 "confirm"
[AuditLog] user_confirmed, rule_id=WL004/WH006
    │
    ▼  步骤 s4 (tool_call): manage_service(nginx, status)
[RiskClassifier] → SAFE
    │
    ▼  步骤 s5 (display):
[WorkflowEngine] 渲染展示文案，推送给 TUI BlockWidget
[TUI BlockWidget] 状态=⚠（含 WARN 操作）
```

---

## 五、设计决策变更记录（v3 → v4）

| 问题 | v3 设计 | v4 修订 | 修订原因 |
|---|---|---|---|
| disk_cleanup 输出 shell 命令 | 输出 `rm`/`apt clean` 等命令字符串 | 改为 display 步骤 + 系统提示禁止 | 与核心约束"不生成裸 shell"正面冲突 |
| RemoteExecutor 复合命令 | `"uname -a && free -h"` 字符串 | 单条命令列表，多次调用 | 接口约定 list[str]，复合命令破坏约定 |
| 路径参数检测 | 硬编码 `args.get("path")` | PATH_PARAMS 注册表 + `get_path_args()` | `find_files.search_path` 被漏检 |
| B005 穿越检测 | 字符串含 `..` | normpath 组件检测 | 误伤合法文件名（如 `..hidden`） |
| read_log 风险 | 始终 SAFE | unit=None → WL005 WARN-LOW | 全局系统日志含敏感信息 |
| Planning/Workflow 触发 | PLAN_REQUIRED / WORKFLOW: 文本前缀 | set_execution_mode 元工具 | 文本前缀依赖模型输出格式，不可靠 |
| Workflow 伪步骤 | `[确认]`、`[展示列表]` 混入 tool_call | type=confirm / display 类型化字段 | schema 未闭合，无法通用处理 |
| API key 策略 | 摘要"拒绝"vs 正文"允许+警告"矛盾 | 统一"允许+警告"，.env 权限非600则拒 | 消除前后矛盾 |
| 自检 BLOCK 数 | 输出"7/7" | 输出"9/9"（B003 移除后共 9 条）| 与实际规则数一致 |
| B003 不可能触发 | 检查 create_user.uid==0 | 删除 B003 | 工具无 uid 参数，规则永远不触发 |
| manage_service restart 粒度 | 全部 WARN-LOW | CRITICAL_SERVICES 名单：restart=WARN-HIGH | 重启 mysql/nginx 等有明显业务影响 |

---

## 六、关键设计决策总结（v4 完整版）

| 决策 | 方案 | 原因 |
|---|---|---|
| 不生成裸 shell（含自然语言回复）| system prompt 明确禁止；disk_cleanup 改为 display 步骤 | 维护安全门和审计链的完整性 |
| PATH_PARAMS 注册表 | 每个工具声明其路径类参数名 | 路径风控规则不依赖参数命名惯例 |
| normpath 穿越检测 | 按路径组件判断而非字符串匹配 | 防止误伤含 `..` 的合法文件名 |
| set_execution_mode 元工具 | 结构化 tool_call 触发编排，不解析文本前缀 | 可靠，即使未触发也优雅降级 |
| Workflow step type | tool_call / confirm / display 三种类型 | 将所有步骤行为闭合在 schema 内 |
| CRITICAL_SERVICES 名单 | 关键服务 restart=WARN-HIGH，其余 WARN-LOW | 比全部 WARN-LOW 更准确反映业务风险 |
| RemoteExecutor 单命令原则 | 每次 `run()` 执行一条命令，工具层多次调用 | 维持接口 list[str]，不引入 shell 语义 |
| WL005 read_log 全局日志 | unit=None → WARN-LOW | 全局日志信息敏感度不亚于 WARN-LOW 操作 |
| B003 删除 | create_user 无 uid 参数，规则不可能触发 | 死规则制造虚假安全感 |
| API key 权限检测 | .env 文件权限非 600 → 拒绝读取并报错 | 防止 key 因文件权限过宽被其他进程读取 |

---

## 七、开发优先级（v4）

```
P0（核心通路）
  ClaudeClient（agentic loop + set_execution_mode 元工具检测）
  ToolRegistry（OS 工具 + set_execution_mode 定义）
  RiskClassifier（PATH_PARAMS 注册表 + normpath B005 + B001-B010 共 9 条）
  只读工具：get_system_info / get_disk_usage / list_processes / get_port_status / get_network_info
  manage_service(status) — 只读，SAFE
  AuditLog
  Simple CLI（快速验证通路）
  --test-tools 自检（9/9 BLOCK + WL003/WL005）

P1（完整功能）
  UserTool（create_user / delete_user / modify_user_groups）
  ServiceTool（manage_service 全 action）
  LogTool（read_log + WL005）
  WARN 确认流程（ConfirmModal，含 confirm step 类型）
  ConversationManager（Turn Bundle + token 裁剪）
  Textual TUI + BlockWidget
  WL003 kill fallback

P2（加分项）
  PlanningEngine（set_execution_mode → plan，RollbackAdvisor）
  WorkflowEngine（YAML + 5 个模板，step type 支持）
  RemoteExecutor（SSH 模式，单命令原则）
  SystemPromptBuilder（实时快照 + 禁止 shell 命令输出约束）
  AppConfig（allowlist 参数模式 + 会话管理）

P3（可选增强）
  VoiceInput（--voice，Whisper 本地转写）
  F3 审计日志面板 / F4 会话管理面板
```

---

## 八、演示场景设计（评分维度覆盖，v4）

| 场景 | 覆盖评分维度 | 关键特性 |
|---|---|---|
| 基础四项：磁盘 / 进程 / 端口 / 用户 | 基础需求执行（10分）| 4 个 OS 工具直接调用 |
| 安全演示：kill PID 1 → BLOCK，删用户 → WARN-HIGH，`find_files(search_path="/../..")` → B005 BLOCK | 高风险识别（15分）| PATH_PARAMS + normpath 穿越检测 |
| 磁盘清理分析：find_files → display 大文件列表 → Claude 解释，不输出 shell 命令 | 行为可解释（10分）| disk_cleanup v4 改版 |
| 重启 nginx（关键服务）：set_execution_mode → service_restart workflow → WARN-HIGH 确认 | 复杂连续任务（15分）、闭环稳定性（10分）| WH006 + Workflow step type |
| 多轮上下文："内存怎么样"→"哪个进程占最多"→"用户 alice 跑的进程有哪些" | 持续状态更新（10分）、反馈连贯性（10分）| ConversationManager 上下文 |
| SSH 远程管理：从外部通过 `--ssh` 连接 openEuler，所有工具通过 RemoteExecutor 执行 | 环境感知（5+5分）| RemoteExecutor 单命令接口 |
| 语音输入：说"查看 nginx 日志"→ Whisper 转写 → read_log(unit=nginx) | 多模态无劣变（10分）| VoiceInput + SAFE 工具调用 |
| 审计日志 F3：展示 BLOCK / WARN / SAFE / user_cancelled 全类型记录 | 行为可解释（5分）| AuditLog decision 字段 |
