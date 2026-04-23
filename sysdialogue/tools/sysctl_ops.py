"""工具: manage_sysctl."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult


def manage_sysctl(
    executor: SafeExecutor,
    action: str,
    key: str | None = None,
    value: str | None = None,
    persist: bool = False,
) -> ToolResult:
    """内核参数管理（list/get/set/apply-file）。"""
    if action == "list":
        cmd = ["sysctl", "-a"]
        out, code = executor.run(cmd, timeout=10)
        return ToolResult(success=(code == 0), data=out, cmd_trace=[" ".join(cmd)])

    if action == "get":
        if not key:
            return ToolResult(success=False, error="get 需要 key 参数")
        cmd = ["sysctl", key]
        out, code = executor.run(cmd, timeout=5)
        return ToolResult(success=(code == 0), data=out, cmd_trace=[" ".join(cmd)])

    if action == "set":
        if not key or value is None:
            return ToolResult(success=False, error="set 需要 key 和 value 参数")
        cmd = ["sysctl", "-w", f"{key}={value}"]
        out, code = executor.run(cmd, timeout=10)
        traces = [" ".join(cmd)]
        if code != 0:
            return ToolResult(success=False, error=out, cmd_trace=traces)
        if persist:
            # 持久化写入 /etc/sysctl.d/99-sysdialogue.conf
            conf_path = "/etc/sysctl.d/99-sysdialogue.conf"
            cmd_p = ["bash", "-c", f"echo '{key} = {value}' >> {conf_path}"]
            out_p, _ = executor.run(cmd_p, timeout=5)
            traces.append(" ".join(cmd_p))
        return ToolResult(success=True, data=out, cmd_trace=traces)

    if action == "apply-file":
        if not key:  # key 字段复用为文件路径
            return ToolResult(success=False, error="apply-file 需要 key 参数（文件路径）")
        cmd = ["sysctl", "-p", key]
        out, code = executor.run(cmd, timeout=10)
        return ToolResult(success=(code == 0), data=out, cmd_trace=[" ".join(cmd)])

    return ToolResult(success=False, error=f"未知 action: {action}")
