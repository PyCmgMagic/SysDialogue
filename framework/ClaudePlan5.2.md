# SysDialogue — v5.2 扩展设计文档

> 基于 v4.1，新增：扩充工具面（12 → 21，新增 9 个）+ 动态工具创建机制  
> 本文档只描述**相对 v4.1 的增量变更**，原有设计不变  
> v5.2 相对 v5.1 的修订：manage_firewall 结构化参数、B017 语义收紧、  
> RemoteLockoutChecker 共享判定器、已知防锁门盲区文档化

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
| 20 | `manage_firewall` | 防火墙 | `backend`, `action`, `target{port,service,protocol,source_ip}`, `direction`, `policy` | SAFE(list) / WARN-HIGH(allow/deny/delete/set-default) / BLOCK(flush 或 远程锁门) |
| 21 | `get_set_system_config` | 系统配置 | `key: hostname\|timezone\|locale`, `value(可选)` | SAFE(get) / WARN-HIGH(set) |

### 2.2 关键设计约束（防止与 v4.1 冲突）

**`read_file` — 为什么风险是 WARN-LOW 而非 SAFE？**

读取系统敏感文件（凭证文件、私钥）可能泄漏高价值信息，需 BLOCK 拦截。

**`write_file` — mode 与风险的关系**

覆盖 nginx.conf 等配置立即生效，错误内容可使服务中断，`overwrite/append` 始终 WARN-HIGH。  
`create_only` 仅在目标不存在时写入，但落在持久化入口路径（见下）时同样升级为 WARN-HIGH。

**`manage_package(action=update)` — 不比 install/remove 安全**

包升级常伴随服务重启、ABI 变化、配置迁移，一律列为 WARN-HIGH。

**`manage_firewall` — 结构化参数，废弃自由文本 rule_spec**

v5.1 的自由文本 `rule_spec` 导致 B016/B017 和 StaticRuleMapper 均需字符串解析，  
不同后端语法差异大，误判/漏判概率高。v5.2 改为结构化参数，schema 层直接表达语义：

```json
{
  "name": "manage_firewall",
  "input_schema": {
    "type": "object",
    "properties": {
      "backend":   {"type": "string", "enum": ["auto","ufw","firewalld","iptables"]},
      "action":    {"type": "string", "enum": ["list","allow","deny","delete","set-default","flush"]},
      "target": {
        "type": "object",
        "properties": {
          "port":      {"type": "integer", "minimum": 1, "maximum": 65535},
          "service":   {"type": "string", "description": "命名服务，如 ssh/http/https"},
          "protocol":  {"type": "string", "enum": ["tcp","udp","all"], "default": "tcp"},
          "source_ip": {"type": "string", "description": "来源 IP 或 CIDR，如 192.168.1.0/24"}
        }
      },
      "direction": {"type": "string", "enum": ["in","out","forward"], "default": "in"},
      "policy":    {"type": "string", "enum": ["accept","drop","reject"],
                    "description": "仅 action=set-default 时使用"}
    },
    "required": ["action"]
  }
}
```

实现层（`firewall.py`）负责将结构化参数翻译为各后端具体命令，规则引擎不再解析命令字符串。

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
                            # 本地模式时探测 ss -tlnp / /proc/net/tcp（不读 sshd_config）

    # available_cmds（v4.1 已有）扩展 key：ufw, firewalld, iptables
```

**ssh_port 来源优先级：**

1. `RemoteExecutor.__init__(port=...)` 中的实际连接端口（最可靠）
2. 本地模式：扫描 `ss -tlnp` 或 `/proc/net/tcp` 中监听的 SSH 相关端口
3. 上述均失败时，默认 22

不使用 `/etc/ssh/sshd_config`：该文件为 v4.1 B009 敏感对象，且配置值与实际监听端口可能不一致。

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
      "estimated_risk": {"type": "string", "enum": ["WARN-LOW", "WARN-HIGH", "UNKNOWN"]},
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

此元工具与 `set_execution_mode` 一样不经过 RiskClassifier，但提案创建时  
`validate_proposal()` 会对模板本身运行 CommandSafetyChecker。

### 3.3 DynamicTool 数据结构

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

### 3.4 RemoteLockoutChecker（新增共享判定器）

新模块 `security/remote_lockout.py`，是 B015-B017 和 CS010-CS014 的**唯一实现来源**。  
两条执行路径（静态工具 / 动态工具）均调用此模块，消除双轨逻辑漂移。

```python
@dataclass
class LockoutRisk:
    level: str              # "BLOCK" | "WARN-HIGH" | "SAFE"
    rule_id: str            # "B015" / "B016" / "B017" / "CS010" 等
    explanation: str        # 中文解释
    self_lockout_warning: bool  # 远程模式下任何非 list 操作均为 True

def assess_tool(tool: str, args: dict, env_profile: EnvProfile) -> LockoutRisk:
    """供 RiskClassifier 调用，参数已结构化（B015-B017 路径）"""
    if not env_profile.remote_mode:
        return LockoutRisk("SAFE", "", "", False)  # 本地模式不判定锁门

    if tool == "manage_firewall":
        action = args.get("action")
        if action == "flush":
            return LockoutRisk("BLOCK", "B015", "远程模式下清空防火墙规则将切断所有连接", True)
        if action == "set-default" and args.get("policy") in ("drop", "reject"):
            return LockoutRisk("BLOCK", "B016", "远程模式下将默认策略改为拒绝将切断所有连接", True)
        if action == "deny":
            t = args.get("target", {})
            if _matches_ssh(t, env_profile.ssh_port):
                return LockoutRisk("BLOCK", "B017", "远程模式下封锁 SSH 端口将切断当前连接", True)
        # 其他非 list 操作：不 BLOCK，但追加自锁警示
        if action != "list":
            return LockoutRisk("SAFE", "", "", self_lockout_warning=True)

    if tool == "manage_service":
        name = args.get("name", "")
        action = args.get("action", "")
        if action in ("stop", "disable") and _is_ssh_service(name):
            return LockoutRisk("BLOCK", "B010", "远程模式下停止/禁用 SSH 服务将切断当前连接", True)

    return LockoutRisk("SAFE", "", "", False)

def assess_cmd(cmd: list[str], env_profile: EnvProfile) -> LockoutRisk:
    """供 CommandSafetyChecker 调用，命令为原始 argv（CS010-CS014 路径）"""
    # 先尝试通过 StaticRuleMapper 转换为结构化参数，复用 assess_tool 逻辑
    mapped = StaticRuleMapper.map(cmd)
    if mapped and mapped.tool in ("manage_service", "manage_firewall"):
        return assess_tool(mapped.tool, mapped.args, env_profile)
    # 映射失败时对已知危险模式做轻量模式匹配（仅覆盖明确的锁门命令）
    return _check_raw_lockout_patterns(cmd, env_profile)

def _check_raw_lockout_patterns(cmd: list[str], env_profile: EnvProfile) -> LockoutRisk:
    """映射失败时的兜底：只匹配最明确的远程锁门命令形态"""
    if not env_profile.remote_mode:
        return LockoutRisk("SAFE", "", "", False)
    flat = " ".join(cmd)
    # iptables -F / ufw --flush / firewall-cmd --panic-on
    if re.search(r'\biptables\b.*\s-F\b', flat) or \
       re.search(r'\bufw\b.*--flush\b', flat) or \
       re.search(r'\bfirewall-cmd\b.*--panic-on\b', flat):
        return LockoutRisk("BLOCK", "CS011", "清空防火墙规则（远程模式）", True)
    # ufw default deny / firewall-cmd --set-default-policy=DROP
    if re.search(r'\bufw\b\s+default\s+(deny|reject)\b', flat) or \
       re.search(r'--set-default-policy=(drop|reject)', flat, re.I):
        return LockoutRisk("BLOCK", "CS013", "将默认策略改为拒绝（远程模式）", True)
    # 含 ssh_port 的 DROP/REJECT
    port_str = str(env_profile.ssh_port)
    if re.search(rf'--dport\s+{port_str}\b.*\b(DROP|REJECT)\b', flat) or \
       re.search(rf'\bufw\b\s+deny\s+(ssh|{port_str})\b', flat):
        return LockoutRisk("BLOCK", "CS012", f"封锁 SSH 端口 {port_str}（远程模式）", True)
    return LockoutRisk("SAFE", "", "", False)
```

**与现有模块的集成：**

- `RiskClassifier.classify()` 在处理 `manage_firewall` / `manage_service` 之前，  
  调用 `RemoteLockoutChecker.assess_tool()`，若返回 BLOCK 则直接拒绝。
- `CommandSafetyChecker.check_command()` 在 CS010-CS014 位置调用  
  `RemoteLockoutChecker.assess_cmd()`，结果合并进 SafetyCheckResult。
- B015-B017 和 CS010-CS014 **不再各自维护独立匹配逻辑**，均委托此模块。

### 3.5 CommandSafetyChecker

模块 `security/command_safety.py`，CS010-CS014 委托 RemoteLockoutChecker：

```python
def check_command(cmd: list[str], env_profile: EnvProfile) -> SafetyCheckResult:
    flags, explanations = [], []

    # CS001-CS009：命令形态检查（不依赖 env_profile）
    _check_shell_metachar(cmd, flags, explanations)      # CS001
    _check_path_traversal(cmd, flags, explanations)      # CS002
    _check_dangerous_rm(cmd, flags, explanations)        # CS003
    _check_destructive_cmds(cmd, flags, explanations)    # CS004
    _check_chmod_777(cmd, flags, explanations)           # CS005
    _check_chown_root(cmd, flags, explanations)          # CS006
    _check_credential_paths(cmd, flags, explanations)    # CS007
    _check_arg_length(cmd, flags, explanations)          # CS008
    _check_curl_wget(cmd, flags, explanations)           # CS009

    # CS010-CS014：委托 RemoteLockoutChecker
    lockout = RemoteLockoutChecker.assess_cmd(cmd, env_profile)
    if lockout.level != "SAFE":
        flags.append(lockout.rule_id)
        explanations.append(lockout.explanation)

    escalated = _max_risk(flags)
    return SafetyCheckResult(
        safe=(escalated in ("SAFE", "WARN-LOW")),
        flags=flags,
        explanation="；".join(explanations),
        escalated_risk=escalated,
        self_lockout_warning=lockout.self_lockout_warning,
    )
```

**CS 规则表（CS001-CS009，CS010-CS014 见 RemoteLockoutChecker）：**

| ID | 检查项 | 升级到 |
|---|---|---|
| CS001 | argv 中含 shell 元字符（`&&`, `\|`, `;`, `$(`, `` ` ``） | BLOCK |
| CS002 | cmd[0] 或任意 arg 含路径穿越（`..`） | BLOCK |
| CS003 | 命令为 `rm`/`rmdir` + `-rf`/`-r` + 系统目录路径 | BLOCK |
| CS004 | 命令为 `mkfs`、`dd`、`shred` | BLOCK |
| CS005 | 命令为 `chmod` + 模式 `777`/`000` + 系统目录路径 | WARN-HIGH |
| CS006 | 命令为 `chown` + 目标用户为 `root` | WARN-HIGH |
| CS007 | 命令涉及 SENSITIVE_CREDENTIAL_PATHS 路径 | WARN-HIGH |
| CS008 | 任意 arg 超过 8192 字符 | WARN-HIGH |
| CS009 | 命令为 `curl`/`wget` 且 args 中含等效管道/输出执行模式 | BLOCK |
| CS010-CS014 | 远程锁门（委托 RemoteLockoutChecker.assess_cmd） | BLOCK / WARN-HIGH |

### 3.6 DynamicToolRegistry：三层执行链

```python
class DynamicToolRegistry:
    def execute(self, tool_name: str, args: dict,
                executor: ExecutorAdapter,
                env_profile: EnvProfile,
                confirm_fn: ConfirmCallback) -> ToolResult:

        tool = self._find(tool_name)
        cmd = self._render(tool.cmd_template, args)

        # ── 第一层：CommandSafetyChecker（命令形态 + 远程锁门） ───────────
        safety = check_command(cmd, env_profile)
        if safety.escalated_risk == "BLOCK":
            self._audit(tool, cmd, blocked=True, safety=safety)
            return ToolResult(blocked=True, reason=safety.explanation)

        # ── 第二层：StaticRuleMapper + RiskClassifier（对象语义） ─────────
        mapped = StaticRuleMapper.map(cmd)
        if mapped:
            rc_result = RiskClassifier.classify(mapped.tool, mapped.args)
            if rc_result.level == "BLOCK":
                self._audit(tool, cmd, blocked=True, rc=rc_result, mapped=mapped)
                return ToolResult(blocked=True, reason=rc_result.reason)
            semantic_unmapped = False
        else:
            rc_result = None
            semantic_unmapped = True

        # ── 第三层：UNKNOWN 确认（始终触发） ─────────────────────────────
        user_ok = confirm_fn(tool, cmd, safety, rc_result)
        if not user_ok:
            self._audit(tool, cmd, decision="user_cancelled")
            return ToolResult(cancelled=True)

        output, exit_code = executor.run(cmd, timeout=30)
        tool.usage_count += 1
        if not safety.safe:
            tool.safety_overrides += 1
        self._audit(tool, cmd, safety=safety, rc=rc_result,
                    semantic_unmapped=semantic_unmapped)
        return ToolResult(output=output, exit_code=exit_code)
```

**StaticRuleMapper — 有限白名单，防火墙条目产出结构化 target：**

```python
WHITELIST_MAPPINGS = [
    (["systemctl", ACT, SVC],       → manage_service(name=SVC, action=ACT)),
    (["service",   SVC, ACT],       → manage_service(name=SVC, action=ACT)),
    (["apt",    "install", PKG],    → manage_package(name=PKG, action="install", manager="apt")),
    (["apt-get","install", PKG],    → manage_package(name=PKG, action="install", manager="apt")),
    (["yum",    "install", PKG],    → manage_package(name=PKG, action="install", manager="yum")),
    (["dnf",    "install", PKG],    → manage_package(name=PKG, action="install", manager="dnf")),
    (["apt",    "remove",  PKG],    → manage_package(name=PKG, action="remove",  manager="apt")),

    # 防火墙条目：产出结构化 target，而非 rule_spec 字符串
    (["ufw", "allow",  PORT_OR_SVC], → manage_firewall(backend="ufw", action="allow",
                                        target=_parse_ufw_target(a[2]))),
    (["ufw", "deny",   PORT_OR_SVC], → manage_firewall(backend="ufw", action="deny",
                                        target=_parse_ufw_target(a[2]))),
    (["ufw", "default", POLICY],     → manage_firewall(backend="ufw", action="set-default",
                                        policy=_map_policy(a[2]))),
    (["iptables", ...],              → manage_firewall(backend="iptables", action=_map_ipt_action(a),
                                        target=_parse_ipt_target(a),
                                        direction=_parse_ipt_dir(a))),
    (["firewall-cmd", ...],          → manage_firewall(backend="firewalld", action=_map_fcmd_action(a),
                                        target=_parse_fcmd_target(a))),
]
# _parse_ufw_target("22/tcp") → {port:22, protocol:"tcp"}
# _parse_ufw_target("ssh")    → {service:"ssh"}
# 未在此表中的命令：semantic_unmapped=True
```

映射失败后，RemoteLockoutChecker.assess_cmd() 的 `_check_raw_lockout_patterns()`  
仍对已知危险模式做轻量正则兜底，确保映射盲区不形成安全漏洞。

### 3.7 validate_proposal()

在 `DynamicToolProposalModal` 接受用户 `approve` 之前运行：

```
1. cmd_template 中所有 {param} 占位符均存在于 params 定义（正向检查）
2. params 定义中无多余占位符（反向检查）
3. proposed_tool_name 不与任何静态工具名完全匹配
4. proposed_tool_name 不是保留名（propose_dynamic_tool, set_execution_mode 等）
5. 每个 cmd_template 元素 ≤ 256 字符
6. cmd_template 总元素数 ≤ 10
7. 对 cmd_template（占位符未渲染）运行 CommandSafetyChecker，BLOCK 时拒绝
8. 已注册动态工具数量 < 20
```

### 3.8 持久化与并发安全

```
文件路径：~/.sysdialogue/dynamic_tools.json，创建时 os.chmod(path, 0o600)
写入策略：write → .tmp → os.replace()（原子替换）
并发保护：filelock.FileLock(path + ".lock") 保护所有写操作
上限：    20 个工具
```

### 3.9 与现有模块的集成

**ClaudeClient**

```python
if first_call.name == "set_execution_mode":
    ...
elif first_call.name == "propose_dynamic_tool":
    dynamic_engine.handle_proposal(first_call.input)
elif dynamic_registry.is_dynamic(first_call.name):
    result = dynamic_registry.execute(
        first_call.name, first_call.input, executor, env_profile, confirm_fn)
else:
    # 原有静态工具流程
    ...
```

**RiskClassifier**

不修改核心逻辑。在处理 `manage_firewall` 和 `manage_service` 时，  
新增对 `RemoteLockoutChecker.assess_tool()` 的调用：

```python
# RiskClassifier.classify() 内，在现有规则之前插入
lockout = RemoteLockoutChecker.assess_tool(tool_name, args, env_profile)
if lockout.level == "BLOCK":
    return RiskResult("BLOCK", lockout.rule_id, lockout.explanation)
if lockout.self_lockout_warning:
    decision_trace.append(f"lockout_warning({lockout.rule_id or 'remote_firewall'})")
# 继续现有规则判定 ...
```

**AuditLog（新增字段）**

```json
{
  "dynamic": true,
  "dynamic_tool_id": "dyn_a1b2c3d4",
  "semantic_unmapped": false,
  "mapped_to": "manage_firewall",
  "safety_check": {
    "safe": true,
    "flags": [],
    "escalated_risk": "SAFE",
    "self_lockout_warning": true
  },
  "risk_classifier_result": "WARN-HIGH",
  "rule_id": "WH010",
  "decision_trace": ["lockout_warning(remote_firewall)", "user_confirmed"]
}
```

`semantic_unmapped=true` 时，`mapped_to` 和 `risk_classifier_result` 字段缺省。

**TUI**

`DynamicToolProposalModal`：展示提案，键入 `approve`/`reject`。  
`ConfirmModal` 在 `self_lockout_warning=true` 时追加醒目的自锁风险提示文字。

---

## 四、新模块文件清单

```
sysdialogue/
├── security/
│   ├── remote_lockout.py     ← 新增：RemoteLockoutChecker（B015-B017 / CS010-CS014 共享）
│   ├── command_safety.py     ← 新增：CommandSafetyChecker CS001-CS009 + 委托 CS010-CS014
│   └── ...（原有不变）
├── tools/
│   ├── dynamic_registry.py   ← 新增：DynamicToolRegistry + StaticRuleMapper + validate_proposal
│   ├── file_ops.py           ← 新增：read_file / write_file / delete_path / create_directory / copy_move_path
│   ├── packages.py           ← 新增：manage_package
│   ├── monitoring.py         ← 新增：get_resource_stats
│   ├── firewall.py           ← 新增：manage_firewall（结构化参数 → 后端命令翻译）
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

| ID | 触发条件 | 适用工具 | 实现位置 |
|---|---|---|---|
| B011 | `read_file(path)` 匹配 SENSITIVE_CREDENTIAL_PATHS | `read_file` | RiskClassifier |
| B012 | `write_file(path)` 目标为系统关键文件（passwd/shadow/sudoers/boot/systemd/sshd_config） | `write_file` | RiskClassifier |
| B013 | `delete_path(path, recursive=true)` 目标为 `/`、`/etc`、`/usr`、`/boot`、`/lib`、`/bin`、`/sbin` | `delete_path` | RiskClassifier |
| B014 | `delete_path(path)` 路径匹配 B001 路径集合 | `delete_path` | RiskClassifier |
| B015 | 远程模式下：`manage_firewall(action=flush)` | `manage_firewall` | RemoteLockoutChecker |
| B016 | 远程模式下：`manage_firewall(action=set-default, policy=drop\|reject)` | `manage_firewall` | RemoteLockoutChecker |
| B017 | 远程模式下：`manage_firewall(action=deny, target.port=ssh_port 或 target.service="ssh"\|"sshd")` | `manage_firewall` | RemoteLockoutChecker |

**B017 语义说明：** 仅 `action=deny` 触发 BLOCK。`action=delete` 指向 ssh_port 的规则为  
WARN-HIGH（由 WH010 覆盖）——删除规则的效果取决于被删规则的方向，不能在 schema 层确定，  
强制 BLOCK 会误拦"删除现有拒绝规则（即恢复访问）"的合法操作。

**已知防锁门盲区（文档化限制）：**

以下场景当前无法在规则层自动检测，由 `self_lockout_warning=true` 在 TUI 提示用户自行排查：

- 基于来源 IP 的封禁（`source_ip` 字段匹配当前客户端 IP 需运行时感知）
- IPv6 地址段封禁（EnvProfile 当前不跟踪 IPv6 连接地址）
- firewalld zone 整体策略变更（zone 语义复杂，未完整映射）
- 动态工具中 StaticRuleMapper 映射失败的防火墙命令（兜底正则仅覆盖最明确模式）

B015-B017 均需 `env_profile.remote_mode == True` 方才触发；本地模式降级为 WARN-HIGH。

---

## 六、新增 WARN-HIGH / WARN-LOW 规则

**WARN-HIGH 新增（WH007-WH012）：**

| ID | 触发条件 | 适用工具 |
|---|---|---|
| WH007 | `write_file(mode=overwrite\|append)` 到任意现有文件；或 `create_only` 命中 PERSISTENCE_ENTRY_PATHS | `write_file` |
| WH008 | `delete_path(recursive=false)` 任意路径 | `delete_path` |
| WH009 | `manage_package(action=install\|remove\|update)` | `manage_package` |
| WH010 | `manage_firewall(action=allow\|deny\|delete\|set-default)` | `manage_firewall` |
| WH011 | `get_set_system_config(value=<任意>)` | `get_set_system_config` |
| WH012 | `copy_move_path(action=move)` 且目标已存在 | `copy_move_path` |

WH010 说明：`action=delete` 命中 ssh_port 的情形由此规则覆盖为 WARN-HIGH（不 BLOCK），  
与 B017 仅拦截主动 `deny` 操作的语义保持一致。

**WARN-LOW 新增（WL006-WL009）：**

| ID | 触发条件 | 说明 |
|---|---|---|
| WL006 | `read_file(path)` 匹配 `/etc/*`（非 BLOCK 路径） | 系统配置可能含内部信息 |
| WL007 | `write_file(mode=create_only)` 且目标路径**不在** PERSISTENCE_ENTRY_PATHS | 见 WH007 例外 |
| WL008 | `copy_move_path(action=copy)` | 只读复制 |
| WL009 | `create_directory(path)` 在 `/` 或 `/etc` 下 | 系统目录下建子目录 |

---

## 七、Workflow Schema 扩展：input step 类型

v4.1 定义了三种 step 类型（`tool_call`、`confirm`、`display`）。v5 新增第四种：

```yaml
- id: 步骤ID
  type: input
  prompt: "提示文字"
  param: variable_name   # 采集结果写入上下文变量，供后续步骤 {{variable_name}} 引用
  multiline: true        # 可选，默认 false；true 时 TUI 呈现全屏编辑区
  depends_on: [前序步骤ID]
```

`input` step 不调用工具，不经过 RiskClassifier，仅采集用户文本写入 workflow 上下文。

### 7.1 新增 Workflow 模板：file_edit.yaml

执行顺序：**读取 → 采集新内容 → 预览 → 确认 → 写入 → 验证**

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

---

## 八、潜在冲突点与规避方案

| 风险点 | 原因 | 规避 |
|---|---|---|
| 动态工具绕过静态安全规则 | 执行链独立 | 三层链：CS → StaticRuleMapper+RC → UNKNOWN confirm；任意层 BLOCK 硬拒绝 |
| `propose_dynamic_tool` 绕过 BLOCK 对象 | Claude 可能对 BLOCK 操作改用提案 | SystemPrompt 限制指令 + validate_proposal CS001 拦截危险模板；执行时三层重检 |
| B015-B017 与 CS011-CS014 逻辑漂移 | 双轨实现 | 统一委托 RemoteLockoutChecker，B/CS 规则均为此模块的调用别名 |
| manage_firewall 结构化参数后端翻译错误 | firewall.py 实现 | 单元测试覆盖 ufw/firewalld/iptables 各后端的参数→命令翻译，与 RemoteLockoutChecker 测试独立 |
| StaticRuleMapper 防火墙条目解析失败 | ufw/iptables 语法变体多 | 映射失败时 semantic_unmapped=True + UNKNOWN confirm；RemoteLockoutChecker 兜底正则覆盖最危险模式 |
| write_file 持久化提权 | create_only 在 /etc/cron.d/ 等路径 | PERSISTENCE_ENTRY_PATHS 集合升级为 WARN-HIGH，不受 WL007 豁免 |
| dynamic_tools.json 并发写丢失 | 多提案并发 | filelock + os.replace() 原子写 |
| 动态工具上下文膨胀 | 工具数量增加 token | 上限 20 个；SystemPrompt 只注入 name + description 摘要 |

---

## 九、开发优先级调整

在 v4.1 P0/P1 基础上新增：

```
P0 新增
  security/remote_lockout.py（RemoteLockoutChecker，B015-B017 / CS010-CS014 共享）
  security/command_safety.py（CS001-CS009 + 委托 CS010-CS014）
  tools/file_ops.py（read_file, write_file, delete_path, create_directory, copy_move_path）
  tools/packages.py（manage_package + CapabilityProbe 联动）
  tools/monitoring.py（get_resource_stats）
  B011-B017 BLOCK 规则，WH007-WH012，WL006-WL009
  SENSITIVE_CREDENTIAL_PATHS / PERSISTENCE_ENTRY_PATHS 集合注册

P1 新增
  tools/firewall.py（manage_firewall 结构化参数 + 后端翻译层）
  tools/dynamic_registry.py（DynamicToolRegistry + StaticRuleMapper + validate_proposal）
  ToolRegistry 集成动态工具
  ClaudeClient 新增 propose_dynamic_tool / dynamic tool 路由
  ui/dynamic_proposal.py（DynamicToolProposalModal）
  tools/system_config.py（get_set_system_config）
  Workflow Schema 扩展 input step type
  file_edit.yaml workflow
  AuditLog dynamic / semantic_unmapped / self_lockout_warning 字段
  SystemPromptBuilder 动态工具注入

P2 新增
  动态工具清单管理（--list-dynamic-tools, --delete-dynamic-tool）
  DynamicTool 使用统计与安全审计报告
  file_edit.yaml diff 视图（s3 展示行级 diff 而非全量内容）
```

---

## 十、结论

v5.2 在 v5.1 基础上收紧四个设计缺口：

- **manage_firewall 结构化参数**：废弃自由文本 `rule_spec`，规则引擎操作语义字段而非解析字符串
- **B017 语义收紧**：仅拦截主动 `deny` SSH 端口，`delete` 降为 WARN-HIGH，避免误拦恢复访问操作
- **RemoteLockoutChecker 共享判定器**：B015-B017 与 CS010-CS014 统一实现，消除双轨漂移
- **已知盲区文档化**：IPv6/来源 IP 等无法静态判定的场景以 `self_lockout_warning` 提示用户，  
  而非假装已完整覆盖

关键承诺不变：**安全门不可绕过**。动态工具通路三层链任意层 BLOCK 均为硬拒绝，不提供用户覆盖入口。
