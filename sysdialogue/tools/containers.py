"""工具: manage_container."""

from __future__ import annotations

import re

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult
from sysdialogue.security import path_policies as pp

VALID_ACTIONS = {
    "list", "status", "pull", "start", "stop", "restart",
    "logs", "inspect", "run", "remove", "exec",
}


def manage_container(
    executor: SafeExecutor,
    action: str,
    backend: str = "auto",
    name: str | None = None,
    image: str | None = None,
    ports: list[dict] | None = None,
    env_vars: dict | None = None,
    volumes: list[dict] | None = None,
    restart_policy: str | None = None,
    command: list[str] | None = None,
    lines: int = 50,
    env_profile: dict | None = None,
) -> ToolResult:
    """容器管理（不提供 exec shell / privileged / host network）。"""
    if action not in VALID_ACTIONS:
        return ToolResult(success=False, error=f"无效 action: {action}")

    be = _resolve_backend(backend, env_profile)
    if not be:
        return ToolResult(success=False, error="无法确定容器后端，请指定 backend 参数")

    if action == "list":
        cmd = [be, "ps", "-a"]
    elif action == "status":
        cmd = [be, "inspect", "--format={{.State.Status}}", name or ""]
    elif action == "pull":
        if not image:
            return ToolResult(success=False, error="pull 需要 image 参数")
        cmd = [be, "pull", image]
    elif action == "start":
        cmd = [be, "start", name or ""]
    elif action == "stop":
        cmd = [be, "stop", name or ""]
    elif action == "restart":
        cmd = [be, "restart", name or ""]
    elif action == "logs":
        cmd = [be, "logs", "--tail", str(lines), name or ""]
    elif action == "inspect":
        cmd = [be, "inspect", name or ""]
    elif action == "remove":
        cmd = [be, "rm", "-f", name or ""]
    elif action == "exec":
        if not name:
            return ToolResult(success=False, error="exec 需要 name 参数")
        if not command:
            return ToolResult(success=False, error="exec 需要 command 参数")
        if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
            return ToolResult(success=False, error="exec command 必须是非空字符串数组")
        if len(command) > 20:
            return ToolResult(success=False, error="exec command 参数过长")
        cmd = [be, "exec", name, *command]
    elif action == "run":
        cmd = _build_run_cmd(be, name, image, ports, env_vars, volumes, restart_policy)
        if isinstance(cmd, str):
            return ToolResult(success=False, error=cmd)
    else:
        return ToolResult(success=False, error=f"未实现 action: {action}")

    timeout = 120 if action in ("pull", "run") else 30
    out, code = executor.run(cmd, timeout=timeout)
    data = out
    if action == "exec":
        data = {
            "container": name,
            "command": list(command or []),
            "exit_code": code,
            "stdout": out,
            "stderr": "",
            "verification_candidate": _is_read_only_exec_command(command or []),
        }
    return ToolResult(success=(code == 0), data=data, error=out if code != 0 else "", exit_code=code, cmd_trace=[" ".join(cmd)])


def _resolve_backend(backend: str, env_profile: dict | None) -> str | None:
    if backend != "auto":
        return backend
    if env_profile:
        cb = env_profile.get("container_backend", "none")
        return cb if cb != "none" else None
    return None


def _build_run_cmd(be, name, image, ports, env_vars, volumes, restart_policy) -> list[str] | str:
    if not image:
        return "run 需要 image 参数"

    # 安全检查：bind mount 敏感路径
    for vol in (volumes or []):
        src = vol.get("source", "") if isinstance(vol, dict) else str(vol)
        if pp.matches_container_sensitive_bind(src):
            return f"禁止挂载敏感目录 {src}（B022）"

    cmd = [be, "run", "-d"]
    if name:
        cmd += ["--name", name]
    if restart_policy:
        cmd += ["--restart", restart_policy]
    for p in (ports or []):
        if isinstance(p, dict):
            cmd += ["-p", f"{p.get('host_port')}:{p.get('container_port')}"]
        else:
            cmd += ["-p", str(p)]
    for k, v in (env_vars or {}).items():
        cmd += ["-e", f"{k}={v}"]
    for vol in (volumes or []):
        if isinstance(vol, dict):
            src = vol.get("source", "")
            dst = vol.get("target", "")
            cmd += ["-v", f"{src}:{dst}"]
        else:
            cmd += ["-v", str(vol)]
    cmd.append(image)
    return cmd


def _is_read_only_exec_command(command: list[str]) -> bool:
    text = " ".join(command).strip().lower()
    if not text:
        return False
    if re.search(r"\b(create|alter|drop|insert|update|delete|truncate|grant|revoke|replace)\b", text):
        return False
    return bool(
        re.search(r"\b(select|show|describe|desc|explain)\b", text)
        or "mysqladmin ping" in text
        or "pg_isready" in text
        or "redis-cli ping" in text
        or text.startswith(("curl ", "wget ", "nc "))
    )
