"""工具: list_processes, kill_process, get_port_status, get_network_info, find_files."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult


def list_processes(
    executor: SafeExecutor,
    top_n: int = 20,
    sort_by: str = "cpu",
    filter_user: str | None = None,
) -> ToolResult:
    """列出进程，按 cpu/mem/pid 排序。"""
    sort_flag = {"cpu": "-C", "mem": "-M", "pid": "-p"}.get(sort_by, "-C")
    cmd = ["ps", "aux", "--sort", sort_flag]
    out, code = executor.run(cmd, timeout=10)
    lines = out.splitlines()
    if filter_user:
        header = lines[:1] if lines else []
        body = [l for l in lines[1:] if l.split()[0] == filter_user]
        lines = header + body
    if top_n and len(lines) > top_n + 1:
        lines = lines[: top_n + 1]
    return ToolResult(success=(code == 0), data="\n".join(lines), cmd_trace=[" ".join(cmd)])


def kill_process(executor: SafeExecutor, pid: int, signal: str = "SIGTERM") -> ToolResult:
    """终止进程。"""
    sig_map = {"SIGTERM": "15", "SIGKILL": "9", "SIGHUP": "1"}
    sig = sig_map.get(signal.upper(), "15")
    cmd = ["kill", f"-{sig}", str(pid)]
    out, code = executor.run(cmd, timeout=5)
    return ToolResult(success=(code == 0), data=out or f"已发送 {signal} 到 PID {pid}", cmd_trace=[" ".join(cmd)])


def get_port_status(executor: SafeExecutor, port: int | None = None, protocol: str = "all") -> ToolResult:
    """查看端口/监听状态。"""
    # 优先使用 ss，回退到 netstat
    use_ss_cmd = ["which", "ss"]
    _, code = executor.run(use_ss_cmd, timeout=3)
    if code == 0:
        cmd = ["ss", "-tlnp"] if protocol in ("tcp", "all") else ["ss", "-ulnp"]
        if protocol == "all":
            cmd = ["ss", "-tlnpu"]
    else:
        cmd = ["netstat", "-tlnp"] if protocol in ("tcp", "all") else ["netstat", "-ulnp"]
    out, code = executor.run(cmd, timeout=10)
    if port is not None:
        lines = out.splitlines()
        header = lines[:2] if lines else []
        body = [l for l in lines[2:] if f":{port}" in l or f" {port} " in l]
        out = "\n".join(header + body)
    return ToolResult(success=(code == 0), data=out, cmd_trace=[" ".join(cmd)])


def get_network_info(executor: SafeExecutor, interface: str | None = None) -> ToolResult:
    """获取网络接口信息。"""
    results: dict = {}
    traces: list[str] = []

    # ip addr 或 ifconfig
    if interface:
        cmd_addr = ["ip", "addr", "show", interface]
    else:
        cmd_addr = ["ip", "addr"]
    out, code = executor.run(cmd_addr, timeout=5)
    traces.append(" ".join(cmd_addr))
    if code != 0:
        cmd_addr = ["ifconfig"] + ([interface] if interface else [])
        out, _ = executor.run(cmd_addr, timeout=5)
        traces.append(" ".join(cmd_addr))
    results["interfaces"] = out

    # 路由表
    cmd_route = ["ip", "route"]
    out_r, _ = executor.run(cmd_route, timeout=5)
    traces.append(" ".join(cmd_route))
    results["routes"] = out_r

    return ToolResult(success=True, data=results, cmd_trace=traces)


def find_files(
    executor: SafeExecutor,
    search_path: str = ".",
    pattern: str = "*",
    min_size_mb: float | None = None,
    max_depth: int = 5,
) -> ToolResult:
    """搜索文件。"""
    from sysdialogue.security import path_policies as pp
    if pp.matches_v41_block(search_path):
        return ToolResult(success=False, error=f"禁止检索路径 {search_path}（安全规则 B001）")
    if pp.matches_sensitive_dir(search_path):
        return ToolResult(success=False, error=f"禁止枚举敏感目录 {search_path}（安全规则 B031）")

    cmd = ["find", search_path, "-maxdepth", str(max_depth), "-name", pattern, "-type", "f"]
    if min_size_mb is not None:
        cmd += ["-size", f"+{int(min_size_mb * 1024)}k"]

    out, code = executor.run(cmd, timeout=30)
    return ToolResult(success=(code == 0), data=out, cmd_trace=[" ".join(cmd)])
