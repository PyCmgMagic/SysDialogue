"""Operator-approved mutation drill collection for release acceptance."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Mapping

from sysdialogue.agent.workflow_engine import WorkflowEngine
from sysdialogue.security.output_sanitizer import sanitize_text, sanitize_value

A07_APPROVAL_PHRASE = "I APPROVE A07 MUTATION DRILL"
ALLOWED_A07_WORKFLOWS = frozenset({"service_restart", "safe_config_patch"})

_SERVICE_RESTART_CONFIRM_TOOLS = frozenset(
    {
        "workflow:confirm:s2",
        "manage_service",
    }
)
_SAFE_CONFIG_CONFIRM_TOOLS = frozenset(
    {
        "workflow:approval:s4",
        "replace_in_file",
        "backup_path",
        "manage_service",
    }
)


def collect_operator_approved_mutation_drill_evidence(
    controller: Any,
    request: Mapping[str, Any],
    *,
    workflows_dir: str | Path | None = None,
) -> dict[str, dict[str, str]]:
    """Run one constrained mutating workflow and return A07 evidence.

    This collector is intentionally not part of the safe preflight or
    read-only paths. Callers must provide the fixed approval phrase, a
    disposable/low-risk target assertion, and impact plus rollback text before
    the collector will temporarily auto-approve the built-in workflow prompts.
    """

    workflow_name = str(request.get("workflow_name") or request.get("workflowName") or "").strip()
    args = request.get("args") or {}
    if not isinstance(args, dict):
        return {"A07": _fail("Drill args must be a JSON object.")}
    validation_error = _validate_drill_request(workflow_name, args, request)
    if validation_error:
        return {"A07": _fail(validation_error)}

    allowed_confirm_tools = (
        _SERVICE_RESTART_CONFIRM_TOOLS
        if workflow_name == "service_restart"
        else _SAFE_CONFIG_CONFIRM_TOOLS
    )
    confirmations: list[dict[str, Any]] = []
    old_confirm_callback = controller.confirm_callback

    def approve_drill_confirmation(req: Any) -> bool:
        display = req.to_display() if hasattr(req, "to_display") else {
            "tool": getattr(req, "tool", ""),
            "reason": getattr(getattr(req, "risk", None), "reason", ""),
        }
        confirmations.append(display)
        return str(getattr(req, "tool", "")) in allowed_confirm_tools

    root = Path(workflows_dir) if workflows_dir else Path(__file__).parent.parent / "workflows"
    task_id = f"acceptance_a07_{uuid.uuid4().hex[:10]}"
    if getattr(controller, "task_store", None) is not None:
        controller.task_store.create(
            task_id=task_id,
            session_id=getattr(controller, "session_id", ""),
            surface=getattr(controller, "surface", "acceptance"),
            goal=f"A07 operator-approved mutation drill: {workflow_name}",
            mode="workflow",
            status="running",
            current_phase="act",
        )

    controller.confirm_callback = approve_drill_confirmation
    controller.bind_task(task_id)
    try:
        execution = WorkflowEngine(controller=controller, workflows_dir=root).run(workflow_name, args)
    except Exception as exc:
        return {"A07": _fail(f"Mutation drill failed before workflow completion: {type(exc).__name__}: {exc}")}
    finally:
        controller.unbind_task()
        controller.confirm_callback = old_confirm_callback

    if getattr(controller, "task_store", None) is not None:
        try:
            controller.task_store.update(
                task_id,
                status="completed" if execution.final_status == "completed" else "failed",
                current_phase="finish",
            )
        except Exception:
            pass

    status = "pass" if execution.final_status == "completed" else "fail"
    manual_action = "" if status == "pass" else "Review workflow failure, rollback state, and sanitized replay evidence before release."
    evidence = {
        "workflow": workflow_name,
        "final_status": execution.final_status,
        "final_message": execution.final_message,
        "audit_session_id": getattr(controller.audit_log, "session_id", ""),
        "task_id": task_id,
        "impact": request.get("impact") or "",
        "rollback": request.get("rollback") or "",
        "post_change_verification": request.get("verification") or request.get("post_change_verification") or "",
        "confirmations": confirmations,
        "steps": {
            step_id: {"status": result.status, "error": result.error}
            for step_id, result in execution.steps_state.items()
        },
    }
    return {
        "A07": {
            "status": status,
            "evidence": _trim("Operator-approved A07 mutation drill: " + str(sanitize_value(evidence))),
            "manual_action": manual_action,
        }
    }


def _validate_drill_request(
    workflow_name: str,
    args: dict[str, Any],
    request: Mapping[str, Any],
) -> str:
    if workflow_name not in ALLOWED_A07_WORKFLOWS:
        return "A07 drill only allows service_restart or safe_config_patch workflows."
    if str(request.get("approval_phrase") or request.get("approvalPhrase") or "").strip() != A07_APPROVAL_PHRASE:
        return f"Approval phrase must exactly match: {A07_APPROVAL_PHRASE}"
    if not bool(request.get("disposable_target") or request.get("disposableTarget")):
        return "A07 drill requires disposableTarget=true for the chosen target."
    for key in ("impact", "rollback"):
        if len(str(request.get(key) or "").strip()) < 12:
            return f"A07 drill requires a concrete {key} statement."
    verification = request.get("verification") or request.get("post_change_verification")
    if len(str(verification or "").strip()) < 12:
        return "A07 drill requires a concrete post-change verification statement."
    if workflow_name == "service_restart":
        if not str(args.get("service_name") or "").strip():
            return "service_restart drill requires args.service_name."
    if workflow_name == "safe_config_patch":
        for key in ("file_path", "search_text", "replace_text"):
            if not str(args.get(key) or "").strip():
                return f"safe_config_patch drill requires args.{key}."
    return ""


def _fail(message: str) -> dict[str, str]:
    return {
        "status": "fail",
        "evidence": _trim(message),
        "manual_action": "Provide an explicit low-risk or disposable target, approval phrase, impact, rollback, and verification evidence.",
    }


def _trim(value: str) -> str:
    return sanitize_text(value, limit=1800)
