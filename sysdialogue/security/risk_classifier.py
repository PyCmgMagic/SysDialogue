"""RiskClassifier — 对结构化工具调用判定风险等级。

覆盖规则：B001-B031 / WH001-WH025 / WL001-WL017
规则优先级：BLOCK > WARN-HIGH > WARN-LOW > SAFE
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sysdialogue.security import path_policies as pp
from sysdialogue.security.remote_lockout import assess_tool as lockout_assess

if TYPE_CHECKING:
    from sysdialogue.runtime.capability_probe import EnvProfile


@dataclass
class RiskDecision:
    level: str                        # SAFE | WARN-LOW | WARN-HIGH | BLOCK
    rule_ids: list[str] = field(default_factory=list)
    reason: str = ""
    requires_confirmation: bool = False
    rollback_hint: str = ""


_LEVEL_ORDER = {"SAFE": 0, "WARN-LOW": 1, "WARN-HIGH": 2, "BLOCK": 3}


def _max(a: RiskDecision, b: RiskDecision) -> RiskDecision:
    if _LEVEL_ORDER.get(b.level, 0) > _LEVEL_ORDER.get(a.level, 0):
        return b
    return a


def classify(
    tool: str,
    args: dict,
    env_profile: "EnvProfile | None" = None,
    session_counters: dict | None = None,
) -> RiskDecision:
    """判定工具调用风险。env_profile 可选，远程锁门检测需要。"""
    ep: EnvProfile = env_profile or {}
    fn = _CLASSIFIERS.get(tool)
    if fn is None:
        return RiskDecision(level="SAFE")
    result = fn(args, ep)
    result = _apply_network_probe_risk(tool, args, result, session_counters)
    # 远程锁门检测（对所有工具叠加）
    lockout = lockout_assess(tool, args, ep)
    if _LEVEL_ORDER.get(lockout.level, 0) > _LEVEL_ORDER.get(result.level, 0):
        result = RiskDecision(
            level=lockout.level,
            rule_ids=lockout.rule_ids,
            reason=lockout.reason,
            requires_confirmation=(lockout.level == "WARN-HIGH"),
        )
    if result.level in ("WARN-HIGH", "BLOCK"):
        result.requires_confirmation = (result.level == "WARN-HIGH")
    return result


def _apply_network_probe_risk(
    tool: str,
    args: dict,
    current: RiskDecision,
    session_counters: dict | None,
) -> RiskDecision:
    if tool not in ("resolve_dns", "check_endpoint"):
        return current

    hosts: list[str] = []
    if tool == "resolve_dns":
        hosts.extend([args.get("name", ""), args.get("resolver", "")])
    else:
        hosts.append(args.get("host", ""))

    subnet_counts = (session_counters or {}).get("private_probe_subnets", {})
    for host in hosts:
        subnet_key = pp.private_subnet_key(host)
        if not subnet_key:
            continue
        count = subnet_counts.get(subnet_key, 0) + 1
        if count > 10:
            return RiskDecision(
                level="WARN-HIGH",
                rule_ids=["WH025"],
                reason=f"同一私网段 {subnet_key} 在本次会话内探测超过 10 次",
                requires_confirmation=True,
            )
        return current
    return current


# --------------------------------------------------------------------------
# 工具分类器
# --------------------------------------------------------------------------

def _classify_get_disk_usage(args: dict, ep: EnvProfile) -> RiskDecision:
    path = args.get("path", "/")
    if pp.matches_v41_block(path):
        return RiskDecision(level="BLOCK", rule_ids=["B001"], reason=f"禁止访问敏感路径 {path}")
    if args.get("recursive"):
        return RiskDecision(level="WARN-LOW", rule_ids=["WL001"], reason="recursive 磁盘扫描")
    return RiskDecision(level="SAFE")


def _classify_find_files(args: dict, ep: EnvProfile) -> RiskDecision:
    path = args.get("search_path", "/")
    if pp.matches_v41_block(path):
        return RiskDecision(level="BLOCK", rule_ids=["B001"], reason=f"禁止检索敏感路径 {path}")
    if pp.matches_sensitive_dir(path):
        return RiskDecision(level="BLOCK", rule_ids=["B031"], reason=f"禁止枚举敏感凭证目录 {path}")
    max_depth = args.get("max_depth", 3)
    if path == "/" and max_depth > 5:
        return RiskDecision(level="BLOCK", rule_ids=["B006"], reason="根目录递归深度超过 5")
    if path == "/" and max_depth <= 5:
        return RiskDecision(level="WARN-LOW", rule_ids=["WL002"], reason="根目录检索")
    return RiskDecision(level="SAFE")


def _classify_list_processes(args: dict, ep: EnvProfile) -> RiskDecision:
    return RiskDecision(level="SAFE")


def _classify_kill_process(args: dict, ep: EnvProfile) -> RiskDecision:
    pid = args.get("pid")
    if pid == 1:
        return RiskDecision(level="BLOCK", rule_ids=["B002"], reason="禁止终止 PID 1（init/systemd）")
    current_user = ep.get("current_user", "")
    # 无法在此层判断进程属主，保守判定
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH001"],
        reason="终止进程可能影响服务稳定性，需确认",
        requires_confirmation=True,
    )


def _classify_get_port_status(args: dict, ep: EnvProfile) -> RiskDecision:
    return RiskDecision(level="SAFE")


def _classify_get_system_info(args: dict, ep: EnvProfile) -> RiskDecision:
    return RiskDecision(level="SAFE")


def _classify_get_network_info(args: dict, ep: EnvProfile) -> RiskDecision:
    return RiskDecision(level="SAFE")


def _classify_read_log(args: dict, ep: EnvProfile) -> RiskDecision:
    unit = args.get("unit")
    if unit is None:
        return RiskDecision(level="WARN-LOW", rule_ids=["WL005"], reason="读取全局日志")
    return RiskDecision(level="SAFE")


def _classify_create_user(args: dict, ep: EnvProfile) -> RiskDecision:
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH003"],
        reason="创建用户操作需确认", requires_confirmation=True,
    )


def _classify_delete_user(args: dict, ep: EnvProfile) -> RiskDecision:
    username = args.get("username", "")
    if username == "root":
        return RiskDecision(level="BLOCK", rule_ids=["B004"], reason="禁止删除 root 用户")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH002"],
        reason="删除用户操作需确认", requires_confirmation=True,
    )


def _classify_modify_user_groups(args: dict, ep: EnvProfile) -> RiskDecision:
    username = args.get("username", "")
    if username == "root":
        return RiskDecision(level="BLOCK", rule_ids=["B004"], reason="禁止修改 root 用户组")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH004"],
        reason="修改用户组操作需确认", requires_confirmation=True,
    )


def _classify_manage_service(args: dict, ep: EnvProfile) -> RiskDecision:
    name = (args.get("name") or "").lower()
    action = (args.get("action") or "").lower()

    if action in ("stop", "disable"):
        return RiskDecision(
            level="WARN-HIGH", rule_ids=["WH005"],
            reason=f"停止/禁用服务 {name} 可能影响系统可用性", requires_confirmation=True,
        )
    if action == "reload":
        if name in pp.CRITICAL_SERVICES:
            return RiskDecision(
                level="WARN-HIGH", rule_ids=["WH022"],
                reason=f"reload 关键服务 {name} 可能导致短暂不可用", requires_confirmation=True,
            )
        return RiskDecision(level="WARN-LOW", rule_ids=["WL004"], reason=f"reload 非关键服务 {name}")
    if action in ("start", "restart", "enable"):
        if name in pp.CRITICAL_SERVICES:
            return RiskDecision(
                level="WARN-HIGH", rule_ids=["WH006"],
                reason=f"操作关键服务 {name} 需确认", requires_confirmation=True,
            )
        return RiskDecision(level="WARN-LOW", rule_ids=["WL004"], reason=f"操作非关键服务 {name}")
    if action == "daemon-reload":
        return RiskDecision(level="WARN-LOW", reason="systemd daemon-reload")
    return RiskDecision(level="SAFE")


def _classify_read_file(args: dict, ep: EnvProfile) -> RiskDecision:
    path = args.get("path", "")
    if pp.has_path_traversal(path):
        return RiskDecision(level="BLOCK", rule_ids=["B005"], reason="路径包含 .. 组件")
    if pp.matches_sensitive_credential(path):
        return RiskDecision(level="BLOCK", rule_ids=["B011"], reason=f"禁止读取凭证文件 {path}")
    if path.startswith("/etc/"):
        return RiskDecision(level="WARN-LOW", rule_ids=["WL006"], reason="读取 /etc/ 下配置文件")
    return RiskDecision(level="SAFE")


def _classify_write_file(args: dict, ep: EnvProfile) -> RiskDecision:
    path = args.get("path", "")
    mode = args.get("mode", "overwrite")
    if pp.has_path_traversal(path):
        return RiskDecision(level="BLOCK", rule_ids=["B005"], reason="路径包含 .. 组件")
    if pp.matches_critical_edit(path):
        return RiskDecision(level="BLOCK", rule_ids=["B012"], reason=f"禁止覆盖关键系统文件 {path}")
    if mode == "create_only" and pp.matches_persistence_entry(path):
        return RiskDecision(
            level="WARN-HIGH", rule_ids=["WH007"],
            reason=f"在持久化入口目录创建文件 {path}", requires_confirmation=True,
        )
    if mode in ("overwrite", "append"):
        return RiskDecision(
            level="WARN-HIGH", rule_ids=["WH007"],
            reason=f"覆盖/追加写入文件 {path}", requires_confirmation=True,
        )
    return RiskDecision(level="WARN-LOW", rule_ids=["WL007"], reason="新建文件")


def _classify_delete_path(args: dict, ep: EnvProfile) -> RiskDecision:
    path = args.get("path", "")
    recursive = args.get("recursive", False)
    if pp.has_path_traversal(path):
        return RiskDecision(level="BLOCK", rule_ids=["B005"], reason="路径包含 .. 组件")
    if pp.matches_v41_block(path):
        return RiskDecision(level="BLOCK", rule_ids=["B014"], reason=f"禁止删除敏感路径 {path}")
    if recursive:
        for root_path in ["/", "/etc", "/usr", "/boot", "/lib", "/bin", "/sbin"]:
            if pp.normalize(path) == pp.normalize(root_path):
                return RiskDecision(level="BLOCK", rule_ids=["B013"], reason=f"禁止递归删除系统目录 {path}")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH008"],
        reason=f"删除路径 {path}", requires_confirmation=True,
    )


def _classify_create_directory(args: dict, ep: EnvProfile) -> RiskDecision:
    path = args.get("path", "")
    if pp.has_path_traversal(path):
        return RiskDecision(level="BLOCK", rule_ids=["B005"], reason="路径包含 .. 组件")
    p = pp.normalize(path)
    if p.startswith("/") or p.startswith(pp.normalize("/etc")):
        return RiskDecision(level="WARN-LOW", rule_ids=["WL009"], reason=f"在系统目录下创建目录 {path}")
    return RiskDecision(level="SAFE")


def _classify_copy_move_path(args: dict, ep: EnvProfile) -> RiskDecision:
    src = args.get("src", "")
    dst = args.get("dst", "")
    action = args.get("action", "copy")
    if pp.has_path_traversal(src) or pp.has_path_traversal(dst):
        return RiskDecision(level="BLOCK", rule_ids=["B005"], reason="路径包含 .. 组件")
    if action == "copy" and pp.matches_system_dir(dst):
        return RiskDecision(
            level="WARN-HIGH", rule_ids=["WH024"],
            reason=f"拷贝文件到系统目录 {dst}", requires_confirmation=True,
        )
    if action == "move":
        return RiskDecision(
            level="WARN-HIGH", rule_ids=["WH012"],
            reason=f"移动文件到 {dst}", requires_confirmation=True,
        )
    return RiskDecision(level="WARN-LOW", rule_ids=["WL008"], reason="拷贝文件")


def _classify_manage_package(args: dict, ep: EnvProfile) -> RiskDecision:
    action = args.get("action", "list")
    if action in ("list", "search"):
        return RiskDecision(level="SAFE")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH009"],
        reason=f"包管理操作 {action} 将修改系统软件", requires_confirmation=True,
    )


def _classify_get_resource_stats(args: dict, ep: EnvProfile) -> RiskDecision:
    return RiskDecision(level="SAFE")


def _classify_manage_firewall(args: dict, ep: EnvProfile) -> RiskDecision:
    """本地语义分级；远程锁门（B015/B016/B017/WH023）由 remote_lockout 统一叠加。"""
    action = (args.get("action") or "").lower()
    if action == "list":
        return RiskDecision(level="SAFE")
    if action == "reload":
        # 本地 reload WARN-LOW；远程模式下 remote_lockout 会升级为 WH023
        return RiskDecision(level="WARN-LOW", reason="防火墙配置 reload")
    if action in ("flush", "set-default", "allow", "deny", "delete"):
        return RiskDecision(
            level="WARN-HIGH", rule_ids=["WH010"],
            reason=f"防火墙规则变更 {action}", requires_confirmation=True,
        )
    return RiskDecision(level="SAFE")


def _classify_get_set_system_config(args: dict, ep: EnvProfile) -> RiskDecision:
    value = args.get("value")
    if value is None:
        return RiskDecision(level="SAFE")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH011"],
        reason="修改系统配置", requires_confirmation=True,
    )


def _classify_list_directory(args: dict, ep: EnvProfile) -> RiskDecision:
    path = args.get("path", ".")
    if pp.has_path_traversal(path):
        return RiskDecision(level="BLOCK", rule_ids=["B005"], reason="路径包含 .. 组件")
    if pp.matches_sensitive_dir(path):
        return RiskDecision(level="BLOCK", rule_ids=["B031"], reason=f"禁止枚举敏感凭证目录 {path}")
    recursive = args.get("recursive", False)
    max_depth = args.get("max_depth", 1)
    if recursive or max_depth > 2:
        return RiskDecision(level="WARN-LOW", rule_ids=["WL010"], reason="深层递归目录列表")
    return RiskDecision(level="SAFE")


def _classify_stat_path(args: dict, ep: EnvProfile) -> RiskDecision:
    if args.get("with_hash"):
        return RiskDecision(level="WARN-LOW", rule_ids=["WL011"], reason="计算文件哈希")
    return RiskDecision(level="SAFE")


def _classify_search_file_content(args: dict, ep: EnvProfile) -> RiskDecision:
    path = args.get("search_path", ".")
    if pp.has_path_traversal(path):
        return RiskDecision(level="BLOCK", rule_ids=["B005"], reason="路径包含 .. 组件")
    if pp.matches_sensitive_credential(path):
        return RiskDecision(level="BLOCK", rule_ids=["B025"], reason=f"禁止检索凭证文件 {path}")
    if pp.matches_v41_block(path):
        return RiskDecision(level="BLOCK", rule_ids=["B025"], reason=f"禁止检索敏感路径 {path}")
    if args.get("regex") or path.startswith("/etc/"):
        return RiskDecision(level="WARN-LOW", rule_ids=["WL012"], reason="正则/etc 内容检索")
    return RiskDecision(level="SAFE")


def _classify_backup_path(args: dict, ep: EnvProfile) -> RiskDecision:
    action = args.get("action", "list")
    path = args.get("path", "")
    if action in ("list",):
        return RiskDecision(level="SAFE")
    if action == "create":
        import os
        n = pp.normalize(path)
        if os.path.isdir(n):
            return RiskDecision(level="WARN-LOW", rule_ids=["WL013"], reason="备份目录")
        return RiskDecision(level="SAFE")
    if action in ("restore", "delete"):
        if pp.matches_critical_edit(path):
            return RiskDecision(level="BLOCK", rule_ids=["B019"], reason=f"禁止自动还原关键系统文件 {path}")
        return RiskDecision(
            level="WARN-HIGH", rule_ids=["WH013"],
            reason=f"备份 {action} 操作需确认", requires_confirmation=True,
        )
    return RiskDecision(level="SAFE")


def _classify_replace_in_file(args: dict, ep: EnvProfile) -> RiskDecision:
    path = args.get("path", "")
    if pp.has_path_traversal(path):
        return RiskDecision(level="BLOCK", rule_ids=["B005"], reason="路径包含 .. 组件")
    if pp.matches_critical_edit(path):
        return RiskDecision(level="BLOCK", rule_ids=["B018"], reason=f"禁止精准编辑关键系统文件 {path}")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH014"],
        reason=f"精准替换文件内容 {path}", requires_confirmation=True,
    )


def _classify_validate_config(args: dict, ep: EnvProfile) -> RiskDecision:
    return RiskDecision(level="SAFE")


def _classify_manage_cron(args: dict, ep: EnvProfile) -> RiskDecision:
    action = args.get("action", "list")
    if action == "list":
        return RiskDecision(level="SAFE")
    job_target = args.get("job_target") or {}
    # 递归判定 job_target 风险
    if isinstance(job_target, dict):
        kind = job_target.get("kind", "")
        target_name = job_target.get("name", "")
        target_args = job_target.get("args") or {}
        if kind == "tool":
            sub = classify(target_name, target_args, ep)
            if sub.level == "BLOCK":
                return RiskDecision(level="BLOCK", rule_ids=["B026"], reason=f"计划任务目标 {target_name} 为 BLOCK 级")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH015"],
        reason=f"计划任务 {action} 操作需确认", requires_confirmation=True,
    )


def _classify_manage_sysctl(args: dict, ep: EnvProfile) -> RiskDecision:
    action = args.get("action", "list")
    if action in ("list", "get"):
        return RiskDecision(level="SAFE")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH016"],
        reason="修改内核参数需确认", requires_confirmation=True,
    )


def _classify_resolve_dns(args: dict, ep: EnvProfile) -> RiskDecision:
    name = args.get("name", "")
    resolver = args.get("resolver", "")
    # WL016: 私网目标或私网 resolver
    if pp.is_private_host(name) or (resolver and pp.is_private_host(resolver)):
        return RiskDecision(
            level="WARN-LOW", rule_ids=["WL016"],
            reason="DNS 查询目标或 resolver 位于私网地址段",
        )
    return RiskDecision(level="SAFE")


def _classify_check_endpoint(args: dict, ep: EnvProfile) -> RiskDecision:
    kind = (args.get("kind") or "tcp").lower()
    host = args.get("host", "")
    timeout = args.get("timeout", 5)
    # WL016: 私网探测（优先级 > WL015）
    if pp.is_private_host(host):
        return RiskDecision(
            level="WARN-LOW", rule_ids=["WL016"],
            reason=f"探测目标 {host} 位于私网地址段",
        )
    if kind in ("http", "tls") and timeout > 10:
        return RiskDecision(level="WARN-LOW", rule_ids=["WL015"], reason="HTTP/TLS 探测超时较长")
    return RiskDecision(level="SAFE")


def _classify_manage_archive(args: dict, ep: EnvProfile) -> RiskDecision:
    action = args.get("action", "list")
    if action == "list":
        return RiskDecision(level="SAFE")
    if action == "create":
        return RiskDecision(level="WARN-LOW", rule_ids=["WL014"], reason="创建归档")
    if action == "extract":
        target = args.get("target_path", "")
        if pp.matches_archive_block(target):
            return RiskDecision(level="BLOCK", rule_ids=["B021"], reason=f"禁止解压到系统目录 {target}")
        return RiskDecision(
            level="WARN-HIGH", rule_ids=["WH017"],
            reason=f"解压归档到 {target}", requires_confirmation=True,
        )
    return RiskDecision(level="SAFE")


def _classify_manage_mount(args: dict, ep: EnvProfile) -> RiskDecision:
    action = args.get("action", "list")
    if action == "list":
        return RiskDecision(level="SAFE")
    target = args.get("target", "")
    if pp.matches_mount_block(target):
        return RiskDecision(level="BLOCK", rule_ids=["B020"], reason=f"禁止挂载/卸载系统关键目录 {target}")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH018"],
        reason=f"挂载操作 {action} 需确认", requires_confirmation=True,
    )


def _classify_manage_container(args: dict, ep: EnvProfile) -> RiskDecision:
    action = (args.get("action") or "list").lower()
    if action in ("list", "status", "logs", "inspect"):
        return RiskDecision(level="SAFE")
    if action == "run":
        if args.get("privileged"):
            return RiskDecision(level="BLOCK", rule_ids=["B029"], reason="禁止以 privileged 模式运行容器")
        if args.get("network_mode") == "host":
            return RiskDecision(level="BLOCK", rule_ids=["B030"], reason="禁止使用 host 网络模式运行容器")
        for vol in (args.get("volumes") or []):
            src = vol.get("source", "") if isinstance(vol, dict) else str(vol)
            if pp.matches_container_sensitive_bind(src):
                return RiskDecision(level="BLOCK", rule_ids=["B022"], reason=f"禁止挂载敏感目录 {src}")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH019"],
        reason=f"容器操作 {action} 需确认", requires_confirmation=True,
    )


def _classify_manage_authorized_keys(args: dict, ep: EnvProfile) -> RiskDecision:
    action = (args.get("action") or "list").lower()
    if action == "list":
        return RiskDecision(level="SAFE")
    username = args.get("username", "")
    public_key = args.get("public_key", "")
    # B028: root 账户
    if username == "root":
        return RiskDecision(level="BLOCK", rule_ids=["B028"], reason="禁止通过自动化通路修改 root 公钥")
    # B023: 非公钥格式 / 疑似私钥
    if public_key and not _is_public_key(public_key):
        return RiskDecision(level="BLOCK", rule_ids=["B023"], reason="输入内容不是有效公钥格式")
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH020"],
        reason=f"修改 SSH 授权公钥 {action} 需确认", requires_confirmation=True,
    )


def _is_public_key(s: str) -> bool:
    s = s.strip()
    if "PRIVATE" in s:
        return False
    prefixes = ("ssh-rsa", "ssh-ed25519", "ssh-ecdsa", "ecdsa-sha2", "sk-ssh-")
    return any(s.startswith(p) for p in prefixes)


def _classify_manage_power(args: dict, ep: EnvProfile) -> RiskDecision:
    return RiskDecision(
        level="WARN-HIGH", rule_ids=["WH021"],
        reason="重启/关机操作需确认", requires_confirmation=True,
    )


def _classify_manage_hosts_entries(args: dict, ep: EnvProfile) -> RiskDecision:
    action = (args.get("action") or "list").lower()
    if action == "list":
        return RiskDecision(level="SAFE")
    # B024: 修改受保护条目（hostname=localhost 或 ip=127.0.0.1/::1）
    hostname = args.get("hostname")
    ip_addrs = args.get("ip_addrs") or []
    if pp.matches_hosts_protected(hostname, ip_addrs):
        return RiskDecision(
            level="BLOCK", rule_ids=["B024"],
            reason="禁止修改 localhost / 127.0.0.1 / ::1 受保护 hosts 条目",
        )
    return RiskDecision(
        level="WARN-HIGH",
        reason=f"修改 /etc/hosts 条目 {action} 需确认", requires_confirmation=True,
    )


# --------------------------------------------------------------------------
# 分类器注册表
# --------------------------------------------------------------------------

_CLASSIFIERS = {
    "get_disk_usage": _classify_get_disk_usage,
    "find_files": _classify_find_files,
    "list_processes": _classify_list_processes,
    "kill_process": _classify_kill_process,
    "get_port_status": _classify_get_port_status,
    "get_system_info": _classify_get_system_info,
    "get_network_info": _classify_get_network_info,
    "read_log": _classify_read_log,
    "create_user": _classify_create_user,
    "delete_user": _classify_delete_user,
    "modify_user_groups": _classify_modify_user_groups,
    "manage_service": _classify_manage_service,
    "read_file": _classify_read_file,
    "write_file": _classify_write_file,
    "delete_path": _classify_delete_path,
    "create_directory": _classify_create_directory,
    "copy_move_path": _classify_copy_move_path,
    "manage_package": _classify_manage_package,
    "get_resource_stats": _classify_get_resource_stats,
    "manage_firewall": _classify_manage_firewall,
    "get_set_system_config": _classify_get_set_system_config,
    "list_directory": _classify_list_directory,
    "stat_path": _classify_stat_path,
    "search_file_content": _classify_search_file_content,
    "backup_path": _classify_backup_path,
    "replace_in_file": _classify_replace_in_file,
    "validate_config": _classify_validate_config,
    "manage_cron": _classify_manage_cron,
    "manage_sysctl": _classify_manage_sysctl,
    "resolve_dns": _classify_resolve_dns,
    "check_endpoint": _classify_check_endpoint,
    "manage_archive": _classify_manage_archive,
    "manage_mount": _classify_manage_mount,
    "manage_container": _classify_manage_container,
    "manage_authorized_keys": _classify_manage_authorized_keys,
    "manage_power": _classify_manage_power,
    "manage_hosts_entries": _classify_manage_hosts_entries,
}
