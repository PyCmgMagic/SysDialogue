"""工具: manage_service."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult

VALID_ACTIONS = {
    "start", "stop", "restart", "status", "enable", "disable",
    "reload", "daemon-reload",
}


def manage_service(
    executor: SafeExecutor,
    name: str,
    action: str,
    init_system: str = "systemd",
) -> ToolResult:
    """管理 systemd/sysvinit 服务。"""
    if action not in VALID_ACTIONS:
        return ToolResult(success=False, error=f"无效 action: {action}")

    traces: list[str] = []

    if init_system == "systemd":
        if action == "daemon-reload":
            cmd = ["systemctl", "daemon-reload"]
        elif action == "status":
            cmd = ["systemctl", "status", name, "--no-pager"]
        else:
            cmd = ["systemctl", action, name]
    else:
        # sysvinit 回退（daemon-reload 不支持）
        if action == "daemon-reload":
            return ToolResult(success=False, error="sysvinit 不支持 daemon-reload")
        if action in ("enable", "disable"):
            return ToolResult(success=False, error=f"sysvinit 不支持 {action}，请手动配置 /etc/rc.d/")
        cmd = ["service", name, action]

    out, code = executor.run(cmd, timeout=30)
    traces.append(" ".join(cmd))

    # status 返回 1 时可能只是服务停止，不算错误
    success = code == 0 or (action == "status" and code <= 3)
    return ToolResult(success=success, data=out, error=out if not success else "", cmd_trace=traces)
