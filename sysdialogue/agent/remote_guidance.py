"""User-facing guidance for remote SSH setup and recovery."""

from __future__ import annotations

import shlex
from typing import Any

from sysdialogue.security.output_sanitizer import sanitize_text


def render_remote_startup_error(config: Any, exc: Exception) -> str:
    """Return an actionable, credential-safe SSH startup error."""
    user = _clean(getattr(config, "ssh_user", "") or "root", fallback="root")
    host = _clean(getattr(config, "ssh_host", "") or "(missing-host)", fallback="(missing-host)")
    port = _clean_port(getattr(config, "ssh_port", 22))
    target = _remote_arg(user=user, host=host, port=port)
    detail = sanitize_text(str(exc), limit=500).strip() or type(exc).__name__

    lines = [
        f"Remote SSH connection failed for {target} ({type(exc).__name__}): {detail}.",
        "",
        "Next checks:",
        f"- Verify SSH outside SysDialogue: `{_ssh_probe(user, host, port)}`",
        f"- Re-run a no-API readiness check: `{_doctor_probe(user, host, port)}`",
        "- For key auth, pass `--ssh-key /path/to/key`; for password auth, prefer "
        "`SYSDIALOGUE_SSH_PASSWORD` or use `--ssh-password` only in a trusted shell.",
        "- For host-key errors, verify the server fingerprint before editing `~/.ssh/known_hosts`.",
        "- For sudo failures after connection, set `SYSDIALOGUE_SUDO_PASSWORD` or use an account "
        "with non-interactive sudo.",
    ]
    if _clean(getattr(config, "ssh_proxy_command", ""), fallback=""):
        lines.append(
            "- ProxyCommand is configured; verify the bastion/jump-host path separately and keep "
            "`%h`, `%p`, and `%r` placeholders pointed at the final target."
        )
    lines.extend(
        [
            "",
            "Recovery after an interrupted run:",
            "- Use `/next` to inspect stale work, `/resume` to continue it, `/abandon` to release stale "
            "locks, and `/export-replay` to capture evidence.",
        ]
    )
    return "\n".join(lines)


def render_remote_examples(env: dict[str, Any]) -> list[str]:
    """Return copy-ready remote setup and recovery examples for /examples."""
    user = _clean(env.get("current_user") or env.get("user") or "user", fallback="user")
    host = _clean(env.get("host") or env.get("hostname") or "example.com", fallback="example.com")
    port = _clean_port(env.get("ssh_port", 22))
    return [
        "Remote setup and recovery:",
        f"- First confirm the target without calling the model: `{_doctor_probe(user, host, port)}`",
        f"- Compare with plain SSH if startup fails: `{_ssh_probe(user, host, port)}`",
        "- For bastion access, start with: `sysdialogue --doctor --remote user@private-host:22 --ssh-proxy-command \"ssh -W %h:%p bastion-host\"`",
        "- If a run was interrupted, use `/next`, then `/resume` or `/abandon` before starting new changes.",
        "- If `known_hosts` or host-key verification fails, verify the fingerprint out of band before editing it.",
    ]


def _remote_arg(*, user: str, host: str, port: int) -> str:
    return f"{user}@{host}:{port}"


def _ssh_probe(user: str, host: str, port: int) -> str:
    return f"ssh -p {port} {shlex.quote(f'{user}@{host}')} {shlex.quote('uname -a')}"


def _doctor_probe(user: str, host: str, port: int) -> str:
    return f"sysdialogue --doctor --remote {shlex.quote(_remote_arg(user=user, host=host, port=port))}"


def _clean(value: Any, *, fallback: str) -> str:
    text = sanitize_text(value, limit=120).strip()
    return text or fallback


def _clean_port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return 22
    return port if 1 <= port <= 65535 else 22
