"""Container operations for docker/podman."""

from __future__ import annotations

import re
import time

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.security import path_policies as pp
from sysdialogue.tools.base import ToolResult

VALID_ACTIONS = {
    "list", "status", "pull", "start", "stop", "restart",
    "logs", "inspect", "run", "remove", "exec", "wait_exec",
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
    retries: int = 10,
    interval_sec: float = 2,
    success_contains: str | None = None,
    env_profile: dict | None = None,
) -> ToolResult:
    """Manage containers without shell, privileged, or host-network support."""
    if action not in VALID_ACTIONS:
        return ToolResult(success=False, error=f"Invalid action: {action}")

    be = _resolve_backend(backend, env_profile)
    if not be:
        return ToolResult(success=False, error="Unable to determine container backend")

    if action == "list":
        cmd = [be, "ps", "-a"]
    elif action == "status":
        cmd = [be, "inspect", "--format={{.State.Status}}", name or ""]
    elif action == "pull":
        if not image:
            return ToolResult(success=False, error="pull requires image")
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
    elif action in {"exec", "wait_exec"}:
        valid, error = _validate_exec_args(action, name, command)
        if not valid:
            return ToolResult(success=False, error=error)
        read_only, read_only_reason = _read_only_exec_command_reason(command or [])
        if action == "wait_exec" and not read_only:
            return ToolResult(success=False, error="wait_exec only accepts read-only verification commands")
        cmd = [be, "exec", name or "", *(command or [])]
    elif action == "run":
        cmd = _build_run_cmd(be, name, image, ports, env_vars, volumes, restart_policy)
        if isinstance(cmd, str):
            return ToolResult(success=False, error=cmd)
    else:
        return ToolResult(success=False, error=f"Unhandled action: {action}")

    timeout = 120 if action in ("pull", "run") else 30
    if action == "wait_exec":
        return _run_wait_exec(
            executor,
            cmd,
            name=name or "",
            command=command or [],
            retries=retries,
            interval_sec=interval_sec,
            success_contains=success_contains,
            read_only_reason=read_only_reason,
        )
    if action == "exec":
        return _run_exec(executor, cmd, name=name or "", command=command or [], read_only_reason=read_only_reason)

    out, code = executor.run(cmd, timeout=timeout)
    return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", exit_code=code, cmd_trace=[" ".join(cmd)])


def _run_exec(
    executor: SafeExecutor,
    cmd: list[str],
    *,
    name: str,
    command: list[str],
    read_only_reason: str,
) -> ToolResult:
    result = executor.run_full(cmd, timeout=30)
    data = {
        "container": name,
        "command": list(command),
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "attempts": 1,
        "verification_candidate": bool(read_only_reason),
        "read_only_reason": read_only_reason,
    }
    output = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()
    return ToolResult(success=(result.exit_code == 0), data=data, error=output if result.exit_code != 0 else "", exit_code=result.exit_code, cmd_trace=[" ".join(cmd)])


def _run_wait_exec(
    executor: SafeExecutor,
    cmd: list[str],
    *,
    name: str,
    command: list[str],
    retries: int,
    interval_sec: float,
    success_contains: str | None,
    read_only_reason: str,
) -> ToolResult:
    attempts: list[dict] = []
    total = max(1, min(int(retries or 1), 60))
    interval = max(0.0, min(float(interval_sec or 0), 30.0))
    last_stdout = ""
    last_stderr = ""
    last_code = 1
    matched = False
    for index in range(1, total + 1):
        result = executor.run_full(cmd, timeout=30)
        last_stdout = result.stdout
        last_stderr = result.stderr
        last_code = result.exit_code
        combined = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()
        matched = result.exit_code == 0 and (not success_contains or success_contains in combined)
        attempts.append(
            {
                "attempt": index,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "matched": matched,
            }
        )
        if matched:
            break
        if index < total and interval:
            time.sleep(interval)
    data = {
        "container": name,
        "command": list(command),
        "exit_code": last_code,
        "stdout": last_stdout,
        "stderr": last_stderr,
        "attempts": attempts,
        "verification_candidate": True,
        "read_only_reason": read_only_reason,
    }
    output = (last_stdout + ("\n" + last_stderr if last_stderr else "")).strip()
    return ToolResult(success=matched, data=data, error=output if not matched else "", exit_code=last_code, cmd_trace=[" ".join(cmd)])


def _validate_exec_args(action: str, name: str | None, command: list[str] | None) -> tuple[bool, str]:
    if not name:
        return False, f"{action} requires name"
    if not command:
        return False, f"{action} requires command"
    if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
        return False, f"{action} command must be a non-empty argv list"
    if len(command) > 20:
        return False, f"{action} command is too long"
    return True, ""


def _resolve_backend(backend: str, env_profile: dict | None) -> str | None:
    if backend != "auto":
        return backend
    if env_profile:
        cb = env_profile.get("container_backend", "none")
        return cb if cb != "none" else None
    return None


def _build_run_cmd(be, name, image, ports, env_vars, volumes, restart_policy) -> list[str] | str:
    if not image:
        return "run requires image"
    for vol in (volumes or []):
        src = vol.get("source", "") if isinstance(vol, dict) else str(vol)
        if pp.matches_container_sensitive_bind(src):
            return f"Sensitive bind mount is blocked: {src}"

    cmd = [be, "run", "-d"]
    if name:
        cmd += ["--name", name]
    if restart_policy:
        cmd += ["--restart", restart_policy]
    for p in (ports or []):
        if isinstance(p, dict):
            proto = p.get("protocol") or "tcp"
            mapping = f"{p.get('host_port')}:{p.get('container_port')}"
            if proto != "tcp":
                mapping += f"/{proto}"
            cmd += ["-p", mapping]
        else:
            cmd += ["-p", str(p)]
    for k, v in (env_vars or {}).items():
        cmd += ["-e", f"{k}={v}"]
    for vol in (volumes or []):
        if isinstance(vol, dict):
            src = vol.get("source", "")
            dst = vol.get("target", "")
            suffix = ":ro" if vol.get("read_only") else ""
            cmd += ["-v", f"{src}:{dst}{suffix}"]
        else:
            cmd += ["-v", str(vol)]
    cmd.append(image)
    return cmd


def _is_read_only_exec_command(command: list[str]) -> bool:
    return _read_only_exec_command_reason(command)[0]


def _read_only_exec_command_reason(command: list[str]) -> tuple[bool, str]:
    text = " ".join(command).strip().lower()
    if not text:
        return False, ""
    if re.search(r"\b(create|alter|drop|insert|update|delete|truncate|grant|revoke|replace)\b", text):
        return False, "contains write keyword"
    if re.search(r"\b(select|show|describe|desc|explain)\b", text):
        return True, "SQL read-only query"
    if "mysqladmin" in text and "ping" in text:
        return True, "MySQL readiness ping"
    if "pg_isready" in text:
        return True, "Postgres readiness check"
    if "redis-cli" in text and "ping" in text:
        return True, "Redis readiness ping"
    if text.startswith(("curl ", "wget ", "nc ")):
        return True, "network health check"
    return False, ""
