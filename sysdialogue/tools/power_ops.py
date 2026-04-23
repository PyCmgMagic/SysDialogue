"""工具: manage_power."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult


def manage_power(
    executor: SafeExecutor,
    action: str,
    delay_sec: int = 0,
    reason: str = "",
    force: bool = False,
) -> ToolResult:
    """重启/关机操作（WH021 高风险）。"""
    if action not in ("reboot", "shutdown"):
        return ToolResult(success=False, error=f"无效 action: {action}（支持 reboot / shutdown）")

    if action == "reboot":
        if force:
            cmd = ["reboot", "--force"]
        elif delay_sec > 0:
            cmd = ["shutdown", "-r", f"+{delay_sec // 60}"]
            if reason:
                cmd.append(reason)
        else:
            cmd = ["systemctl", "reboot"]
    else:  # shutdown
        if force:
            cmd = ["poweroff", "--force"]
        elif delay_sec > 0:
            cmd = ["shutdown", "-h", f"+{delay_sec // 60}"]
            if reason:
                cmd.append(reason)
        else:
            cmd = ["systemctl", "poweroff"]

    out, code = executor.run(cmd, timeout=15)
    return ToolResult(
        success=(code == 0),
        data=out or f"{action} 已发起",
        error=out if code != 0 else "",
        cmd_trace=[" ".join(cmd)],
    )
