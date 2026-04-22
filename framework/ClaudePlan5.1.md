# SysDialogue — v5.1 扩展设计文档

> 基于 v4.1，新增：扩充工具面（12 → 21，新增 9 个）+ 动态工具创建机制  
> 本文档只描述**相对 v4.1 的增量变更**，原有设计不变

---

## 一、背景与问题

v4.1 的 12 个工具仅满足赛题最低要求（磁盘、文件检索、进程、端口、用户），  
无法覆盖真正"无命令运维"的核心场景：

| 场景 | v4.1 能做吗 | 原因 |
|---|---|---|
| 查看/编辑 nginx.conf | ✗ | 无 read_file / write_file |
| 安装 git | ✗ | 无包管理工具 |
| 清理磁盘（实际删除） | ✗ | 故意不注册 delete_file |
| 查看 CPU 实时负载 | 部分 | get_system_info 不够细 |
| 管理防火墙规则 | ✗ | 无 firewall 工具 |
| 改 hostname / 时区 | ✗ | 无系统配置工具 |
| 用户请求安装 Docker | ✗ | 无包管理 + 工具面外 |

---

## 二、扩充静态工具面（新增 9 个）

### 2.1 新工具列表

| # | 工具名 | 能力域 | 关键参数 | 最高风险 |
|---|---|---|---|---|
| 13 | `read_file` | 文件 | `path`, `start_line`, `end_line`, `max_bytes=8192` | WARN-LOW |
| 14 | `write_file` | 文件 | `path`, `content`, `mode: overwrite\|append\|create_only` | WARN-HIGH |
| 15 | `delete_path` | 文件 | `path`, `recursive: bool` | WARN-HIGH / BLOCK |
| 16 | `create_directory` | 文件 | `path`, `parents: bool` | WARN-LOW |
| 17 | `copy_move_path` | 文件 | `src`, `dst`, `action: copy\|move` | WARN-LOW / WARN-HIGH |
| 18 | `manage_package` | 包管理 | `name\|names[]`, `action: install\|remove\|update\|list\|search`, `manager: auto\|apt\|yum\|dnf` | SAFE(list/search) / WARN-HIGH(install/remove/update) |
| 19 | `get_resource_stats` | 监控 | `resource: cpu\|memory\|all`, `top_n_procs` | SAFE |
| 20 | `manage_firewall` | 防火墙 | `backend: auto\|ufw\|firewalld\|iptables`, `action: list\|allow\|deny\|delete\|set-default\|flush`, `rule_spec` | SAFE(list) / WARN-HIGH(allow/deny/delete/set-default) / BLOCK(flush 远程模式) |
| 21 | `get_set_system_config` | 系统配置 | `key: hostname\|timezone\|locale`, `value(可选)` | SAFE(get) / WARN-HIGH(set) |

### 2.2 关键设计约束（防止与 v4.1 冲突）

**`read_file` — 为什么风险是 WARN-LOW 而非 SAFE？**

读取系统敏感文件（凭证文件、私钥）可能泄漏高价值信息，需 BLOCK 拦截。

**`write_file` — mode 与风险的关系**

覆盖 nginx.conf 等配置立即生效，错误内容可使服务中断，`overwrite/append` 始终 WARN-HIGH。  
`create_only` 仅在目标不存在时写入，但落在持久化入口路径（见下）时同样升级为 WARN-HIGH。

**`manage_package(action=update)` — 不比 install/remove 安全**

包升级常伴随服务重启、ABI 变化、配置迁移，一律列为 WARN-HIGH。

**`manage_firewall` — action 枚举扩展**

新增 `set-default`（修改默认策略）和 `flush`（清空所有规则），以支持 B015–B017 规则的  
精确匹配。`set-default` 为 WARN-HIGH；`flush` 在远程模式为 BLOCK（见第五节）。

**`manage_package` / `manage_firewall` — CapabilityProbe 联动**

CapabilityProbe 探测 `apt/yum/dnf`、`ufw/firewalld/iptables` 可用性，  
`manager="auto"` 和 `backend="auto"` 时按 `EnvProfile` 中对应字段路由。

**`delete_path` — 与 disk_cleanup.yaml 搭配**

原 workflow 输出"请手动审核后删除"，用户现在可在确认 `find_files` 结果后  
明确调用 `delete_path`，不改变原有 workflow 逻辑。

### 2.3 路径与凭证集合定义

v5 新增两个具名集合，供规则引擎引用。

**SENSITIVE_CREDENTIAL_PATHS（触发 B011）**

```text
精确路径：
  /etc/shadow, /etc/gshadow

glob 模式：
  ~/.ssh/id_*           # 私钥（id_rsa / id_ed25519 / id_ecdsa 等）
  ~/.ssh/authorized_keys
  ~/.aws/credentials
  ~/.aws/config
  ~/.kube/config
  **/.env               # 任意目录下的 .env 文件
  .env.*                # .env.production 等变体
  *.pem                 # 证书/私钥（非 .pub）
  *.key                 # 私钥（非 .pub）
  *_rsa, *_ed25519, *_ecdsa   # 裸私钥文件
  *.pfx, *.p12          # PKCS#12 证书包
```

**PERSISTENCE_ENTRY_PATHS（影响 write_file create_only 风险级别）**

```text
/etc/systemd/system/
/etc/cron.d/
/etc/cron.daily/
/etc/cron.hourly/
/etc/cron.weekly/
/etc/cron.monthly/
/etc/init.d/
/etc/profile.d/
/etc/rc.d/
/etc/ld.so.conf.d/
```

在这些路径下创建新文件（即使 `create_only`）可建立持久化或提权入口，风险升级为 WARN-HIGH。

### 2.4 EnvProfile 扩展字段

v4.1 已有 `package_manager: str`，本次仅补充两个字段：

```python
class EnvProfile(TypedDict):
    # ... v4.1 原有 9 个字段保持不变 ...

    # 新增
    firewall_backend: str   # "ufw" | "firewalld" | "iptables" | "none"
    ssh_port: int           # 来源：RemoteExecutor 当前连接参数（首选），
                            # 本地模式时探测 /proc/net/tcp 监听端口（不读 sshd_config）

    # available_cmds（v4.1 已有）扩展 key：ufw, firewalld, iptables
```

**ssh_port 来源优先级：**

1. `RemoteExecutor.__init__(port=...)` 中的实际连接端口（最可靠）
2. 本地模式：扫描 `ss -tlnp` 或 `/proc/net/tcp` 中监听的 SSH 相关端口
3. 上述均失败时，默认 22

**不使用 `/etc/ssh/sshd_config` 作为探测源**：该文件被 v4.1 列为 B009 敏感对象，  
且配置值与实际监听端口可能不一致（动态重载未生效等情形）。

---

## 三、动态工具创建机制（DynTool）

### 3.1 设计目标

当用户请求**超出全部 21 个静态工具的能力边界**时：

1. Claude 评估意图，推断所需命令和风险
2. 向用户呈现"工具提案"（命令模板、后果、风险评估、可逆性）
3. 用户键入 `approve` → 校验通过后创建持久化 DynamicTool，风险永远标记为 `UNKNOWN`
4. 每次执行经过三层安全检查（见 3.5）
5. 结果写入 AuditLog，含 `dynamic: true` 和映射状态

### 3.2 新增元工具 `propose_dynamic_tool`

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
      "params": {
        "type": "object",
        "description": "参数定义：{param_name: {type, description, required}}"
      },
      "consequences": {"type": "string"},
      "risk_assessment": {"type": "string"},
      "estimated_risk": {
        "type": "string",
        "enum": ["WARN-LOW", "WARN-HIGH", "UNKNOWN"]
      },
      "reversible": {"type": "boolean"}
    },
    "required": ["intent_summary", "proposed_tool_name", "cmd_template",
                 "consequences", "risk_assessment", "estimated_risk"]
  }
}
```

**SystemPrompt 中的限制指令（防止绕过静态安全门）：**

```text
当用户请求的操作可以由现有静态工具完成时，严禁调用 propose_dynamic_tool。
propose_dynamic_tool 仅用于静态工具完全无法覆盖的全新能力。
```

**此元工具与 `set_execution_mode` 一样，不经过 RiskClassifier，但提案创建时  
`validate_proposal()` 会对模板本身运行 CommandSafetyChecker。**

### 3.3 DynamicTool 数据结构

```python
class DynamicTool(TypedDict):
    tool_id: str            # "dyn_" + uuid4()[:8]
    name: str               # propose_dynamic_tool.proposed_tool_name
    description: str        # intent_summary
    cmd_template: list[str] # subprocess argv template，含 {param} 占位符
    params: dict            # 参数定义
    risk_level: str         # 永远 "UNKNOWN"
    estimated_risk: str     # 提案时的评估，仅展示用
    reversible: bool
    created_at: str         # ISO8601
    created_for: str        # 原始用户输入
    consequences: str
    risk_assessment: str
    usage_count: int
    safety_overrides: int   # CommandSafetyChecker 升级风险的累计次数
```

### 3.4 CommandSafetyChecker

新模块 `security/command_safety.py`，接收 `env_profile` 以支持上下文感知规则：

```python
@dataclass
class SafetyCheckResult:
    safe: bool
    flags: list[str]          # 触发的规则 ID
    explanation: str          # 中文解释
    escalated_risk: str       # "WARN-LOW" | "WARN-HIGH" | "BLOCK"

def check_command(cmd: list[str], env_profile: EnvProfile) -> SafetyCheckResult:
    ...
```

**检查规则（CS 系列）：**

| ID | 检查项 | 升级到 | 备注 |
|---|---|---|---|
| CS001 | argv 中含 shell 元字符（`&&`, `\|`, `;`, `$(`, `` ` ``） | BLOCK | shell=False 下防模板注入 |
| CS002 | cmd[0] 或任意 arg 含路径穿越（`..`） | BLOCK | 镜像 B005 |
| CS003 | 命令为 `rm`/`rmdir` + `-rf`/`-r` + 系统目录路径 | BLOCK | |
| CS004 | 命令为 `mkfs`、`dd`、`shred` | BLOCK | |
| CS005 | 命令为 `chmod` + 模式 `777`/`000` + 系统目录路径 | WARN-HIGH | |
| CS006 | 命令为 `chown` + 目标用户为 `root` | WARN-HIGH | |
| CS007 | 命令涉及 SENSITIVE_CREDENTIAL_PATHS 路径 | WARN-HIGH | |
| CS008 | 任意 arg 超过 8192 字符 | WARN-HIGH | 防注入载荷 |
| CS009 | 命令为 `curl`/`wget` 且 args 含 `-o`/`\|`（在 argv 中拼接后等效管道） | BLOCK | |
| CS010 | **remote_mode=true**：`systemctl`/`service` + `stop`/`disable` + 含 `ssh`/`sshd` | BLOCK | 镜像 B010，补 CS 覆盖 |
| CS011 | **remote_mode=true**：`iptables -F`、`ufw --flush`、`firewall-cmd --panic-on` 等清空命令 | BLOCK | 映射失败时的远程锁门防护 |
| CS012 | **remote_mode=true**：防火墙命令含 `--dport {ssh_port}` + `DROP`/`REJECT` | BLOCK | |
| CS013 | **remote_mode=true**：`ufw default deny` 类命令 | BLOCK | |
| CS014 | **remote_mode=true**：`firewall-cmd --set-default-policy=DROP` 类命令 | BLOCK | |

CS010–CS014 仅在 `env_profile.remote_mode == True` 时触发，本地模式降级为 WARN-HIGH。

### 3.5 DynamicToolRegistry：三层执行链

```python
class DynamicToolRegistry:
    def execute(self, tool_name: str, args: dict,
                executor: ExecutorAdapter,
                env_profile: EnvProfile,
                confirm_fn: ConfirmCallback) -> ToolResult:

        tool = self._find(tool_name)
        cmd = self._render(tool.cmd_template, args)

        # ── 第一层：CommandSafetyChecker（命令形态 + 远程上下文） ──────────
        safety = check_command(cmd, env_profile)
        if safety.escalated_risk == "BLOCK":
            self._audit(tool, cmd, blocked=True, safety=safety)
            return ToolResult(blocked=True, reason=safety.explanation)

        # ── 第二层：StaticRuleMapper + RiskClassifier（对象语义） ──────────
        mapped = StaticRuleMapper.map(cmd)          # 有限白名单映射，失败返回 None
        if mapped:
            rc_result = RiskClassifier.classify(mapped.tool, mapped.args)
            if rc_result.level == "BLOCK":
                self._audit(tool, cmd, blocked=True, rc=rc_result, mapped=mapped)
                return ToolResult(blocked=True, reason=rc_result.reason)
            semantic_unmapped = False
        else:
            rc_result = None
            semantic_unmapped = True

        # ── 第三层：UNKNOWN 确认（始终触发，无论前两层结果） ────────────────
        user_ok = confirm_fn(tool, cmd, safety, rc_result)
        if not user_ok:
            self._audit(tool, cmd, decision="user_cancelled", ...)
            return ToolResult(cancelled=True)

        # 执行
        output, exit_code = executor.run(cmd, timeout=30)
        tool.usage_count += 1
        if not safety.safe:
            tool.safety_overrides += 1
        self._audit(tool, cmd, safety=safety, rc=rc_result,
                    semantic_unmapped=semantic_unmapped, ...)
        return ToolResult(output=output, exit_code=exit_code)
```

**StaticRuleMapper — 有限白名单，不做通用命令语义解析：**

```python
WHITELIST_MAPPINGS = [
    # cmd 模式                                    → 等价静态工具调用
    (["systemctl", ACT, SVC],                    lambda a: ("manage_service",  {"name": a[2], "action": a[1]})),
    (["service",   SVC, ACT],                    lambda a: ("manage_service",  {"name": a[1], "action": a[2]})),
    (["apt",       "install", PKG],              lambda a: ("manage_package",  {"name": a[2], "action": "install", "manager": "apt"})),
    (["apt-get",   "install", PKG],              lambda a: ("manage_package",  {"name": a[2], "action": "install", "manager": "apt"})),
    (["yum",       "install", PKG],              lambda a: ("manage_package",  {"name": a[2], "action": "install", "manager": "yum"})),
    (["dnf",       "install", PKG],              lambda a: ("manage_package",  {"name": a[2], "action": "install", "manager": "dnf"})),
    (["apt",       "remove",  PKG],              lambda a: ("manage_package",  {"name": a[2], "action": "remove",  "manager": "apt"})),
    (["ufw",       ACT,       RULE],             lambda a: ("manage_firewall", {"backend": "ufw",      "action": a[1], "rule_spec": a[2]})),
    (["firewall-cmd", RULE_FLAG, RULE],          lambda a: ("manage_firewall", {"backend": "firewalld","action": _map_fcmd(a), "rule_spec": a[2]})),
    (["iptables",  FLAG, ...],                   lambda a: ("manage_firewall", {"backend": "iptables", "action": _map_ipt(a),  "rule_spec": " ".join(a[1:])})),
]
# 覆盖范围：systemctl/service、apt/apt-get/yum/dnf、ufw/firewall-cmd/iptables
# 未在此表中的命令：semantic_unmapped=True，仅走 CS 检查 + UNKNOWN 确认
```

### 3.6 validate_proposal()

在 `DynamicToolProposalModal` 接受用户 `approve` 之前运行，任意失败则拒绝创建：

```
1. cmd_template 中所有 {param} 占位符均存在于 params 定义（正向检查）
2. params 定义中无多余占位符（反向检查）
3. proposed_tool_name 不与任何静态工具名完全匹配（ToolRegistry.all_names()）
4. proposed_tool_name 不是保留名（propose_dynamic_tool, set_execution_mode 及所有元工具）
5. 每个 cmd_template 元素 ≤ 256 字符
6. cmd_template 总元素数 ≤ 10
7. 对 cmd_template 整体（含占位符未渲染）运行 CommandSafetyChecker，
   escalated_risk == BLOCK 时拒绝（CS001 在创建时即拦截含 shell 元字符的模板）
8. 已注册动态工具数量 < 20（否则提示用户先删除旧工具）
```

### 3.7 持久化与并发安全

```
文件路径：~/.sysdialogue/dynamic_tools.json
创建时：  os.chmod(path, 0o600)
写入策略：tmp_path = path + ".tmp"
          写入 tmp_path → os.replace(tmp_path, path)  # 原子替换
并发保护：filelock.FileLock(path + ".lock") 保护所有写操作（读不加锁）
          确保同一会话内的并发提案不会互相覆盖
上限：    20 个工具；超出时 validate_proposal() 在第 8 步拒绝
```

### 3.8 与现有模块的集成

**ToolRegistry（tools/__init__.py）**

```python
# 静态工具 definitions 不变
# get_all_definitions() 末尾追加 DynamicToolRegistry.get_tool_definitions()
# all_names() 合并静态 + 动态名称（供 validate_proposal 步骤 3 使用）
```

**ClaudeClient**

```python
if first_call.name == "set_execution_mode":       # 已有
    ...
elif first_call.name == "propose_dynamic_tool":   # 新增
    dynamic_engine.handle_proposal(first_call.input)
elif dynamic_registry.is_dynamic(first_call.name): # 新增，走三层执行链
    result = dynamic_registry.execute(
        first_call.name, first_call.input,
        executor, env_profile, confirm_fn)
else:
    # 原有静态工具流程（RiskClassifier → ConfirmModal → SafeExecutor）
    ...
```

**RiskClassifier**

不修改。静态工具调用路径不变。动态工具通过独立三层执行链提供等效的安全覆盖。

**AuditLog（新增字段）**

```json
{
  "dynamic": true,
  "dynamic_tool_id": "dyn_a1b2c3d4",
  "semantic_unmapped": false,
  "mapped_to": "manage_service",
  "safety_check": {
    "safe": true,
    "flags": [],
    "escalated_risk": "SAFE"
  },
  "risk_classifier_result": "WARN-HIGH",
  "rule_id": "WH005"
}
```

`semantic_unmapped=true` 时，`mapped_to` 和 `risk_classifier_result` 字段缺省。

**TUI**

新增 `DynamicToolProposalModal`：展示提案（cmd_template、后果、风险、可逆性、validate 结果），  
用户键入 `approve` / `reject`。复用 `ConfirmModal` 基础组件，布局扩展。

**SystemPromptBuilder**

在系统提示词末尾注入已注册动态工具摘要：

```text
已注册的动态工具（由用户授权创建，风险级别 UNKNOWN，每次执行均需确认）：
- dyn_a1b2c3d4: install_docker — 安装 Docker CE
```

---

## 四、新模块文件清单

```
sysdialogue/
├── security/
│   ├── command_safety.py     ← 新增：CommandSafetyChecker + CS001-CS014
│   └── ...（原有不变）
├── tools/
│   ├── dynamic_registry.py   ← 新增：DynamicToolRegistry + StaticRuleMapper + validate_proposal
│   ├── file_ops.py           ← 新增：read_file / write_file / delete_path / create_directory / copy_move_path
│   ├── packages.py           ← 新增：manage_package
│   ├── monitoring.py         ← 新增：get_resource_stats
│   ├── firewall.py           ← 新增：manage_firewall
│   ├── system_config.py      ← 新增：get_set_system_config
│   └── ...（原有不变）
├── ui/
│   ├── dynamic_proposal.py   ← 新增：DynamicToolProposalModal
│   └── ...（原有不变）
└── workflows/
    └── file_edit.yaml        ← 新增：文件编辑工作流（含 input step）
```

---

## 五、新增 BLOCK 规则汇总（B011-B017）

在 v4.1 的 B001-B010 基础上追加：

| ID | 触发条件 | 适用工具 |
|---|---|---|
| B011 | `read_file(path)` 匹配 SENSITIVE_CREDENTIAL_PATHS | `read_file` |
| B012 | `write_file(path)` 目标为 `/etc/passwd`、`/etc/shadow`、`/etc/sudoers`、`/etc/sudoers.d/*`、`/boot/*`、`/lib/systemd/*`、`/etc/ssh/sshd_config` | `write_file` |
| B013 | `delete_path(path, recursive=true)` 目标为 `/`、`/etc`、`/usr`、`/boot`、`/lib`、`/bin`、`/sbin` | `delete_path` |
| B014 | `delete_path(path)` 路径匹配 B001 路径集合 | `delete_path` |
| B015 | 远程模式下：`manage_firewall(action=flush)` | `manage_firewall` |
| B016 | 远程模式下：`manage_firewall(action=set-default, rule_spec 含 "deny"\|"drop"\|"DROP"\|"DENY")` | `manage_firewall` |
| B017 | 远程模式下：`manage_firewall(action=deny\|delete, rule_spec 匹配 ssh_port 或 port 22)` | `manage_firewall` |

B015–B017 均需 `env_profile.remote_mode == True` 方才触发；本地模式降级为 WARN-HIGH。

---

## 六、新增 WARN-HIGH / WARN-LOW 规则

**WARN-HIGH 新增（WH007-WH012）：**

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WH007 | `write_file(mode=overwrite\|append)` 到任意现有文件 | `write_file` |
| WH008 | `delete_path(recursive=false)` 任意路径 | `delete_path` |
| WH009 | `manage_package(action=install\|remove\|update)` | `manage_package` |
| WH010 | `manage_firewall(action=allow\|deny\|delete\|set-default)` | `manage_firewall` |
| WH011 | `get_set_system_config(value=<任意>)` | `get_set_system_config` |
| WH012 | `copy_move_path(action=move)` 且目标已存在 | `copy_move_path` |

**WARN-LOW 新增（WL006-WL009）：**

| ID | 触发条件 | 说明 |
|---|---|---|
| WL006 | `read_file(path)` 匹配 `/etc/*`（非 BLOCK 路径） | 系统配置可能含内部信息 |
| WL007 | `write_file(mode=create_only)` 且目标路径**不在** PERSISTENCE_ENTRY_PATHS | 不在持久化入口则低风险 |
| WL008 | `copy_move_path(action=copy)` | 只读复制 |
| WL009 | `create_directory(path)` 在 `/` 或 `/etc` 下 | 系统目录下建子目录 |

**例外说明：** `write_file(mode=create_only)` 目标路径命中 PERSISTENCE_ENTRY_PATHS  
时，不适用 WL007，直接适用 WH007（WARN-HIGH），防止通过"新建文件"建立持久化入口。

---

## 七、Workflow Schema 扩展：input step 类型

v4.1 定义了三种 step 类型（`tool_call`、`confirm`、`display`）。  
v5 新增第四种：

```yaml
- id: 步骤ID
  type: input
  prompt: "提示文字（展示给用户）"
  param: variable_name        # 采集结果写入上下文变量，供后续步骤 {{variable_name}} 引用
  multiline: true             # 可选，默认 false；true 时允许多行输入（如文件内容）
  depends_on: [前序步骤ID]
```

`input` step 不调用任何工具，不经过 RiskClassifier，仅采集用户文本并写入 workflow 上下文。  
在 TUI 中呈现为全屏文本编辑区（`multiline=true`）或单行输入框（`multiline=false`）。

### 7.1 新增 Workflow 模板：file_edit.yaml

正确的执行顺序：**读取 → 采集新内容 → 预览 → 确认 → 写入 → 验证**

```yaml
name: 编辑配置文件
description: 安全地查看并修改指定配置文件，确认后才写入
parameters:
  - {name: file_path, type: text, required: true, description: 要编辑的文件路径}

steps:
  - id: s1
    type: tool_call
    tool: read_file
    args: {path: "{{file_path}}"}
    description: 读取当前文件内容

  - id: s2
    type: input
    prompt: "文件当前内容已显示。请输入替换后的完整内容（将完整覆盖原文件）："
    param: new_content
    multiline: true
    depends_on: [s1]

  - id: s3
    type: display
    depends_on: [s2]
    template: |
      预览：将写入以下内容至 {{file_path}}
      ───────────────────────────────
      {{new_content}}
      ───────────────────────────────
      此操作将完整覆盖原文件，不可自动还原。

  - id: s4
    type: confirm
    message: "确认以上预览内容无误，将覆盖写入 {{file_path}}？"
    depends_on: [s3]

  - id: s5
    type: tool_call
    tool: write_file
    args: {path: "{{file_path}}", content: "{{new_content}}", mode: overwrite}
    depends_on: [s4]

  - id: s6
    type: tool_call
    tool: read_file
    args: {path: "{{file_path}}"}
    description: 验证写入结果
    depends_on: [s5]

  - id: s7
    type: display
    source_step: s6
    template: "{{file_path}} 已更新。验证内容：\n{{s6.result}}"
    depends_on: [s6]
```

**确认顺序保证：** 用户在 s4（confirm）时看到的是 s3（display）展示的完整预览内容，  
即"将写入什么"，而非"准备改文件"。

---

## 八、潜在冲突点与规避方案

| 风险点 | 原因 | 规避 |
|---|---|---|
| 动态工具绕过静态安全规则 | 执行链独立，与 RiskClassifier 解耦 | 三层执行链：CS → StaticRuleMapper+RC → UNKNOWN confirm；任意层 BLOCK 硬拒绝，不可覆盖 |
| `propose_dynamic_tool` 绕过 BLOCK 对象 | Claude 可能对被 BLOCK 的操作改用提案 | SystemPrompt 限制指令 + validate_proposal CS001 在模板创建时即拦截；DynamicToolRegistry 执行时三层重检 |
| StaticRuleMapper 映射过度，误判语义 | 白名单过宽 | 映射表仅 ~10 个命令族，不做通用解析；映射结果视为"等价调用 hint"，RC 判定结果才是决策依据 |
| manage_firewall 远程锁门 | flush/default-deny/block-22 | B015-B017（静态工具）+ CS011-CS014（动态工具通路）双重覆盖 |
| write_file 持久化提权 | create_only 在 /etc/cron.d/ 等路径 | PERSISTENCE_ENTRY_PATHS 集合升级为 WARN-HIGH，不受 WL007 豁免 |
| dynamic_tools.json 并发写丢失 | 多工具提案同时进行 | filelock.FileLock + os.replace() 原子写入；实际场景中 TUI 串行处理提案弹窗，锁是防御性保护 |
| 动态工具上下文膨胀 | 工具数量增加 token 消耗 | 上限 20 个；SystemPrompt 只注入 name + description 摘要，不注入 cmd_template |
| `input` step 引入的 new_content 被模板注入 | 用户输入内容直接填入 write_file args | write_file 的 content 参数不经过 RiskClassifier 内容扫描（内容本身不是安全边界）；路径安全由 B011-B014 保证；write 操作本身触发 WH007 强制确认 |

---

## 九、开发优先级调整

在 v4.1 P0/P1 基础上新增：

```
P0 新增（核心通路，与 v4.1 P0 并行实现）
  security/command_safety.py（CommandSafetyChecker CS001-CS014）
  tools/file_ops.py（read_file, write_file, delete_path, create_directory, copy_move_path）
  tools/packages.py（manage_package + CapabilityProbe 联动）
  tools/monitoring.py（get_resource_stats）
  B011-B017 BLOCK 规则，WH007-WH012，WL006-WL009
  SENSITIVE_CREDENTIAL_PATHS / PERSISTENCE_ENTRY_PATHS 集合注册

P1 新增
  tools/dynamic_registry.py（DynamicToolRegistry + StaticRuleMapper + validate_proposal）
  ToolRegistry 集成动态工具
  ClaudeClient 新增 propose_dynamic_tool / dynamic tool 路由
  ui/dynamic_proposal.py（DynamicToolProposalModal）
  tools/firewall.py（manage_firewall + B015-B017）
  tools/system_config.py（get_set_system_config）
  Workflow Schema 扩展 input step type
  file_edit.yaml workflow
  AuditLog dynamic / semantic_unmapped 字段
  SystemPromptBuilder 动态工具注入

P2 新增
  动态工具清单管理（--list-dynamic-tools, --delete-dynamic-tool）
  DynamicTool 使用统计与安全审计报告
  file_edit.yaml diff 视图（s3 展示行级 diff 而非全量内容）
```

---

## 十、结论

v5 在 v4.1 五大核心能力之上，新增：

- **文件完整生命周期管理**（读/写/删/移动/目录），含凭证路径 BLOCK 和持久化入口保护
- **包管理与系统配置**（真正的"无命令"运维，含 update 风险对齐）
- **防火墙管理**，含远程模式 B015-B017 防锁门规则
- **资源监控**
- **动态工具创建**，三层执行链等效覆盖所有静态安全规则，有边界、可审计

关键承诺不变：**安全门不可绕过**。动态工具通路以独立三层链实现等效强度，  
任意层 BLOCK 均为硬拒绝，不提供用户覆盖入口。
