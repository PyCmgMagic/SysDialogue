"""工具: resolve_dns, check_endpoint."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),    # 链路本地
    ipaddress.ip_network("fc00::/7"),           # IPv6 ULA
    ipaddress.ip_network("127.0.0.0/8"),        # loopback（非健康检查豁免场景）
]
_LOCALHOST_WHITELIST = {"localhost", "127.0.0.1", "::1"}


def _is_private_ip(host: str) -> bool:
    """检测是否为私网/链路本地地址（localhost 白名单除外）。"""
    if host in _LOCALHOST_WHITELIST:
        return False
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(host))
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except Exception:
        return False


def _private_subnet_key(host: str) -> str | None:
    if not host or host in _LOCALHOST_WHITELIST:
        return None
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(host))
    except Exception:
        return None
    if not any(addr in net for net in _PRIVATE_NETWORKS):
        return None
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{addr}/24", strict=False))
    return str(ipaddress.ip_network(f"{addr}/64", strict=False))


def _track_private_probe(_session_counters: dict | None, *hosts: str) -> None:
    if _session_counters is None:
        return
    subnet_counts = _session_counters.setdefault("private_probe_subnets", {})
    for host in hosts:
        subnet_key = _private_subnet_key(host)
        if subnet_key:
            subnet_counts[subnet_key] = subnet_counts.get(subnet_key, 0) + 1


def resolve_dns(
    executor: SafeExecutor,
    name: str,
    record_type: str = "A",
    resolver: str | None = None,
    _session_counters: dict | None = None,
) -> ToolResult:
    """DNS 解析。"""
    traces: list[str] = []

    # WL017: 超频检测
    if _session_counters is not None:
        cnt = _session_counters.get("resolve_dns", 0) + 1
        _session_counters["resolve_dns"] = cnt
        if cnt > 40:
            return ToolResult(success=False, error="单次会话 DNS 解析超过 40 次（WL017），已拒绝")
    _track_private_probe(_session_counters, name, resolver or "")

    # 尝试 dig → nslookup → getent
    cmd: list[str] | None = None
    for tool in ("dig", "nslookup"):
        out, code = executor.run(["which", tool], timeout=3)
        if code == 0:
            if tool == "dig":
                cmd = ["dig"]
                if resolver:
                    cmd.append(f"@{resolver}")
                cmd += [name, record_type, "+short"]
            else:
                cmd = ["nslookup", name]
                if resolver:
                    cmd.append(resolver)
            break

    if cmd is None:
        cmd = ["getent", "hosts", name]

    out, code = executor.run(cmd, timeout=10)
    traces.append(" ".join(cmd))
    return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=traces)


def check_endpoint(
    executor: SafeExecutor,
    kind: str,
    host: str,
    port: int | None = None,
    path: str = "/",
    method: str = "GET",
    expected_status: int | None = None,
    timeout: int = 5,
    _session_counters: dict | None = None,
) -> ToolResult:
    """连通性检测（ping / tcp / http / tls）。"""
    traces: list[str] = []

    # WL017: 超频检测
    if _session_counters is not None:
        cnt = _session_counters.get("check_endpoint", 0) + 1
        _session_counters["check_endpoint"] = cnt
        if cnt > 20:
            return ToolResult(success=False, error="单次会话探测超过 20 次（WL017），已拒绝")
    _track_private_probe(_session_counters, host)

    kind = kind.lower()

    if kind == "ping":
        cmd = ["ping", "-c", "3", "-W", str(timeout), host]
        out, code = executor.run(cmd, timeout=timeout + 5)
        traces.append(" ".join(cmd))
        return ToolResult(success=(code == 0), data=out, cmd_trace=traces)

    if kind == "tcp":
        port_str = str(port or 80)
        cmd = ["nc", "-zv", "-w", str(timeout), host, port_str]
        out, code = executor.run(cmd, timeout=timeout + 3)
        traces.append(" ".join(cmd))
        if code == 0:
            return ToolResult(success=True, data=f"TCP {host}:{port_str} 可达", cmd_trace=traces)
        # 回退：bash /dev/tcp（不可用时用 curl）
        return ToolResult(success=False, data=out, error=f"TCP {host}:{port_str} 不可达", cmd_trace=traces)

    if kind in ("http", "tls"):
        scheme = "https" if kind == "tls" else "http"
        url = f"{scheme}://{host}"
        if port:
            url = f"{scheme}://{host}:{port}"
        url += path
        cmd = [
            "curl", "-s", "-o", "/dev/null",
            "-w", "%{http_code} %{redirect_url}",
            "--max-time", str(timeout),
            "-X", method,
        ]
        if kind == "tls":
            cmd += ["--ssl-reqd"]
        cmd.append(url)
        out, code = executor.run(cmd, timeout=timeout + 5)
        traces.append(" ".join(cmd))
        parts = out.strip().split(maxsplit=1)
        status_code = int(parts[0]) if parts and parts[0].isdigit() else 0
        redirect_url = parts[1].strip() if len(parts) > 1 else ""
        if redirect_url:
            redirect_host = urlparse(redirect_url).hostname or ""
            _track_private_probe(_session_counters, redirect_host)
            if _is_private_ip(redirect_host):
                return ToolResult(
                    success=False,
                    data={"http_status": status_code, "url": url, "redirect_url": redirect_url},
                    error=f"WH025：HTTP 重定向目标进入私网地址段（{redirect_host}）",
                    cmd_trace=traces,
                )
        if expected_status is not None:
            success = (status_code == expected_status)
        else:
            success = (200 <= status_code < 400)
        return ToolResult(
            success=success,
            data={"http_status": status_code, "url": url, "redirect_url": redirect_url},
            error="" if success else f"HTTP 状态码 {status_code}",
            cmd_trace=traces,
        )

    return ToolResult(success=False, error=f"不支持的探测类型：{kind}")
