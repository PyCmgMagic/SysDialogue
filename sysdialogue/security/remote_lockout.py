"""RemoteLockoutChecker — 远程锁门检测（B010/B015-B017）。

静态工具通路和 DynTool 通路共用此模块作为唯一实现来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sysdialogue.security.path_policies import SSH_SERVICE_ALIASES

if TYPE_CHECKING:
    from sysdialogue.runtime.capability_probe import EnvProfile


@dataclass
class LockoutRisk:
    level: str  # "SAFE" | "WARN-HIGH" | "BLOCK"
    rule_ids: list[str] = field(default_factory=list)
    reason: str = ""


def assess_tool(tool: str, args: dict, env_profile: "EnvProfile") -> LockoutRisk:
    """供 RiskClassifier 调用，已结构化参数。"""
    if not env_profile.get("remote_mode", False):
        return LockoutRisk(level="SAFE")

    # B010: 远程模式下停止/禁用 SSH 或 systemd
    if tool == "manage_service":
        name = (args.get("name") or "").lower()
        action = (args.get("action") or "").lower()
        if action in ("stop", "disable"):
            if name in SSH_SERVICE_ALIASES or name == "systemd":
                return LockoutRisk(
                    level="BLOCK",
                    rule_ids=["B010"],
                    reason=f"远程模式下禁止 stop/disable SSH 或 systemd 服务（{name}），防止失联",
                )

    # B015: 远程模式下 flush 防火墙
    if tool == "manage_firewall":
        action = (args.get("action") or "").lower()
        if action == "flush":
            return LockoutRisk(
                level="BLOCK",
                rule_ids=["B015"],
                reason="远程模式下禁止 flush 防火墙规则，可能导致 SSH 连接失联",
            )

        # B016: set-default policy=drop|reject
        if action == "set-default":
            policy = (args.get("policy") or "").lower()
            if policy in ("drop", "reject"):
                return LockoutRisk(
                    level="BLOCK",
                    rule_ids=["B016"],
                    reason=f"远程模式下禁止将默认防火墙策略设为 {policy}，可能导致 SSH 失联",
                )

        # B017: deny SSH 端口或服务
        if action == "deny":
            target = args.get("target") or {}
            if isinstance(target, dict):
                port = target.get("port")
                service = (target.get("service") or "").lower()
                ssh_port = env_profile.get("ssh_port", 22)
                if (port is not None and int(port) == ssh_port) or service in SSH_SERVICE_ALIASES:
                    return LockoutRisk(
                        level="BLOCK",
                        rule_ids=["B017"],
                        reason="远程模式下禁止拒绝 SSH 端口/服务流量，防止失联",
                    )

        # WH023: 远程模式下 reload 防火墙
        if action == "reload":
            return LockoutRisk(
                level="WARN-HIGH",
                rule_ids=["WH023"],
                reason="远程模式下防火墙规则刷新可能因配置差异临时中断连接",
            )

    return LockoutRisk(level="SAFE")


def assess_cmd(cmd: list[str], env_profile: "EnvProfile") -> LockoutRisk:
    """供 CommandSafetyChecker 调用，原始 argv。"""
    if not cmd:
        return LockoutRisk(level="SAFE")

    base = cmd[0].split("/")[-1]  # 取可执行文件名

    if base in ("systemctl", "service"):
        # 尝试识别 service name 和 action
        args_mock: dict = {}
        if base == "systemctl" and len(cmd) >= 3:
            args_mock["action"] = cmd[1]
            args_mock["name"] = cmd[2]
        elif base == "service" and len(cmd) >= 3:
            args_mock["name"] = cmd[1]
            args_mock["action"] = cmd[2]
        return assess_tool("manage_service", args_mock, env_profile)

    if base in ("ufw", "firewall-cmd", "iptables"):
        args_mock = _parse_firewall_cmd(cmd)
        return assess_tool("manage_firewall", args_mock, env_profile)

    return LockoutRisk(level="SAFE")


def _parse_firewall_cmd(cmd: list[str]) -> dict:
    """简单解析防火墙命令为 manage_firewall args 结构。"""
    joined = " ".join(cmd)
    action = "unknown"
    if "flush" in joined:
        action = "flush"
    elif "--set-default" in joined or "default" in joined:
        action = "set-default"
        policy = "drop" if "drop" in joined else ("reject" if "reject" in joined else "")
        return {"action": action, "policy": policy}
    elif "--deny" in joined or "deny" in joined:
        action = "deny"
    elif "--reload" in joined or "reload" in joined:
        action = "reload"
    return {"action": action}
