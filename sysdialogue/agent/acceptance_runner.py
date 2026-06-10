"""Guided acceptance runner for A01-A10 release gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sysdialogue.agent.acceptance_checklist import _remote_arg, _steps, _target_label
from sysdialogue.agent.evidence_matrix import (
    check_evidence_references,
    coverage_gaps,
    render_evidence_matrix,
)
from sysdialogue.security.output_sanitizer import sanitize_text

AcceptanceRunnerMode = Literal["auto-local", "auto-model", "auto-conversation", "auto-read-only", "auto-replay", "auto-ui", "manual", "operator-approved"]
AcceptanceRunMode = Literal["safe-preflight", "model-check", "conversation-check", "ui-review", "read-only-collect", "recovery-drill", "replay-export", "operator-approved-drill"]


@dataclass(frozen=True)
class AcceptanceRunStep:
    step_id: str
    gate: str
    title: str
    command: str
    expected_evidence: str
    release_note: str
    mode: AcceptanceRunnerMode
    status: str
    evidence: str
    manual_action: str


@dataclass(frozen=True)
class AcceptanceRun:
    target: str
    mode: AcceptanceRunMode
    steps: tuple[AcceptanceRunStep, ...]
    notes: tuple[str, ...]


def run_guided_acceptance(
    env: dict[str, Any] | None = None,
    *,
    mode: AcceptanceRunMode = "safe-preflight",
    collected: dict[str, dict[str, str]] | None = None,
) -> AcceptanceRun:
    """Run safe local preflight checks and produce a guided A01-A10 artifact.

    The runner intentionally avoids model calls, SSH commands, and mutations by
    default. Steps that need a real operator, disposable target, model endpoint,
    or explicit mutation approval are emitted as partial with concrete next
    actions and evidence slots.
    """

    env = env or {}
    collected = collected or {}
    target = _target_label(env)
    remote_arg = _remote_arg(env)
    proxy_suffix = " --ssh-proxy-command <proxy-command>" if env.get("ssh_proxy_command_configured") else ""
    steps = []
    for step in _steps(remote_arg, proxy_suffix):
        status, evidence, step_mode, manual_action = _evaluate_step(step.step_id, env, mode, collected)
        steps.append(
            AcceptanceRunStep(
                step.step_id,
                step.gate,
                step.title,
                step.command,
                step.expected_evidence,
                step.release_note,
                step_mode,
                status,
                evidence,
                manual_action,
            )
        )
    notes = _runner_notes(mode)
    return AcceptanceRun(target, mode, tuple(steps), notes)


def render_guided_acceptance_run(run: AcceptanceRun) -> str:
    lines = [
        "Operator acceptance checklist:",
        "Guided acceptance runner artifact:",
        f"Target: {run.target}",
        f"Mode: {run.mode}",
        "",
        "Runbook:",
    ]
    for step in run.steps:
        lines.append(f"- [{_checkbox(step.status)}] {step.step_id} {step.title} [{step.gate}]")
        lines.append(f"  Status: {step.status}")
        lines.append(f"  Runner mode: {step.mode}")
        lines.append(f"  Command: {step.command}")
        lines.append(f"  Expected evidence: {step.expected_evidence}")
        lines.append(f"  Evidence: {step.evidence}")
        if step.manual_action:
            lines.append(f"  Manual action: {step.manual_action}")
        lines.append(f"  Release-note line: {step.release_note}")
    lines.append("")
    lines.append("Runner notes:")
    lines.extend(f"- {note}" for note in run.notes)
    return "\n".join(lines)


def guided_acceptance_to_dict(run: AcceptanceRun) -> dict[str, Any]:
    return {
        "target": run.target,
        "mode": run.mode,
        "steps": [
            {
                "stepId": step.step_id,
                "gate": step.gate,
                "title": step.title,
                "command": step.command,
                "expectedEvidence": step.expected_evidence,
                "releaseNote": step.release_note,
                "mode": step.mode,
                "status": step.status,
                "evidence": step.evidence,
                "manualAction": step.manual_action,
            }
            for step in run.steps
        ],
        "notes": list(run.notes),
    }


def render_guided_acceptance(
    env: dict[str, Any] | None = None,
    *,
    mode: AcceptanceRunMode = "safe-preflight",
    collected: dict[str, dict[str, str]] | None = None,
) -> str:
    return render_guided_acceptance_run(run_guided_acceptance(env, mode=mode, collected=collected))


def _evaluate_step(
    step_id: str,
    env: dict[str, Any],
    run_mode: AcceptanceRunMode,
    collected: dict[str, dict[str, str]],
) -> tuple[str, str, AcceptanceRunnerMode, str]:
    if step_id == "A01":
        gaps = coverage_gaps()
        missing = check_evidence_references()
        if gaps or missing:
            parts = []
            if gaps:
                parts.append(f"coverage gaps={len(gaps)}")
            if missing:
                parts.append(f"missing references={len(missing)}")
            return "fail", sanitize_text("; ".join(parts), limit=400), "auto-local", "Fix evidence matrix gaps before release."
        return "pass", "Self-check evidence matrix has no coverage gaps or missing references.", "auto-local", ""
    if step_id == "A10":
        matrix = render_evidence_matrix()
        if "Completion evidence matrix:" in matrix and "Suggested smoke commands:" in matrix:
            return "pass", "Completion evidence matrix rendered and is ready to attach.", "auto-local", ""
        return "fail", "Completion evidence matrix did not render expected sections.", "auto-local", "Fix evidence matrix rendering before release."
    if step_id == "A02":
        collected_result = _collected_result(step_id, collected)
        if collected_result is not None:
            return collected_result[0], collected_result[1], "auto-model", collected_result[2]
        return (
            "partial",
            "Model endpoint diagnostic is not run in safe-preflight mode.",
            "manual",
            "Run `sysdialogue --check-model --model <model-name>` with the release model and paste the sanitized result.",
        )
    if step_id == "A03":
        collected_result = _collected_result(step_id, collected)
        if collected_result is not None:
            return collected_result[0], collected_result[1], "auto-read-only", collected_result[2]
        if env.get("remote_mode"):
            evidence = (
                "Remote target is configured in this runner context; run doctor to verify live connectivity."
                if run_mode == "safe-preflight"
                else "Read-only collection was requested, but doctor evidence was not returned."
            )
        else:
            evidence = "No remote target is configured in this runner context."
        return (
            "partial",
            evidence,
            "manual",
            "Run the generated `sysdialogue --doctor --remote ...` command and paste the sanitized output.",
        )
    if step_id == "A04":
        collected_result = _collected_result(step_id, collected)
        if collected_result is not None:
            return collected_result[0], collected_result[1], "auto-ui", collected_result[2]
        return (
            "partial",
            "Command surfaces are listed, but operator readability requires visual/TUI review.",
            "manual",
            "Open TUI or simple CLI and confirm /help, /examples, /playbooks, /evidence, /acceptance, and /doctor are visible.",
        )
    if step_id == "A05":
        collected_result = _collected_result(step_id, collected)
        if collected_result is not None:
            return collected_result[0], collected_result[1], "auto-conversation", collected_result[2]
        return (
            "partial",
            "Conversation behavior requires the configured model and live session.",
            "manual",
            'Ask "hello" and confirm no OS-facing tool is executed.',
        )
    if step_id == "A06":
        collected_result = _collected_result(step_id, collected)
        if collected_result is not None:
            return collected_result[0], collected_result[1], "auto-read-only", collected_result[2]
        return (
            "partial",
            (
                "Read-only workflow is intentionally not executed by safe-preflight mode."
                if run_mode == "safe-preflight"
                else "Read-only collection was requested, but security_audit evidence was not returned."
            ),
            "manual",
            "Run `sysdialogue --demo --remote ...` on the release target and attach the sanitized audit session id.",
        )
    if step_id == "A07":
        collected_result = _collected_result(step_id, collected)
        if collected_result is not None:
            return collected_result[0], collected_result[1], "operator-approved", collected_result[2]
        return (
            "partial",
            "Mutation gate requires explicit operator-approved disposable target or low-risk service.",
            "operator-approved",
            "Run a low-risk service_restart or safe_config_patch only after impact/rollback review and approval.",
        )
    if step_id == "A08":
        collected_result = _collected_result(step_id, collected)
        if collected_result is not None:
            return collected_result[0], collected_result[1], "auto-local", collected_result[2]
        return (
            "partial",
            "Interrupted-task recovery requires an operator-driven cancellation/resume drill.",
            "manual",
            "Start a safe long-running task, interrupt it, then verify /next plus /resume or /abandon.",
        )
    if step_id == "A09":
        collected_result = _collected_result(step_id, collected)
        if collected_result is not None:
            return collected_result[0], collected_result[1], "auto-replay", collected_result[2]
        return (
            "partial",
            "Replay export requires a real audit session from the acceptance run.",
            "manual",
            "Export `/export-replay <session_id>` and confirm SUMMARY.md plus sanitized JSONL are present.",
        )
    return "partial", "No automated evaluator is defined for this step.", "manual", "Collect and paste sanitized evidence."


def _checkbox(status: str) -> str:
    if status == "pass":
        return "x"
    if status == "fail":
        return "!"
    if status == "partial":
        return "~"
    return " "


def _collected_result(
    step_id: str,
    collected: dict[str, dict[str, str]],
) -> tuple[str, str, str] | None:
    item = collected.get(step_id)
    if not item:
        return None
    status = item.get("status") or "partial"
    if status not in {"pass", "partial", "fail", "missing", "unknown"}:
        status = "unknown"
    evidence = sanitize_text(item.get("evidence") or "", limit=1200)
    manual_action = sanitize_text(item.get("manual_action") or item.get("manualAction") or "", limit=500)
    return status, evidence, manual_action


def _runner_notes(mode: AcceptanceRunMode) -> tuple[str, ...]:
    if mode == "model-check":
        return (
            "Model-check mode calls the configured model exactly once with a synthetic diagnostic tool.",
            "It never dispatches OS-facing SysDialogue tools and does not touch local or remote systems.",
            "Combine this artifact with read-only collection, recovery drill, replay export, and A07 evidence before claiming full release readiness.",
        )
    if mode == "conversation-check":
        return (
            "Conversation-check mode sends one plain greeting through the configured model.",
            "It verifies that no command_trace audit records are produced for non-operational conversation.",
            "It never asks the model to inspect or mutate the local or remote system.",
            "Combine this artifact with model diagnostics, read-only collection, A07 evidence, recovery drill, and replay export before claiming full release readiness.",
        )
    if mode == "ui-review":
        return (
            "UI-review mode checks operator-facing command discoverability across slash help, TUI welcome text, and Web Release controls.",
            "It does not replace final human visual review for spacing, readability, and click targets on the release workstation.",
            "Combine this artifact with model diagnostics, read-only collection, A07 evidence, recovery drill, and replay export before claiming full release readiness.",
        )
    if mode == "operator-approved-drill":
        return (
            "Operator-approved-drill mode may execute exactly one constrained low-risk mutation workflow after the fixed approval phrase is supplied.",
            "Allowed drill workflows are service_restart and safe_config_patch; the target must be disposable or explicitly low-risk.",
            "Impact, rollback, approval prompts, mutation result, post-change verification, and audit session id are attached to A07 evidence.",
            "Run remaining partial steps on staging, a disposable host, or an explicitly approved low-risk service before release.",
        )
    if mode == "replay-export":
        return (
            "Replay-export mode writes a real sanitized replay ZIP for the supplied audit session.",
            "A09 passes only when the generated ZIP contains SUMMARY.md and JSONL audit data.",
            "Place the replay ZIP beside the acceptance artifact before running release-readiness or release-gate.",
            "Run remaining partial steps on staging, a disposable host, or an explicitly approved low-risk service before release.",
        )
    if mode == "recovery-drill":
        return (
            "Recovery-drill mode creates a synthetic interrupted task in SysDialogue's own state store.",
            "It exercises /next and /abandon, releases the synthetic lock, and never dispatches OS-facing tools.",
            "It refuses to overwrite an existing active task; finish, resume, or abandon active work before rerunning.",
            "Combine this artifact with model diagnostics, read-only collection, A07 evidence, and replay export before claiming full release readiness.",
        )
    if mode == "read-only-collect":
        return (
            "Read-only-collect mode may run non-mutating doctor and security_audit collection against the connected target.",
            "No model calls, approvals, or mutation drills are executed by this mode.",
            "Run remaining partial steps on staging, a disposable host, or an explicitly approved low-risk service before release.",
            "Use `/export-replay` or `sysdialogue --export-replay` and attach the sanitized bundle before claiming full pass.",
        )
    return (
        "Safe-preflight mode only runs local, non-mutating checks.",
        "Run partial steps on staging, a disposable host, or an explicitly approved low-risk service before release.",
        "Use `/export-replay` or `sysdialogue --export-replay` and attach the sanitized bundle before claiming full pass.",
    )
