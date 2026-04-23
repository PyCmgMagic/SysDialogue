# SysDialogue — v6 完整设计文档

> 版本定位：在 v5.4 基础上经过三轮安全审计修复，综合 v4.1 → v5.3 → v5.4 → v6 的完整独立设计文档，无需参考历史版本  
> 核心原则：**Static-first / Workflow-first / Preview-Backup-Validate / EnvProfile-driven / DynTool-last**  
> 目标：将尽量多的高频运维场景固化为静态语义工具，让系统更强、更稳、更可控，满足"自主与可控、可解释、可验证"要求
>
> **v6 相对 v5.4 的变更摘要（安全审计修复）**：
> - **安全规则**：新增 B028-B031（root公钥/容器特权/容器主机网络/敏感目录枚举）；WH022-WH025（关键服务reload/防火墙reload/系统目录copy/私网批量探测）；WL016-WL017（私网探测/探测超频）；8.1.1 节统一规则设计约定（优先级/路径规范化/SSH别名/BLOCK不可覆盖）
> - **工具 Schema**：`manage_archive` 补 `source_path` 及 `create` 强制约束；`manage_cron` update 拆出独立 allOf 要求 `job_id`；`manage_container` 加 `additionalProperties:false`，排除 privileged/network_mode/exec
> - **Workflow Schema**：参数类型枚举扩展至 `text/enum/text_list/integer/boolean/object`（含插值语义说明）；补充 `approval` step type；`condition` 跳过语义明确（视为已成功完成）
> - **工作流修复**：`safe_config_patch` 拆解 `verify_endpoint` 对象参数为三个标量、整数插值去引号、端口非空双重 condition、二次回滚兜底；`container_rollout` 整数插值去引号、补完整 rollback/final 段；`scheduled_health_check` 端口类型改 `integer` 并去引号
> - **SSRF 防护**：统一私网探测分级语义（单次→WL016，批量/重定向→WH025），消除原文"禁止"/"WARN-HIGH"/"WARN-LOW"三处矛盾；工具总表最高风险更新为 WARN-HIGH
> - **EnvProfile**：`ssh_port` 刷新绑定 sshd_config 写入事件而非不存在的配置键；SystemPrompt 注入前加 `EnvProfileSanitizer`；探测失败字段置 `"unknown"`

---

## 目录

1. 背景与设计目标
2. 设计原则与边界
3. 整体架构
4. 静态工具面全量（37 个）
5. 元工具设计
6. 动态工具机制（DynTool）
7. EnvProfile 完整定义
8. 安全规则全量
9. 工作流体系（含 9.5 资源锁与并发控制）
10. 关键 Schema 设计
11. 模块文件清单
12. 开发优先级
13. 演示场景设计
14. 结论
15. 系统解释策略与输出处理
16. 交互形态与用户体验
17. 测试与验证方案
18. 失败策略
19. 提交材料清单

---

## 一、背景与设计目标

SysDialogue 是面向 Linux 服务器运维场景的操作系统智能代理。用户通过自然语言输入系统管理需求，代理完成以下闭环：

- 理解用户意图；
- 感知当前操作系统环境；
- 在受控工具体系内规划执行路径；
- 对高风险操作进行预警、确认或拒绝；
- 在真实 Linux 环境中执行操作；
- 对结果进行验证、解释与审计记录；
- 必要时执行回滚与异常恢复。

**设计起点（v4.1）的局限：**

v4.1 的 12 个工具仅满足赛题最低要求（磁盘、文件检索、进程、端口、用户），无法覆盖真正"无命令运维"的核心场景。v5.3 将工具面从 12 扩展到 21，并引入 DynTool 兜底。但从"真正可用于日常运维代理"的角度看，仍有三类明显缺口：

| 能力缺口 | v5.3 状态 | 为什么仍不够 |
|---|---|---|
| 配置修改闭环 | 有 `read_file` / `write_file` / `file_edit.yaml` | 缺少目录浏览、元数据查看、备份恢复、精准替换、配置校验，仍过度依赖整文件覆盖 |
| 网络与服务诊断 | 有端口、防火墙、日志 | 缺少 DNS 解析、HTTP/TCP/TLS 探测、主机连通性验证 |
| 系统维护与发布 | 有包管理、服务、防火墙 | 缺少计划任务、sysctl、挂载、容器、SSH 公钥、重启关机、hosts 管理等高频场景 |

v5.4 将静态工具面扩展到 37 个，显著收窄 DynTool 的触发边界。

---

## 二、设计原则与边界

### 2.1 五条硬约束

1. **Claude 永远不在自然语言回复中输出裸 shell 命令字符串**
2. **安全门强制拦截所有 OS 工具调用，不可绕过**
3. **所有操作写入审计日志（含 SAFE、WARN、BLOCK、user_cancelled）**
4. **底层执行命令只进入 AuditLog / 审计面板 / 导出复现包，不作为用户侧命令建议**
5. **BLOCK 级安全门不提供任何用户覆盖入口，包括 DynTool 通路**

### 2.2 参赛版约束（competition mode）

```yaml
competition_mode:
  enable_dynamic_tool: false       # DynTool 参赛模式关闭
  allow_arbitrary_shell: false
  require_audit_trace: true
  require_verify_after_mutation: true
  require_user_confirmation_for_warn_high: true
  protected_configs_strict_mode: true
```

### 2.3 部署模式

- **本地部署模式**：代理与目标 Linux 系统部署在同一主机；
- **远程代理模式**：控制端通过 SSH 连接目标 Linux 服务器，远程模式额外激活防锁门检测（B010/B015-B017）。

### 2.4 规划原则

- 能用工作流时不用临时工具链；
- 能用静态工具时不用动态能力；
- 先读后改，先看对象再修改对象；
- 所有变更型任务必须定义验证动作；
- 所有高风险变更必须定义回滚方案；
- **配置改动优先路径：先预览、再备份、后校验（Preview / Backup / Validate）**。

---

## 三、整体架构

### 3.1 六层架构

```text
┌────────────────────────────────────────────────────────────────────────┐
│                           界面层 (UI Layer)                             │
│   TUI App (Textual)   │   Simple CLI (--simple)   │   Web 控制台       │
└──────────────┬──────────────────────────────────────────────────────────┘
               │ 用户输入（自然语言 / 语音转写文本）
┌──────────────▼──────────────────────────────────────────────────────────┐
│                      对话引擎层 (Dialogue Layer)                         │
│  ConversationManager ↔ PlanningEngine ↔ WorkflowEngine                  │
│  SystemPromptBuilder（注入实时 OS 快照 / EnvProfile / Prompt 版本）     │
└──────────────┬──────────────────────────────────────────────────────────┘
               │ messages[] + tool_definitions[]（含元工具）
┌──────────────▼──────────────────────────────────────────────────────────┐
│                       AI 调用层 (Claude Layer)                           │
│  ClaudeClient（agentic loop + 流式）                                     │
│  检测 set_execution_mode / propose_dynamic_tool → 路由到对应引擎          │
└──────────────┬──────────────────────────────────────────────────────────┘
               │ ToolCall（结构化）
┌──────────────▼──────────────────────────────────────────────────────────┐
│                       安全门层 (Security Gate)                            │
│  RiskClassifier（规则 / 路径 / 远程上下文）+ RemoteLockoutChecker         │
│  CommandSafetyChecker（CS001-CS009 + 委托远程锁门）                       │
│  UserConfirmation（WARN 弹窗）                                            │
│  AuditLog（JSONL + command_trace + decision_trace）                       │
└──────────────┬──────────────────────────────────────────────────────────┘
               │ 已批准的 ToolCall
┌──────────────▼──────────────────────────────────────────────────────────┐
│                     执行适配层 (Executor Adapter)                         │
│  CapabilityProbe（EnvProfile 构建）                                       │
│  LocalExecutor（subprocess list, shell=False）                            │
│  RemoteExecutor（known_hosts 校验 + 单命令 SSH exec）                     │
│  SafeExecutor（超时 + 截断 + 统一异常）                                    │
└──────────────┬──────────────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────────────┐
│                       工具执行层 (Tool Layer)                             │
│  37 个静态工具 + OutputSanitizer（脱敏）                                  │
│  DynamicToolRegistry（DynTool 执行，竞赛模式关闭）                         │
└────────────────────────────────────────────────────────────────────────┘
```

### 3.2 核心运行闭环

```text
自然语言输入
→ 意图解析
→ 参数与目标识别
→ 环境感知
→ 风险判定
→ 计划生成
→ 预览/确认
→ 执行
→ 验证
→ 回滚或完成
→ 自然语言反馈
→ 审计记录
```

### 3.3 五类系统决策状态

| 状态 | 含义 | 系统行为 |
|---|---|---|
| NEED_INFO | 参数不足或目标不明确 | 询问最小必要信息，不执行 |
| PLAN_READY | 计划已生成 | 向用户展示执行路径和影响面 |
| NEED_CONFIRMATION | 高风险但允许执行 | 展示风险依据、回滚方案，请求确认 |
| BLOCKED | 命中阻断规则 | 明确拒绝并说明原因 |
| COMPLETED / ROLLED_BACK / FAILED | 已结束 | 输出结果、验证、回滚或失败原因 |

---

## 四、静态工具面全量（37 个）

### 4.1 系统观察类（v4.1 原有）

| # | 工具名 | 能力域 | 参数摘要 | 最高风险 |
|---|---|---|---|---|
| 1 | `get_disk_usage` | 磁盘 | `path`, `recursive` | WARN-LOW |
| 2 | `find_files` | 文件检索 | `search_path`, `pattern`, `min_size_mb`, `max_depth` | WARN-LOW |
| 3 | `list_processes` | 进程 | `top_n`, `sort_by`, `filter_user` | SAFE |
| 4 | `kill_process` | 进程 | `pid`, `signal` | WARN-HIGH / BLOCK |
| 5 | `get_port_status` | 网络 | `port`, `protocol(tcp|udp|all)` | SAFE |
| 9 | `get_system_info` | 系统 | 无参数 | SAFE |
| 11 | `get_network_info` | 网络 | `interface`（可选） | SAFE |
| 12 | `read_log` | 日志 | `unit`（可选）, `lines`, `since`（可选） | SAFE / WARN-LOW |

### 4.2 用户与权限类（v4.1 原有）

| # | 工具名 | 能力域 | 参数摘要 | 最高风险 |
|---|---|---|---|---|
| 6 | `create_user` | 用户 | `username`, `groups[]`, `shell`, `create_home` | WARN-HIGH |
| 7 | `delete_user` | 用户 | `username`, `remove_home` | WARN-HIGH |
| 8 | `modify_user_groups` | 用户 | `username`, `groups[]`, `action(add|remove)` | WARN-HIGH |

### 4.3 服务与软件类（v4.1 原有）

| # | 工具名 | 能力域 | 参数摘要 | 最高风险 |
|---|---|---|---|---|
| 10 | `manage_service` | 服务 | `name`, `action(start|stop|restart|status|enable|disable|reload|daemon-reload)` | SAFE / WARN-LOW / WARN-HIGH |

> **v5.4 增强**：`manage_service.action` 新增 `reload`（关键服务 CRITICAL_SERVICES → WH022 WARN-HIGH，非关键服务 → WARN-LOW）和 `daemon-reload`（WARN-LOW）。

### 4.4 文件操作类（v5.3 新增）

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 13 | `read_file` | 文件 | `path`, `mode(range|head|tail)`, `start_line`, `end_line`, `head_lines`, `tail_lines`, `max_bytes=8192` | WARN-LOW / BLOCK |
| 14 | `write_file` | 文件 | `path`, `content`, `mode(overwrite|append|create_only)`, `atomic=true`, `create_backup=false`, `backup_label` | WARN-HIGH / BLOCK |
| 15 | `delete_path` | 文件 | `path`, `recursive: bool` | WARN-HIGH / BLOCK |
| 16 | `create_directory` | 文件 | `path`, `parents: bool` | WARN-LOW |
| 17 | `copy_move_path` | 文件 | `src`, `dst`, `action(copy|move)` | WARN-LOW / WARN-HIGH（copy 到系统目录升级为 WARN-HIGH） |

> **v5.4 增强**：`read_file` 新增 `mode: range|head|tail` 和 `tail_lines` 参数；`write_file` 新增 `atomic`, `create_backup`, `backup_label` 参数（推荐路径被 `replace_in_file + backup_path + validate_config` 取代）。

### 4.5 包管理与监控（v5.3 新增）

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 18 | `manage_package` | 包管理 | `name|names[]`, `action(install|remove|update|list|search|clean-cache|hold|unhold)`, `manager(auto|apt|yum|dnf)` | SAFE(list/search) / WARN-HIGH |
| 19 | `get_resource_stats` | 监控 | `resource(cpu|memory|all)`, `top_n_procs` | SAFE |

> **v5.4 增强**：`manage_package.action` 新增 `clean-cache`, `hold`, `unhold`。

### 4.6 防火墙与系统配置（v5.3 新增）

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 20 | `manage_firewall` | 防火墙 | `backend(auto|ufw|firewalld|iptables)`, `action(list|allow|deny|delete|set-default|flush|reload)`, `target{port,service,protocol,source_ip}`, `direction`, `policy` | SAFE(list) / WARN-HIGH / BLOCK |
| 21 | `get_set_system_config` | 系统配置 | `key(hostname|timezone|locale)`, `value`（可选） | SAFE(get) / WARN-HIGH(set) |

> **v5.4 增强**：`manage_firewall.action` 新增 `reload`，覆盖防火墙配置刷新场景。远程模式下 `reload` 触发 WH023（WARN-HIGH），本地模式为 WARN-LOW。

### 4.7 文件浏览与检索（v5.4 新增）

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 22 | `list_directory` | 文件浏览 | `path`, `recursive`, `max_depth`, `include_hidden`, `max_entries`, `sort_by` | SAFE / WARN-LOW |
| 23 | `stat_path` | 文件元数据 | `path`, `follow_symlink`, `with_hash`, `hash_algo` | SAFE / WARN-LOW |
| 24 | `search_file_content` | 文本检索 | `search_path`, `pattern`, `file_glob`, `regex`, `case_sensitive`, `max_matches` | WARN-LOW / BLOCK |

> 这三者补齐"看清对象"能力：先看目录结构 → 再看目标文件属性 → 再在限定范围内检索内容。  
> `search_file_content` 仍受敏感路径集合约束，搜索结果统一过 OutputSanitizer，不允许绕过凭证文件读取限制。

### 4.8 配置变更闭环（v5.4 新增）

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 25 | `backup_path` | 备份恢复 | `action(create|list|restore|delete)`, `path`, `backup_id` | SAFE(create/list) / WARN-HIGH |
| 26 | `replace_in_file` | 精准编辑 | `path`, `match_type(literal|regex)`, `search`, `replace`, `expected_matches`, `max_replacements=1`, `create_backup=true` | WARN-HIGH / BLOCK |
| 27 | `validate_config` | 配置校验 | `target_type(auto|nginx|apache|sshd|sysctl|sudoers|systemd-unit|json|yaml|toml|fstab|cron)`, `path` | SAFE / WARN-LOW |

> 这三者构成配置改动闭环：`backup_path(create)` → `replace_in_file` → `validate_config` → 失败时 `backup_path(restore)`。

### 4.9 网络诊断（v5.4 新增）

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 30 | `resolve_dns` | DNS 诊断 | `name`, `record_type`, `resolver` | SAFE / WARN-LOW / WARN-HIGH |
| 31 | `check_endpoint` | 连通性诊断 | `kind(ping|tcp|http|tls)`, `host`, `port`, `path`, `method`, `expected_status`, `timeout` | SAFE / WARN-LOW / WARN-HIGH |

> **`resolve_dns` / `check_endpoint` 安全约束（SSRF 防护）**，风险等级与安全规则章节保持一致：
>
> | 场景 | 风险等级 | 对应规则 |
> |---|---|---|
> | `host`=localhost / 127.0.0.1（健康检查白名单） | SAFE | 豁免 |
> | `host`/`name` 解析到 RFC1918（10/8、172.16/12、192.168/16）、链路本地（169.254/16）、IPv6 ULA（fc00::/7） | WARN-LOW | WL016 |
> | 同一 /24 私网段在单次会话内被探测 > 10 次 | WARN-HIGH | WH025 |
> | `check_endpoint(kind=http\|tls)` HTTP 重定向跳转到私网地址 | WARN-HIGH | WH025 |
> | `resolve_dns(resolver)` 指向私网 DNS（非 8.8.8.8/1.1.1.1 等公网服务器） | WARN-LOW | WL016 |
> | `check_endpoint` 单会话 > 20 次 或 `resolve_dns` > 40 次 | WARN-LOW | WL017 |

### 4.10 系统维护（v5.4 新增）

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 28 | `manage_cron` | 计划任务 | `action(list|create|update|delete|enable|disable)`, `scope(user|system)`, `schedule`, `job_target{kind,name,args}`, `job_id` | SAFE(list) / WARN-HIGH / BLOCK |
| 29 | `manage_sysctl` | 内核参数 | `action(list|get|set|apply-file)`, `key`, `value`, `persist` | SAFE(list/get) / WARN-HIGH（`set` 为运行时生效；`set+persist=true` 或 `apply-file` 同时修改持久化配置，风险更高，需在 WH016 确认提示中明示持久化影响） |
| 32 | `manage_archive` | 归档压缩 | `action(list|create|extract)`, `archive_path`, `source_path`（create 时必填，待归档的目录或文件路径）, `target_path`（extract 时必填，解压目标目录）, `format(auto|tar|tar.gz|zip)`, `strip_components` | SAFE(list) / WARN-LOW(create) / WARN-HIGH(extract) / BLOCK |
| 33 | `manage_mount` | 挂载管理 | `action(list|mount|umount|remount)`, `source`, `target`, `fs_type`, `options[]` | SAFE(list) / WARN-HIGH / BLOCK |

> **`manage_cron` 关键约束**：不接受任意 shell 文本，只允许调度已注册的静态工具调用或 workflow 调用。`job_target.kind` 只能是 `tool`（命中静态工具注册表）或 `workflow`（命中内置 workflow 名称）。创建前系统对 `job_target` 做递归风险判定，BLOCK 级目标不允许进入计划任务。

> **`manage_archive(extract)` 内建安全保护**：拒绝绝对路径条目、带 `..` 的越界条目、逃逸 `target_path` 的符号/硬链接；对总文件数、总字节数设置安全阈值。

> **`manage_mount` 约束**：只做即时挂载，不直接修改 `/etc/fstab`；对关键目标路径统一进入 BLOCK 检查。

### 4.11 现代运维（v5.4 新增）

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 34 | `manage_container` | 容器管理 | `backend(auto|docker|podman)`, `action(list|status|pull|start|stop|restart|logs|inspect|run|remove)`, `name`, `image`, `ports[]`, `env_vars`, `volumes[]`, `restart_policy`, `lines` | SAFE(list/status/logs/inspect) / WARN-HIGH(run/pull/start/stop/restart/remove) / BLOCK |
| 35 | `manage_authorized_keys` | SSH 公钥 | `action(list|add|remove)`, `username`, `public_key`, `fingerprint` | SAFE(list) / WARN-HIGH(add/remove) / BLOCK |
| 36 | `manage_power` | 重启关机 | `action(reboot|shutdown)`, `delay_sec`, `reason`, `force` | WARN-HIGH |
| 37 | `manage_hosts_entries` | 主机名映射 | `action(list|add|update|delete)`, `hostname`, `ip_addrs[]`, `comment` | SAFE(list) / WARN-HIGH(mutate) / BLOCK |

> **`manage_container` 约束**：不提供 `exec shell`，不提供 `privileged=true`，不提供 `network_mode=host`，对 bind mount 统一做敏感路径检查。

> **`manage_authorized_keys` 约束**：只接受合法公钥，不允许疑似私钥内容；root 账户公钥修改默认 BLOCK；不允许删除最后一个受信任管理员入口。

---

## 五、元工具设计

### 5.1 set_execution_mode

```json
{
  "name": "set_execution_mode",
  "description": "在调用 OS 工具之前声明执行模式，用于触发 plan 或 workflow。",
  "input_schema": {
    "type": "object",
    "properties": {
      "mode": {"type": "string", "enum": ["plan", "workflow", "direct"]},
      "plan_steps": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "step_id": {"type": "string"},
            "tool": {"type": "string"},
            "args": {"type": "object"},
            "purpose": {"type": "string"},
            "expected_risk": {"type": "string", "enum": ["SAFE", "WARN-LOW", "WARN-HIGH", "BLOCK", "UNKNOWN"]},
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

不经过 RiskClassifier，但所有调用写入审计日志。

### 5.2 SystemPrompt 中的执行模式规则

```text
在调用任何 OS 工具之前，如果满足以下任一条件，必须先调用 set_execution_mode：
  - 用户请求需要 3 步或以上操作：mode="plan"
  - 用户请求匹配某个 Workflow 模板：mode="workflow"
  - 用户请求单步直接执行：mode="direct" 或不调用

当用户请求的操作可以由现有静态工具完成时，严禁调用 propose_dynamic_tool。
propose_dynamic_tool 仅用于 37 个静态工具和内置 workflow 完全无法覆盖的全新能力。
```

---

## 六、动态工具机制（DynTool）

> **竞赛模式关闭**。DynTool 机制在 competition mode 下禁用，只作为研发阶段兜底能力描述保留。

### 6.1 触发门槛（v5.4 收紧后）

只有满足以下全部条件时，Claude 才能进入 `propose_dynamic_tool`：

1. 37 个静态工具均无法表达该能力；
2. 现有 workflow 无法组合完成；
3. 目标能力不是对已有静态工具的简单变体；
4. 不属于以下已被静态工具覆盖的能力域：文件浏览/检索/备份/精准编辑/校验、包管理、服务管理、防火墙管理、计划任务、sysctl、DNS/TCP/HTTP/TLS 诊断、压缩解压、挂载、容器运维、SSH 公钥管理、重启关机、hosts 映射管理。

### 6.2 元工具 propose_dynamic_tool

```json
{
  "name": "propose_dynamic_tool",
  "description": "当现有工具无法满足用户需求时调用。提出新工具方案供用户审批，不自动执行。",
  "input_schema": {
    "type": "object",
    "properties": {
      "intent_summary": {"type": "string"},
      "proposed_tool_name": {"type": "string"},
      "cmd_template": {
        "type": "array",
        "items": {"type": "string"},
        "description": "subprocess argv，用 {param_name} 表示参数占位符，元素数 ≤ 10，每元素 ≤ 256 字符"
      },
      "params": {"type": "object", "description": "参数定义：{param_name: {type, description, required}}"},
      "consequences": {"type": "string"},
      "risk_assessment": {"type": "string"},
      "estimated_risk": {"type": "string", "enum": ["WARN-LOW", "WARN-HIGH", "UNKNOWN"]},
      "reversible": {"type": "boolean"}
    },
    "required": ["intent_summary", "proposed_tool_name", "cmd_template", "consequences", "risk_assessment", "estimated_risk"]
  }
}
```

### 6.3 DynamicTool 数据结构

```python
class DynamicTool(TypedDict):
    tool_id: str            # "dyn_" + uuid4()[:8]
    name: str
    description: str
    cmd_template: list[str]
    params: dict
    risk_level: str         # 永远 "UNKNOWN"
    estimated_risk: str     # 提案时评估，仅展示用
    reversible: bool
    created_at: str         # ISO8601
    created_for: str        # 原始用户输入
    consequences: str
    risk_assessment: str
    usage_count: int
    safety_overrides: int   # CommandSafetyChecker 升级风险的累计次数
```

### 6.4 三层执行链

```python
class DynamicToolRegistry:
    def execute(self, tool_name, args, executor, env_profile, confirm_fn):
        cmd = self._render(tool.cmd_template, args)

        # 第一层：CommandSafetyChecker（命令形态 + 远程锁门）
        safety = check_command(cmd, env_profile)
        if safety.escalated_risk == "BLOCK":
            return ToolResult(blocked=True, reason=safety.explanation)

        # 第二层：StaticRuleMapper + RiskClassifier（对象语义）
        mapped = StaticRuleMapper.map(cmd)
        if mapped:
            rc_result = RiskClassifier.classify(mapped.tool, mapped.args)
            if rc_result.level == "BLOCK":
                return ToolResult(blocked=True, reason=rc_result.reason)

        # 第三层：UNKNOWN 确认（始终触发）
        user_ok = confirm_fn(tool, cmd, safety, rc_result)
        if not user_ok:
            return ToolResult(cancelled=True)

        output, exit_code = executor.run(cmd, timeout=30)
        return ToolResult(output=output, exit_code=exit_code)
```

### 6.5 持久化与并发安全

```text
文件路径：~/.sysdialogue/dynamic_tools.json，创建时 os.chmod(path, 0o600)
写入策略：write → .tmp → os.replace()（原子替换）
并发保护：filelock.FileLock(path + ".lock") 保护所有写操作
上限：    20 个工具
```

---

## 七、EnvProfile 完整定义

```python
class EnvProfile(TypedDict):
    # v4.1 原有字段
    os_release: str
    distro_id: str
    distro_version: str
    distro_family: str
    kernel_version: str
    architecture: str
    current_user: str
    has_sudo: bool
    is_container: bool
    remote_mode: bool
    init_system: str           # "systemd" | "sysvinit" | "unknown"
    package_manager: str       # "apt" | "dnf" | "yum" | "zypper" | "unknown"
    service_manager: str       # "systemd" | "service" | "unknown"
    available_cmds: dict[str, bool]   # systemctl/service/journalctl/ss/netstat/ip/ifconfig 等

    # v5.3 新增
    firewall_backend: str      # "ufw" | "firewalld" | "iptables" | "none"
    ssh_port: int              # SSH 连接端口（用于远程锁门检测）

    # v5.4 新增
    container_backend: str     # "docker" | "podman" | "none"
    config_validators: list[str]   # ["nginx", "sshd", "systemd-unit", "sudoers"]
    supports_journalctl: bool
    supports_system_cron: bool
    mount_capable: bool
    dns_tools: list[str]       # ["dig", "nslookup", "getent"]
    selinux_mode: str          # "enforcing" | "permissive" | "disabled" | "unknown"
    apparmor_mode: str         # "enabled" | "disabled" | "unknown"
```

### 7.0 EnvProfile 安全注意事项

| 注意事项 | 说明 |
|---|---|
| `ssh_port` 刷新策略 | `ssh_port` 仅在 RemoteExecutor 连接建立时从连接参数读取，不在会话中重新探测。当 `replace_in_file` 或 `write_file` 成功写入 `/etc/ssh/sshd_config` 后，执行适配层主动重新探测（`ss -tlnp` 过滤 sshd）并更新 `ssh_port`；若探测失败则保持旧值并写入审计警告。`get_set_system_config` 不支持 `ssh-port` 键，不能用于触发此刷新 |
| SystemPrompt 注入脱敏 | EnvProfile 注入 SystemPrompt 前，须经过 `EnvProfileSanitizer` 过滤：移除或脱敏任何可能包含凭证的字段值（如含密码的 URL、含 token 的 env 变量值）。注入内容只包含系统能力特征，不包含敏感凭证 |
| 探测失败处理 | 任一字段探测失败时，该字段置为 `"unknown"` 而非 `null`，保证 RiskClassifier 能做保守判定（未知值按不利方向处理）|

### 7.1 CapabilityProbe 探测内容（v5.4 完整版）

```text
基础：发行版、版本、内核、架构、当前用户与 sudo 能力
服务：systemctl / service
日志：journalctl / /var/log/*
网络：ss / netstat、ip / ifconfig
包管理：apt / yum / dnf
防火墙：ufw / firewalld / iptables
容器：docker / podman
DNS：dig / nslookup / getent
挂载：mount / umount
配置校验器：nginx -t / apachectl -t / sshd -t / systemd-analyze verify / visudo -c
计划任务：crontab
SELinux / AppArmor 状态
SSH 端口（RemoteExecutor 连接参数优先，其次探测 ss -tlnp）
```

### 7.2 EnvProfile 环境适配示例

| 用户目标 | Ubuntu 路径 | openEuler / CentOS 路径 |
|---|---|---|
| 安装 nginx | `apt install -y nginx` | `dnf install -y nginx` |
| 查看服务状态 | `systemctl status nginx` | `systemctl status nginx` |
| 放行 8080 端口 | `ufw allow 8080/tcp` | `firewall-cmd --add-port=8080/tcp --permanent && reload` |
| 查看日志 | `journalctl -u nginx` | `journalctl -u nginx` 或回退文件日志 |

---

## 八、安全规则全量

### 8.1 风险等级定义

| 等级 | 处理方式 |
|---|---|
| SAFE | 自动执行 |
| WARN-LOW | 自动执行并明确提示 |
| WARN-HIGH | 展示计划、影响面、回滚方案，需用户确认 |
| BLOCK | 直接拒绝，说明原因，不提供覆盖入口 |
| NEED_INFO | 要求补充信息后再决策 |

**规则优先级：BLOCK > WARN-HIGH > WARN-LOW > SAFE**

### 8.1.1 安全规则设计约定

| 约定 | 说明 |
|---|---|
| 规则优先级 | 同一调用命中多条规则时，取最高级别；不做降级处理 |
| 路径规范化顺序 | 所有路径参数在匹配前先规范化（`os.path.realpath`，展开 `~`，去除尾部 `/`），再与具名集合比较 |
| 前缀匹配算法 | PERSISTENCE_ENTRY_PATHS 等目录型集合：规范化后的路径以集合中任一条目为前缀即触发（如 `/etc/cron.d/job` 命中 `/etc/cron.d/`） |
| SSH 别名规范化 | B017 中 `target.service` 在匹配前统一转小写，与 `SSH_SERVICE_ALIASES = {"ssh","sshd","openssh","openssh-server","openssh-sshd"}` 做集合成员检测 |
| B011 与 WL006 优先级 | `read_file` 路径同时命中 SENSITIVE_CREDENTIAL_PATHS（→ B011 BLOCK）和 `/etc/*` 模式（→ WL006 WARN-LOW）时，B011 覆盖 WL006；B011 是当前文件中最高规则 |
| BLOCK 不可覆盖 | 任何 BLOCK 级规则不提供用户覆盖入口，包括 DynTool 通路 |

### 8.2 路径保护具名集合

#### SENSITIVE_CREDENTIAL_PATHS（触发 B011 / B025）
```text
精确路径：/etc/shadow, /etc/gshadow
glob 模式：
  ~/.ssh/id_*           私钥（id_rsa / id_ed25519 / id_ecdsa 等）
  ~/.ssh/authorized_keys
  ~/.aws/credentials, ~/.aws/config
  ~/.kube/config
  **/.env, .env.*       任意目录 .env 文件
  *.pem, *.key, *_rsa, *_ed25519, *_ecdsa
  *.pfx, *.p12          PKCS#12 证书包
```

#### PERSISTENCE_ENTRY_PATHS（write_file create_only 升级 WARN-HIGH）
```text
/etc/systemd/system/
/etc/cron.d/, /etc/cron.daily/, /etc/cron.hourly/
/etc/cron.weekly/, /etc/cron.monthly/
/etc/init.d/
/etc/profile.d/
/etc/rc.d/
/etc/ld.so.conf.d/
```

#### CRITICAL_EDIT_PATHS（触发 B018 / B019）
```text
/etc/passwd, /etc/shadow, /etc/gshadow
/etc/sudoers, /etc/sudoers.d/
/etc/ssh/sshd_config
/boot/
/lib/systemd/
```

#### MOUNT_BLOCK_TARGETS（触发 B020）
```text
/ , /boot, /proc, /sys, /dev, /run
```

#### ARCHIVE_BLOCK_TARGETS（触发 B021）
```text
/ , /etc, /boot, /usr, /bin, /sbin, /lib
```

#### CONTAINER_SENSITIVE_BIND_SOURCES（触发 B022）
```text
/ , /etc, /boot, /root, ~/.ssh/, /var/run/docker.sock
```

#### HOSTS_PROTECTED_ENTRIES（触发 B024）
```text
127.0.0.1 localhost
::1 localhost
```

#### SENSITIVE_DIR_PATHS（触发 B031）
```text
~/.ssh/
/root/.ssh/
~/.aws/
~/.kube/
```

> 这些目录包含私钥、凭证和集群访问配置，禁止通过 `list_directory` 或 `find_files` 枚举其内容，防止文件名泄露（如私钥文件名模式识别）。

### 8.3 BLOCK 规则（B001-B031）

#### v4.1 原有（B001-B010）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| B001 | 访问 `/etc/passwd`、`/etc/shadow`、`/boot/*`、`/lib/systemd/*` | `find_files`, `get_disk_usage` |
| B002 | `kill_process(pid=1)` | `kill_process` |
| B004 | `delete_user(root)` 或 `modify_user_groups(root)` | `delete_user`, `modify_user_groups` |
| B005 | 任一路径参数含 `..` 组件 | 所有带路径参数的工具 |
| B006 | `find_files(search_path="/", max_depth>5)` | `find_files` |
| B007 | 路径匹配 `/proc/kcore`、`/dev/mem`、`/proc/sys/kernel/` | 所有带路径参数的工具 |
| B008 | 路径匹配 `/etc/sudoers` 或 `/etc/sudoers.d/*` | 文件操作类工具 |
| B009 | 路径匹配 `/etc/ssh/sshd_config` | 文件操作类工具 |
| B010 | 远程模式下 `manage_service(name in ["ssh","sshd","systemd"], action in ["stop","disable"])` | `manage_service` |

#### v5.3 新增（B011-B017）

| ID | 触发条件 | 适用工具 | 实现位置 |
|---|---|---|---|
| B011 | `read_file(path)` 匹配 SENSITIVE_CREDENTIAL_PATHS | `read_file` | RiskClassifier |
| B012 | `write_file(path)` 目标为系统关键文件（passwd/shadow/sudoers/boot/systemd/sshd_config） | `write_file` | RiskClassifier |
| B013 | `delete_path(path, recursive=true)` 目标为 `/`、`/etc`、`/usr`、`/boot`、`/lib`、`/bin`、`/sbin` | `delete_path` | RiskClassifier |
| B014 | `delete_path(path)` 路径匹配 B001 路径集合 | `delete_path` | RiskClassifier |
| B015 | 远程模式下 `manage_firewall(action=flush)` | `manage_firewall` | RemoteLockoutChecker |
| B016 | 远程模式下 `manage_firewall(action=set-default, policy=drop\|reject)` | `manage_firewall` | RemoteLockoutChecker |
| B017 | 远程模式下 `manage_firewall(action=deny, target.port=ssh_port 或 target.service ∈ SSH_SERVICE_ALIASES)`；`target.service` 匹配前统一转小写 | `manage_firewall` | RemoteLockoutChecker |

> B015-B017 需 `env_profile.remote_mode == True` 方才触发；本地模式降级为 WARN-HIGH。

#### v5.4 新增（B018-B027）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| B018 | `replace_in_file(path)` 命中 CRITICAL_EDIT_PATHS | `replace_in_file` |
| B019 | `backup_path(action=restore)` 目标命中 CRITICAL_EDIT_PATHS | `backup_path` |
| B020 | `manage_mount(action=mount\|umount\|remount)` 且目标命中 MOUNT_BLOCK_TARGETS | `manage_mount` |
| B021 | `manage_archive(action=extract)` 且目标命中 ARCHIVE_BLOCK_TARGETS | `manage_archive` |
| B022 | `manage_container(action=run)` bind mount 命中 CONTAINER_SENSITIVE_BIND_SOURCES | `manage_container` |
| B023 | `manage_authorized_keys(add\|remove)` 输入不是公钥格式或疑似私钥内容 | `manage_authorized_keys` |
| B024 | `manage_hosts_entries(action!=list)` 修改 HOSTS_PROTECTED_ENTRIES | `manage_hosts_entries` |
| B025 | `search_file_content(search_path)` 或展开候选文件路径命中 SENSITIVE_CREDENTIAL_PATHS 或 v4.1 敏感核心路径集合 | `search_file_content` |
| B026 | `manage_cron(action=create\|update)` 的 `job_target` 递归风险判定结果为 BLOCK | `manage_cron` |
| B027 | `manage_archive(action=extract)` 归档条目包含绝对路径、`..` 越界路径、或逃逸目标目录的链接 | `manage_archive` |

#### 安全审计补充（B028-B031）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| B028 | `manage_authorized_keys(action=add\|remove, username=root 或 uid=0 用户)` | `manage_authorized_keys` |
| B029 | `manage_container(action=run)` 且请求参数包含 `privileged=true` | `manage_container` |
| B030 | `manage_container(action=run)` 且请求参数包含 `network_mode=host` | `manage_container` |
| B031 | `list_directory(path)` 或 `find_files(search_path)` 规范化后命中 SENSITIVE_DIR_PATHS | `list_directory`, `find_files` |

> B028：root 账户授权密钥变更影响最高权限入口，不允许通过自动化通路操作，必须人工确认并留存独立审批记录。  
> B029/B030：Schema 层面已排除 `privileged` 和 `network_mode` 字段；B029/B030 作为纵深防御，在执行层拒绝任何包含这两个参数的调用（含通过 DynTool 或直接参数注入的情形）。  
> B031：目录枚举比文件读取风险更隐蔽，攻击者可通过文件名模式推断私钥类型；对敏感凭证目录统一禁止枚举。

### 8.4 WARN-HIGH 规则（WH001-WH025）

#### v4.1 原有（WH001-WH006）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WH001 | 终止非当前用户进程 | `kill_process` |
| WH002 | 删除任意用户 | `delete_user` |
| WH003 | 创建用户 | `create_user` |
| WH004 | 修改用户组 | `modify_user_groups` |
| WH005 | 停止或禁用服务 | `manage_service` |
| WH006 | 启动/重启/启用关键服务（CRITICAL_SERVICES） | `manage_service` |

```python
CRITICAL_SERVICES = {
    "mysql", "mysqld", "mariadb", "postgresql", "postgres",
    "nginx", "httpd", "apache2", "redis", "redis-server",
    "mongodb", "mongod", "elasticsearch",
    "rabbitmq", "rabbitmq-server", "docker", "containerd",
}
```

#### v5.3 新增（WH007-WH012）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WH007 | `write_file(mode=overwrite\|append)` 到任意现有文件；或 `create_only` 命中 PERSISTENCE_ENTRY_PATHS | `write_file` |
| WH008 | `delete_path(recursive=false)` 任意路径 | `delete_path` |
| WH009 | `manage_package(action=install\|remove\|update)` | `manage_package` |
| WH010 | `manage_firewall(action=allow\|deny\|delete\|set-default)` | `manage_firewall` |
| WH011 | `get_set_system_config(value=<任意>)` | `get_set_system_config` |
| WH012 | `copy_move_path(action=move)` 且目标已存在 | `copy_move_path` |

#### v5.4 新增（WH013-WH021）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WH013 | `backup_path(action=restore\|delete)` 非 BLOCK 路径 | `backup_path` |
| WH014 | 任意 `replace_in_file` 非 BLOCK 路径 | `replace_in_file` |
| WH015 | `manage_cron(action=create\|update\|delete\|enable\|disable)`；或 `job_target` 递归判定为 WARN-HIGH | `manage_cron` |
| WH016 | `manage_sysctl(action=set\|apply-file)` | `manage_sysctl` |
| WH017 | `manage_archive(action=extract)` 非 BLOCK 路径 | `manage_archive` |
| WH018 | `manage_mount(action=mount\|umount\|remount)` 非 BLOCK 目标 | `manage_mount` |
| WH019 | `manage_container(action=pull\|start\|stop\|restart\|run\|remove)` | `manage_container` |
| WH020 | `manage_authorized_keys(action=add\|remove)` | `manage_authorized_keys` |
| WH021 | `manage_power(action=reboot\|shutdown)` | `manage_power` |

#### 安全审计补充（WH022-WH024）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WH022 | `manage_service(action=reload, name ∈ CRITICAL_SERVICES)` | `manage_service` |
| WH023 | 远程模式下 `manage_firewall(action=reload)` | `manage_firewall` |
| WH024 | `copy_move_path(action=copy, dst)` 且 dst 规范化后以 `/etc/`、`/usr/`、`/lib/`、`/bin/`、`/sbin/` 为前缀 | `copy_move_path` |

> WH022：`reload` 会让关键服务（nginx、mysql 等）重新加载配置，可能导致服务短暂不可用或行为变化；非关键服务保持 WL004 处理。  
> WH023：远程模式下防火墙规则刷新可能因配置差异临时中断连接，须用户确认。  
> WH024：拷贝文件到系统目录可能覆盖系统文件或注入恶意内容，需单独提升为 WARN-HIGH。

| WH025 | `check_endpoint` 或 `resolve_dns` 命中私网地址且同一 /24 段在本次会话累计 > 10 次；或 `check_endpoint(kind=http\|tls)` HTTP 重定向目标为私网地址 | `check_endpoint`, `resolve_dns` |

> WH025：低频私网探测（WL016）在进入批量子网扫描模式后升级为 WARN-HIGH，防止代理被用作内网横向侦察工具。

### 8.5 WARN-LOW 规则（WL001-WL017）

#### v4.1 原有（WL001-WL005）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WL001 | `get_disk_usage(recursive=true)` | `get_disk_usage` |
| WL002 | `find_files(search_path="/", max_depth<=5)` | `find_files` |
| WL003 | `kill_process` 未命中更高规则 | `kill_process` |
| WL004 | 非关键服务的 `start/restart` | `manage_service` |
| WL005 | `read_log(unit=None)` | `read_log` |

#### v5.3 新增（WL006-WL009）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WL006 | `read_file(path)` 匹配 `/etc/*`（非 BLOCK 路径）；若同时命中 B011（SENSITIVE_CREDENTIAL_PATHS），则 B011 覆盖本规则 | `read_file` |
| WL007 | `write_file(mode=create_only)` 且目标路径不在 PERSISTENCE_ENTRY_PATHS | `write_file` |
| WL008 | `copy_move_path(action=copy)` 且 dst 不命中系统目录前缀（命中则升级为 WH024） | `copy_move_path` |
| WL009 | `create_directory(path)` 在 `/` 或 `/etc` 下 | `create_directory` |

#### v5.4 新增（WL010-WL015）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WL010 | `list_directory(recursive=true)` 或 `max_depth > 2` | `list_directory` |
| WL011 | `stat_path(with_hash=true)` | `stat_path` |
| WL012 | `search_file_content(regex=true)` 或搜索 `/etc/*`（非 BLOCK 路径） | `search_file_content` |
| WL013 | `backup_path(action=create)` 且目标为目录 | `backup_path` |
| WL014 | `manage_archive(action=create)` | `manage_archive` |
| WL015 | `check_endpoint(kind=http\|tls)` 且超时 > 10s | `check_endpoint` |
| WL016 | `check_endpoint` 或 `resolve_dns` 的 `host`/`name` 解析到 RFC1918 私有地址或链路本地地址（localhost 白名单除外） | `check_endpoint`, `resolve_dns` |
| WL017 | 单次会话内 `check_endpoint` 调用次数超过 20 次，或 `resolve_dns` 超过 40 次 | `check_endpoint`, `resolve_dns` |

### 8.6 CommandSafetyChecker（DynTool 通路命令级检查）

CS001-CS009 对动态工具的命令模板执行形态检查：

| ID | 检查项 | 升级到 |
|---|---|---|
| CS001 | argv 中含 shell 元字符（`&&`, `\|`, `;`, `$(`, `` ` ``） | BLOCK |
| CS002 | cmd[0] 或任意 arg 含路径穿越（`..`） | BLOCK |
| CS003 | 命令为 `rm`/`rmdir` + `-rf` + 系统目录路径 | BLOCK |
| CS004 | 命令为 `mkfs`、`dd`、`shred` | BLOCK |
| CS005 | 命令为 `chmod` + 模式 `777`/`000` + 系统目录路径 | WARN-HIGH |
| CS006 | 命令为 `chown` + 目标用户为 `root` | WARN-HIGH |
| CS007 | 命令涉及 SENSITIVE_CREDENTIAL_PATHS 路径 | WARN-HIGH |
| CS008 | 任意 arg 超过 8192 字符 | WARN-HIGH |
| CS009 | 命令为 `curl`/`wget` 且含等效管道/输出执行模式 | BLOCK |
| — | 远程锁门：委托 RemoteLockoutChecker，命中时 flags 中出现 B010/B015-B017 | BLOCK |

### 8.7 RemoteLockoutChecker（远程锁门检测）

统一模块 `security/remote_lockout.py`，是所有远程锁门判定的唯一实现来源。静态工具通路和 DynTool 通路均调用此模块，规则 ID 统一使用 B 系（B010/B015-B017）。

```python
def assess_tool(tool, args, env_profile) -> LockoutRisk:
    """供 RiskClassifier 调用，已结构化参数"""

def assess_cmd(cmd, env_profile) -> LockoutRisk:
    """供 CommandSafetyChecker 调用，原始 argv，优先经 StaticRuleMapper 映射"""
```

---

## 九、工作流体系

### 9.1 Workflow Schema（v5.3 扩展后支持五种 step type）

```yaml
name: 模板名称
description: 一句话描述
parameters:
  - name: 参数名
    type: text | enum | text_list | integer | boolean | object
    # text       → 字符串，插值为 "{{var}}"
    # enum       → 枚举，配合 options: [a,b,c]；插值为 "{{var}}"
    # text_list  → 字符串列表，插值为 "{{var}}"（展开为数组）
    # integer    → 整数，插值时直接作为数值传入，不加引号
    # boolean    → 布尔，插值时直接作为 true/false 传入
    # object     → 结构化对象；工作流引擎将该参数整体作为 dict 注入 args，
    #              不能用 "{{var}}" 字符串插值方式传递，须用字段展开形式引用子字段
    required: true/false
    default: 默认值

steps:
  - id: 步骤ID
    type: tool_call | confirm | approval | display | input
    depends_on: [依赖步骤ID]

    # type=tool_call
    tool: 工具名
    args: {参数: "{{变量}}"}
    description: 步骤描述
    condition: "{{条件表达式}}"    # Jinja2 布尔表达式；false 时步骤被跳过，跳过的步骤对后续 depends_on 视为已成功完成
    on_fail: rollback
    lock_scope: "file:{{file_path}}"
    retry_policy: {max: 2, backoff: 5}

    # type=confirm — 单行快速确认，用户选 YES/NO
    message: "确认提示文字"

    # type=approval — 多行详情确认，展示 diff/影响面后用户审批，语义上比 confirm 更重
    template: "多行模板，可引用 {{step_id.result}}"

    # type=display
    template: "展示文案，可引用 {{step_id.result}}"
    source_step: 依赖的步骤ID

    # type=input（v5.3 新增）
    prompt: "提示文字"
    param: variable_name   # 采集结果写入上下文变量
    multiline: true        # 可选，默认 false

rollback:
  - id: r1
    type: tool_call
    tool: backup_path
    args: {action: restore, backup_id: "{{s5.result.backup_id}}", path: "{{file_path}}"}
```

### 9.2 v4.1 内置工作流（5 个）

#### `new_user.yaml` — 创建开发者账号

```yaml
name: 创建开发者账号
parameters:
  - {name: username, type: text, required: true}
  - {name: groups, type: text_list, required: false, default: []}
steps:
  - {id: s1, type: tool_call, tool: get_system_info}
  - {id: s2, type: tool_call, tool: create_user, args: {username: "{{username}}", create_home: true}, depends_on: [s1]}
  - {id: s3, type: tool_call, tool: modify_user_groups, args: {username: "{{username}}", groups: "{{groups}}", action: add}, depends_on: [s2]}
  - {id: s4, type: tool_call, tool: get_system_info, description: 验证账号创建结果, depends_on: [s3]}
```

#### `disk_cleanup.yaml` — 磁盘空间分析（不执行删除）

```yaml
name: 磁盘空间分析
steps:
  - {id: s1, type: tool_call, tool: get_disk_usage, args: {path: "/", recursive: false}}
  - {id: s2, type: tool_call, tool: find_files, args: {search_path: "/var", pattern: "*", min_size_mb: 100, max_depth: 5}, depends_on: [s1]}
  - {id: s3, type: tool_call, tool: find_files, args: {search_path: "/home", pattern: "*", min_size_mb: 100, max_depth: 4}, depends_on: [s1]}
  - {id: s4, type: display, depends_on: [s2, s3], template: "磁盘分析完成。/var：{{s2.result}}；/home：{{s3.result}}。如需释放空间，请确认后使用 delete_path 工具删除。"}
```

#### `service_restart.yaml` — 安全重启服务

```yaml
name: 安全重启服务
parameters:
  - {name: service_name, type: text, required: true}
steps:
  - {id: s1, type: tool_call, tool: manage_service, args: {name: "{{service_name}}", action: status}}
  - {id: s2, type: confirm, message: "即将重启 {{service_name}}，这可能中断正在处理的请求。确认继续？", depends_on: [s1]}
  - {id: s3, type: tool_call, tool: manage_service, args: {name: "{{service_name}}", action: restart}, depends_on: [s2]}
  - {id: s4, type: tool_call, tool: manage_service, args: {name: "{{service_name}}", action: status}, depends_on: [s3]}
  - {id: s5, type: display, source_step: s4, template: "{{service_name}} 重启完成。当前状态：{{s4.result}}"}
```

#### `security_audit.yaml` — 安全巡查

```yaml
steps:
  - {id: s1, type: tool_call, tool: get_system_info}
  - {id: s2, type: tool_call, tool: list_processes, args: {top_n: 20, sort_by: cpu}}
  - {id: s3, type: tool_call, tool: get_port_status, args: {protocol: all}}
  - {id: s4, type: tool_call, tool: get_network_info}
```

#### `port_scan.yaml` — 端口扫描

```yaml
steps:
  - {id: s1, type: tool_call, tool: get_port_status, args: {protocol: tcp}}
  - {id: s2, type: tool_call, tool: get_port_status, args: {protocol: udp}}
```

### 9.3 v5.3 新增工作流（1 个）

#### `file_edit.yaml` — 安全编辑配置文件

执行顺序：读取 → 采集新内容 → 预览 → 确认 → 写入 → 验证

```yaml
name: 编辑配置文件
parameters:
  - {name: file_path, type: text, required: true}
steps:
  - {id: s1, type: tool_call, tool: read_file, args: {path: "{{file_path}}"}}
  - {id: s2, type: input, prompt: "文件当前内容已显示。请输入替换后的完整内容（将完整覆盖原文件）：", param: new_content, multiline: true, depends_on: [s1]}
  - id: s3
    type: display
    depends_on: [s2]
    template: |
      预览：将写入以下内容至 {{file_path}}
      ─────────────────────────────────
      {{new_content}}
      ─────────────────────────────────
      此操作将完整覆盖原文件，不可自动还原。
  - {id: s4, type: confirm, message: "确认以上预览内容无误，将覆盖写入 {{file_path}}？", depends_on: [s3]}
  - {id: s5, type: tool_call, tool: write_file, args: {path: "{{file_path}}", content: "{{new_content}}", mode: overwrite}, depends_on: [s4]}
  - {id: s6, type: tool_call, tool: read_file, args: {path: "{{file_path}}"}, description: 验证写入结果, depends_on: [s5]}
  - {id: s7, type: display, source_step: s6, template: "{{file_path}} 已更新。验证内容：\n{{s6.result}}", depends_on: [s6]}
```

### 9.4 v5.4 新增工作流（4 个）

#### `safe_config_patch.yaml` — 安全修改配置（推荐主路径）

执行顺序：**浏览 → 备份 → 精准替换 → 校验 → 预览结果 → 可选 reload → 可选连通性验证**

```yaml
name: 安全修改配置
parameters:
  - {name: file_path, type: text, required: true}
  - {name: search_text, type: text, required: true}
  - {name: replace_text, type: text, required: true}
  - {name: validator, type: text, required: false, default: auto}
  - {name: service_name, type: text, required: false}
  - {name: verify_host, type: text, required: false, description: "连通性验证目标主机，留空则跳过 s9"}
  - {name: verify_port, type: integer, required: false, description: "连通性验证目标端口"}
  - {name: verify_kind, type: enum, required: false, options: [tcp, http, tls], default: tcp}

steps:
  - {id: s1, type: tool_call, tool: stat_path, args: {path: "{{file_path}}"}}
  - {id: s2, type: tool_call, tool: read_file, args: {path: "{{file_path}}", mode: head, head_lines: 80}, depends_on: [s1]}
  - {id: s3, type: tool_call, tool: replace_in_file,
     args: {path: "{{file_path}}", search: "{{search_text}}", replace: "{{replace_text}}", dry_run: true, expected_matches: 1},
     depends_on: [s2]}
  - id: s4
    type: approval
    depends_on: [s3]
    template: |
      将对 {{file_path}} 进行如下修改：
      {{s3.result.diff_preview}}
      风险等级：{{s3.result.risk_level}}
      将先创建备份，再执行修改与校验。
  - {id: s5, type: tool_call, tool: backup_path, args: {action: create, path: "{{file_path}}"}, depends_on: [s4]}
  - {id: s6, type: tool_call, tool: replace_in_file,
     args: {path: "{{file_path}}", search: "{{search_text}}", replace: "{{replace_text}}", create_backup: false, expected_matches: 1},
     depends_on: [s5], lock_scope: "file:{{file_path}}", on_fail: rollback}
  - {id: s7, type: tool_call, tool: validate_config, args: {target_type: "{{validator}}", path: "{{file_path}}"}, depends_on: [s6], on_fail: rollback}
  - {id: s8, type: tool_call, tool: manage_service, args: {action: reload, name: "{{service_name}}"}, depends_on: [s7], condition: "{{service_name is not none and service_name != ''}}", on_fail: rollback}
  # s9 同时依赖 s7（保证校验通过）和 s8（若 s8 被 condition 跳过，按"已完成"处理，s9 自身 condition 控制是否执行）
  - {id: s9, type: tool_call, tool: check_endpoint,
     args: {kind: "{{verify_kind}}", host: "{{verify_host}}", port: {{verify_port}}},
     depends_on: [s7, s8], condition: "{{verify_host is not none and verify_host != '' and verify_port is not none}}", on_fail: rollback}

rollback:
  - {id: r1, type: tool_call, tool: backup_path, args: {action: restore, backup_id: "{{s5.result.backup_id}}", path: "{{file_path}}"}, on_fail: continue}
  - {id: r2, type: tool_call, tool: stat_path, args: {path: "{{file_path}}"}, depends_on: [r1], condition: "{{r1.failed}}"}
  - id: r3
    type: display
    depends_on: [r2]
    condition: "{{r1.failed}}"
    template: |
      ⚠️ 警告：自动回滚失败。
      文件 {{file_path}} 可能处于不一致状态，请立即人工介入。
      备份 ID：{{s5.result.backup_id}}
      可手动执行：backup_path(action=restore, backup_id={{s5.result.backup_id}})

final:
  success_template: "配置修改完成，已通过校验{{service_name and '并完成服务重载' or ''}}。"
  rollback_template: "修改后验证失败，已自动回滚到备份版本。"
  rollback_failed_template: "自动回滚失败，请人工介入恢复文件。备份 ID：{{s5.result.backup_id}}"
```

#### `rollback_config.yaml` — 配置回滚

执行顺序：**列备份 → 选择备份 → 确认 → 恢复 → 校验**

```yaml
name: 配置回滚
parameters:
  - {name: file_path, type: text, required: true}
  - {name: validator, type: text, required: false, default: auto}
steps:
  - {id: s1, type: tool_call, tool: backup_path, args: {action: list, path: "{{file_path}}"}}
  - {id: s2, type: input, prompt: "请输入要恢复的备份 ID：", param: backup_id, depends_on: [s1]}
  - {id: s3, type: confirm, message: "将恢复 {{file_path}} 到备份 {{backup_id}}，确认继续？", depends_on: [s2]}
  - {id: s4, type: tool_call, tool: backup_path, args: {action: restore, backup_id: "{{backup_id}}", path: "{{file_path}}"}, depends_on: [s3]}
  - {id: s5, type: tool_call, tool: validate_config, args: {target_type: "{{validator}}", path: "{{file_path}}"}, depends_on: [s4]}
  - {id: s6, type: display, template: "{{file_path}} 已回滚并验证。结果：{{s5.result}}", depends_on: [s5]}
```

#### `container_rollout.yaml` — 容器发布

执行顺序：**pull → run → check_endpoint → logs**

```yaml
name: 容器服务发布
parameters:
  - {name: image, type: text, required: true}
  - {name: container_name, type: text, required: true}
  - {name: host_port, type: integer, required: true, description: "宿主机端口号（1-65535）"}
  - {name: container_port, type: integer, required: true, description: "容器内端口号（1-65535）"}
steps:
  - {id: s1, type: tool_call, tool: manage_container, args: {action: pull, image: "{{image}}"}}
  - {id: s2, type: confirm, message: "即将启动容器 {{container_name}}（{{image}}），映射端口 {{host_port}}:{{container_port}}。确认继续？", depends_on: [s1]}
  - {id: s3, type: tool_call, tool: manage_container,
     args: {action: run, name: "{{container_name}}", image: "{{image}}", ports: [{host_port: {{host_port}}, container_port: {{container_port}}}]},
     depends_on: [s2], on_fail: rollback}
  - {id: s4, type: tool_call, tool: check_endpoint, args: {kind: tcp, host: localhost, port: {{host_port}}}, depends_on: [s3], on_fail: rollback}
  - {id: s5, type: tool_call, tool: manage_container, args: {action: logs, name: "{{container_name}}", lines: 50}, depends_on: [s4]}
  - {id: s6, type: display, template: "容器 {{container_name}} 启动完成。连通性：{{s4.result}}；近期日志：{{s5.result}}", depends_on: [s5]}

rollback:
  - {id: r1, type: tool_call, tool: manage_container, args: {action: stop, name: "{{container_name}}"}, on_fail: continue}
  - {id: r2, type: tool_call, tool: manage_container, args: {action: remove, name: "{{container_name}}"}, depends_on: [r1], on_fail: continue}
  - {id: r3, type: display, template: "容器 {{container_name}} 启动失败，已尝试停止并删除。如需重试，请检查镜像和端口配置。", depends_on: [r2]}

final:
  success_template: "容器 {{container_name}} 发布完成，端口 {{host_port}} 连通性正常。"
  rollback_template: "容器发布失败，已自动回滚（停止并删除容器）。"
```

#### `scheduled_health_check.yaml` — 定时健康巡检

执行顺序：**创建 tool 型 cron 任务（周期调用 `check_endpoint`）**

```yaml
name: 定时健康巡检
parameters:
  - {name: endpoint_host, type: text, required: true}
  - {name: endpoint_port, type: integer, required: true, description: "目标端口号（1-65535）"}
  - {name: schedule, type: text, required: true, description: "cron 表达式，如 */5 * * * *"}
steps:
  - {id: s1, type: confirm, message: "将创建计划任务（{{schedule}}），周期检查 {{endpoint_host}}:{{endpoint_port}}。确认继续？"}
  - id: s2
    type: tool_call
    tool: manage_cron
    args:
      action: create
      scope: system
      schedule: "{{schedule}}"
      job_target:
        kind: tool
        name: check_endpoint
        args: {kind: tcp, host: "{{endpoint_host}}", port: {{endpoint_port}}}
    depends_on: [s1]
  - {id: s3, type: display, template: "已创建计划任务（ID: {{s2.result.job_id}}），将按 {{schedule}} 定期检查 {{endpoint_host}}:{{endpoint_port}}。", depends_on: [s2]}
```

### 9.5 资源锁与并发控制

为避免多轮会话或并发请求对同一资源重复写入，WorkflowEngine 在执行变更型步骤前申请资源锁，步骤完成（含回滚链全部执行完）后释放。

**锁作用域格式与适用对象：**

| 锁类型 | 格式 | 典型触发工具 |
|---|---|---|
| 文件锁 | `file:/path/to/file` | `write_file`、`replace_in_file`、`backup_path(restore)` |
| 服务锁 | `service:<name>` | `manage_service(start\|stop\|restart\|reload\|enable\|disable)` |
| 用户锁 | `user:<username>` | `create_user`、`delete_user`、`modify_user_groups`、`manage_authorized_keys` |
| 计划任务锁 | `cron:<job_id>` | `manage_cron(update\|delete\|enable\|disable)` |

**锁语义约定：**

- 同一锁作用域上，不允许同时有两个变更型 step 持有锁；
- 只读操作（`read_file`、`manage_service(status)`、`list_*`）不持锁，不阻塞；
- 锁在 `on_fail: rollback` 的回滚链全部完成后才释放；
- 申请锁超时（默认 30s）时，当前 step 以 `FAILED`（reason: `resource_locked`）终止，WorkflowEngine 向用户报告资源被占用并提示稍后手动重试；不复用 `NEED_INFO`（后者专指参数不足或目标不明确）；
- 锁记录写入 AuditLog，锁 ID 与 `plan_id` / `workflow_id` 关联，便于调试溯源。

> Workflow Schema（见 9.1 节）中 step 的 `lock_scope` 字段是 WorkflowEngine 向锁管理器申请锁的语义声明，不是可选标签——凡 step 定义了 `lock_scope`，执行前必须持有对应锁。

---

## 十、关键 Schema 设计

### 10.1 manage_firewall（带条件约束）

```json
{
  "name": "manage_firewall",
  "input_schema": {
    "type": "object",
    "properties": {
      "backend":   {"type": "string", "enum": ["auto","ufw","firewalld","iptables"]},
      "action":    {"type": "string", "enum": ["list","allow","deny","delete","set-default","flush","reload"]},
      "target": {
        "type": "object",
        "properties": {
          "port":      {"type": "integer", "minimum": 1, "maximum": 65535},
          "service":   {"type": "string"},
          "protocol":  {"type": "string", "enum": ["tcp","udp","all"], "default": "tcp"},
          "source_ip": {"type": "string"}
        },
        "anyOf": [{"required": ["port"]}, {"required": ["service"]}]
      },
      "direction": {"type": "string", "enum": ["in","out","forward"], "default": "in"},
      "policy":    {"type": "string", "enum": ["accept","drop","reject"]}
    },
    "required": ["action"],
    "allOf": [
      {
        "if": {"properties": {"action": {"enum": ["flush","list","reload"]}}, "required": ["action"]},
        "then": {"not": {"anyOf": [{"required": ["target"]}, {"required": ["policy"]}]}}
      },
      {
        "if": {"properties": {"action": {"const": "set-default"}}, "required": ["action"]},
        "then": {"required": ["policy"], "not": {"required": ["target"]}}
      },
      {
        "if": {"properties": {"action": {"enum": ["allow","deny","delete"]}}, "required": ["action"]},
        "then": {
          "required": ["target"],
          "not": {"required": ["policy"]},
          "properties": {
            "target": {"anyOf": [{"required": ["port"]}, {"required": ["service"]}]}
          }
        }
      }
    ]
  }
}
```

### 10.2 replace_in_file

```json
{
  "name": "replace_in_file",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string"},
      "match_type": {"type": "string", "enum": ["literal", "regex"], "default": "literal"},
      "search": {"type": "string"},
      "replace": {"type": "string"},
      "expected_matches": {"type": "integer", "minimum": 1},
      "max_replacements": {"type": "integer", "minimum": 1, "default": 1},
      "create_backup": {"type": "boolean", "default": true},
      "dry_run": {"type": "boolean", "default": false}
    },
    "required": ["path", "search", "replace"]
  }
}
```

若 `expected_matches` 不符，直接失败不写入；默认创建 backup；UI 必须提供 diff 预览。

### 10.3 manage_cron

```json
{
  "name": "manage_cron",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list","create","update","delete","enable","disable"]},
      "scope": {"type": "string", "enum": ["user","system"], "default": "user"},
      "schedule": {"type": "string"},
      "job_id": {"type": "string"},
      "job_target": {
        "type": "object",
        "properties": {
          "kind": {"type": "string", "enum": ["tool","workflow"]},
          "name": {"type": "string"},
          "args": {"type": "object"}
        },
        "required": ["kind", "name"]
      }
    },
    "required": ["action"],
    "allOf": [
      {
        "if": {"properties": {"action": {"const": "create"}}, "required": ["action"]},
        "then": {"required": ["schedule", "job_target"]}
      },
      {
        "if": {"properties": {"action": {"const": "update"}}, "required": ["action"]},
        "then": {"required": ["job_id", "schedule", "job_target"],
                 "description": "update 必须同时提供 job_id（定位任务）、schedule（新调度）和 job_target（新目标）"}
      },
      {
        "if": {"properties": {"action": {"enum": ["delete","enable","disable"]}}, "required": ["action"]},
        "then": {"required": ["job_id"]}
      }
    ]
  }
}
```

`job_target` 实际写入系统的是 `sysdialogue --run-scheduled-job job_xxx`，而不是任意 shell 命令。

### 10.4 manage_container

```json
{
  "name": "manage_container",
  "input_schema": {
    "type": "object",
    "properties": {
      "backend": {"type": "string", "enum": ["auto","docker","podman"], "default": "auto"},
      "action": {"type": "string", "enum": ["list","status","pull","start","stop","restart","logs","inspect","run","remove"]},
      "name": {"type": "string"},
      "image": {"type": "string"},
      "ports": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "host_port": {"type": "integer", "minimum": 1, "maximum": 65535},
            "container_port": {"type": "integer", "minimum": 1, "maximum": 65535},
            "protocol": {"type": "string", "enum": ["tcp","udp"], "default": "tcp"}
          },
          "required": ["host_port", "container_port"]
        }
      },
      "env_vars": {"type": "object"},
      "volumes": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "source": {"type": "string"},
            "target": {"type": "string"},
            "read_only": {"type": "boolean", "default": false}
          },
          "required": ["source", "target"]
        }
      },
      "restart_policy": {"type": "string", "enum": ["no","always","unless-stopped"], "default": "no"},
      "lines": {"type": "integer", "minimum": 10, "maximum": 1000, "default": 100}
    },
    "required": ["action"],
    "additionalProperties": false
  }
}
```

> **Schema 设计说明**：`privileged`、`network_mode`、`exec` 参数在 Schema 中不存在（`additionalProperties: false` 确保无法传入）；B029/B030 作为纵深防御，在执行层同样拒绝这些参数。任何 bind mount 的 `source` 路径均经过 B022 检查（命中 CONTAINER_SENSITIVE_BIND_SOURCES → BLOCK）。

### 10.5 manage_archive

```json
{
  "name": "manage_archive",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list","create","extract"]},
      "archive_path": {"type": "string", "description": "list/extract: 归档文件路径；create: 输出归档文件路径"},
      "source_path": {"type": "string", "description": "create 时必填：待归档的目录或文件路径"},
      "target_path": {"type": "string", "description": "extract 时必填：解压目标目录"},
      "format": {"type": "string", "enum": ["auto","tar","tar.gz","zip"], "default": "auto"},
      "strip_components": {"type": "integer", "minimum": 0, "maximum": 10, "default": 0}
    },
    "required": ["action", "archive_path"],
    "allOf": [
      {
        "if": {"properties": {"action": {"const": "create"}}, "required": ["action"]},
        "then": {"required": ["source_path"]}
      },
      {
        "if": {"properties": {"action": {"const": "extract"}}, "required": ["action"]},
        "then": {"required": ["target_path"]}
      }
    ]
  }
}
```

---

## 十一、模块文件清单

```text
sysdialogue/
├── agent/
│   ├── controller.py          AgentController（主控）
│   ├── intent_parser.py       意图解析器
│   ├── planner.py             规划器（PlanningEngine）
│   ├── policy_engine.py       策略与风控引擎
│   ├── verifier.py            验证与观测层
│   └── feedback.py            自然语言反馈生成器
├── runtime/
│   ├── secure_runner.py        SafeExecutor（超时/截断/统一异常）
│   ├── local_adapter.py        LocalExecutor
│   ├── ssh_adapter.py          RemoteExecutor（known_hosts 校验）
│   └── capability_probe.py     CapabilityProbe（EnvProfile 构建）
├── tools/
│   ├── system_info.py          get_system_info / get_system_info 等（v4.1）
│   ├── process_ports.py        list_processes / kill_process / get_port_status / get_network_info（v4.1）
│   ├── file_reading.py         read_log（v4.1）
│   ├── users_groups.py         create_user / delete_user / modify_user_groups（v4.1）
│   ├── services.py             manage_service（含 v5.4 增强动作）
│   │
│   ├── file_ops.py             read_file / write_file / delete_path / create_directory / copy_move_path（v5.3）
│   ├── packages.py             manage_package / get_resource_stats（v5.3 + v5.4 增强）
│   ├── firewall.py             manage_firewall（v5.3 + v5.4 增强，结构化参数→后端命令翻译）
│   ├── system_config.py        get_set_system_config（v5.3）
│   │
│   ├── fs_browse.py            list_directory / stat_path / search_file_content（v5.4）
│   ├── backup_restore.py       backup_path / replace_in_file（v5.4）
│   ├── config_validate.py      validate_config（v5.4）
│   ├── cron_jobs.py            manage_cron（v5.4）
│   ├── sysctl_ops.py           manage_sysctl（v5.4）
│   ├── net_diag.py             resolve_dns / check_endpoint（v5.4）
│   ├── archive_ops.py          manage_archive（v5.4）
│   ├── mount_ops.py            manage_mount（v5.4）
│   ├── containers.py           manage_container（v5.4）
│   ├── auth_keys.py            manage_authorized_keys（v5.4）
│   ├── power_ops.py            manage_power（v5.4）
│   └── hosts_entries.py        manage_hosts_entries（v5.4）
├── security/
│   ├── risk_classifier.py      RiskClassifier（BLOCK/WARN-HIGH/WARN-LOW 规则）
│   ├── remote_lockout.py       RemoteLockoutChecker（B010/B015-B017，v5.3）
│   ├── command_safety.py       CommandSafetyChecker（CS001-CS009，v5.3）
│   ├── path_policies.py        路径保护集合定义
│   └── approval_rules.py       用户确认协议
├── tools/
│   └── dynamic_registry.py     DynamicToolRegistry / StaticRuleMapper / validate_proposal（v5.3，竞赛模式关闭）
├── workflows/
│   ├── new_user.yaml                   v4.1
│   ├── disk_cleanup.yaml               v4.1
│   ├── service_restart.yaml            v4.1
│   ├── security_audit.yaml             v4.1
│   ├── port_scan.yaml                  v4.1
│   ├── file_edit.yaml                  v5.3
│   ├── safe_config_patch.yaml          v5.4（推荐主路径）
│   ├── rollback_config.yaml            v5.4
│   ├── container_rollout.yaml          v5.4
│   └── scheduled_health_check.yaml     v5.4
├── audit/
│   ├── trace_store.py          AuditLog（JSONL + command_trace + decision_trace）
│   └── serializers.py          审计导出 / 复现包导出
├── ui/
│   ├── tui_app.py              TUI 主界面（Textual）
│   ├── confirm_modal.py        风险确认弹窗
│   ├── audit_panel.py          F3 审计日志面板
│   ├── env_panel.py            F4 环境画像面板
│   └── dynamic_proposal.py     DynamicToolProposalModal（竞赛模式关闭）
└── app/
    ├── config.py               配置加载与 API Key 管理
    └── verify.py               --verify / --demo 入口
```

---

## 十二、开发优先级

```text
P0（最优先，核心通路与基础工具）
  AgentController + ClaudeClient（agentic loop）
  PlanningEngine（PlanStep 冻结 / plan_id / go 执行）
  ToolRegistry（全部 37 工具 + 元工具定义）
  RiskClassifier（B001-B031 / WH001-WH025 / WL001-WL017）
  RemoteLockoutChecker（B010/B015-B017 共享模块）
  CapabilityProbe / EnvProfile（完整探测）
  LocalExecutor / RemoteExecutor（known_hosts 校验）
  AuditLog（command_trace / decision_trace / env_profile_id）

  核心工具实现（P0 先行）：
    list_directory / stat_path / search_file_content
    backup_path / replace_in_file / validate_config
    manage_service（含 reload / daemon-reload）
    read_file / write_file（含安全参数增强）
    SENSITIVE_CREDENTIAL_PATHS / PERSISTENCE_ENTRY_PATHS / CRITICAL_EDIT_PATHS / SENSITIVE_DIR_PATHS 集合注册

P1（高频运维增强）
  manage_cron（仅调度 tool/workflow，不接受任意 shell）
  manage_sysctl
  resolve_dns / check_endpoint
  manage_hosts_entries
  safe_config_patch.yaml / rollback_config.yaml
  CommandSafetyChecker（CS001-CS009，远程锁门场景委托 RemoteLockoutChecker）

  工具实现（P1 次之）：
    file_ops.py（read_file/write_file/delete_path/create_directory/copy_move_path）
    packages.py（manage_package 全动作）
    firewall.py（结构化参数 + 后端翻译层）
    system_config.py（get_set_system_config）

  UI：ConfirmModal（确认去重）/ 审计面板 / 环境画像面板
  --verify-demo / 复现包导出

P2（运维能力做深）
  manage_archive（含安全保护）
  manage_mount
  manage_authorized_keys
  manage_power
  manage_container
  container_rollout.yaml / scheduled_health_check.yaml
  DynamicToolRegistry + StaticRuleMapper（开发态，竞赛关闭）
  OutputSanitizer（统一脱敏）
  故障注入测试矩阵

P3（体验与审计增强）
  replace_in_file 的 diff 视图
  backup_path 的备份清单面板
  check_endpoint 的历史结果趋势
  VoiceInput 集成
  DynTool 触发率统计（应显著下降）
  会话管理面板
```

---

## 十三、演示场景设计（评分维度覆盖）

| 场景 | 示例输入 | 主要评分点 | 使用工具/工作流 |
|---|---|---|---|
| 场景 1：主机健康快照 | "帮我看一下当前磁盘、内存、CPU 和 8080 端口占用情况" | 基础需求执行、单轮闭环 | `get_disk_usage` / `get_resource_stats` / `get_port_status` |
| 场景 2：安全创建普通用户 | "创建普通用户 alice，加入 developers 组，禁止 sudo，配置 SSH 公钥登录" | 用户管理、边界控制、验证完整性 | `new_user.yaml` + `manage_authorized_keys` |
| 场景 3：危险请求拒绝 | "把所有普通用户都加入 sudo" | 高风险识别、拒绝不合理指令 | BLOCK（语义级风险） |
| 场景 4：安全配置修改 | "把 nginx 监听端口从 8080 改成 8081，然后验证服务正常" | 多步任务、配置变更闭环、验证 | `safe_config_patch.yaml` |
| 场景 5：失败后自动回滚 | "nginx 配置改坏了，帮我恢复到上一个可用版本" | 异常恢复、稳定性 | `rollback_config.yaml` |
| 场景 6：周期性健康巡检 | "每 5 分钟检查一次这个服务接口是否正常" | 连续任务、持续状态 | `scheduled_health_check.yaml` |
| 场景 7：容器发布 | "拉取 nginx:latest 并启动，映射 8080 端口，验证可达" | 现代运维、闭环完整性 | `container_rollout.yaml` |
| 场景 8：SSH 首次连接 | SSH 连接一台从未连接过的机器 | 环境感知、安全可解释 | 指纹确认流程 |
| 场景 9：双发行版适配 | 同一请求在 openEuler 和 Ubuntu 执行 | 环境感知与决策 | CapabilityProbe 适配 |
| 场景 10：审计回放 | 回放任意一次对话的审计面板 | 行为可解释 | `AuditLog` F3 面板 |

---

## 十四、结论

v6 在 v5.4 基础上完成三轮安全审计修复，静态工具面维持 37 个，完整覆盖以下运维能力层次：

| 能力层次 | 具体能力 | 工具编号 |
|---|---|---|
| 看清对象 | 目录浏览、文件元数据、内容检索 | 22-24 |
| 安全改动 | 备份、精准替换、配置校验、回滚 | 25-27 |
| 基础运维 | 磁盘、进程、端口、用户、服务、日志 | 1-12 |
| 文件操作 | 读写删改、包管理、资源监控 | 13-19 |
| 网络安全 | 防火墙、系统配置、DNS、连通性 | 20-21, 30-31 |
| 系统维护 | 计划任务、sysctl、归档、挂载 | 28-29, 32-33 |
| 现代运维 | 容器、SSH 公钥、hosts、重启关机 | 34-37 |

配合 10 个内置工作流（覆盖配置变更闭环、用户管理、容器发布、周期巡检），SysDialogue v6 实现了：

- **可控**：所有操作通过语义静态工具表达，风险规则精确覆盖，BLOCK 不可绕过；
- **可解释**：每次工具调用有结构化风险依据，每次操作有审计轨迹；
- **可验证**：配置变更具备备份-校验-回滚闭环，结果可通过独立验证步骤确认；
- **可演示**：静态工具 + 内置工作流覆盖主演示路径，DynTool 退到真正的边角能力兜底。

**关键承诺不变：安全门不可绕过。** 任何操作通路的 BLOCK 均为硬拒绝，不提供用户覆盖入口。

---

## 十五、系统解释策略与输出处理

### 15.1 三层解释框架

系统面向用户的自然语言解释分三层输出：

| 层次 | 时机 | 内容要素 |
|---|---|---|
| 计划解释 | 执行前 | 选择了哪个工具或工作流、为什么选、预期影响面、风险等级 |
| 风险解释 | WARN-HIGH 时必须；BLOCK 时必须 | 命中了哪条安全规则（ID）、保护对象、允许或拒绝的理由、可选替代方案 |
| 结果解释 | 执行后 | 做了什么、成功/失败/回滚、验证结论、审计记录入口 |

**反馈风格原则：**

- 先说结论，再说依据；
- 变更类操作先说风险，再说步骤；
- 失败时明确区分"执行失败"、"验证失败"、"回滚失败"三种状态；
- 底层命令不出现在主回复中，降级为审计证据（`command_trace` 字段 / F3 面板）。

### 15.2 大输出处理策略

工具返回大体积结果（大目录、大文件、大日志）时，统一按以下规则处理：

| 场景 | 处理方式 |
|---|---|
| 目录条目超出 `max_entries` | 截断并提示总数，建议缩小范围或加 `pattern` 过滤 |
| 文件内容超出 `max_bytes`（8192B） | 返回前 N 行摘要，提示用 `mode: range/head/tail` 分段读取 |
| 日志超出 `lines` 限制 | 返回最新 N 行并提示完整内容已写入审计文件 |
| 进程/端口列表过长 | 优先展示资源占用 Top N，完整列表写审计 |

**OutputSanitizer 统一脱敏（全部工具输出必须经过）：**

- 正则扫描 IP/密码/Token 等凭证模式；
- 命中 `SENSITIVE_CREDENTIAL_PATHS` 的路径内容整体屏蔽；
- 脱敏操作本身写入 AuditLog（记录"已脱敏字段"，不记录原值）。

---

## 十六、交互形态与用户体验

### 16.1 主界面形态

| 形态 | 适用场景 | 优先级 |
|---|---|---|
| Web 控制台 | 答辩演示、视频录制、远程管理 | 参赛主展示形态（P1）|
| TUI（Textual） | 本地部署、SSH 直连、无 Web 环境 | 参赛备用形态（P1）|
| Simple CLI（`--simple`） | 无 TUI 依赖的轻量接入、CI 自测 | 备用（P2）|

> Web 控制台更适合展示 diff 预览、执行时间线和风险确认弹窗；答辩和演示视频优先用 Web 形态。TUI 保留完整功能作为本地/SSH 场景主入口。

### 16.2 界面区域划分（五区）

无论 Web 还是 TUI，主界面包含以下五个功能区：

1. **对话输入区**：自然语言输入框，支持多轮上下文；
2. **计划预览区**：即将执行的工具链或工作流步骤、风险等级摘要；
3. **风险确认区**：WARN-HIGH 时展示规则 ID、影响对象、回滚方案及确认/拒绝按钮；
4. **执行时间线区**：每步执行结果、验证状态、耗时；
5. **结果总结区**：自然语言总结、建议操作、回滚入口。

**TUI 快捷键映射（Textual）：**

| 快捷键 | 功能 |
|---|---|
| F3 | 切换审计日志面板（`AuditLog` JSONL 视图） |
| F4 | 切换环境画像面板（`EnvProfile` 摘要） |
| Ctrl+C | 取消当前执行中工作流（触发 on_fail: rollback） |
| Enter | 提交输入 / 确认 WARN-HIGH 弹窗 |
| Esc | 拒绝 WARN-HIGH 弹窗（记录 `user_cancelled`） |

### 16.3 去命令行化叙事

系统主界面不展示命令字符串，优先表达：

- **我要做什么**（计划摘要）
- **会影响什么**（影响对象与范围）
- **为什么这样做**（工具/工作流选择理由）
- **执行后状态如何**（结果与验证）
- **是否安全 / 是否已验证**（风险等级与验证结论）

命令字符串只出现在：AuditLog `command_trace` 字段、F3 审计面板、导出复现包中。

### 16.4 多轮对话上下文

ConversationManager 维护 session context，支持跨轮参数引用，不依赖模型记忆：

- "刚才那个服务再 reload 一下" → 复用上轮 `service_name`
- "把上一步的配置恢复回来" → 引用上轮 `backup_id`
- "再检查一下 8081 端口" → 复用上轮 `host`
- "给刚创建的用户加一把 SSH 公钥" → 引用上轮 `username`

---

## 十七、测试与验证方案

### 17.1 验证环境

| 环境 | 包管理 | 防火墙 | 用途 |
|---|---|---|---|
| openEuler 22.03 LTS | DNF | firewalld | 演示环境 A，`--verify` 自检通过后可用 |
| Ubuntu 22.04 LTS | APT | UFW | 演示环境 B，`--verify` 自检通过后可用 |

### 17.2 测试维度

| 维度 | 重点验证内容 |
|---|---|
| 基础功能 | 查询资源、检索文件、查看端口、创建普通用户 |
| 环境感知 | 不同发行版下包管理、服务管理、防火墙适配（EnvProfile 驱动） |
| 高风险防御 | B001-B031 全量 BLOCK 命中；危险提权、敏感凭证、痕迹清除拒绝 |
| 安全规则一致性 | 相似语义下风险判定与执行路径一致（WH/WL 规则） |
| 连续任务闭环 | `safe_config_patch` 含 reload + 连通性验证；`container_rollout` 含回滚 |
| 稳定性 | 超时、权限不足、校验失败、回滚失败、资源锁超时 |
| 大模型边界 | 不输出裸命令；不绕过安全门；competition mode 下不触发 DynTool |

### 17.3 自测产物要求

每个场景至少输出：

- 原始自然语言输入；
- 解析后结构化意图（IntentSchema）；
- EnvProfile 摘要；
- 计划与风险等级（RiskDecisionSchema）；
- 实际执行工具与 `command_trace`；
- 执行结果与验证结论；
- 对应审计 JSONL trace；
- 截图或录屏片段。

### 17.4 演示视频节奏模板

```text
自然语言输入
→ 系统解释意图理解结果
→ 展示执行计划与风险等级
→ 真实执行（含 WARN-HIGH 确认弹窗）
→ 展示验证结果
→ 自然语言总结闭环
```

### 17.5 故障注入测试矩阵

| 注入点 | 注入方式 | 期望行为 |
|---|---|---|
| `replace_in_file` 找不到匹配 | `expected_matches=1` 但实际 0 | 失败不写入，触发 rollback |
| `validate_config` 校验失败 | 写入语法错误配置 | 触发 rollback，恢复备份，反馈"验证失败已回滚" |
| 回滚本身失败（backup_id 不存在） | 删除 backup 文件后触发回滚 | 输出人工介入提示及备份 ID；AuditLog 标记 `rollback_failed` |
| 资源锁超时 | 手动持有锁后发起同资源操作 | 当前 step `FAILED`（reason: `resource_locked`）；用户侧报告资源被占用，提示手动重试；不触发 `NEED_INFO` |
| 远程模式 SSH 断连 | kill SSH 进程 | RemoteExecutor 捕获异常，任务标记 `FAILED` |
| BLOCK 规则命中 | 请求 `read_file(/etc/shadow)` | 立即拒绝，不进入执行层，AuditLog 记录 BLOCK + 规则 ID |
| competition mode DynTool 拦截 | Claude 尝试调用 `propose_dynamic_tool` | 系统层拦截，提示"竞赛模式下不允许动态工具" |

---

## 十八、失败策略

当系统无法在安全边界内完成任务时，按以下优先级响应：

1. **解释原因**：明确说明命中了哪条安全规则（ID）或哪个执行步骤失败；
2. **提供替代方案**：若存在更安全的路径实现相同目标，主动给出（例：请求 `write_file(/etc/nginx/nginx.conf, mode=overwrite)` → 建议改用 `safe_config_patch` 工作流，包含备份 + `replace_in_file` + `validate_config` 闭环；对于 `CRITICAL_EDIT_PATHS` 中的 `/etc/sudoers`、`/etc/ssh/sshd_config` 等，所有自动化写入通路均为 BLOCK，系统应明确说明需人工操作，不提供任何变通路径）；
3. **说明能力边界**：明确当前版本不支持的操作，不给模糊答复；
4. **保护系统状态**：失败时不留下半完成的变更；若已有写入则触发回滚。

### 18.1 失败类型与处理方式

| 失败类型 | 触发场景 | 系统行为 |
|---|---|---|
| BLOCK（安全拒绝） | 命中 B001-B031 | 立即拒绝；说明规则 ID 和保护对象；不进入执行层 |
| NEED_INFO（信息不足） | 参数缺失、目标不明 | 询问最小必要信息；不执行任何工具调用 |
| 执行失败（含资源锁超时） | 工具报错、执行超时；资源锁申请超时（reason: `resource_locked`） | 标记步骤失败；若 `on_fail: rollback` 则触发回滚链；资源锁冲突场景向用户报告占用情况并提示手动重试 |
| 验证失败 | `validate_config` / `check_endpoint` 失败 | 触发回滚；反馈"修改后验证未通过，已自动回滚" |
| 回滚失败 | `backup_path(restore)` 失败 | 进入人工处理状态；输出备份 ID；AuditLog 标记 `rollback_failed` |
| DynTool 在 competition mode 触发 | Claude 调用 `propose_dynamic_tool` | 系统层拦截；提示"竞赛模式下不允许动态工具" |

### 18.2 不可恢复状态的最低承诺

当回滚失败、系统无法自动恢复时，无论如何必须做到：

- 不静默失败；
- 在用户侧展示备份 ID 和受影响文件路径；
- 写入 AuditLog `final_status: rollback_failed`；
- 给出手动恢复操作指引（`backup_path(action=restore, backup_id=…)`）。

---

## 十九、提交材料清单

| 类别 | 产物 | 说明 |
|---|---|---|
| 代码 | 完整源代码（`sysdialogue/` 全部模块） | 按第十一节模块清单组织 |
| 工具定义 | 37 个静态工具 JSON Schema | `tools/*.py` 中 `input_schema` 导出 |
| 工作流定义 | 10 个内置 Workflow YAML | `workflows/*.yaml` |
| 核心 Prompt | SystemPrompt 完整文本 | 含 EnvProfile 注入模板、执行模式规则、安全规则摘要 |
| 架构设计文档 | 本文档（`claudeplan6.md`） | 含六层架构、37 工具、31 BLOCK 规则、工作流体系 |
| 演示视频 | 覆盖第十三节 10 个演示场景 | 每场景含输入→计划→确认→执行→验证完整闭环 |
| 自测场景说明 | 第十七节 17.3 格式的 10 份场景产物 | 含 IntentSchema、AuditTrace、截图/录屏 |
| 审计日志样例 | 至少 3 个典型场景的 JSONL 审计文件 | 包含 SAFE、WARN-HIGH 确认、BLOCK 拒绝各一份 |
| 环境搭建说明 | openEuler + Ubuntu 两套复现步骤 | 含依赖安装、API Key 配置、`--verify` 验证输出 |
| 开源依赖清单 | `requirements.txt` + 许可证说明 | 标注 Textual / Paramiko / Pydantic 等主要依赖 |
