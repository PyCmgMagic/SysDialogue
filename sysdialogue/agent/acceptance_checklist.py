"""Operator acceptance checklist for release and real-host validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AcceptanceStep:
    step_id: str
    gate: str
    title: str
    command: str
    expected_evidence: str
    release_note: str


def render_acceptance_checklist(env: dict[str, Any] | None = None) -> str:
    env = env or {}
    target = _target_label(env)
    remote_arg = _remote_arg(env)
    proxy_suffix = " --ssh-proxy-command <proxy-command>" if env.get("ssh_proxy_command_configured") else ""
    remote_note = "configured target" if env.get("remote_mode") else "replace placeholder target before running"
    steps = _steps(remote_arg, proxy_suffix)

    lines = [
        "Operator acceptance checklist:",
        f"Target: {target} ({remote_note})",
        "Use this checklist on a staging or disposable production-like host before attaching results to release notes.",
        "",
        "Runbook:",
    ]
    for step in steps:
        lines.append(f"- [ ] {step.step_id} {step.title} [{step.gate}]")
        lines.append(f"  Command: {step.command}")
        lines.append(f"  Expected evidence: {step.expected_evidence}")
        lines.append(f"  Release-note line: {step.release_note}")

    lines.extend(
        [
            "",
            "Release notes attachment template:",
            "- Target: " + target,
            "- SysDialogue commit/version: <commit-or-version>",
            "- Acceptance result: <pass/fail/partial>",
            "- Evidence matrix: attach `/evidence` or `sysdialogue --evidence` output.",
            "- Replay/audit artifacts: attach sanitized ZIP/JSONL paths from `/export-replay` or `--export-replay`.",
            "- Exceptions and follow-up owners: <none-or-ticket-links>",
            "",
            "Safety boundary:",
            "- Run mutating steps only on staging, a disposable host, or an explicitly approved low-risk service.",
            "- Do not paste raw passwords, tokens, private keys, or full ProxyCommand secrets into release notes.",
        ]
    )
    return "\n".join(lines)


def _steps(remote_arg: str, proxy_suffix: str) -> tuple[AcceptanceStep, ...]:
    doctor_remote = f"sysdialogue --doctor --remote {remote_arg}{proxy_suffix}"
    demo_remote = f"sysdialogue --demo --remote {remote_arg}{proxy_suffix}"
    return (
        AcceptanceStep(
            "A01",
            "Startup",
            "Run local self-check and metadata checks",
            "sysdialogue --verify",
            "Self-check passes, evidence matrix shows no gaps or missing references.",
            "A01 startup self-check passed.",
        ),
        AcceptanceStep(
            "A02",
            "Startup",
            "Validate model tool-call compatibility",
            "sysdialogue --check-model --model <model-name>",
            "Diagnostic status is ok and returned a diagnostic tool call.",
            "A02 model tool-call diagnostic passed for <model-name>.",
        ),
        AcceptanceStep(
            "A03",
            "Startup",
            "Validate remote connection readiness",
            doctor_remote,
            "Doctor reports target profile, tool registry, state stores, and no secret leakage.",
            f"A03 remote doctor passed for {remote_arg}.",
        ),
        AcceptanceStep(
            "A04",
            "Usability",
            "Capture operator-facing command surfaces",
            "In TUI or simple CLI run: /help, /examples, /playbooks, /evidence, /acceptance, /doctor",
            "First screen and help make the next action, diagnostics, playbooks, and evidence visible.",
            "A04 operator-facing controls were visible and understandable.",
        ),
        AcceptanceStep(
            "A05",
            "Conversation",
            "Check non-operational conversation stays non-invasive",
            'Ask: "hello"',
            "Agent answers without executing OS tools and explains no action was needed.",
            "A05 casual conversation completed without system access.",
        ),
        AcceptanceStep(
            "A06",
            "Audit",
            "Run a read-only production workflow",
            demo_remote,
            "security_audit completes or reports unsupported host clearly; audit session id is printed.",
            "A06 read-only audit workflow produced reviewable evidence.",
        ),
        AcceptanceStep(
            "A07",
            "Mutation safety",
            "Exercise a low-risk approved mutation",
            "Use Web Release Drill, or run sysdialogue --acceptance-runner --acceptance-runner-mode operator-approved-drill --acceptance-drill-plan a07-drill.json --remote ...",
            "Fixed approval phrase, impact, rollback, lock, confirmation, mutation, post-change verification, and audit session id are visible.",
            "A07 mutation safety gate passed with approval and verification evidence.",
        ),
        AcceptanceStep(
            "A08",
            "Recovery",
            "Exercise interrupted-task recovery",
            "Start a safe long-running task, cancel/interruption-test it, then run /next and either /resume or /abandon.",
            "The active task is discoverable, no successful side effect repeats, and stale locks are released.",
            "A08 recovery path passed for interrupted work.",
        ),
        AcceptanceStep(
            "A09",
            "Security hygiene",
            "Export sanitized audit/replay evidence",
            "/audit, then /export-replay <session_id> or sysdialogue --export-replay <session_id> --export-dir <dir>",
            "SUMMARY.md is present and artifacts do not include raw credentials, tokens, private keys, or proxy secrets.",
            "A09 sanitized replay package attached.",
        ),
        AcceptanceStep(
            "A10",
            "Release evidence",
            "Attach completion evidence to release notes",
            "sysdialogue --evidence",
            "Product bar and verification gates list concrete tests, docs, and smoke commands.",
            "A10 evidence matrix attached to release notes.",
        ),
    )


def _target_label(env: dict[str, Any]) -> str:
    if env.get("remote_mode"):
        return f"ssh://{_host(env)}:{_port(env)}{_proxy_label(env)}"
    return "local controller; remote acceptance target placeholder is " + _remote_arg(env)


def _remote_arg(env: dict[str, Any]) -> str:
    user = str(env.get("ssh_user") or env.get("current_user") or "user").strip() or "user"
    return f"{user}@{_host(env)}:{_port(env)}"


def _host(env: dict[str, Any]) -> str:
    return str(env.get("host") or env.get("ssh_host") or env.get("hostname") or "host").strip() or "host"


def _port(env: dict[str, Any]) -> str:
    return str(env.get("ssh_port") or "22").strip() or "22"


def _proxy_label(env: dict[str, Any]) -> str:
    return " via ProxyCommand" if env.get("ssh_proxy_command_configured") else ""
