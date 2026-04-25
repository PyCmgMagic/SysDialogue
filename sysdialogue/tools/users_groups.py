"""工具: create_user, delete_user, modify_user_groups."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult


def create_user(
    executor: SafeExecutor,
    username: str,
    groups: list[str] | None = None,
    shell: str = "/bin/bash",
    create_home: bool = True,
) -> ToolResult:
    """创建用户。"""
    cmd = ["useradd"]
    if create_home:
        cmd.append("-m")
    cmd += ["-s", shell, username]
    out, code = executor.run_privileged(cmd, timeout=15)
    traces = [" ".join(cmd)]

    if code != 0:
        return ToolResult(success=False, error=out, cmd_trace=traces)

    # 加入附加组
    if groups:
        for g in groups:
            cmd_g = ["usermod", "-aG", g, username]
            out_g, code_g = executor.run_privileged(cmd_g, timeout=10)
            traces.append(" ".join(cmd_g))
            if code_g != 0:
                return ToolResult(success=False, error=f"加入组 {g} 失败：{out_g}", cmd_trace=traces)

    return ToolResult(success=True, data=f"用户 {username} 创建成功", cmd_trace=traces)


def delete_user(executor: SafeExecutor, username: str, remove_home: bool = False) -> ToolResult:
    """删除用户。"""
    cmd = ["userdel"]
    if remove_home:
        cmd.append("-r")
    cmd.append(username)
    out, code = executor.run_privileged(cmd, timeout=15)
    return ToolResult(
        success=(code == 0),
        data=out or f"用户 {username} 已删除",
        error=out if code != 0 else "",
        cmd_trace=[" ".join(cmd)],
    )


def modify_user_groups(
    executor: SafeExecutor,
    username: str,
    groups: list[str],
    action: str = "add",
) -> ToolResult:
    """修改用户组（add/remove）。"""
    traces: list[str] = []
    if action == "add":
        for g in groups:
            cmd = ["usermod", "-aG", g, username]
            out, code = executor.run_privileged(cmd, timeout=10)
            traces.append(" ".join(cmd))
            if code != 0:
                return ToolResult(success=False, error=f"加入组 {g} 失败：{out}", cmd_trace=traces)
        return ToolResult(success=True, data=f"已将 {username} 加入 {groups}", cmd_trace=traces)

    if action == "remove":
        # 获取当前组列表
        cmd_id = ["id", "-Gn", username]
        out_id, _ = executor.run(cmd_id, timeout=5)
        traces.append(" ".join(cmd_id))
        current = out_id.split()
        remaining = [g for g in current if g not in groups and g != username]
        cmd = ["usermod", "-G", ",".join(remaining), username]
        out, code = executor.run_privileged(cmd, timeout=10)
        traces.append(" ".join(cmd))
        return ToolResult(
            success=(code == 0),
            data=out or f"已将 {username} 从 {groups} 中移除",
            error=out if code != 0 else "",
            cmd_trace=traces,
        )

    return ToolResult(success=False, error=f"未知 action: {action}")
