"""工具: get_system_info, get_disk_usage."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult


def get_system_info(executor: SafeExecutor) -> ToolResult:
    """获取系统基本信息（OS / 内核 / 主机名 / 运行时间 / 负载）。"""
    results: dict = {}
    cmds = [
        ("hostname", ["hostname"]),
        ("os_release", ["cat", "/etc/os-release"]),
        ("kernel", ["uname", "-r"]),
        ("arch", ["uname", "-m"]),
        ("uptime", ["uptime", "-p"]),
        ("load", ["cat", "/proc/loadavg"]),
        ("memory", ["free", "-h"]),
    ]
    traces: list[str] = []
    for key, cmd in cmds:
        out, code = executor.run(cmd, timeout=5)
        traces.append(" ".join(cmd))
        if code == 0:
            results[key] = out
    return ToolResult(success=True, data=results, cmd_trace=traces)


def get_disk_usage(executor: SafeExecutor, path: str = "/", recursive: bool = False) -> ToolResult:
    """获取磁盘使用情况。"""
    from sysdialogue.security import path_policies as pp
    if pp.matches_v41_block(path):
        return ToolResult(success=False, error=f"禁止访问路径 {path}（安全规则 B001）")

    cmd_df = ["df", "-h", path]
    out_df, code_df = executor.run(cmd_df, timeout=10)
    traces = [" ".join(cmd_df)]
    data: dict = {"df": out_df}

    if recursive:
        cmd_du = ["du", "-sh", path]
        out_du, _ = executor.run(cmd_du, timeout=30)
        traces.append(" ".join(cmd_du))
        data["du"] = out_du

    return ToolResult(success=(code_df == 0), data=data, cmd_trace=traces)
