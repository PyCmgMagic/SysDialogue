# SysDialogue v6 — 开发交接文档

> 设计文档：`framework/claudeplan6.md`（1684行，19章，完整独立设计，无需参考历史版本）
> 当前状态：**代码审核暂停**，37个工具全部实现完毕，待实现：ToolRegistry / Agent层 / WorkflowEngine / UI / App入口

---

## 项目简介

SysDialogue v6：面向 Linux 服务器运维场景的操作系统智能代理。用户用自然语言输入运维需求，代理在受控工具体系内规划执行，所有操作经过安全门拦截和审计记录。

**核心原则**：Static-first / Workflow-first / Preview-Backup-Validate / EnvProfile-driven / DynTool-last

---

## 技术栈

| 依赖 | 用途 |
|---|---|
| `anthropic>=0.40.0` | Claude API，agentic loop |
| `paramiko>=3.4.0` | SSH 远程执行 |
| `textual>=0.59.0` | TUI 界面 |
| `pydantic>=2.6.0` | 数据校验 |
| `pyyaml>=6.0.1` | 工作流 YAML 定义 |
| `jinja2>=3.1.4` | 工作流参数插值 |
| `filelock>=3.13.0` | 动态工具注册表并发安全 |
| `click>=8.1.7` | CLI 入口 |
| `python-dotenv>=1.0.1` | 环境变量 / API Key 管理 |

---

## 项目结构（当前文件树）

```
sysdialogue/
├── __init__.py
├── agent/               # ⬜ 待实现：AgentController, PlanningEngine, WorkflowEngine
│   └── __init__.py
├── runtime/             # ✅ 完成
│   ├── __init__.py
│   ├── capability_probe.py   # EnvProfile TypedDict + CapabilityProbe + EnvProfileSanitizer
│   ├── secure_runner.py      # SafeExecutor 抽象基类 + LocalExecutor
│   ├── local_adapter.py      # LocalExecutor 重导出
│   └── ssh_adapter.py        # RemoteExecutor（paramiko，known_hosts RejectPolicy）
├── tools/               # ✅ 完成（37个工具全部实现）
│   ├── __init__.py
│   ├── base.py               # ToolResult
│   ├── system_info.py        # get_system_info, get_disk_usage
│   ├── process_ports.py      # list_processes, kill_process, get_port_status, get_network_info, find_files
│   ├── file_reading.py       # read_log
│   ├── users_groups.py       # create_user, delete_user, modify_user_groups
│   ├── services.py           # manage_service (含reload/daemon-reload)
│   ├── file_ops.py           # read_file, write_file, delete_path, create_directory, copy_move_path
│   ├── packages.py           # manage_package, get_resource_stats
│   ├── firewall.py           # manage_firewall (ufw/firewalld/iptables三后端)
│   ├── system_config.py      # get_set_system_config
│   ├── fs_browse.py          # list_directory, stat_path, search_file_content
│   ├── backup_restore.py     # backup_path, replace_in_file (dry_run+diff预览+原子写入)
│   ├── config_validate.py    # validate_config (nginx/sshd/sudoers/json/yaml等)
│   ├── net_diag.py           # resolve_dns, check_endpoint (ping/tcp/http/tls)
│   ├── cron_jobs.py          # manage_cron (只允许tool/workflow调度)
│   ├── sysctl_ops.py         # manage_sysctl
│   ├── archive_ops.py        # manage_archive (含B027条目安全校验)
│   ├── mount_ops.py          # manage_mount
│   ├── containers.py         # manage_container (docker/podman)
│   ├── auth_keys.py          # manage_authorized_keys
│   ├── power_ops.py          # manage_power
│   └── hosts_entries.py      # manage_hosts_entries
├── security/            # ✅ 完成
│   ├── __init__.py
│   ├── path_policies.py      # 路径保护具名集合 + 匹配函数
│   ├── risk_classifier.py    # classify(tool, args, env_profile) → RiskDecision
│   ├── remote_lockout.py     # assess_tool / assess_cmd → LockoutRisk
│   └── approval_rules.py     # ConfirmationRequest 标准化结构
├── audit/               # ✅ 完成
│   ├── __init__.py
│   ├── trace_store.py        # AuditLog（JSONL，线程安全）
│   └── serializers.py        # export_replay_package + format_audit_table
├── workflows/           # ⬜ 待创建（10个YAML文件）
├── ui/                  # ⬜ 待实现
│   └── __init__.py
└── app/                 # ⬜ 待实现
    └── __init__.py
```

---

## 已完成模块详情

### 1. `runtime/capability_probe.py`
- `EnvProfile` TypedDict（v4.1/v5.3/v5.4 全字段，含 ssh_port/container_backend/selinux_mode 等）
- `CapabilityProbe.probe()` → 探测 OS/用户/命令/init/包管理/防火墙/容器/配置校验/DNS/MAC/SSH端口
- `EnvProfileSanitizer.sanitize()` → 注入 SystemPrompt 前脱敏（移除凭证字段）
- 探测失败字段置 `"unknown"`（保守判定原则）

### 2. `runtime/secure_runner.py` + `ssh_adapter.py`
- `SafeExecutor.run(cmd, timeout)` → `(output_str, exit_code)`，统一超时/截断(512KB)/异常
- `LocalExecutor` → subprocess shell=False
- `RemoteExecutor(SSHConfig)` → paramiko，known_hosts RejectPolicy，单命令 exec_command，不开 PTY
- `RemoteExecutor.connect()` / `.disconnect()` / 上下文管理器支持

### 3. `security/path_policies.py`
具名集合（含匹配函数）：
- `SENSITIVE_CREDENTIAL_PATHS` + `SENSITIVE_CREDENTIAL_GLOBS` → `matches_sensitive_credential(path)`
- `PERSISTENCE_ENTRY_PATHS` → `matches_persistence_entry(path)`
- `CRITICAL_EDIT_PATHS` → `matches_critical_edit(path)`
- `MOUNT_BLOCK_TARGETS` → `matches_mount_block(path)`
- `ARCHIVE_BLOCK_TARGETS` → `matches_archive_block(path)`
- `CONTAINER_SENSITIVE_BIND_SOURCES` → `matches_container_sensitive_bind(path)`
- `SENSITIVE_DIR_PATHS` → `matches_sensitive_dir(path)`
- `V41_BLOCK_PATHS` → `matches_v41_block(path)`
- `SYSTEM_DIR_PREFIXES` → `matches_system_dir(path)`
- `CRITICAL_SERVICES`（set），`SSH_SERVICE_ALIASES`（set）
- `normalize(path)`，`has_path_traversal(path)`

### 4. `security/risk_classifier.py`
- `classify(tool, args, env_profile) → RiskDecision` 入口
- `RiskDecision(level, rule_ids, reason, requires_confirmation, rollback_hint)`
- 覆盖全部 37 个工具：B001-B031 / WH001-WH025 / WL001-WL017
- 自动叠加 `remote_lockout.assess_tool()` 结果，取最高等级

### 5. `security/remote_lockout.py`
- `assess_tool(tool, args, env_profile) → LockoutRisk` — 供 RiskClassifier 调用
- `assess_cmd(cmd_list, env_profile) → LockoutRisk` — 供 CommandSafetyChecker 调用
- 覆盖：B010（远程stop/disable SSH）/ B015（flush）/ B016（set-default drop/reject）/ B017（deny SSH端口）/ WH023（reload）

### 6. `audit/trace_store.py`
- `AuditLog(session_id, log_dir)` → `~/.sysdialogue/audit/<session>.jsonl`
- 方法：`log_decision / log_command / log_workflow_step / log_env_profile / log_final`
- `read_all()` → `list[dict]`

### 7. 工具层（37个，全在 `tools/`）
所有工具签名：`fn(executor: SafeExecutor, **kwargs) → ToolResult`
例外：`backup_path` / `replace_in_file`（纯本地文件操作，不需要executor）

**关键实现细节**：
- `replace_in_file`: 支持 `dry_run=True`（返回 diff_preview，不写入），原子写入（.tmp → os.replace）
- `backup_path`: 备份存储在 `~/.sysdialogue/backups/`，JSON 索引，支持 create/list/restore/delete
- `manage_cron`: job_target.kind 只允许 `"tool"` 或 `"workflow"`，不接受任意 shell
- `manage_container`: 不暴露 `privileged`/`network_mode=host`/`exec` 参数，B029/B030 纵深防御
- `check_endpoint`: 内置会话超频计数器（`_session_counters` dict 参数注入），WL017 限制

---

## 待实现模块（按优先级）

### 🔴 P0 — 必须先完成（核心通路）

#### Task 10：ToolRegistry + 元工具 + AgentController + ClaudeClient

**文件待创建**：
- `sysdialogue/agent/controller.py` — AgentController（主控循环）
- `sysdialogue/agent/intent_parser.py` — 意图解析器（从用户输入提取 tool + args）
- `sysdialogue/tools/registry.py` — ToolRegistry（工具定义注册表，JSON Schema 导出）
- `sysdialogue/tools/meta_tools.py` — 元工具定义（set_execution_mode, propose_dynamic_tool）

**核心逻辑**：
- `ToolRegistry` 维护工具名→(函数, JSON Schema)的映射，供 ClaudeClient 组装 `tool_definitions[]`
- `AgentController.run_turn(user_message)` → 调用 ClaudeClient → 收到 tool_call → 通过 RiskClassifier 检查 → 调用工具 → 写 AuditLog → 返回结果
- `ClaudeClient` 封装 Anthropic SDK agentic loop（`while True: response = client.messages.create(…)`）
- 检测 `set_execution_mode` 调用 → 路由到 PlanningEngine 或 WorkflowEngine

**关键设计约束（来自 claudeplan6.md §5.2）**：
```
当用户请求可由静态工具完成时，严禁调用 propose_dynamic_tool
propose_dynamic_tool 仅用于 37 个静态工具和内置 workflow 无法覆盖的全新能力
competition_mode 下 enable_dynamic_tool: false
```

#### Task 11：PlanningEngine + WorkflowEngine + 10个Workflow YAML

**文件待创建**：
- `sysdialogue/agent/planner.py` — PlanningEngine（PlanStep / plan_id / 执行控制）
- `sysdialogue/agent/workflow_engine.py` — WorkflowEngine（YAML 加载 / Jinja2 插值 / 步骤执行）
- `sysdialogue/workflows/*.yaml` — 10个内置工作流

**工作流列表与文件名**（来自 claudeplan6.md §9）：
```
workflows/
├── new_user.yaml                # v4.1 — 创建开发者账号
├── disk_cleanup.yaml            # v4.1 — 磁盘空间分析
├── service_restart.yaml         # v4.1 — 安全重启服务
├── security_audit.yaml          # v4.1 — 安全巡查
├── port_scan.yaml               # v4.1 — 端口扫描
├── file_edit.yaml               # v5.3 — 安全编辑配置文件
├── safe_config_patch.yaml       # v5.4 — 安全修改配置（推荐主路径）
├── rollback_config.yaml         # v5.4 — 配置回滚
├── container_rollout.yaml       # v5.4 — 容器服务发布
└── scheduled_health_check.yaml  # v5.4 — 定时健康巡检
```

**WorkflowEngine 关键设计**（来自 claudeplan6.md §9.1, §9.5）：
- 步骤类型：`tool_call | confirm | approval | display | input`
- 参数类型：`text | enum | text_list | integer | boolean | object`
- integer 类型插值**不加引号**（直接作为数值）
- `condition` 为 false 时步骤跳过，视为已成功完成（后续 depends_on 正常执行）
- `on_fail: rollback` → 触发 rollback 段
- `lock_scope: "file:{{path}}"` → WorkflowEngine 向锁管理器申请锁（见下方资源锁）
- 资源锁：文件锁/服务锁/用户锁/计划任务锁，锁超时30s → FAILED(reason: resource_locked)

**safe_config_patch.yaml 重要修复点**（来自 claudeplan6.md v6变更摘要）：
- `verify_endpoint` 拆解为三个标量（verify_host/verify_port/verify_kind），不用对象参数
- 整数插值（verify_port）去引号：`port: {{verify_port}}` 而非 `port: "{{verify_port}}"`
- 端口非空双重 condition（s8 和 s9 各自 condition 控制）
- 二次回滚兜底（r2/r3 处理 r1 失败的情况）

---

### 🟡 P1 — 核心功能增强

#### Task 12：CommandSafetyChecker + DynamicToolRegistry

**文件待创建**：
- `sysdialogue/security/command_safety.py` — CS001-CS009 命令形态检查
- `sysdialogue/tools/dynamic_registry.py` — DynamicToolRegistry（竞赛模式关闭）

**CS001-CS009 规则**（来自 claudeplan6.md §8.6）：
```
CS001: argv 含 shell 元字符 (&&, |, ;, $(, `) → BLOCK
CS002: cmd[0] 或 arg 含 ..                    → BLOCK
CS003: rm/rmdir + -rf + 系统目录               → BLOCK
CS004: mkfs / dd / shred                       → BLOCK
CS005: chmod + 777/000 + 系统目录              → WARN-HIGH
CS006: chown + root                            → WARN-HIGH
CS007: 涉及 SENSITIVE_CREDENTIAL_PATHS         → WARN-HIGH
CS008: 任意 arg 超过 8192 字符                 → WARN-HIGH
CS009: curl/wget + 管道/输出执行               → BLOCK
```

#### Task 13：UI 层（Textual TUI）

**文件待创建**：
- `sysdialogue/ui/tui_app.py` — 主界面（五区布局）
- `sysdialogue/ui/confirm_modal.py` — WARN-HIGH 确认弹窗
- `sysdialogue/ui/audit_panel.py` — F3 审计日志面板
- `sysdialogue/ui/env_panel.py` — F4 环境画像面板

**快捷键**：F3（审计）/ F4（环境）/ Ctrl+C（取消）/ Enter（确认）/ Esc（拒绝）

#### Task 14：App 入口

**文件待创建**：
- `sysdialogue/app/config.py` — 配置加载（API Key / competition_mode 等）
- `sysdialogue/app/cli.py` — Click CLI 入口（sysdialogue 命令）
- `sysdialogue/app/verify.py` — `--verify` 自检 / `--demo` 演示场景

---

### 🟢 P2 — 体验增强

- `sysdialogue/tools/output_sanitizer.py` — OutputSanitizer 统一脱敏（所有工具输出必须经过）
- `sysdialogue/tools/dynamic_registry.py` — DynamicToolRegistry + StaticRuleMapper（开发态）
- 审计面板：replace_in_file diff 视图、backup_path 清单、check_endpoint 历史趋势

---

## 关键接口约定（继续实现时必读）

### ToolResult（`tools/base.py`）
```python
@dataclass
class ToolResult:
    success: bool
    data: Any = None       # 成功时的返回数据
    error: str = ""        # 失败时的错误信息
    exit_code: int = 0
    cmd_trace: list[str]   # 底层命令列表（用于 AuditLog command_trace）
```

### RiskDecision（`security/risk_classifier.py`）
```python
@dataclass
class RiskDecision:
    level: str                     # SAFE | WARN-LOW | WARN-HIGH | BLOCK
    rule_ids: list[str]
    reason: str
    requires_confirmation: bool    # level == WARN-HIGH 时为 True
    rollback_hint: str
```

### EnvProfile（`runtime/capability_probe.py`）
TypedDict，关键字段：
- `remote_mode: bool` — 远程模式（影响 B010/B015-B017 触发）
- `ssh_port: int` — SSH 端口（B017 判断用）
- `init_system: str` — "systemd" | "sysvinit" | "unknown"
- `package_manager: str` — "apt" | "dnf" | "yum" | "unknown"
- `firewall_backend: str` — "ufw" | "firewalld" | "iptables" | "none"
- `container_backend: str` — "docker" | "podman" | "none"

### 安全门调用流程（AgentController 实现时遵循）
```
tool_call 收到
  → RiskClassifier.classify(tool, args, env_profile)
  → level == BLOCK   → AuditLog.log_decision(decision="BLOCK") → 返回拒绝
  → level == WARN-HIGH → 弹出 ConfirmModal → 用户拒绝 → log(decision="user_cancelled")
                       → 用户确认 → 继续执行
  → level in (SAFE, WARN-LOW) → 直接执行
  → 执行工具函数
  → AuditLog.log_command(tool, cmd_trace, exit_code, output_preview)
  → 返回结果给 Claude
```

---

## 已知设计 & 实现注意事项（代码审核前整理）

### 待审核点（审核时重点关注）

1. **`backup_restore.py` 的 `backup_path()`**：当前直接操作本机文件系统，**远程模式下无法备份**。
   设计要求应通过 executor 执行（`tar czf` 等），目前仅支持本地部署模式，需补充说明或修复。

2. **`hosts_entries.py` 的写入方式**：直接操作 `Path(HOSTS_FILE)`，远程模式下会写本机而非目标机。
   同上，需要通过 executor 的 `write_file` 工具或通过 SSH 执行 `sed` 等方式处理。

3. **`auth_keys.py` 的 remove 逻辑**：fingerprint 删除路径未完整实现（注释说明了简化），
   只实现了按 public_key 内容删除。

4. **`risk_classifier.py` 的 `_classify_kill_process`**：由于无法在安全门层获取进程属主，
   当前所有 kill_process 都判定为 WARN-HIGH（保守处理），设计上 WH001 应只对非当前用户进程触发。

5. **`firewall.py` 的 iptables delete 操作**：当前 delete 和 deny 共用同一逻辑，
   实际上 `iptables -D`（删除规则）需要匹配完整规则或行号，尚未完整实现。

6. **`net_diag.py` 的 `_session_counters`**：超频计数器需要调用方（AgentController）
   维护并传入，工具函数本身不持有状态。如 AgentController 未传入则无效。

7. **`manage_cron` 的调度执行**：当前只维护 JSON 索引，并不实际写入系统 crontab。
   完整实现需要在工作流引擎层将 job_target 翻译为实际 crontab 条目写入。

### SystemPrompt 注入模板（供 AgentController 实现参考）
参见 claudeplan6.md §3.2 和 §5.2，SystemPrompt 需包含：
- `EnvProfileSanitizer.sanitize(env_profile)` 的结果（能力特征，非凭证）
- 执行模式规则（set_execution_mode 调用条件）
- 安全规则摘要（BLOCK 不可绕过，competition_mode 关闭 DynTool）

---

## Git 提交历史（本次实现部分）

```
535ea33 feat(P1/P2): v5.4 剩余工具 9 个 — 系统维护 + 现代运维
9527ab1 feat(P1): v5.3 工具 9 个 — file_ops + packages + firewall + system_config
c21e15a feat(P0): v5.4 核心工具 — fs_browse + backup_restore + config_validate
054773f feat(P0): v4.1 核心工具 10 个
0d08c70 feat(P0): AuditLog — JSONL 审计日志 + 复现包导出
2c73882 feat(P0): 执行适配层 — SafeExecutor + LocalExecutor + RemoteExecutor
332051e feat(P0): 安全规则核心 — path_policies + RiskClassifier + RemoteLockoutChecker
8856ba4 feat(P0): EnvProfile + CapabilityProbe
d3c29a5 feat: 项目脚手架 — pyproject.toml, requirements, package structure
```

---

## 下一步行动（继续实现时的起点）

**最优先**：Task 10 — ToolRegistry + AgentController + ClaudeClient

建议实现顺序：
1. `sysdialogue/tools/registry.py` — ToolRegistry，将37个工具的 JSON Schema 集中注册
2. `sysdialogue/tools/meta_tools.py` — set_execution_mode / propose_dynamic_tool Schema 定义
3. `sysdialogue/agent/controller.py` — AgentController（安全门流程 + AuditLog 集成）
4. Task 11 — PlanningEngine + WorkflowEngine + 10个YAML
5. Task 12 — CommandSafetyChecker
6. Task 13 — TUI
7. Task 14 — CLI 入口

**开始 Task 10 前必读**：claudeplan6.md §3（架构）/ §4（工具面）/ §5（元工具）/ §8（安全规则）

---

## 演示场景（最终验收）

参见 `framework/claudeplan6.md §13`，共10个场景，覆盖：
- 场景4：`safe_config_patch.yaml` — nginx端口修改完整闭环
- 场景5：`rollback_config.yaml` — 配置损坏后自动回滚
- 场景7：`container_rollout.yaml` — 容器发布 + 连通性验证
- 场景3：危险请求拒绝（BLOCK级语义风险）
