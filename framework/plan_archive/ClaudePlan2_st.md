# SysDialogue — 模块设计文档 v2

> AI Hackathon 2026 · 操作系统智能代理  
> 设计参考：Warp Terminal 架构 · 无具体代码，仅模块设计  
> v2 修订：修复 permissive 安全语义、allowlist 粒度、并发执行冲突、历史裁剪、工具面不一致、Workflow 双真相源、审计日志措辞

---

## 一、整体架构概览

```
┌──────────────────────────────────────────────────────────────────┐
│                         界面层 (UI Layer)                        │
│  TUI App (Textual)  │  Simple CLI (fallback)  │  Voice (可选)   │
└──────────────┬───────────────────────────────────────────────────┘
               │ 用户输入（自然语言）
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
│                     工具执行层 (Tool Layer)                       │
│  DiskTool │ FileTool │ ProcessTool │ PortTool │ UserTool         │
│              SafeExecutor（超时 + 截断 + 日志）                   │
└──────────────────────────────────────────────────────────────────┘
               │ ToolResult → 返回 Claude Layer 继续循环
```

**核心约束：**
- Claude 只接收工具结果，**永远不生成裸 shell 字符串**
- 安全门在工具调用前强制拦截，不可绕过
- 所有工具操作追加到审计日志，包括被 BLOCK 的操作

---

## 二、统一工具面（Single Source of Truth）

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

**工具面约束说明：**
- `delete_file` **不注册**：文件删除不在比赛要求的基础能力范围内，风险过高且不易恢复，不提供此工具
- 文件写操作 `write_file` **不注册**：同上，不在基础能力范围
- `add_to_group` 不单独存在：合并为 `modify_user_groups`，语义更完整
- 路径保护（原 B001 规则）不依赖工具名，而是在 RiskClassifier 内对所有含 `path` 参数的工具统一做路径校验

---

## 三、模块详细设计

---

### 模块 1：ClaudeClient（AI 调用层）

**职责：** 管理与 Claude API 的全部通信，实现 agentic loop。

**核心设计决策：**
- 应用层自控循环（不托管给 SDK），最多 10 次工具迭代，防止 runaway
- 使用流式 API（streaming），文本 chunk 实时推送给 UI，实现"打字机效果"
- BLOCK 时向 API 返回 `is_error=True` 的 tool_result，让 Claude 优雅解释原因（不抛出异常）

**工具调用执行策略（串行优先）：**

同一响应中 Claude 可能返回多个工具调用块。执行策略如下：

| 场景 | 策略 | 原因 |
|---|---|---|
| 所有工具均已被 RiskClassifier 判定为 SAFE | 可并发执行，统一收集结果 | 只读操作无副作用，无顺序依赖 |
| 任一工具为 WARN 或 BLOCK | 强制串行，按 Claude 返回顺序逐一处理 | WARN 需用户确认，顺序影响用户判断；BLOCK 后续步骤通常应取消 |
| 工具间存在数据依赖（由 Planning 描述）| 强制串行 | 依赖关系要求前序工具结果作为后序工具输入 |

**不支持的执行模式：** WARN 级工具的并发执行。原因：用户在同时收到两个确认弹窗时无法判断顺序，审计日志的因果链也会混乱。

**输入：**
- `messages[]`：当前会话历史（ConversationManager 提供）
- `tool_definitions[]`：工具 JSON Schema 列表（ToolRegistry 提供）
- `system_prompt`：含实时 OS 快照的系统提示（SystemPromptBuilder 提供）

**输出（通过回调）：**
- `on_text_chunk(chunk)`：流式文本片段 → 推给 UI Block
- `on_tool_call(ToolCall)` → 触发安全门 → 返回 ToolResult
- `on_tool_event(status, name, detail)`：工具执行状态 → 推给 UI Block

---

### 模块 2：ConversationManager（对话历史层）

**职责：** 管理多轮对话历史，确保上下文完整性，支持会话持久化。

**历史结构说明：**

tool use 场景下的消息序列不是简单的 user/assistant 交替，而是按"完整 turn bundle"组织：

```
Turn Bundle N:
  user message (自然语言输入)
  assistant message (含 text block + 多个 tool_use block)
  user message (含对应的多个 tool_result block)
  assistant message (最终自然语言输出)
```

一个 turn bundle 是不可分割的原子单位。裁剪时只能删除完整的 bundle，不能在 bundle 中间截断（否则 Claude API 会因 user/assistant 结构不合法报错）。

**裁剪策略（替换原"20轮成对消息"）：**

1. **Token 预算**：设定总上下文预算（默认 80K token）。预留 8K 给 system prompt，剩余 72K 用于历史
2. **Token 估算**：以字符数 ÷ 4 作为 token 估算（足够准确，避免引入 tokenizer 依赖）
3. **裁剪单位**：每次从最早的完整 turn bundle 删除，直到估算 token 低于预算
4. **保护最近 N 轮**：无论 token 如何，始终保留最近 3 个完整 turn bundle（保证上下文连续性）

**状态：**
- `bundles[]`：按 turn bundle 存储的历史（便于整体裁剪）
- `session_id`：唯一会话标识符
- `session_path`：持久化文件路径

**关键行为：**
- `start_bundle(user_text)`：开始新 bundle，追加 user 消息
- `append_to_bundle(content_blocks)`：追加 assistant 响应到当前 bundle
- `close_bundle()`：提交 bundle，触发 token 预算裁剪
- `to_messages()`：将 bundles 展开为 Claude API 所需的 `messages[]` 格式
- `save()` / `resume(session_id)`：持久化与恢复

---

### 模块 3：SystemPromptBuilder（系统提示构建器）

**职责：** 每轮对话前构建包含实时系统状态的 system prompt，让 Claude 具备环境感知能力。

**核心设计决策（借鉴 Warp 的环境感知）：**
- 每轮**重新构建**（非缓存），确保 Claude 看到的是当前系统状态
- 使用 psutil 采集数据，不依赖 shell 命令，跨发行版兼容
- 注入内容：主机名、OS 发行版、当前用户、内存使用率、磁盘 / 使用率、系统负载、当前安全模式

**注入的行为约束（直接写入 system prompt）：**
- 禁止生成裸 shell 命令让用户自己执行；始终通过工具完成操作
- 执行操作前用一句话说明意图
- 工具失败时分析原因并提出替代方案
- 多步骤任务先展示计划再逐步执行

**采集的 OS 信息来源：**
- `psutil.virtual_memory()` → 内存
- `psutil.disk_usage("/")` → 磁盘
- `psutil.getloadavg()` → 负载
- `platform.freedesktop_os_release()` → 发行版名称
- `socket.gethostname()` → 主机名

---

### 模块 4：ToolRegistry（工具注册表）

**职责：** 统一管理所有工具的定义，生成传给 Claude API 的 `tool_definitions` 列表。

**核心设计决策（窄接口原则）：**
- 每个工具参数有**明确类型和约束**（如 `max_depth: integer, maximum: 10`）
- **禁止** `run_shell(command: str)` 这类宽接口——无法审计，无法分类风险
- 注册的工具以第二节"统一工具面"为准，不得引用未注册工具

**输出：** `List[ToolDefinition]`，直接传入 `client.messages.create(tools=...)`

---

### 模块 5：RiskClassifier（风险分类器）

**职责：** 对每个 ToolCall 进行风险评级，返回四级结论。

**核心设计决策（最高价值模块）：**
- **确定性规则引擎，不调用 LLM** — 毫秒级响应，可审计，不受模型幻觉影响
- 操作结构化的 `ToolCall` 对象（tool_name + args dict），**不解析 shell 字符串**，无法被绕过
- 规则按优先级评估：BLOCK 规则最优先，首条匹配即返回

**四级风险定义：**

| 级别 | 语义 | 典型操作 |
|---|---|---|
| BLOCK | 无条件拒绝，所有模式下均不可执行 | kill PID=1、路径穿越、UID=0 账户 |
| WARN-HIGH | 不可逆或高影响操作，**所有模式**下均需人工确认 | create_user、delete_user、kill 系统进程、modify_user_groups |
| WARN-LOW | 低风险写操作或耗时查询，balanced 下需确认，permissive 下自动执行 | recursive 磁盘扫描、find_files 根路径 |
| SAFE | 只读、无副作用操作，所有模式下自动执行 | list_processes、get_port_status、get_system_info、get_disk_usage（非 recursive）|

**三级权限模式（修订后）：**

| 模式 | SAFE | WARN-LOW | WARN-HIGH | BLOCK |
|---|---|---|---|---|
| strict | 需确认 | 需确认 | 需确认 | 拒绝 |
| balanced（默认）| 自动执行 | 需确认 | 需确认 | 拒绝 |
| permissive | 自动执行 | 自动执行 | **仍需确认** | 拒绝 |

> **permissive 模式的边界**：仅放宽 WARN-LOW（耗时查询类）的确认要求。create_user、delete_user、kill_process（非自身进程）、modify_user_groups 无论任何模式均需人工确认，因为这类操作不可逆或影响系统账号体系。

**BLOCK 规则清单（7条）：**

| ID | 触发条件 | 原因 |
|---|---|---|
| B001 | 任意工具的 `path` 参数指向 `/etc/passwd`、`/etc/shadow`、`/boot/*`、`/lib/systemd/*` | 系统完整性保护（适用于所有含 path 参数的工具）|
| B002 | `kill_process` 且 `pid == 1` | 杀死 systemd 导致系统崩溃 |
| B003 | `create_user` 且 `uid == 0`（若参数中显式指定）| UID 0 等同 root |
| B004 | `delete_user` 或 `modify_user_groups` 且 `username == "root"` | 保护超级管理员账户 |
| B005 | 任意参数的字符串值含 `..`（路径穿越检测）| 防路径遍历攻击 |
| B006 | `find_files` 且 `search_path == "/"` 且 `max_depth > 5` | 防资源耗尽 |
| B007 | 任意工具的 `path` 参数指向 `/proc/kcore`、`/dev/mem`、`/proc/sys/kernel/` 前缀 | 内核内存访问 |

> **说明**：B001/B007 不绑定 `delete_file`（该工具未注册），而是作用于所有工具中携带 `path` 参数的调用。这是工具面统一后的正确表达。

**WARN-HIGH 规则清单（4条，所有模式均确认）：**

| ID | 触发条件 | 警告原因 |
|---|---|---|
| WH001 | `kill_process` 且目标进程非当前用户所有 | 终止他人进程，影响不可预测 |
| WH002 | `delete_user` 任意用户名 | 永久删除账号，不可恢复 |
| WH003 | `create_user` 任意请求 | 新账号获得系统登录权限 |
| WH004 | `modify_user_groups` 任意请求 | 权限变更立即生效，影响安全边界 |

**WARN-LOW 规则清单（2条，permissive 模式自动执行）：**

| ID | 触发条件 | 警告原因 |
|---|---|---|
| WL001 | `get_disk_usage` 且 `recursive == true` | 可能产生大量 I/O，耗时较长 |
| WL002 | `find_files` 且 `search_path == "/"` 且 `max_depth <= 5` | 全盘搜索耗时，但深度受控 |

---

### 模块 6：AuditLog（审计日志）

**职责：** 以追加式 JSON Lines 格式记录所有工具操作。

**核心设计决策：**
- **所有操作均记录**，包括 BLOCK（未执行）和用户取消的操作
- 格式：每行一个 JSON 对象，包含时间戳、工具名、参数（脱敏）、风险级别、决策、规则ID、耗时、退出码
- **参数脱敏**：自动替换含 `password`、`token`、`secret`、`key` 字段的值为 `***REDACTED***`
- 路径：`~/.sysdialogue/audit.log`，可通过 TUI 的 F3 键实时查看

**关于日志完整性的准确表述：**

本日志为**单进程追加式本地日志**，不提供密码学意义上的防篡改保证。实际保证：
- 程序始终以 `append` 模式写入，不会在正常运行中覆盖历史记录
- 文件权限设为 `644`，非文件所有者无法修改
- root 用户或文件所有者仍可手动修改或删除日志文件

在 hackathon 演示和常规运维场景下，此级别的完整性足够可信。若需强完整性保证（如合规审计），需在此基础上增加哈希链或外部写入，属于本项目范围外的扩展需求。

**记录的 `decision` 字段值：**
- `auto_executed`：SAFE 自动执行
- `auto_executed_permissive`：WARN-LOW 在 permissive 模式下自动执行
- `user_confirmed`：WARN 用户确认后执行
- `user_cancelled`：WARN 用户取消
- `blocked_by_rule`：BLOCK 规则拒绝（含 rule_id）

---

### 模块 7：SafeExecutor（安全执行器）

**职责：** 将已批准的 ToolCall 路由到对应工具函数，并统一管理超时、输出截断。

**核心设计决策：**
- 维护 `tool_name → function` 的路由表，唯一执行入口
- 所有工具调用强制超时（默认 15 秒），防止命令挂起阻塞 TUI
- 输出截断：超过 50KB 时截断并附说明，防止 Claude 上下文爆炸
- 异常统一捕获为 `ToolResult(is_error=True)`，不向上传播导致崩溃

**工具函数约束：**
- 所有工具函数返回 `ToolResult(content: str, is_error: bool, duration_ms: int)`
- 不接受 `command: str` 类宽泛参数，每个参数有明确类型
- 使用 `subprocess` 时：**禁止** `shell=True`，必须传参数列表，防止注入

---

### 模块 8：工具集（DiskTool / FileTool / ProcessTool / PortTool / UserTool）

**职责：** 具体的 OS 操作实现，每个工具实现一个明确功能。

**共同设计决策：**
- **psutil 优先**（纯 Python，跨发行版，无需 shell），subprocess 作为补充
- 每个工具定义 **fallback 链**（借鉴 Warp Self-Correction），命令不存在时自动降级

#### DiskTool

| 函数 | 实现方式 | Fallback |
|---|---|---|
| `get_disk_usage(path, recursive)` | `psutil.disk_usage()` | `df -h` subprocess |
| 递归子目录统计 | `subprocess ["du", "-sh", "--max-depth=2", path]` | 无（标注不可用）|

路径安全：执行前与 B001/B007 规则的 BLOCKED_PATHS 列表二次校验（RiskClassifier 是第一道防线，工具层是第二道）。

#### ProcessTool

| 函数 | 实现方式 | 说明 |
|---|---|---|
| `list_processes(top_n, sort_by, filter_user)` | `psutil.process_iter()` | 跨发行版，无 shell |
| `kill_process(pid, signal)` | `psutil.Process(pid).terminate()` | SIGTERM 优先；root 进程由安全门拦截 |
| `get_system_info()` | psutil + socket + platform | 不依赖任何外部命令 |

#### PortTool

Fallback 链（按优先级）：
1. `psutil.net_connections()` — 无需 root，纯 Python
2. `subprocess ["ss", "-tlnp"]` — 现代 Linux 标准
3. `subprocess ["netstat", "-tlnp"]` — 旧系统兼容
4. `/proc/net/tcp` 原始解析 — 最终兜底

`protocol` 参数支持 `tcp`、`udp`、`all`，统一由单个工具处理，不拆分为多个工具。

#### FileTool

| 函数 | 实现方式 | 安全限制 |
|---|---|---|
| `find_files(search_path, pattern, min_size_mb, max_depth)` | `subprocess ["find", path, "-maxdepth", N, "-name", pattern]` | 禁止 `-exec`；max_depth ≤ 10；根路径+深度>5 走 B006 BLOCK |

**不提供文件删除、写入工具。** 原因：不在比赛基础能力范围内，误操作不可恢复，与安全设计原则冲突。

#### UserTool

| 函数 | 命令 | sudo 要求 |
|---|---|---|
| `create_user(username, groups, shell, create_home)` | `useradd -m -s <shell> [-G <groups>] <username>` | 必须 |
| `delete_user(username, remove_home)` | `userdel [-r] <username>` | 必须 |
| `modify_user_groups(username, groups, action)` | `usermod -a -G` / `gpasswd -d` | 必须 |
| 启动时检测 sudo 可用性 | `sudo -l` 验证特定命令 | 不可用则用户工具禁用，界面提示 |

跨发行版：`useradd`/`userdel`/`usermod` 在 openEuler、CentOS、Ubuntu 行为一致。

---

### 模块 9：PlanningEngine（计划引擎）

**职责：** 检测复杂多步骤意图，生成执行计划展示给用户确认，再串行执行。

**设计来源：** Warp 的 `/plan` 命令

**触发条件：**
- 用户输入含"配置"、"帮我"、"批量"、"部署"等关键词
- Claude 在同一响应中返回的工具调用数量 ≥ 3
- 任一待执行步骤为 WARN 级别

**计划展示格式（使用统一工具面的工具名）：**
```
我将执行以下计划，请确认后输入 'go' 开始执行：

  步骤 1 [安全]     get_system_info()  → 确认当前用户状态
  步骤 2 [⚠需确认]  create_user(alice, groups=[developers]) → 新账号获得登录权限
  步骤 3 [⚠需确认]  modify_user_groups(alice, [sudo], add) → 权限变更立即生效
  步骤 4 [安全]     get_system_info()  → 验证账号创建结果

输入 'go' 开始执行，或描述修改意见 >
```

**执行模式：**
- 用户输入 `go` 后**串行**逐步执行，每步单独走安全门（WARN-HIGH 步骤仍需单独确认）
- 任一步骤被 BLOCK 或用户取消，终止后续步骤，向用户汇报进度
- 执行完成后，输出统一摘要（"已完成 3/4 步，第 3 步被用户取消"）

> 计划模式天然要求串行：计划中的步骤往往有顺序依赖（创建用户 → 加组），强制串行是正确的执行语义。

---

### 模块 10：WorkflowEngine（工作流引擎）

**职责：** 加载 YAML 模板，检测自然语言触发，替换参数后交给 PlanningEngine 串行执行。

**设计来源：** Warp Workflows YAML

**Workflow YAML 结构（修订：移除 `risk` 字段）：**

```yaml
name: 模板名称
description: 一句话描述
triggers: [触发关键词列表]
parameters:
  - name: 参数名
    type: text | enum
    description: 参数说明
    required: true/false
    default: 默认值
    enum_values: [可选值列表]  # type=enum 时
steps:
  - id: 步骤ID
    tool: 工具名              # 必须在统一工具面中存在
    args: {参数: "{{变量}}"}
    description: 步骤描述     # 仅用于 Planning 展示
    depends_on: [依赖步骤ID]  # 声明顺序依赖
```

> **`risk` 字段已移除**：Workflow 模板不声明风险级别，只描述步骤和依赖关系。实际风险等级**始终且唯一地由 RiskClassifier** 在参数展开后实时计算。如果 YAML 中写了预估风险，会与 RiskClassifier 的实际结论产生双真相源，UI 展示与执行行为不一致。
>
> PlanningEngine 在渲染计划时，通过 RiskClassifier 对每个已展开参数的 ToolCall 预先调用 `classify()`，将结论填入展示格式，**而非读取 YAML 中的 risk 字段**。

**内置模板（4个，工具名已与统一工具面对齐）：**

| 文件名 | 触发词 | 步骤（使用的工具）|
|---|---|---|
| `new_user.yaml` | 新建用户、创建账号、添加用户 | get_system_info → create_user → modify_user_groups → get_system_info |
| `disk_cleanup.yaml` | 磁盘清理、清理日志、释放空间 | get_disk_usage → find_files(size>100MB) → [用户确认] → （仅展示，不删除）|
| `security_audit.yaml` | 安全审计、检查权限 | get_system_info → list_processes → get_port_status → get_system_info |
| `port_scan.yaml` | 端口扫描、查看所有端口 | get_port_status(protocol=tcp) → get_port_status(protocol=udp) |

> `disk_cleanup.yaml` 注：由于不提供 `delete_file` 工具，清理流程只能扫描并**展示**大文件列表，不支持自动删除。这是工具面约束的直接结果，在设计文档中应明确告知用户。

---

### 模块 11：AppConfig（配置模块）

**职责：** 统一管理应用配置，支持文件 + 命令行参数双渠道。

**配置文件路径：** `~/.sysdialogue/config.toml`

**allowlist 粒度修订（参数模式白名单，替换原工具名白名单）：**

原设计以"工具名"整体降级，会导致：将 `kill_process` 加入 allowlist 后，任何 PID 都被强制 SAFE，绕过了参数级风控。

新设计：allowlist 以 `工具名:参数=值` 格式定义，只有满足条件的特定参数组合才降级。且**只有 SAFE 或 WARN-LOW 工具才能被加入 allowlist**，WARN-HIGH 工具（create_user、delete_user、kill_process、modify_user_groups）不在 allowlist 有效范围内。

```toml
[security]
mode = "balanced"

# allowlist 格式：工具名，可选参数约束
# 仅 SAFE / WARN-LOW 工具有效；WARN-HIGH 工具不生效（即使写入也被忽略）
allowlist = [
    "get_disk_usage",                    # 无参数约束，所有调用均 SAFE
    "find_files:max_depth<=3",           # 仅深度≤3的 find_files 降为 SAFE
    "get_disk_usage:recursive=true",     # 允许 recursive 磁盘扫描无需确认
]

# denylist：强制升级为 BLOCK（工具名粒度，因为这里是升级不是降级，无需参数约束）
denylist = ["create_user", "delete_user"]  # 本机器上彻底禁用用户管理
```

**配置项分组：**

| 分组 | 配置项 | 说明 |
|---|---|---|
| `[api]` | `model`、`max_iterations`、`max_tokens` | Claude API 参数 |
| `[security]` | `mode`（strict/balanced/permissive）| 全局安全模式 |
| `[security]` | `allowlist`（参数模式列表）| 参数级别降级为 SAFE（仅 SAFE/WARN-LOW 工具有效）|
| `[security]` | `denylist`（工具名列表）| 强制升级为 BLOCK |
| `[history]` | `token_budget`、`min_keep_bundles` | 历史裁剪参数 |
| `[ui]` | `theme`、`status_refresh_interval` | 界面参数 |
| `[executor]` | `timeout_seconds`、`max_output_kb` | 执行限制 |

**命令行参数优先级高于配置文件：**
- `--strict` / `--permissive` 覆盖 `security.mode`
- `--resume <id>` 指定恢复的会话
- `--test-tools` 快速自检模式（不启动 UI）

---

### 模块 12：TUI App（界面层）

**职责：** 基于 Textual 框架实现 Block-Based 终端界面。

**设计来源：** Warp Terminal Block UI 模型

#### 12.1 整体布局

```
┌─ SysDialogue v2.0 ──────── hostname ─── user@host ─── 14:32 ─┐
│                                                                 │
│ ┌─ Block #1  ✓ 完成  0.3s ──────────────────────────────────┐ │
│ │ > 查看 /var 目录磁盘使用情况                                │ │
│ │ /var 目录占用 12.4 GB，主要来源：                           │ │
│ │   /var/log    8.1 GB (65%)                                  │ │
│ │   ✓ get_disk_usage(path=/var) → 成功 0.3s                  │ │
│ └────────────────────────────────────────────────────────────┘ │
│                                │  系统状态（每5s刷新）          │
│ ┌─ Block #2  ⚠ 已确认 ───────┐ │  CPU:  12%                   │
│ │ > 删除用户 testuser         │ │  内存: 4.2/16GB (26%)        │
│ │ 用户 testuser 已删除         │ │  磁盘: /  45% 已用           │
│ │   ⚠ delete_user → 已确认    │ │  负载: 0.42 0.38 0.35        │
│ └─────────────────────────────┘ │                              │
│                                  │  最近操作                    │
│ ┌─ Block #3  ✗ 已拒绝 ──────┐  │  ✓ get_disk_usage (0.3s)    │
│ │ > kill PID 1               │  │  ⚠ delete_user [WH002]      │
│ │ 操作被拒绝（规则 B002）：   │  │  ✗ kill_process [B002]      │
│ │ PID 1 是 systemd，终止它   │  │                              │
│ │ 会导致系统崩溃。            │  └──────────────────────────────┘
│ └────────────────────────────┘
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ > 输入指令...                                                   │
├─────────────────────────────────────────────────────────────────┤
│ [balanced] F1:帮助  F2:历史  F3:审计日志  Ctrl+C:退出          │
└─────────────────────────────────────────────────────────────────┘
```

> UI 示例中不再出现 `delete_file`（未注册工具），改为 `kill_process [B002]` 作为 BLOCK 演示场景。

#### 12.2 核心 Widget 设计

**BlockWidget（借鉴 Warp Block）：**
- 每次用户输入 + Agent 响应 = 一个独立 Block
- Block 头部：`编号 | 状态图标 | 耗时 | 时间戳`
- Block 状态 4 种：⟳ 执行中（蓝色）/ ✓ 完成（绿色）/ ⚠ 已确认（黄色）/ ✗ 已拒绝（红色）
- 内容区：用户输入（粗体）+ Agent 自然语言输出（流式追加）+ 工具调用摘要（可折叠）
- 工具摘要格式：`{icon} {tool_name}({关键参数}) → {状态} {耗时}ms [{规则ID，若有}]`

**StatusPanel（右侧系统面板）：**
- 每 5 秒通过 `set_interval` 刷新
- 显示：CPU%、内存、磁盘、负载
- 显示最近 5 条审计记录（带状态图标和规则ID）

**ConfirmModal（确认弹窗）：**
- WARN 级操作触发，以 ModalScreen 覆盖整个界面
- 显示具体风险说明（非通用措辞，来自 RiskClassifier 的 `explanation` 字段）
- 要求用户键入 `confirm`，而非 y/n（防止误触）
- 取消后向 Claude 返回"用户已取消"的 `is_error=True` tool_result

**CommandInput（输入框）：**
- 支持上下方向键浏览命令历史
- Agent 执行期间禁用（防止并发操作）
- 支持 `go` 关键词触发 Planning 确认

#### 12.3 Simple CLI 降级模式（`--simple`）

- 无 Textual 依赖，纯 `input()` 循环
- 用 Rich `Panel`/`Markdown` 渲染输出（有颜色，无布局）
- WARN 确认改为终端文本提示 `[需确认] 输入 confirm 继续:`
- 适用场景：哑终端、SSH 带宽受限

---

## 四、模块间数据流

```
用户输入 "为alice创建账号并加入sudo组"
    │
    ▼
[TUI App]
  创建 BlockWidget，状态=⟳，禁用输入框
    │
    ▼
[ConversationManager]
  start_bundle(user_text)
  to_messages() 展开为 messages[]
    │
    ▼
[SystemPromptBuilder]
  采集实时 OS 快照，构建 system_prompt
    │
    ▼
[ClaudeClient]
  stream(messages, tool_definitions, system_prompt)
    ├─ text_chunk → BlockWidget.append_text()
    └─ stop_reason=tool_use
         检测：有 WARN-HIGH 工具 → 强制串行模式
         │
         ▼ 串行处理第 1 个 ToolCall: create_user(alice)
       [RiskClassifier]
         classify → WARN-HIGH (WH003)
         │
         ▼
       [TUI App: ConfirmModal]
         显示"新账号将获得系统登录权限"
         用户键入 "confirm"
         │
         ▼
       [AuditLog]
         record(create_user, {username:alice}, WARN-HIGH, user_confirmed)
         │
         ▼
       [SafeExecutor]
         route → UserTool.create_user(alice)
         返回 ToolResult("用户 alice 已创建")
         │
         ▼ 串行处理第 2 个 ToolCall: modify_user_groups(alice, [sudo], add)
       [RiskClassifier]
         classify → WARN-HIGH (WH004)
         │
         ▼
       [TUI App: ConfirmModal]
         显示"权限变更将立即生效"
         用户键入 "confirm"
         │
         ▼
       [SafeExecutor]
         返回 ToolResult("已将 alice 加入 sudo 组")
         │
         ▼
       [ClaudeClient]
         追加所有 tool_results 到 messages
         继续 stream → 最终文本输出（流式）
         │
         ▼
[ConversationManager]
  close_bundle()
  触发 token 预算裁剪（若超限）
    │
    ▼
[TUI App]
  BlockWidget 状态=⚠（含有 WARN-HIGH 操作）
  恢复输入框
```

---

## 五、设计决策变更记录（v1 → v2）

| 问题 | v1 设计 | v2 修订 | 修订原因 |
|---|---|---|---|
| permissive 安全语义 | WARN 全部自动执行 | 引入 WARN-HIGH/WARN-LOW；WARN-HIGH 任何模式均需确认 | create_user/delete_user 不可逆，不应被 permissive 放行 |
| allowlist 粒度 | 工具名整体降级 | 参数模式白名单；WARN-HIGH 工具不在有效范围 | 工具名粒度会绕过参数级风控（如 kill_process 整体降级）|
| 并发工具调用 | 默认并发，统一收集 | 有 WARN 时强制串行；纯 SAFE 才可并发 | WARN 需用户确认，并发弹窗顺序混乱；顺序依赖要求串行 |
| 历史裁剪 | 20轮成对消息 | Turn Bundle 裁剪 + token 预算（80K） | tool_use/tool_result 块使"成对消息"概念失效；真正的瓶颈是 token 不是轮数 |
| 工具面一致性 | delete_file、add_to_group 出现在规则/workflow 但未注册 | 统一工具面为 9 个工具；delete_file 不注册；add_to_group 合并为 modify_user_groups | 设计文档内部不一致会导致实现边界模糊 |
| Workflow risk 字段 | YAML 中声明 risk | 移除 risk 字段；PlanningEngine 通过 RiskClassifier 实时计算 | 两个来源的风险等级可能不一致，产生双真相源 |
| 审计日志措辞 | "不可篡改" | 改为"追加式本地日志"，明确实际保证边界 | 本地 JSONL 不满足密码学意义的不可篡改，措辞不实 |

---

## 六、关键设计决策总结

| 决策 | 方案 | 原因 |
|---|---|---|
| 不生成裸 shell | 所有操作建模为类型化 ToolCall | 防注入，安全门可精确分类 |
| 规则引擎不调用 LLM | 确定性 `condition(tool, args) → bool` | 毫秒级响应，可审计，无幻觉风险 |
| 应用层自控 loop | 不用 SDK tool_runner | 唯一能在调用前插入安全确认的方式 |
| WARN-HIGH 始终确认 | 不受模式影响 | 不可逆操作不能被配置项绕过 |
| 工具面单一真相源 | 9个工具，文档开头统一定义 | 规则/workflow/UI 引用同一份表，不出现未注册工具 |
| 串行执行 WARN 操作 | 有 WARN 时强制串行 | 顺序依赖 + 用户确认语义要求串行 |
| Turn Bundle 裁剪 | 按完整 bundle 删除，token 预算控制 | 工具调用链不可拆断，token 才是真实限制 |
| Workflow 无 risk 字段 | 风险由 RiskClassifier 实时计算 | 消除双真相源，保证 UI 展示与执行一致 |
| psutil 优先 | 进程/端口/磁盘均用 psutil | 跨发行版，无 shell 依赖 |
| fallback 链 | 每个工具有降级方案 | openEuler/busybox 兼容，演示不崩溃 |
| 审计追加式本地日志 | 明确完整性边界 | 诚实表述实际保证，不夸大安全性 |

---

## 七、开发优先级

```
P0（核心通路，优先保证可演示）
  ClaudeClient（agentic loop + 串行工具执行）
  ToolRegistry + 4个只读工具（磁盘/进程/端口/系统信息）
  RiskClassifier（BLOCK 规则 + WARN-HIGH 规则）
  AuditLog
  Simple CLI（快速验证通路）

P1（完整功能）
  UserTool（create_user / delete_user / modify_user_groups）
  FileTool（find_files）
  WARN 确认流程（ConfirmModal）
  ConversationManager（Turn Bundle + token 裁剪）
  Textual TUI + BlockWidget

P2（加分项）
  PlanningEngine（多步骤计划+串行执行）
  WorkflowEngine + YAML 模板（4个内置模板）
  SystemPromptBuilder（实时 OS 快照）
  AppConfig（allowlist 参数模式解析）

P3（可选增强）
  Voice 输入（--voice 模式）
  审计日志 F3 查看面板
```
