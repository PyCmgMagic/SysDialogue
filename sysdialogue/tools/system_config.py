"""工具: get_set_system_config."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult

SUPPORTED_KEYS = {"hostname", "timezone", "locale"}


def get_set_system_config(
    executor: SafeExecutor,
    key: str,
    value: str | None = None,
) -> ToolResult:
    """获取或设置系统配置（hostname / timezone / locale）。"""
    if key not in SUPPORTED_KEYS:
        return ToolResult(success=False, error=f"不支持的配置键：{key}（支持 {SUPPORTED_KEYS}）")

    if value is None:
        return _get(executor, key)
    return _set(executor, key, value)


def _get(executor: SafeExecutor, key: str) -> ToolResult:
    if key == "hostname":
        cmd = ["hostname"]
    elif key == "timezone":
        cmd = ["timedatectl", "show", "--property=Timezone", "--value"]
    elif key == "locale":
        cmd = ["localectl", "status"]
    else:
        return ToolResult(success=False, error=f"不支持 get {key}")

    out, code = executor.run(cmd, timeout=5)
    return ToolResult(success=(code == 0), data={key: out}, error=out if code != 0 else "", cmd_trace=[" ".join(cmd)])


def _set(executor: SafeExecutor, key: str, value: str) -> ToolResult:
    if key == "hostname":
        cmd = ["hostnamectl", "set-hostname", value]
    elif key == "timezone":
        cmd = ["timedatectl", "set-timezone", value]
    elif key == "locale":
        cmd = ["localectl", "set-locale", value]
    else:
        return ToolResult(success=False, error=f"不支持 set {key}")

    out, code = executor.run(cmd, timeout=10)
    return ToolResult(success=(code == 0), data=out or f"{key} 已设为 {value}", error=out if code != 0 else "", cmd_trace=[" ".join(cmd)])
