"""工具: read_log."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult


def read_log(
    executor: SafeExecutor,
    unit: str | None = None,
    lines: int = 100,
    since: str | None = None,
    supports_journalctl: bool = True,
) -> ToolResult:
    """读取系统日志或服务日志。"""
    traces: list[str] = []

    if supports_journalctl:
        cmd = ["journalctl", "--no-pager", f"-n{lines}"]
        if unit:
            cmd += ["-u", unit]
        if since:
            cmd += ["--since", since]
        out, code = executor.run(cmd, timeout=10)
        traces.append(" ".join(cmd))
        if code == 0:
            return ToolResult(success=True, data=out, cmd_trace=traces)

    # 回退：读取文件日志
    if unit:
        paths = [f"/var/log/{unit}.log", f"/var/log/{unit}/{unit}.log"]
    else:
        paths = ["/var/log/syslog", "/var/log/messages"]

    for path in paths:
        cmd_tail = ["tail", f"-n{lines}", path]
        out, code = executor.run(cmd_tail, timeout=10)
        traces.append(" ".join(cmd_tail))
        if code == 0:
            return ToolResult(success=True, data=out, cmd_trace=traces)

    return ToolResult(success=False, error="未找到日志来源（journalctl 和文件日志均失败）", cmd_trace=traces)
