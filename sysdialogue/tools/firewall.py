"""工具: manage_firewall（结构化参数 → 后端命令翻译层）。"""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult

VALID_ACTIONS = {
    "list", "allow", "deny", "delete", "set-default", "flush", "reload",
}


def manage_firewall(
    executor: SafeExecutor,
    action: str,
    backend: str = "auto",
    target: dict | None = None,
    direction: str = "in",
    policy: str | None = None,
    env_profile: dict | None = None,
) -> ToolResult:
    """防火墙管理。"""
    if action not in VALID_ACTIONS:
        return ToolResult(success=False, error=f"无效 action: {action}")

    fw = _resolve_backend(backend, env_profile)
    if not fw:
        return ToolResult(success=False, error="无法确定防火墙后端，请指定 backend 参数")

    if fw == "ufw":
        return _ufw(executor, action, target, direction, policy)
    elif fw == "firewalld":
        return _firewalld(executor, action, target, direction, policy)
    elif fw == "iptables":
        return _iptables(executor, action, target, direction, policy)
    return ToolResult(success=False, error=f"不支持的后端：{fw}")


def _resolve_backend(backend: str, env_profile: dict | None) -> str | None:
    if backend != "auto":
        return backend
    if env_profile:
        fb = env_profile.get("firewall_backend", "none")
        return fb if fb != "none" else None
    return None


def _ufw(executor, action, target, direction, policy) -> ToolResult:
    if action == "list":
        cmd = ["ufw", "status", "verbose"]
    elif action == "allow":
        cmd = _ufw_rule("allow", target, direction)
    elif action == "deny":
        cmd = _ufw_rule("deny", target, direction)
    elif action == "delete":
        cmd = _ufw_rule("delete", target, direction)
    elif action == "set-default":
        cmd = ["ufw", "default", policy or "deny", direction]
    elif action == "flush":
        cmd = ["ufw", "--force", "reset"]
    elif action == "reload":
        cmd = ["ufw", "reload"]
    else:
        return ToolResult(success=False, error=f"ufw 不支持 {action}")

    out, code = executor.run(cmd, timeout=15)
    return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=[" ".join(cmd)])


def _ufw_rule(action: str, target: dict | None, direction: str) -> list[str]:
    cmd = ["ufw", action]
    if not target:
        return cmd
    if direction == "in":
        cmd.append("in")
    port = target.get("port")
    proto = target.get("protocol", "tcp")
    service = target.get("service")
    source_ip = target.get("source_ip")
    if source_ip:
        cmd += ["from", source_ip]
    if port:
        cmd += ["to", "any", "port", str(port), "proto", proto]
    elif service:
        cmd.append(service)
    return cmd


def _firewalld(executor, action, target, direction, policy) -> ToolResult:
    if action == "list":
        cmd = ["firewall-cmd", "--list-all"]
    elif action == "allow":
        cmd = _firewalld_rule("add", target)
    elif action == "deny":
        cmd = _firewalld_rule("remove", target)
    elif action == "delete":
        cmd = _firewalld_rule("remove", target)
    elif action == "set-default":
        cmd = ["firewall-cmd", f"--set-default-zone={policy or 'drop'}"]
    elif action == "flush":
        cmd = ["firewall-cmd", "--complete-reload"]
    elif action == "reload":
        cmd = ["firewall-cmd", "--reload"]
    else:
        return ToolResult(success=False, error=f"firewalld 不支持 {action}")

    out, code = executor.run(cmd, timeout=15)
    return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=[" ".join(cmd)])


def _firewalld_rule(op: str, target: dict | None) -> list[str]:
    cmd = ["firewall-cmd", "--permanent"]
    if not target:
        return cmd
    port = target.get("port")
    proto = target.get("protocol", "tcp")
    service = target.get("service")
    if port:
        flag = "--add-port" if op == "add" else "--remove-port"
        cmd.append(f"{flag}={port}/{proto}")
    elif service:
        flag = "--add-service" if op == "add" else "--remove-service"
        cmd.append(f"{flag}={service}")
    return cmd


def _iptables(executor, action, target, direction, policy) -> ToolResult:
    if action == "list":
        cmd = ["iptables", "-L", "-n", "--line-numbers"]
    elif action == "flush":
        cmd = ["iptables", "-F"]
    elif action == "reload":
        cmd = ["iptables-restore", "/etc/iptables/rules.v4"]
    elif action == "set-default":
        chain = "INPUT" if direction == "in" else "OUTPUT"
        pol = (policy or "DROP").upper()
        cmd = ["iptables", "-P", chain, pol]
    elif action in ("allow", "deny", "delete"):
        if action == "delete" and not target:
            return ToolResult(success=False, error="iptables delete 需要 target 参数")
        if action == "allow":
            op = "-A"
        elif action == "deny":
            op = "-I"
        else:
            op = "-D"
        chain = "INPUT" if direction == "in" else "OUTPUT"
        cmd = ["iptables", op, chain]
        if target:
            port = target.get("port")
            proto = target.get("protocol", "tcp")
            source = target.get("source_ip")
            if source:
                cmd += ["-s", source]
            if port:
                cmd += ["-p", proto, "--dport", str(port)]
            if action != "delete" and target.get("service") and not port:
                return ToolResult(success=False, error="iptables 规则变更需要明确 port 参数")
            if action == "delete":
                delete_policy = (policy or "drop").upper()
                jump = f"-j {'ACCEPT' if delete_policy == 'ACCEPT' else delete_policy}"
            else:
                jump = "-j ACCEPT" if action == "allow" else "-j DROP"
            cmd += jump.split()
    else:
        return ToolResult(success=False, error=f"iptables 不支持 {action}")

    out, code = executor.run(cmd, timeout=15)
    return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=[" ".join(cmd)])
