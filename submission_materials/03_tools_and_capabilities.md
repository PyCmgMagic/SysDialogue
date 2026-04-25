# 03. 工具及能力定义文档

## 1. 总体能力

当前运行时注册：

- 37 个静态工具
- 6 个元工具
- 10 个内置 workflow
- DynTool 动态工具能力
- Markdown Skills
- Controlled Hooks
- Role Handoff
- Slash Commands

所有 OS-facing 工具都必须经过：

```text
工具调用 -> 风险分类 -> 权限策略 -> 必要审批 -> 执行器 -> 审计/Trace -> ReAct 完成门
```

## 2. 37 个静态工具

| # | 工具 | 能力摘要 |
| --- | --- | --- |
| 1 | `get_system_info` | 获取主机名、内核、架构、运行时间、内存、负载。 |
| 2 | `get_disk_usage` | 查看磁盘空间使用情况。 |
| 3 | `find_files` | 按模式查找文件，可按最小大小过滤。 |
| 4 | `list_processes` | 列出进程，支持排序和用户过滤。 |
| 5 | `kill_process` | 发送信号终止进程，WARN-HIGH。 |
| 6 | `get_port_status` | 查看端口和监听状态。 |
| 7 | `get_network_info` | 获取网络接口和路由表。 |
| 8 | `read_log` | 读取系统日志或服务日志。 |
| 9 | `create_user` | 创建用户，WARN-HIGH。 |
| 10 | `delete_user` | 删除用户，WARN-HIGH，root 禁止。 |
| 11 | `modify_user_groups` | 修改用户附加组，WARN-HIGH。 |
| 12 | `manage_service` | 管理 systemd/sysvinit 服务。 |
| 13 | `read_file` | 安全读取文件 head/tail/range，凭证文件禁止。 |
| 14 | `write_file` | 写文件，关键系统文件受限。 |
| 15 | `delete_path` | 删除文件/目录，关键目录递归删除 BLOCK。 |
| 16 | `create_directory` | 创建目录。 |
| 17 | `copy_move_path` | 拷贝或移动文件/目录。 |
| 18 | `manage_package` | apt/dnf/yum 包管理。 |
| 19 | `get_resource_stats` | 获取 CPU/内存资源和 top 进程。 |
| 20 | `manage_firewall` | 管理 ufw/firewalld/iptables，远程锁门规则保护 SSH。 |
| 21 | `get_set_system_config` | 读取/设置 hostname/timezone/locale。 |
| 22 | `list_directory` | 列目录，敏感目录受限。 |
| 23 | `stat_path` | 获取文件元数据，可选哈希。 |
| 24 | `search_file_content` | grep 搜索，敏感路径受限。 |
| 25 | `backup_path` | 备份、列出、还原、删除备份。 |
| 26 | `replace_in_file` | 精准替换文件，支持 dry-run diff 和自动备份。 |
| 27 | `validate_config` | 校验 nginx/apache/sshd/sudoers/systemd/sysctl/json/yaml/toml。 |
| 28 | `manage_cron` | 管理计划任务，只允许 tool/workflow target。 |
| 29 | `manage_sysctl` | 管理内核参数。 |
| 30 | `resolve_dns` | DNS 解析。 |
| 31 | `check_endpoint` | ping/tcp/http/tls 连通性诊断。 |
| 32 | `manage_archive` | 归档压缩，防止目录穿越。 |
| 33 | `manage_mount` | 挂载管理，不改 `/etc/fstab`。 |
| 34 | `manage_container` | docker/podman 容器管理。 |
| 35 | `manage_authorized_keys` | 管理 SSH authorized_keys。 |
| 36 | `manage_power` | 重启/关机，WARN-HIGH。 |
| 37 | `manage_hosts_entries` | 管理 `/etc/hosts`，保护 localhost。 |

## 3. 6 个元工具

| 元工具 | 用途 | 安全说明 |
| --- | --- | --- |
| `set_execution_mode` | 声明 direct / plan / workflow | 多步任务和命中 workflow 时使用。 |
| `propose_dynamic_tool` | 注册可复用 DynTool | 不执行命令，只注册能力；最后手段。 |
| `execute_dynamic_tool` | 执行已注册或 inline DynTool | 支持 `argv` 与 `shell` 两种模式，走命令安全、风险、权限、审批、审计、ReAct 门。 |
| `activate_skill` | 激活 Markdown Skill | 只注入说明，不执行 OS 操作。 |
| `handoff_to_role` | 向内置角色请求结构化建议 | 串行、建议性，不转移执行所有权。 |
| `finish_task` | ReAct 任务收口 | 所有 turn 必须以此结束。 |

## 4. 内置 workflows

| Workflow | 典型用途 |
| --- | --- |
| `security_audit` | 安全巡检演示和只读审计。 |
| `safe_config_patch` | 配置变更：预览、备份、修改、校验、失败回滚。 |
| `rollback_config` | 回滚配置。 |
| `service_restart` | 服务重启与验证。 |
| `disk_cleanup` | 磁盘清理。 |
| `container_rollout` | 容器发布。 |
| `scheduled_health_check` | 计划健康检查。 |
| `new_user` | 用户创建。 |
| `file_edit` | 文件编辑。 |
| `port_scan` | 端口诊断。 |

## 5. DynTool 策略

DynTool 始终开启，用于覆盖静态工具和 workflow 之外的命令执行需求。

执行模式：

- `argv`：参数数组模式，默认模式，适合结构化命令。
- `shell`：shell 字符串模式，适合管道、重定向和复合命令；由安全配置档控制。

安全配置档：

| 配置档 | 行为 |
| --- | --- |
| `standard` | 静态工具和 workflow 优先；DynTool 默认需要确认。 |
| `operator` | 允许受控 shell DynTool；SAFE/WARN-LOW 可直接执行，WARN-HIGH 仍需确认。 |
| `break_glass` | DynTool 可作为复杂任务的高能力执行通道；非 HARD-BLOCK 风险自动放行。 |

使用原则：

- 静态工具能覆盖时，优先静态工具。
- workflow 能覆盖时，优先 workflow。
- 一次性命令使用 `execute_dynamic_tool` inline 模式。
- 可复用命令族才使用 `propose_dynamic_tool`。
- 未能证明只读的动态命令，按可能变更状态处理。
- 变更型 DynTool 必须完成后置验证才能 `completed`。

硬拦截不随配置档关闭：

- 密码管道或凭证泄露式提权。
- 交互式 `su` / `runuser`。
- 凭证文件读取。
- 明显毁盘命令。
- 根目录或核心系统目录删除。
- 远程 SSH 自锁操作。

## 6. Skills / Hooks / Role Handoff

Skills：

- 路径：`.sysdialogue/skills/<name>/SKILL.md`、`~/.sysdialogue/skills/<name>/SKILL.md`
- 作用：注入 playbook 和上下文，不执行系统操作。

Hooks：

- 事件：`task_started`、`pre_tool`、`post_tool`、`approval_requested`、`lock_conflict`、`task_finished`、`task_failed`
- 动作：`notify`、`inject_context`、`execute_command`
- `execute_command` 仍走 DynTool 安全链路。

Role Handoff：

- `planner`
- `executor`
- `verifier`
- `risk_reviewer`
- `toolsmith`

角色给出结构化建议，主 ReAct loop 仍负责执行与安全。
