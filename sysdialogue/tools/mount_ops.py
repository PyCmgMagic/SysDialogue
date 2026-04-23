"""工具: manage_mount."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult
from sysdialogue.security import path_policies as pp


def manage_mount(
    executor: SafeExecutor,
    action: str,
    source: str | None = None,
    target: str | None = None,
    fs_type: str | None = None,
    options: list[str] | None = None,
) -> ToolResult:
    """挂载管理（list/mount/umount/remount）。只做即时挂载，不修改 /etc/fstab。"""
    if action == "list":
        cmd = ["mount"]
        out, code = executor.run(cmd, timeout=5)
        return ToolResult(success=(code == 0), data=out, cmd_trace=[" ".join(cmd)])

    if action in ("mount", "umount", "remount"):
        if not target:
            return ToolResult(success=False, error=f"{action} 需要 target 参数")
        if pp.matches_mount_block(target):
            return ToolResult(success=False, error=f"禁止 {action} 系统关键目录 {target}（B020）")

        if action == "umount":
            cmd = ["umount", target]
        elif action == "remount":
            opts = ",".join(options or ["remount"])
            cmd = ["mount", "-o", opts, target]
        else:  # mount
            if not source:
                return ToolResult(success=False, error="mount 需要 source 参数")
            cmd = ["mount"]
            if fs_type:
                cmd += ["-t", fs_type]
            if options:
                cmd += ["-o", ",".join(options)]
            cmd += [source, target]

        out, code = executor.run(cmd, timeout=15)
        return ToolResult(success=(code == 0), data=out or f"{action} 成功", error=out if code != 0 else "", cmd_trace=[" ".join(cmd)])

    return ToolResult(success=False, error=f"未知 action: {action}")
