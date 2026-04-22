# SysDialogue — 模块设计文档 v3

> AI Hackathon 2026 · 操作系统智能代理  
> v3 修订：SSH 远程模式、服务管理工具、kill 规则漏洞修复、语音输入设计、
> 计划失败恢复、PlanningEngine 触发修复、Token 预算修正、BLOCK 规则补全、
> 实时状态注入、会话管理设计

---

## 零、v2 → v3 缺陷清单与修订摘要

| 编号 | 缺陷描述 | 严重程度 | v3 修订 |
|---|---|---|---|
| D001 | **SSH 远程操作支持完全缺失** | 严重 | 新增 RemoteExecutor + SSH 模式 |
| D002 | **服务管理工具（systemctl）缺失** | 严重 | 新增 `manage_service` 工具 |
| D003 | **kill_process 对自有进程无风险规则，误判 SAFE** | 严重 | 新增 WL003 fallback 规则 |
| D004 | **语音输入无具体设计** | 中等 | 新增 VoiceInput 模块设计 |
| D005 | **多步骤计划失败无恢复提示** | 中等 | PlanningEngine 增加 RollbackAdvisor |
| D006 | **PlanningEngine 触发条件不可靠** | 中等 | 改为 Claude intent 分类 + 关键词双重触发 |
| D007 | **Token 预算未扣除 system prompt 开销，中文估算偏低** | 中等 | 预算计算修正（中文×2 + 动态扣减） |
| D008 | **BLOCK 规则缺少 sudoers/sshd_config/hosts 保护** | 中等 | 新增 B008/B009/B010 规则 |
| D009 | **WorkflowEngine 关键词触发语义覆盖不足** | 中等 | 改为 Claude 轻量分类兜底 |
| D010 | **StatusPanel 数据与 Claude 看到的快照脱节** | 中等 | 实时查询时在工具调用前重注入状态 |
| D011 | **会话持久化缺乏 list/expire 设计** | 轻微 | 新增 session 管理命令 |
| D012 | **API Key 管理方案未说明** | 轻微 | 明确 env var 优先，拒绝配置文件存 key |
| D013 | **`--test-tools` 自检模式无设计** | 轻微 | 新增自检模块规范 |
| D014 | **缺少网络信息工具** | 轻微 | 新增 `get_network_info` 工具 |

---

## 一、整体架构概览（v3）

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
               │ messages[] + tool_definitions[]
┌──────────────▼───────────────────────────────────────────────────┐
│                    AI 调用层 (Claude Layer)                       │
│              ClaudeClient（agentic loop + 流式输出）              │
└──────────────┬───────────────────────────────────────────────────┘
               │ ToolCall（结构化，非原始 shell）
┌──────────────▼───────────────────────────────────────────────────┐
│                    安全门层 (Security Gate)                       │
│    RiskClassifier → SAFE / WARN-LOW / WARN-HIGH / BLOCK          │
│    UserConfirmation（WARN 时弹窗）   AuditLog（追加式日志）       │
└──────────────┬───────────────────────────────────────────────────┘
               │ 已批准的 ToolCall
┌──────────────▼───────────────────────────────────────────────────┐
│                    执行适配层 (Executor Adapter)   ← v3 新增      │
│     LocalExecutor（本地）  │  RemoteExecutor（SSH 远程）          │
└──────────────┬───────────────────────────────────────────────────┘
               │ 已批准的 ToolCall → 对应执行环境
┌──────────────▼───────────────────────────────────────────────────┐
│                     工具执行层 (Tool Layer)                       │
│  DiskTool │ FileTool │ ProcessTool │ PortTool │ UserTool         │
│  ServiceTool (NEW) │ NetworkTool (NEW)                            │
│              SafeExecutor（超时 + 截断 + 日志）                   │
└──────────────────────────────────────────────────────────────────┘
               │ ToolResult → 返回 Claude Layer 继续循环
```

**核心约束（不变）：**
- Claude 只接收工具结果，**永远不生成裸 shell 字符串**
- 安全门在工具调用前强制拦截，不可绕过
- 所有工具操作追加到审计日志，包括被 BLOCK 的操作

---

## 二、统一工具面（v3 扩展至 12 个工具）

> 所有规则、Workflow 模板、UI 示例均只能引用此表中存在的工具。

| # | 工具名 | 能力域 | 参数摘要 | 最高可能风险 |
|---|---|---|---|---|
| 1 | `get_disk_usage` | 磁盘 | `path`, `recursive` | WARN-LOW（recursive=true 时）|
| 2 | `find_files` | 文件 | `search_path`, `pattern`, `min_size_mb`, `max_depth` | WARN-LOW（根路径深度>5时）|
| 3 | `list_processes` | 进程 | `top_n`, `sort_by`, `filter_user` | SAFE（始终只读）|
| 4 | `kill_process` | 进程 | `pid`, `signal` | WARN-HIGH / BLOCK |
| 5 | `get_port_status` | 网络 | `port`, `protocol(tcp\|udp\|all)` | SAFE（始终只读）|
| 6 | `create_user` | 用户 | `username`, `groups[]`, `shell`, `create_home` | WARN-HIGH（始终）|
| 7 | `delete_user` | 用户 | `username`, `remove_home` | WARN-HIGH（始终）|
| 8 | `modify_user_groups` | 用户 | `username`, `groups[]`, `action(add\|remove)` | WARN-HIGH（始终）|
| 9 | `get_system_info` | 系统 | 无参数 | SAFE（始终只读）|
| 10 | `manage_service` | 服务 | `name`, `action(start\|stop\|restart\|status\|enable\|disable)` | SAFE(status) / WARN-LOW(start\|restart) / WARN-HIGH(stop\|disable) |
| 11 | `get_network_info` | 网络 | `interface(可选)` | SAFE（始终只读）|
| 12 | `read_log` | 日志 | `unit(可选)`, `lines`, `since(可选)` | SAFE（始终只读）|

**v3 工具面约束补充：**
- `manage_service` action=`status` 始终 SAFE；`start`/`restart` 为 WARN-LOW；`stop`/`disable`/`enable` 为 WARN-HIGH
- `get_network_info` 只读，返回网卡名、IP、MAC、路由摘要；不支持写操作
- `read_log` 封装 `journalctl`，只读，通过 `lines` 和 `since` 限制输出规模
- `delete_file` 和 `write_file` **仍不注册**（理由同 v2）

---

## 三、模块详细设计

---

### 模块 1：ClaudeClient（AI 调用层）

与 v2 相同。以下为 v3 补充：

**API Key 管理（修订 D012）：**

```
优先级：
  1. 环境变量 ANTHROPIC_API_KEY（推荐，最安全）
  2. ~/.sysdialogue/.env 文件（受限权限 600，启动时 warning 提示）
  3. 配置文件 config.toml 中的 api.key 字段（明确警告：不安全，仅开发用）

启动时检测：
  - 若 key 未设置 → 打印清晰错误 + 配置指引，拒绝启动
  - 若 key 来自 config.toml → 打印安全警告（不报错，不阻止）
  - 配置文件路径权限检查：若 ~/.sysdialogue/ 目录权限非 700，打印警告
```

---

### 模块 2：ConversationManager（对话历史层）

**Token 预算修正（修订 D007）：**

v2 的问题：
- 用字符数 ÷ 4 估算 token，对中文严重低估（1个中文字符 ≈ 1-2 tokens，而非 0.25）
- 固定预留 8K 给 system prompt，但 system prompt 会动态增长（OS 快照含大量数字）

v3 修订：

```python
def estimate_tokens(text: str) -> int:
    """分段估算：ASCII 按 ÷4，中文/日文/韩文按 ×1.5"""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    cjk_chars = len(text) - ascii_chars
    return ascii_chars // 4 + int(cjk_chars * 1.5)

# 预算分配
TOTAL_BUDGET = 100_000        # 从 80K 提升至 100K（claude-sonnet-4-6 支持 200K context）
SYSTEM_PROMPT_RESERVE = 16_000  # 从 8K 提升至 16K，容纳 OS 快照 + 行为约束
HISTORY_BUDGET = TOTAL_BUDGET - SYSTEM_PROMPT_RESERVE  # 84K
MIN_KEEP_BUNDLES = 3
```

---

### 模块 3：SystemPromptBuilder（系统提示构建器）

**实时状态注入修订（修订 D010）：**

v2 问题：StatusPanel 每 5 秒刷新显示当前状态，但 Claude 在每轮开始时才采集快照。若用户在第 4 秒询问"当前内存使用"，Claude 看到的是 5 秒前的数据，与 StatusPanel 显示不一致。

v3 修订：
- 在 `get_system_info` 工具被调用时，**额外**将当前快照作为 tool_result 的补充数据实时返回，而非依赖开轮时的快照
- `get_system_info` 工具结果始终包含执行时刻的实时数据（psutil 实时采集）
- system prompt 的快照用于给 Claude 提供背景，工具结果提供精确当前值

```
system prompt 快照（轮开始时，用于背景）：
  "系统概况（采集于 14:32:05）: 主机 euler-dev, Ubuntu 22.04, 内存 26% 已用..."

get_system_info 工具结果（执行时，用于精确回答）：
  "实时数据（采集于 14:32:41）: 内存 31% 已用, CPU 45%, 磁盘 / 78%..."
```

---

### 模块 4：ToolRegistry（工具注册表）

v3 在 v2 基础上增加 `manage_service`、`get_network_info`、`read_log` 三个工具定义。

**`manage_service` 参数约束：**
```json
{
  "name": "manage_service",
  "parameters": {
    "name": {"type": "string", "description": "systemd 服务名，如 nginx、sshd"},
    "action": {"type": "string", "enum": ["start", "stop", "restart", "status", "enable", "disable"]}
  }
}
```

**`get_network_info` 参数约束：**
```json
{
  "name": "get_network_info",
  "parameters": {
    "interface": {"type": "string", "description": "网卡名（如 eth0），留空返回所有"}
  }
}
```

**`read_log` 参数约束：**
```json
{
  "name": "read_log",
  "parameters": {
    "unit": {"type": "string", "description": "systemd 服务名，留空返回系统日志"},
    "lines": {"type": "integer", "minimum": 10, "maximum": 500, "default": 50},
    "since": {"type": "string", "description": "时间范围，如 '1 hour ago'、'2026-04-22'"}
  }
}
```

---

### 模块 5：RiskClassifier（风险分类器）

**新增 BLOCK 规则（修订 D008）：**

| ID | 触发条件 | 原因 |
|---|---|---|
| B001 | 任意工具的 `path` 参数指向 `/etc/passwd`、`/etc/shadow`、`/boot/*`、`/lib/systemd/*` | 系统完整性保护 |
| B002 | `kill_process` 且 `pid == 1` | 杀死 systemd 导致系统崩溃 |
| B003 | `create_user` 且 `uid == 0` | UID 0 等同 root |
| B004 | `delete_user` 或 `modify_user_groups` 且 `username == "root"` | 保护超级管理员 |
| B005 | 任意参数的字符串值含 `..` | 防路径穿越 |
| B006 | `find_files` 且 `search_path == "/"` 且 `max_depth > 5` | 防资源耗尽 |
| B007 | 任意工具的 `path` 参数指向 `/proc/kcore`、`/dev/mem`、`/proc/sys/kernel/` 前缀 | 内核内存访问 |
| **B008** | **任意工具的 `path` 或 `name` 参数匹配 `/etc/sudoers` 或 `/etc/sudoers.d/*`** | **修改 sudo 权限可提权为 root，比删用户危险** |
| **B009** | **任意工具的 `path` 参数匹配 `/etc/ssh/sshd_config`** | **修改 SSH 配置可能导致远程访问被永久锁定** |
| **B010** | **`manage_service` 且 `name` 在 `["sshd", "ssh", "systemd"]` 中 且 `action == "stop"` 或 `"disable"`** | **停止 SSH 服务会切断当前远程连接，极难恢复** |

> **B010 说明**：仅当 SSH 远程模式下停止 sshd 才绝对 BLOCK。本地模式下降级为 WARN-HIGH（用户在本地可恢复）。RemoteExecutor 启动时设置 `remote_mode=True` 标志，RiskClassifier 据此调整 B010 行为。

**kill_process 规则漏洞修复（修订 D003）：**

v2 规则覆盖空白：`kill_process` 且目标进程属于当前用户（非 PID 1）→ 无规则匹配 → 误判 SAFE

v3 新增 WL003：

| ID | 触发条件 | 警告原因 |
|---|---|---|
| WL001 | `get_disk_usage` 且 `recursive == true` | 可能产生大量 I/O |
| WL002 | `find_files` 且 `search_path == "/"` 且 `max_depth <= 5` | 全盘搜索耗时 |
| **WL003** | **`kill_process` 且未匹配任何 BLOCK/WARN-HIGH 规则（fallback）** | **终止进程不可逆，给用户最后确认机会** |

**规则优先级顺序（v3）：**
```
B001-B010（BLOCK）> WH001-WH004（WARN-HIGH）> WL001-WL003（WARN-LOW）> 默认 SAFE
```

WL003 作为 kill_process 的兜底规则，确保任何 kill 操作都至少经过 WARN-LOW 处理，在 permissive 模式下自动执行，balanced/strict 模式下需确认。

**manage_service 风险规则：**

| ID | 触发条件 | 级别 |
|---|---|---|
| WH005 | `manage_service` 且 `action` 在 `["stop", "disable"]` 中 | WARN-HIGH |
| WH006 | `manage_service` 且 `action == "enable"` | WARN-HIGH（持久化变更） |
| WL004 | `manage_service` 且 `action` 在 `["start", "restart"]` 中 | WARN-LOW |
| —— | `manage_service` 且 `action == "status"` | SAFE |

---

### 模块 6：AuditLog（审计日志）

与 v2 相同。v3 补充：

**ToolResult 内容记录：**

v2 只记录调用参数和决策，不记录工具输出。v3 记录截断后的 tool_result 内容（最多 2KB）：

```json
{
  "ts": "2026-04-22T14:32:41Z",
  "tool": "manage_service",
  "args": {"name": "nginx", "action": "restart"},
  "risk": "WARN-LOW",
  "decision": "user_confirmed",
  "rule_id": "WL004",
  "duration_ms": 312,
  "exit_code": 0,
  "result_preview": "nginx 已重启，active (running) since 14:32:41"
}
```

---

### 模块 7：SafeExecutor（安全执行器）

**与 ExecutorAdapter 的关系（v3 新增）：**

v3 引入 ExecutorAdapter 抽象层，SafeExecutor 不再直接调用 subprocess，而是通过 adapter 路由：

```python
class ExecutorAdapter(Protocol):
    def run(self, cmd: list[str], timeout: int) -> tuple[str, int]: ...

class LocalExecutor(ExecutorAdapter):
    """subprocess 本地执行"""

class RemoteExecutor(ExecutorAdapter):
    """SSH 远程执行"""
```

SafeExecutor 持有当前 adapter 实例，工具实现层不感知本地/远程差异。

---

### 模块 7b：RemoteExecutor（SSH 远程执行器）【v3 新增】

**修订目的（D001）：**

题目明确支持"通过远程协议（如 SSH）进行跨环境操作"。v2 完全缺失此能力，v3 通过 `--ssh` 模式补全。

**启动参数：**
```
sysdialogue --ssh user@host [--ssh-port 22] [--ssh-key ~/.ssh/id_rsa]
```

**核心设计：**

```
实现方式：paramiko（纯 Python SSH 库，无需系统 ssh 命令）
连接生命周期：单次连接，会话内复用（不反复握手）
命令执行：Channel 级超时（与本地 SafeExecutor 保持一致的 15s 默认值）
认证优先级：SSH key → SSH agent → 密码（最后才提示输入密码）
```

**远程模式下的特殊处理：**

| 场景 | 处理方式 |
|---|---|
| SSH 连接断开 | 捕获异常，返回 `ToolResult(is_error=True)`，提示用户重连 |
| 工具需要 sudo | sudo 命令通过 SSH channel 执行，密码通过 PTY 传入（不在命令行中出现）|
| B010 规则（停止 sshd）| BLOCK（远程模式下一律拒绝）|
| 审计日志 | 额外记录 `remote_host` 字段 |
| psutil 工具（进程/内存等）| 降级为 subprocess（psutil 只能操作本地进程）|

**psutil 降级策略（远程模式）：**

```
list_processes  → ssh + "ps aux" / "top -bn1"
get_system_info → ssh + "uname -a && free -h && df -h"
get_port_status → ssh + "ss -tlnp" (fallback: netstat)
get_disk_usage  → ssh + "df -h {path}" / "du -sh {path}"
```

**SSH 模式初始化检查：**

```
连接前：
  1. 解析 host/user/port/key
  2. 尝试建立 SSH 连接（5s 超时）
  3. 验证远程 sudo 可用性（`sudo -l`）
  4. 采集远程 OS 快照（传给 SystemPromptBuilder）
  5. 设置 remote_mode=True（供 RiskClassifier B010 判断）
连接成功后：
  TUI 状态栏显示 "🔗 SSH: user@host"
```

---

### 模块 8：工具集（扩展）

#### ServiceTool【v3 新增】

**职责：** 封装 systemctl 服务管理操作。

| 函数 | 实现 | Fallback |
|---|---|---|
| `manage_service(name, action)` | `systemctl {action} {name}` | `service {name} {action}`（init.d 兼容）|

安全约束：
- 服务名称校验：仅允许 `[a-zA-Z0-9_\-.@]` 字符，防止注入
- 不允许以 `../` 或 `/` 开头的服务名

```python
# subprocess 调用示例（禁止 shell=True）
cmd = ["sudo", "systemctl", action, name]
```

#### NetworkTool【v3 新增】

| 函数 | 实现方式 | 输出内容 |
|---|---|---|
| `get_network_info(interface)` | `psutil.net_if_addrs()` + `psutil.net_if_stats()` | 网卡名、IPv4/IPv6、MAC、状态、MTU |
| 路由表 | `subprocess ["ip", "route"]` | 主路由摘要（不超过20条）|

#### LogTool【v3 新增】

| 函数 | 实现方式 | 安全限制 |
|---|---|---|
| `read_log(unit, lines, since)` | `subprocess ["journalctl", ...]` | lines ≤ 500；不支持 `-f` follow 模式 |

fallback：若 journalctl 不可用，尝试读取 `/var/log/syslog` 或 `/var/log/messages`（最后 N 行）。

---

### 模块 9：PlanningEngine（计划引擎）

**触发条件修订（修订 D006）：**

v2 的问题：
- 触发条件之一是"Claude 在同一响应中返回工具调用 ≥ 3"
- Claude 在实际 agentic loop 中几乎不会在单次响应返回 3 个工具调用，通常是逐步调用
- 导致这个触发条件实际上永远不会激活

v3 修订：移除"工具调用数 ≥ 3"条件，改为以下双重触发：

```
触发条件（满足任一即触发）：
  1. 关键词触发：用户输入包含"配置"、"帮我"、"批量"、"部署"、"为 X 创建"、
                  "设置"、"初始化"、"清理"中任意一个
  2. Claude 意图分类触发：system prompt 中加入指令：
     "如果当前任务需要执行 3 步或以上的操作，请先在回复开头输出 PLAN_REQUIRED，
      然后列出计划，等待用户输入 'go' 后再实际调用工具。"
     ClaudeClient 检测到响应以 PLAN_REQUIRED 开头 → 切换到计划模式
```

这样关键词触发覆盖常见场景，Claude 意图分类兜底覆盖非典型表达（"帮我把 nginx 停了顺便检查一下端口"）。

**计划失败恢复（修订 D005）：**

v2 问题：计划执行中途失败只报告进度，不提供恢复路径。

v3 新增 `RollbackAdvisor`：

```
当计划在第 K 步失败时：
  1. 立即停止后续步骤
  2. 统计已完成步骤（1 到 K-1）
  3. 对每个已完成的副作用步骤，检查是否有对应的逆操作：
     - create_user(alice) 已完成 → 建议: delete_user(alice)
     - manage_service(nginx, start) 已完成 → 建议: manage_service(nginx, stop)
     - modify_user_groups(alice, sudo, add) 已完成 → 建议: modify_user_groups(alice, sudo, remove)
  4. 将逆操作建议作为文本提示给用户（不自动执行）：
     "⚠ 计划在第 3 步失败。已完成步骤：create_user(alice)。
      如需回滚，建议执行：delete_user(alice)。请问是否回滚？"
  5. 将建议回滚操作注入下一轮对话上下文
```

不支持自动回滚（自动执行 delete_user 同样需要用户确认）。RollbackAdvisor 只提供建议，用户决定是否执行。

---

### 模块 10：WorkflowEngine（工作流引擎）

**触发检测改进（修订 D009）：**

v2 的关键词列表匹配无法处理语义等价表达（"帮我建个账号" 不包含"新建用户"）。

v3 解决方案：在 system prompt 中加入 Workflow 触发指引，让 Claude 在识别到匹配意图时在响应前缀输出 `WORKFLOW:<name>:<params_json>`：

```
system prompt 附加段：
  "可用 Workflow 模板：
     - new_user: 触发词包含"创建用户/账号/新员工"；参数: username, groups
     - disk_cleanup: 触发词包含"磁盘清理/大文件/释放空间"；无参数
     - security_audit: 触发词包含"安全审计/检查权限/安全检查"；无参数
     - service_restart: 触发词包含"重启服务/重启 nginx"；参数: service_name  [v3新增]
   如用户意图匹配以上模板，在回复开头输出 WORKFLOW:<name>:<params_json>，
   然后再继续正常回复。不确定时不要强行匹配。"
```

ClaudeClient 检测响应前缀 `WORKFLOW:` → 提取 workflow 名和参数 → 传给 WorkflowEngine 执行。

**v3 新增内置模板：**

| 文件名 | 触发词（示例）| 步骤 |
|---|---|---|
| `new_user.yaml` | 创建账号、添加用户 | get_system_info → create_user → modify_user_groups → get_system_info |
| `disk_cleanup.yaml` | 磁盘清理、释放空间 | get_disk_usage → find_files(size>100MB) → [展示列表，提示手动删除] |
| `security_audit.yaml` | 安全审计、检查权限 | get_system_info → list_processes → get_port_status → get_network_info |
| `port_scan.yaml` | 查看所有端口 | get_port_status(tcp) → get_port_status(udp) |
| **`service_restart.yaml`** | **重启服务、重启 nginx** | **manage_service(status) → [确认] → manage_service(restart) → manage_service(status)** |

**disk_cleanup.yaml 交互改进（修订 D014 中磁盘清理体验）：**

v2 只展示大文件列表，用户体验断层（"你只说了有大文件，我怎么删？"）。

v3 修订：展示列表后，Claude 主动提供人工指引：

```
扫描完成，以下文件占用较大空间：
  /var/log/nginx/access.log  1.2 GB
  /var/cache/apt/archives/   800 MB

⚠ SysDialogue 不提供自动删除功能（防止误删）。
建议操作：
  - 删除日志：sudo truncate -s 0 /var/log/nginx/access.log
  - 清理 APT 缓存：sudo apt clean（或: sudo yum clean all）
请复制上述命令在终端执行，或告诉我您想了解更多关于某个文件的信息。
```

---

### 模块 11：AppConfig（配置模块）

与 v2 相同。v3 补充：

**会话管理命令（修订 D011）：**

v2 只设计了 `--resume <id>`，但用户不知道有哪些 session。

v3 新增：

```bash
# 列出所有保存的会话
sysdialogue --list-sessions
  # 输出：
  # ID            创建时间           最后更新           消息数
  # abc123ef      2026-04-20 10:32   2026-04-20 11:45   47
  # def456gh      2026-04-22 14:01   2026-04-22 14:33   12  ← 当前

# 删除会话
sysdialogue --delete-session abc123ef

# 过期策略（配置文件）：
[history]
token_budget = 84000
min_keep_bundles = 3
session_max_count = 20          # 超过时删除最旧的
session_expire_days = 30        # 30天未访问自动删除
```

**自检模式（修订 D013）：**

```bash
sysdialogue --test-tools
```

执行以下自检序列：

```
[1/6] API 连接       → 发送轻量 ping 请求验证 API key
[2/6] 磁盘工具       → get_disk_usage(path="/tmp", recursive=false)
[3/6] 进程工具       → list_processes(top_n=5, sort_by="cpu")
[4/6] 端口工具       → get_port_status(port=22, protocol="tcp")
[5/6] 系统信息       → get_system_info()
[6/6] 安全规则       → 验证 B002(kill pid=1 → BLOCK), WH003(create_user → WARN-HIGH)

输出：
  ✓ API 连接正常（延迟 320ms）
  ✓ 磁盘工具正常（/tmp 已用 12%）
  ✓ 进程工具正常（5 个进程）
  ✓ 端口工具正常（:22 LISTEN）
  ✓ 系统信息正常（ubuntu 22.04）
  ✓ 安全规则正常（7/7 条 BLOCK 规则通过）
  ⚠ UserTool：sudo 不可用，用户管理功能已禁用
  全部基础检查完成，可正常使用（UserTool 除外）
```

---

### 模块 12（新增）：VoiceInput（语音输入模块）

**修订目的（D004）：**

题目探索类能力明确鼓励语音输入，主观创新分（10分）和"多模态交互无劣变"（10分）均与此相关。v2 只提了 `--voice` 模式名称，无任何设计。

**启动方式：**

```bash
sysdialogue --voice
```

**技术方案（两层方案，按依赖可用性自动选择）：**

```
方案 A（推荐，需 pyaudio + openai-whisper）：
  - 本地 Whisper tiny/base 模型（离线，无隐私问题）
  - 触发：用户按下 空格键 开始录音，松开发送
  - 录音：pyaudio 实时采集，VAD 静音检测自动截断
  - 转写：whisper.transcribe() → 文本显示在输入框供确认/编辑
  - 用户按 Enter 确认发送，或修改后再发

方案 B（回退，需联网，使用 SpeechRecognition 库）：
  - Google Web Speech API 或 Vosk 离线模型
  - 触发方式与方案 A 相同
```

**TUI 集成（语音模式下的 UI 差异）：**

```
状态栏额外显示：🎤 语音模式（空格键录音）
输入框下方增加语音状态条：
  [■■■■■■□□□□] 录音中... 3.2s
  → 转写完成：[查看 /var 目录的磁盘使用情况]（可编辑）
```

**"多模态交互无劣变"保障：**
- 语音转写结果显示在输入框，用户可编辑后再发送（防止识别错误被直接执行）
- 高风险操作（WARN-HIGH）的确认弹窗仍要求键入文字 `confirm`（不接受语音确认）
- 若转写失败 → 显示错误提示，切换回键盘输入（不崩溃）

---

### 模块 13：TUI App（界面层）

**v3 新增显示元素：**

```
┌─ SysDialogue v3.0 ──── euler-dev ─── root@euler-dev ─── 14:32 ─┐
│                                                                   │
│ ┌─ Block #1  ✓ 完成  0.3s ────────────────────────────────────┐ │
│ │ > 查看 nginx 服务状态                                         │ │
│ │ nginx 正在运行（active since 14:20:01，已运行 12 分钟）       │ │
│ │   ✓ manage_service(nginx, status) → 成功 0.3s               │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                  │  系统状态（每5s刷新）          │
│ ┌─ Block #2  ⚠ 已确认 ─────────┐ │  CPU:  23%                   │
│ │ > 重启 nginx                  │ │  内存: 4.2/16GB (26%)        │
│ │ nginx 已重启                  │ │  磁盘: /  45% 已用           │
│ │   ⚠ manage_service [WL004]   │ │  负载: 0.42 0.38 0.35        │
│ └───────────────────────────────┘ │  🔗 SSH: admin@192.168.1.10  │
│                                   │                              │
│                                   │  最近操作                    │
│                                   │  ✓ manage_service(status)    │
│                                   │  ⚠ manage_service [WL004]   │
│                                   └──────────────────────────────┘
├───────────────────────────────────────────────────────────────────┤
│ > 输入指令...                                                     │
├───────────────────────────────────────────────────────────────────┤
│ [balanced] [🔗SSH] F1:帮助  F2:历史  F3:审计  F4:会话  Ctrl+C:退出 │
└───────────────────────────────────────────────────────────────────┘
```

v3 新增状态栏元素：
- SSH 连接状态（`🔗 SSH: user@host` 或 `💻 本地`）
- 语音模式指示（`🎤 语音`，仅 `--voice` 时显示）
- F4 快捷键：会话管理（列出/切换会话）

---

## 四、模块间数据流（v3 SSH 远程场景）

```
用户输入（通过 SSH 模式管理远程服务器）："重启 nginx 并查看日志"
    │
    ▼
[TUI App] 创建 BlockWidget，状态=⟳
    │
    ▼
[WorkflowEngine] 检测到 WORKFLOW:service_restart 前缀
    │
    ▼
[PlanningEngine] 展示计划：
    步骤 1 [安全]     manage_service(nginx, status)
    步骤 2 [⚠需确认]  manage_service(nginx, restart)
    步骤 3 [安全]     read_log(unit=nginx, lines=20)
    用户输入 'go'
    │
    ▼ 步骤 1: manage_service(nginx, status)
[RiskClassifier] → SAFE（action=status）
[ExecutorAdapter] → RemoteExecutor
[RemoteExecutor] SSH channel: "systemctl status nginx"
[AuditLog] auto_executed, remote_host=192.168.1.10
    │
    ▼ 步骤 2: manage_service(nginx, restart)
[RiskClassifier] → WARN-LOW（WL004）
[TUI App: ConfirmModal] 显示"nginx 将被重启，当前连接可能短暂中断"
用户键入 "confirm"
[RemoteExecutor] SSH channel: "sudo systemctl restart nginx"
[AuditLog] user_confirmed, rule_id=WL004
    │
    ▼ 步骤 3: read_log(unit=nginx, lines=20)
[RiskClassifier] → SAFE
[RemoteExecutor] SSH channel: "journalctl -u nginx -n 20"
    │
    ▼
[ClaudeClient] 汇总所有 tool_results，继续 stream 最终输出
[ConversationManager] close_bundle() + token 裁剪
[TUI App] BlockWidget 状态=⚠（含 WARN-LOW 操作）
```

---

## 五、设计决策变更记录（v2 → v3）

| 问题 | v2 设计 | v3 修订 | 修订原因 |
|---|---|---|---|
| SSH 远程操作 | 不支持 | 新增 RemoteExecutor + `--ssh` 模式 | 题目明确支持远程操作，v2 完全缺失 |
| 服务管理 | 无 systemctl 工具 | 新增 `manage_service` + ServiceTool | 系统管理核心能力，复杂任务演示必需 |
| kill 规则漏洞 | 自有进程 kill → 无规则 → SAFE | 新增 WL003 fallback | 任何进程终止都应至少有 WARN-LOW 确认 |
| BLOCK 规则覆盖 | 7条，缺 sudoers/SSH 配置 | 新增 B008/B009/B010 | sudoers 和 sshd_config 是高危目标 |
| 语音输入 | P3 一句话提及 | VoiceInput 模块完整设计 | 创新分和多模态评分维度 |
| 计划失败恢复 | 报告进度，无恢复建议 | RollbackAdvisor 提供逆操作建议 | 连续任务闭环稳定性（10分）要求 |
| PlanningEngine 触发 | 工具调用数 ≥ 3（实际不会触发）| 关键词 + Claude PLAN_REQUIRED 前缀 | 修复实际从不触发的缺陷 |
| Token 预算 | char÷4，80K总量，8K系统提示 | 中文×1.5，100K总量，16K系统提示 | 中文估算偏低，系统提示实际更大 |
| WorkflowEngine 触发 | 固定关键词列表 | Claude 语义分类 + WORKFLOW: 前缀 | 语义等价表达无法被关键词匹配 |
| 实时状态与 Claude 脱节 | StatusPanel 5s 刷新，Claude 看快照 | get_system_info 工具结果返回实时数据 | 用户查询状态时 Claude 应给实时值 |
| 会话管理 | 仅 --resume，无 list/expire | 新增 --list-sessions/--delete-session/过期策略 | 用户不知道有哪些 session 可恢复 |
| API Key 安全 | 未说明 | 明确 env var 优先，config 文件最低优先级 + 警告 | 防止 key 被提交到代码仓库 |
| 自检模式 | 提到但无设计 | --test-tools 7步自检序列 | 演示前必须快速验证环境 |

---

## 六、关键设计决策总结（v3 完整版）

| 决策 | 方案 | 原因 |
|---|---|---|
| 不生成裸 shell | 所有操作建模为类型化 ToolCall | 防注入，安全门可精确分类 |
| 规则引擎不调用 LLM | 确定性 `condition(tool, args) → bool` | 毫秒级响应，可审计，无幻觉风险 |
| 应用层自控 loop | 不用 SDK tool_runner | 唯一能在调用前插入安全确认的方式 |
| WARN-HIGH 始终确认 | 不受模式影响，含 permissive | 不可逆操作不能被配置项绕过 |
| WL003 kill fallback | 任何 kill_process 至少 WARN-LOW | 防止安全规则漏洞误判 SAFE |
| SSH 远程模式 | ExecutorAdapter 抽象，工具层无感 | 支持题目远程场景，工具代码不需要改变 |
| B010 sshd 保护 | 远程模式停用 sshd → BLOCK | 防止切断当前管理连接 |
| PLAN_REQUIRED 前缀 | Claude 输出特殊前缀触发计划模式 | 解决 PlanningEngine 无法可靠触发的问题 |
| RollbackAdvisor | 建议逆操作，不自动执行 | 平衡可用性和安全性 |
| VoiceInput 确认框 | 语音识别后人工确认再发送 | 防识别错误导致危险操作 |
| Token 中文修正 | CJK 字符 ×1.5，预算 100K | 避免因低估导致上下文截断不足 |
| API Key env var | 优先环境变量，config 文件降级+警告 | 防止 key 泄露到版本控制 |
| WORKFLOW: 前缀 | Claude 语义识别触发 workflow | 解决纯关键词匹配语义覆盖不足 |
| 会话过期策略 | 30天/20个上限，自动清理 | 防止会话文件无限堆积 |

---

## 七、开发优先级（v3）

```
P0（核心通路，优先保证可演示）
  ClaudeClient（agentic loop + 串行工具执行）
  ToolRegistry + 5个只读工具（磁盘/进程/端口/系统信息/网络信息）
  manage_service(status) — 只读，无需 sudo
  RiskClassifier（BLOCK 规则 B001-B010 + WARN-HIGH 规则）
  AuditLog
  Simple CLI（快速验证通路）
  --test-tools 自检（演示前必用）

P1（完整功能）
  UserTool（create_user / delete_user / modify_user_groups）
  manage_service（start/stop/restart/enable/disable）
  ServiceTool + LogTool
  WARN 确认流程（ConfirmModal）
  ConversationManager（Turn Bundle + token 裁剪，含中文修正）
  Textual TUI + BlockWidget
  WL003 kill fallback 规则

P2（加分项）
  PlanningEngine（PLAN_REQUIRED 触发 + RollbackAdvisor）
  WorkflowEngine + YAML 模板（5个，含 service_restart）
  RemoteExecutor（SSH 模式）
  SystemPromptBuilder（实时 OS 快照）
  AppConfig（allowlist 参数模式解析 + 会话管理）

P3（可选增强）
  VoiceInput（--voice 模式，Whisper 本地转写）
  审计日志 F3 查看面板
  F4 会话管理面板
  disk_cleanup.yaml 改进交互
```

---

## 八、演示场景设计（评分维度覆盖）

| 场景 | 覆盖评分维度 | 工具使用 |
|---|---|---|
| **基础四项**：磁盘/进程/端口/用户 | 基础需求执行（10分） | get_disk_usage, list_processes, get_port_status, create_user |
| **安全演示**：kill PID 1 → BLOCK，delete_user → WARN-HIGH，修改 /etc/sudoers → BLOCK | 高风险识别（15分） | kill_process, delete_user |
| **多轮上下文**："查看内存"→"如果超过80%怎么处理"→"那个进程叫什么" | 持续状态更新（10分）、反馈连贯性（10分）| get_system_info, list_processes |
| **复杂连续任务**："为新员工 alice 创建账号、加入 docker 组、重启 nginx" | 复杂连续任务（15分）、连续任务闭环（10分）| WORKFLOW:new_user + manage_service |
| **SSH 远程管理**：从 Windows 通过 SSH 管理 openEuler 服务器 | 环境感知（5分）、安全判断（5分）| 全套工具，RemoteExecutor |
| **语音输入**：语音说"查看磁盘空间" → 转写 → 执行 | 多模态交互无劣变（10分）| get_disk_usage |
| **审计日志展示**：F3 打开实时日志，展示所有 BLOCK/WARN/SAFE 记录 | 行为可解释（5+5分）| AuditLog |
