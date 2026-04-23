# SysDialogue — v5.4 静态能力增强设计文档

> 基于 v5.3，新增：静态工具面继续扩充（21 → 37，新增 16 个）  
> 核心原则：**Static-first / Workflow-first / DynTool-last**  
> v5.4 目标：尽量把高频运维场景“语义化成静态工具”，显著缩小 DynTool 的触发边界，
> 让系统更强、更稳、更可控，更符合赛题“自主与可控、可解释、可验证”的要求

---

## 一、为什么还要继续扩充静态工具

v5.3 已经把基础能力从 12 个静态工具提升到了 21 个，并引入了 DynTool 兜底。  
但从“真正可用于日常运维代理”的角度看，静态工具面仍有三类明显缺口：

| 能力缺口 | v5.3 状态 | 为什么仍不够 |
|---|---|---|
| 配置修改闭环 | 有 `read_file` / `write_file` / `file_edit.yaml` | 缺少目录浏览、元数据查看、备份恢复、精准替换、配置校验，仍过度依赖整文件覆盖 |
| 网络与服务诊断 | 有端口、防火墙、日志 | 缺少 DNS 解析、HTTP/TCP/TLS 探测、主机连通性验证，定位问题链条仍不完整 |
| 系统维护与发布 | 有包管理、服务、防火墙 | 缺少计划任务、sysctl、挂载、容器、SSH 公钥、重启关机、hosts 管理等高频场景 |

如果继续把这些缺口交给 DynTool，会带来三个副作用：

1. 静态能力边界模糊，方案更像“带审批的命令代理”
2. 安全规则需要反复做命令映射，复杂度陡增
3. 评委更难看到清晰的“产品能力面”，不利于解释和复现

因此，v5.4 的设计原则是：

- **Static-first**：高频运维能力优先固化为静态语义工具
- **Workflow-first**：多步任务优先由工作流编排，而不是现场拼命令
- **Preview / Backup / Validate**：任何配置改动，尽量做到“先预览、再备份、后校验”
- **EnvProfile-driven**：所有工具继续依赖 CapabilityProbe / EnvProfile 做环境适配
- **DynTool-last**：只有 37 个静态工具和内置 workflow 都无法覆盖时，才允许进入动态工具提案

---

## 二、静态工具面扩充（21 → 37）

### 2.1 新增 16 个静态工具

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 22 | `list_directory` | 文件浏览 | `path`, `recursive`, `max_depth`, `include_hidden`, `max_entries`, `sort_by` | SAFE / WARN-LOW |
| 23 | `stat_path` | 文件元数据 | `path`, `follow_symlink`, `with_hash`, `hash_algo` | SAFE / WARN-LOW |
| 24 | `search_file_content` | 文本检索 | `search_path`, `pattern`, `file_glob`, `regex`, `case_sensitive`, `max_matches` | WARN-LOW / BLOCK |
| 25 | `backup_path` | 备份恢复 | `action:create|list|restore|delete`, `path`, `backup_id` | SAFE(create/list) / WARN-HIGH(restore/delete) |
| 26 | `replace_in_file` | 精准编辑 | `path`, `match_type`, `search`, `replace`, `expected_matches`, `max_replacements`, `create_backup` | WARN-HIGH / BLOCK |
| 27 | `validate_config` | 配置校验 | `target_type`, `path` | SAFE / WARN-LOW |
| 28 | `manage_cron` | 计划任务 | `action`, `scope`, `schedule`, `job_target`, `job_id` | SAFE(list) / WARN-HIGH(mutate) / BLOCK |
| 29 | `manage_sysctl` | 内核参数 | `action:list|get|set|apply-file`, `key`, `value`, `persist` | SAFE(list/get) / WARN-HIGH(set/apply) |
| 30 | `resolve_dns` | DNS 诊断 | `name`, `record_type`, `resolver` | SAFE |
| 31 | `check_endpoint` | 连通性诊断 | `kind:ping|tcp|http|tls`, `host`, `port`, `path`, `method`, `expected_status`, `timeout` | SAFE / WARN-LOW |
| 32 | `manage_archive` | 归档压缩 | `action:list|create|extract`, `archive_path`, `target_path`, `format`, `strip_components` | SAFE(list) / WARN-LOW(create) / WARN-HIGH(extract) / BLOCK |
| 33 | `manage_mount` | 挂载管理 | `action:list|mount|umount|remount`, `source`, `target`, `fs_type`, `options[]` | SAFE(list) / WARN-HIGH / BLOCK |
| 34 | `manage_container` | 容器管理 | `backend`, `action`, `name`, `image`, `ports[]`, `env_vars`, `volumes[]`, `restart_policy`, `lines` | SAFE(list/status/logs/inspect) / WARN-HIGH(run/pull/start/stop/restart/remove) / BLOCK |
| 35 | `manage_authorized_keys` | SSH 公钥 | `action:list|add|remove`, `username`, `public_key`, `fingerprint` | SAFE(list) / WARN-HIGH(add/remove) / BLOCK |
| 36 | `manage_power` | 重启关机 | `action:reboot|shutdown`, `delay_sec`, `reason`, `force` | WARN-HIGH |
| 37 | `manage_hosts_entries` | 主机名映射 | `action:list|add|update|delete`, `hostname`, `ip_addrs[]`, `comment` | SAFE(list) / WARN-HIGH(mutate) / BLOCK |

### 2.2 设计意图：为什么这些工具适合“做成静态工具”

#### `list_directory` / `stat_path` / `search_file_content`

这三者补齐了“看清对象”的能力：

- 先看目录结构
- 再看目标文件属性
- 再在有限范围内检索内容

这能显著减少用户一上来就用 `read_file` / `write_file` 的概率，使“修改前确认对象”成为默认路径。  
其中 `search_file_content` 不是“任意 grep 代理”：

- 仍受敏感路径集合约束
- 候选文件展开后，仍逐个经过敏感路径检查
- 搜索结果继续经过 OutputSanitizer
- 不允许用它绕过凭证文件、私钥文件的读取限制

#### `backup_path` / `replace_in_file` / `validate_config`

这三者共同构成“配置改动闭环”：

1. `backup_path(action=create)`
2. `replace_in_file(...)`
3. `validate_config(...)`
4. 若失败，`backup_path(action=restore)`

这比单纯 `write_file(overwrite)` 更像真实运维，也更符合赛题要求的闭环完整性与结果解释性。

#### `manage_cron`

v5.4 中的 `manage_cron` **不接受任意 shell 文本**。  
它调度的是 **SysDialogue 自己的静态工具调用或 workflow 调用**，不是把 shell 命令塞进 crontab。

也就是说，系统会写入类似：

```text
* * * * * sysdialogue --run-scheduled-job job_xxx
```

而不是：

```text
* * * * * curl xxx | bash
```

这样既保留计划任务能力，又不把静态工具系统重新退化成命令代理。

同时，`manage_cron` 的风险不能只看“创建了一个计划任务”，还要看“计划任务将来会执行什么”：

- 若 `job_target` 递归展开后命中 BLOCK 级静态调用，则 `manage_cron` 本身直接 BLOCK
- 若 `job_target` 为 WARN-HIGH，则 `manage_cron` 至少保持 WARN-HIGH
- `job_target` 只能引用已注册静态工具或 workflow，不能嵌套 DynTool

#### `manage_sysctl`

单独做成工具，而不是继续交给 `write_file /etc/sysctl.conf`，有两个好处：

- 运行时与持久化配置分离
- 风险规则可以直接围绕“sysctl key 语义”制定，而不必从文本中猜

#### `resolve_dns` / `check_endpoint`

这是网络故障排查里最缺的两块：

- `resolve_dns` 解决“域名解析到了哪里”
- `check_endpoint` 解决“目标端口 / HTTP / TLS 到底通不通”

有了这两项，很多“服务挂了”的场景才能形成完整诊断链。

#### `manage_archive`

压缩、归档、解压是部署、备份、迁移中的高频操作。  
但解压到系统路径是高风险动作，因此 `extract` 必须独立建模，而不能默默落回 DynTool。

此外，`manage_archive(action=extract)` 必须内建解压安全保护：

- 拒绝绝对路径条目
- 拒绝带 `..` 的越界条目
- 拒绝会逃逸 `target_path` 的符号链接 / 硬链接
- 对单次解压的总文件数、总字节数设置上限

#### `manage_mount`

挂载操作是典型的系统管理能力，且强烈依赖环境与风险边界。  
如果不用静态工具，而改为让模型生成 `mount/umount` 命令，会明显削弱可控性。

v5.4 明确约束：

- `manage_mount` 只做即时挂载，不直接修改 `/etc/fstab`
- `mount|umount|remount` 对关键目标路径统一进入 BLOCK 检查
- 持久化挂载若后续需要，必须单独建模，不混入当前工具

#### `manage_container`

容器场景在今天的运维里非常常见。  
但 v5.4 的定位不是“容器编排平台”，而是提供**有边界的基础容器运维**：

- `list`
- `status`
- `pull`
- `start`
- `stop`
- `restart`
- `logs`
- `inspect`
- `run`
- `remove`

重点约束：

- **不提供 `exec shell`**
- **不提供任意命令字符串**
- `run` 只接受结构化部署参数

#### `manage_authorized_keys`

SSH 公钥管理是非常高频的运维场景。  
如果总是用 `write_file ~/.ssh/authorized_keys` 来做，会把“加公钥”与“任意文件覆盖”混成一类能力，语义过于粗糙。

#### `manage_power`

重启 / 关机不该继续依赖 DynTool。  
这是明确的系统运维动作，应该有清晰的风险提示、审计字段和确认流程。

#### `manage_hosts_entries`

`/etc/hosts` 是很常见的运维调整点。  
单独建模为结构化 hostname → ip 映射，比让模型直接改文本更容易做审计、对比和保护规则。

---

## 三、对现有静态工具的增强（不新增编号）

### 3.1 `manage_service` 扩展 action

在 v5.4 中建议为 `manage_service.action` 增加：

- `reload`
- `daemon-reload`

风险分级：

- `reload`：与 `restart` 接近，但通常影响更小，非关键服务可降为 `WARN-LOW`
- `daemon-reload`：`WARN-LOW`

### 3.2 `read_file` 扩展读取模式

增加：

- `mode: range|head|tail`
- `tail_lines`

这样大文件查看不必依赖 `start_line/end_line` 手工计算，日志类文件也更好处理。

### 3.3 `write_file` 扩展安全参数

增加：

- `atomic: bool = true`
- `create_backup: bool = false`
- `backup_label: str (optional)`

`write_file` 仍保留，但在推荐路径上会被 `replace_in_file + backup_path + validate_config` 取代。

### 3.4 `manage_package` 扩展 action

建议增加：

- `clean-cache`
- `hold`
- `unhold`

这样常见的包管理动作可以留在静态工具面内，不用下沉到 DynTool。

### 3.5 `manage_firewall` 扩展 action

建议增加：

- `reload`

这可以覆盖防火墙配置刷新场景，使工作流更完整。

---

## 四、复杂新工具的 schema 设计

### 4.1 `replace_in_file`

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
      "create_backup": {"type": "boolean", "default": true}
    },
    "required": ["path", "search", "replace"]
  }
}
```

设计约束：

- 若 `expected_matches` 不符，直接失败，不写入
- 默认创建 backup
- UI 必须提供 diff 预览

### 4.2 `manage_cron`

```json
{
  "name": "manage_cron",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list", "create", "update", "delete", "enable", "disable"]},
      "scope": {"type": "string", "enum": ["user", "system"], "default": "user"},
      "schedule": {"type": "string"},
      "job_id": {"type": "string"},
      "job_target": {
        "type": "object",
        "properties": {
          "kind": {"type": "string", "enum": ["tool", "workflow"]},
          "name": {"type": "string"},
          "args": {"type": "object"}
        }
      }
    },
    "required": ["action"],
    "allOf": [
      {
        "if": {"properties": {"action": {"enum": ["create", "update"]}}, "required": ["action"]},
        "then": {
          "required": ["schedule", "job_target"],
          "properties": {
            "job_target": {"required": ["kind", "name"]}
          }
        }
      },
      {
        "if": {"properties": {"action": {"enum": ["delete", "enable", "disable"]}}, "required": ["action"]},
        "then": {"required": ["job_id"]}
      }
    ]
  }
}
```

设计约束：

- `job_target.kind=tool` 时，`name` 必须命中静态工具注册表
- `job_target.kind=workflow` 时，`name` 必须命中内置 workflow 名称
- `create/update` 之前，系统必须对 `job_target` 做一次递归风险判定
- `BLOCK` 级目标不允许进入计划任务

### 4.3 `validate_config`

```json
{
  "name": "validate_config",
  "input_schema": {
    "type": "object",
    "properties": {
      "target_type": {
        "type": "string",
        "enum": ["auto", "nginx", "apache", "sshd", "sysctl", "sudoers", "systemd-unit", "json", "yaml", "toml", "fstab", "cron"]
      },
      "path": {"type": "string"}
    },
    "required": ["path"]
  }
}
```

### 4.4 `manage_container`

```json
{
  "name": "manage_container",
  "input_schema": {
    "type": "object",
    "properties": {
      "backend": {"type": "string", "enum": ["auto", "docker", "podman"], "default": "auto"},
      "action": {"type": "string", "enum": ["list", "status", "pull", "start", "stop", "restart", "logs", "inspect", "run", "remove"]},
      "name": {"type": "string"},
      "image": {"type": "string"},
      "ports": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "host_port": {"type": "integer", "minimum": 1, "maximum": 65535},
            "container_port": {"type": "integer", "minimum": 1, "maximum": 65535},
            "protocol": {"type": "string", "enum": ["tcp", "udp"], "default": "tcp"}
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
      "restart_policy": {"type": "string", "enum": ["no", "always", "unless-stopped"], "default": "no"},
      "lines": {"type": "integer", "minimum": 10, "maximum": 1000, "default": 100}
    },
    "required": ["action"]
  }
}
```

设计约束：

- **不提供 `exec`**
- **不提供 `privileged`**
- **不提供 `network_mode=host`**
- 对 bind mount 统一做风险检查

### 4.5 `manage_archive`

```json
{
  "name": "manage_archive",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list", "create", "extract"]},
      "archive_path": {"type": "string"},
      "target_path": {"type": "string"},
      "format": {"type": "string", "enum": ["auto", "tar", "tar.gz", "zip"], "default": "auto"},
      "strip_components": {"type": "integer", "minimum": 0, "maximum": 10, "default": 0}
    },
    "required": ["action", "archive_path"],
    "allOf": [
      {
        "if": {"properties": {"action": {"const": "extract"}}, "required": ["action"]},
        "then": {"required": ["target_path"]}
      }
    ]
  }
}
```

设计约束：

- `extract` 前必须扫描归档条目，拒绝绝对路径、`..`、越界链接
- 默认不保留 archive 中的 owner/group
- 默认不跟随 archive 内的链接写出目标目录外内容
- 对总文件数、总字节数做安全阈值限制

---

## 五、EnvProfile 扩展

为了支撑新增静态工具，`EnvProfile` 在 v5.4 增加：

```python
class EnvProfile(TypedDict):
    # v4.1 / v5.3 原有字段保持不变

    container_backend: str        # "docker" | "podman" | "none"
    config_validators: list[str]  # 如 ["nginx", "sshd", "systemd-unit", "sudoers"]
    supports_system_cron: bool
    mount_capable: bool
    dns_tools: list[str]          # ["dig", "nslookup", "getent"]
```

`CapabilityProbe` 扩展探测：

- `docker` / `podman`
- `nginx -t`
- `apachectl -t`
- `sshd -t`
- `systemd-analyze verify`
- `visudo -c`
- `crontab`
- `dig` / `nslookup` / `getent`
- `mount` / `umount`

---

## 六、DynTool 边界进一步收紧

v5.4 的一个关键变化不是“删除 DynTool”，而是**让 DynTool 明显更不常用**。

### 6.1 DynTool 不再是主能力扩展路径

以下能力在 v5.4 中已被静态工具覆盖，因此 **禁止** 通过 DynTool 提案进入：

- 文件浏览 / 检索 / 备份 / 精准编辑 / 校验
- 包管理
- 服务管理
- 防火墙管理
- 计划任务
- sysctl
- DNS / TCP / HTTP / TLS 诊断
- 压缩解压
- 挂载
- 容器运维
- SSH 公钥管理
- 重启关机
- hosts 映射管理

### 6.2 DynTool 的触发门槛更新

只有满足以下条件时，Claude 才能进入 `propose_dynamic_tool`：

1. 37 个静态工具均无法表达该能力
2. 现有 workflow 无法组合完成
3. 目标能力不是对已有静态工具的简单变体
4. 不属于上述“禁止经 DynTool 提案”的能力域

这会让整套系统在评审视角里更像“可控的语义代理”，而不是“通用命令代理”。

---

## 七、新增具名集合与风险规则

### 7.1 新增具名集合

**CRITICAL_EDIT_PATHS**

```text
/etc/passwd
/etc/shadow
/etc/gshadow
/etc/sudoers
/etc/sudoers.d/
/etc/ssh/sshd_config
/boot/
/lib/systemd/
```

**MOUNT_BLOCK_TARGETS**

```text
/
/boot
/proc
/sys
/dev
/run
```

**ARCHIVE_BLOCK_TARGETS**

```text
/
/etc
/boot
/usr
/bin
/sbin
/lib
```

**CONTAINER_SENSITIVE_BIND_SOURCES**

```text
/
/etc
/boot
/root
~/.ssh/
/var/run/docker.sock
```

**HOSTS_PROTECTED_ENTRIES**

```text
127.0.0.1 localhost
::1 localhost
```

### 7.2 新增 BLOCK 规则（B018-B027）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| B018 | `replace_in_file(path)` 命中 `CRITICAL_EDIT_PATHS` | `replace_in_file` |
| B019 | `backup_path(action=restore)` 目标命中 `CRITICAL_EDIT_PATHS` | `backup_path` |
| B020 | `manage_mount(action=mount\|umount\|remount)` 且目标命中 `MOUNT_BLOCK_TARGETS` | `manage_mount` |
| B021 | `manage_archive(action=extract)` 且目标命中 `ARCHIVE_BLOCK_TARGETS` | `manage_archive` |
| B022 | `manage_container(action=run)` bind mount 命中 `CONTAINER_SENSITIVE_BIND_SOURCES` | `manage_container` |
| B023 | `manage_authorized_keys(add\|remove)` 输入不是公钥格式或疑似私钥内容 | `manage_authorized_keys` |
| B024 | `manage_hosts_entries(action!=list)` 修改 `HOSTS_PROTECTED_ENTRIES` | `manage_hosts_entries` |
| B025 | `search_file_content(search_path)` 或其展开出的候选文件路径命中 `SENSITIVE_CREDENTIAL_PATHS` 或 v4.1 的敏感核心路径集合 | `search_file_content` |
| B026 | `manage_cron(action=create\|update)` 的 `job_target` 递归风险判定结果为 BLOCK | `manage_cron` |
| B027 | `manage_archive(action=extract)` 的归档条目包含绝对路径、`..` 越界路径、或逃逸目标目录的链接 | `manage_archive` |

### 7.3 新增 WARN-HIGH 规则（WH013-WH021）

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

### 7.4 新增 WARN-LOW 规则（WL010-WL015）

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WL010 | `list_directory(recursive=true)` 或 `max_depth > 2` | `list_directory` |
| WL011 | `stat_path(with_hash=true)` | `stat_path` |
| WL012 | `search_file_content(regex=true)` 或搜索 `/etc/*`（非 BLOCK 路径） | `search_file_content` |
| WL013 | `backup_path(action=create)` 且目标为目录 | `backup_path` |
| WL014 | `manage_archive(action=create)` | `manage_archive` |
| WL015 | `check_endpoint(kind=http\|tls)` 且超时 > 10s | `check_endpoint` |

---

## 八、基于新增静态工具的工作流增强

### 8.1 `safe_config_patch.yaml`

执行顺序：**浏览 → 备份 → 精准替换 → 校验 → 预览结果 → 可选 reload**

```yaml
name: 安全修改配置
parameters:
  - {name: file_path, type: text, required: true}
  - {name: search_text, type: text, required: true}
  - {name: replace_text, type: text, required: true}
  - {name: validator, type: text, required: false, default: auto}

steps:
  - {id: s1, type: tool_call, tool: stat_path, args: {path: "{{file_path}}"}}
  - {id: s2, type: tool_call, tool: backup_path, args: {action: create, path: "{{file_path}}"}, depends_on: [s1]}
  - {id: s3, type: tool_call, tool: replace_in_file,
     args: {path: "{{file_path}}", search: "{{search_text}}", replace: "{{replace_text}}", create_backup: true},
     depends_on: [s2]}
  - {id: s4, type: tool_call, tool: validate_config, args: {target_type: "{{validator}}", path: "{{file_path}}"}, depends_on: [s3]}
  - {id: s5, type: display, template: "修改完成，验证结果：{{s4.result}}", depends_on: [s4]}
```

### 8.2 `rollback_config.yaml`

执行顺序：**列备份 → 选择备份 → 确认 → 恢复 → 校验**

### 8.3 `container_rollout.yaml`

执行顺序：**pull → run → check_endpoint → logs**

### 8.4 `scheduled_health_check.yaml`

执行顺序：**创建 workflow 型 cron 任务 → 周期性执行 `check_endpoint`**

这类 workflow 的价值在于：

- 明显减少现场生成命令的需求
- 强化“多步连续任务处理”的评分表现
- 把静态工具真正变成“可编排能力”

---

## 九、新模块文件清单（v5.4 新增）

```text
sysdialogue/
├── tools/
│   ├── fs_browse.py          ← list_directory / stat_path / search_file_content
│   ├── backup_restore.py     ← backup_path / replace_in_file
│   ├── config_validate.py    ← validate_config
│   ├── cron_jobs.py          ← manage_cron
│   ├── sysctl_ops.py         ← manage_sysctl
│   ├── net_diag.py           ← resolve_dns / check_endpoint
│   ├── archive_ops.py        ← manage_archive
│   ├── mount_ops.py          ← manage_mount
│   ├── containers.py         ← manage_container
│   ├── auth_keys.py          ← manage_authorized_keys
│   ├── power_ops.py          ← manage_power
│   └── hosts_entries.py      ← manage_hosts_entries
├── workflows/
│   ├── safe_config_patch.yaml
│   ├── rollback_config.yaml
│   ├── container_rollout.yaml
│   └── scheduled_health_check.yaml
└── security/
    └── ...（沿用 v5.3 现有规则引擎，并补充 B018-B027 / WH013-WH021 / WL010-WL015）
```

---

## 十、开发优先级（v5.4）

```text
P0（最优先，直接提高可用性）
  list_directory / stat_path / search_file_content
  backup_path / replace_in_file / validate_config
  manage_service 扩展 reload / daemon-reload
  read_file / write_file 安全参数增强
  B018-B027 / WH013-WH017 / WL010-WL014

P1（高频运维增强）
  manage_cron（仅调度 tool/workflow，不接受任意 shell）
  manage_sysctl
  resolve_dns / check_endpoint
  manage_hosts_entries
  safe_config_patch.yaml / rollback_config.yaml

P2（运维能力做深）
  manage_archive
  manage_mount
  manage_authorized_keys
  manage_power
  manage_container
  container_rollout.yaml / scheduled_health_check.yaml

P3（体验与审计增强）
  replace_in_file 的 diff 视图
  backup_path 的备份清单面板
  check_endpoint 的历史结果趋势
  DynTool 触发率统计（应显著下降）
```

---

## 十一、对 DynTool 的影响

v5.4 不是删除 DynTool，而是让它退到真正的边角能力上。

预期效果：

- 主演示路径几乎全部使用静态工具 + workflow
- DynTool 从“常见场景补洞手段”下降为“极少数厂商私有命令的兜底”
- 静态规则命中率显著提高
- `semantic_unmapped=true` 频率下降
- 评委更容易理解系统能力边界

---

## 十二、结论

v5.4 的关键不是“工具数量更多”本身，而是：

**把更多真实高频运维动作，从命令层拉回语义层。**

从 `21` 个静态工具扩充到 `37` 个后，系统将拥有更完整的能力面：

- 看清对象：目录、元数据、内容检索
- 安全改动：备份、精准替换、配置校验、回滚
- 网络诊断：DNS、TCP、HTTP、TLS
- 系统维护：计划任务、sysctl、挂载、压缩归档
- 现代运维：容器、公钥、hosts、重启关机

这会让 SysDialogue 更像一个真正的“操作系统智能代理”，而不是“带审计的命令执行器”。
