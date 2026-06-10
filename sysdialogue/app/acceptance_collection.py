"""Opt-in read-only evidence collection for guided acceptance runs."""

from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
import uuid
import zipfile
from pathlib import Path
from typing import Any

from sysdialogue.agent.command_registry import CommandRegistry
from sysdialogue.agent.model_diagnostics import diagnose_tool_call_support
from sysdialogue.agent.state_store import TaskStepRecord
from sysdialogue.agent.workflow_engine import WorkflowEngine
from sysdialogue.audit.serializers import export_replay_package
from sysdialogue.security.output_sanitizer import sanitize_text, sanitize_value

_A04_HELP_COMMANDS = (
    "/help",
    "/examples",
    "/playbooks",
    "/evidence",
    "/acceptance",
    "/acceptance-runner",
    "/doctor",
)
_A04_COMMAND_OUTPUTS = (
    ("/examples", "Example tasks:"),
    ("/playbooks", "playbook"),
    ("/evidence", "Completion evidence matrix:"),
    ("/acceptance", "Operator acceptance checklist:"),
    ("/acceptance-runner", "Guided acceptance runner artifact:"),
)
_A04_TUI_COMMANDS = ("/examples", "/playbooks", "/evidence", "/acceptance", "/doctor", "/check-model")
_A04_WEB_RELEASE_CONTROLS = ("Checklist", "Runner", "Model", "Chat", "Collect", "Recovery", "Drill", "Replay", "Readiness", "Bundle")


def collect_read_only_acceptance_evidence(
    controller: Any,
    *,
    workflows_dir: str | Path | None = None,
) -> dict[str, dict[str, str]]:
    """Collect A03/A06 evidence without model calls or mutating workflows."""

    return {
        "A03": _collect_doctor(controller),
        "A06": _collect_security_audit(controller, workflows_dir=workflows_dir),
    }


def collect_ui_acceptance_evidence(
    controller: Any | None = None,
    *,
    web_app_path: str | Path | None = None,
) -> dict[str, dict[str, str]]:
    """Collect A04 evidence for operator-facing command surface visibility."""

    try:
        registry = CommandRegistry()
        controller = controller or SimpleNamespace(env_profile={"remote_mode": False})
        help_output = registry.execute(controller, "/help").output
        missing_help = [command for command in _A04_HELP_COMMANDS if command not in help_output]
        command_checks = {}
        command_failures: list[str] = []
        for command, marker in _A04_COMMAND_OUTPUTS:
            try:
                output = registry.execute(controller, command).output
                ok = bool(output.strip()) and marker in output
                command_checks[command] = {
                    "ok": ok,
                    "marker": marker,
                }
                if not ok:
                    command_failures.append(command)
            except Exception as exc:
                command_checks[command] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                command_failures.append(command)

        tui_result = _collect_tui_surface_review()
        web_result = _collect_web_release_surface_review(web_app_path)
        ok = not missing_help and not command_failures and tui_result["ok"] and web_result["ok"]
        evidence = {
            "help_commands_visible": [command for command in _A04_HELP_COMMANDS if command not in missing_help],
            "missing_help_commands": missing_help,
            "command_outputs": command_checks,
            "tui": tui_result,
            "web_release_controls": web_result,
        }
        return {
            "A04": {
                "status": "pass" if ok else "partial",
                "evidence": _trim("A04 command-surface review collected: " + str(sanitize_value(evidence))),
                "manual_action": "" if ok else "Open TUI/Web manually and review spacing, readability, and click targets before release.",
            }
        }
    except Exception as exc:
        return {
            "A04": {
                "status": "fail",
                "evidence": _trim(f"A04 command-surface review failed: {type(exc).__name__}: {exc}"),
                "manual_action": "Run /help, /examples, /playbooks, /evidence, /acceptance, /doctor, and inspect TUI/Web manually.",
            }
        }


def collect_model_diagnostic_acceptance_evidence(llm_client: Any) -> dict[str, dict[str, str]]:
    """Collect A02 evidence by running the synthetic model tool-call diagnostic."""

    try:
        result = diagnose_tool_call_support(llm_client)
        status = "pass" if result.ok else "fail"
        evidence = _trim(
            "Model tool-call diagnostic collected: "
            + str(
                sanitize_value(
                    {
                        "status": result.status,
                        "summary": result.summary,
                        "model": result.model,
                        "base_url": result.base_url,
                        "tool_name": result.tool_name,
                        "stop_reason": result.stop_reason,
                        "error_type": result.error_type,
                        "technical_details": result.technical_details,
                        "next_steps": result.next_steps,
                    }
                )
            )
        )
        return {
            "A02": {
                "status": status,
                "evidence": evidence,
                "manual_action": "" if result.ok else "Fix model/tool-call support before release and rerun model-check collection.",
            }
        }
    except Exception as exc:
        return {
            "A02": {
                "status": "fail",
                "evidence": _trim(f"Model tool-call diagnostic collection failed: {type(exc).__name__}: {exc}"),
                "manual_action": "Fix the model configuration or run `sysdialogue --check-model --model <model-name>` manually.",
            }
        }


def collect_conversation_acceptance_evidence(controller: Any) -> dict[str, dict[str, str]]:
    """Collect A05 evidence by running one non-operational conversation turn.

    The ReAct runner should expose only the finish tool for this prompt. This
    collector verifies the externally visible safety invariant: no command_trace
    audit records are produced while answering a plain greeting.
    """

    try:
        session_store = getattr(controller, "session_store", None)
        task_store = getattr(controller, "task_store", None)
        session_id = str(getattr(controller, "session_id", "") or "")
        surface = str(getattr(controller, "surface", "") or "acceptance")
        if session_store is not None and task_store is not None and session_id:
            record = session_store.ensure(session_id, surface=surface)
            active_task_id = str(getattr(record, "active_task_id", "") or "")
            if active_task_id:
                active = task_store.load(active_task_id)
                if active is not None and not active.is_final():
                    return {
                        "A05": {
                            "status": "partial",
                            "evidence": _trim(f"Conversation check skipped because active task {active_task_id} is still {active.status}."),
                            "manual_action": "Finish, resume, or abandon the active task before running the A05 conversation check.",
                        }
                    }

        audit = getattr(controller, "audit_log", None)
        before = audit.read_all() if audit is not None else []
        prompt = "hello"
        reply = controller.run_turn(prompt)
        after = audit.read_all() if audit is not None else []
        new_records = after[len(before):] if len(after) >= len(before) else after
        command_records = [record for record in new_records if record.get("type") == "command_trace"]
        ok = bool(str(reply or "").strip()) and not command_records
        evidence = {
            "prompt": prompt,
            "reply_preview": sanitize_text(reply, limit=400),
            "new_audit_records": len(new_records),
            "command_trace_count": len(command_records),
            "audit_session_id": getattr(audit, "session_id", ""),
        }
        return {
            "A05": {
                "status": "pass" if ok else "fail",
                "evidence": _trim("A05 non-invasive conversation check collected: " + str(sanitize_value(evidence))),
                "manual_action": "" if ok else "Review the model response and ensure ordinary conversation does not execute OS-facing tools.",
            }
        }
    except Exception as exc:
        return {
            "A05": {
                "status": "fail",
                "evidence": _trim(f"A05 conversation check failed: {type(exc).__name__}: {exc}"),
                "manual_action": "Fix model configuration or run a manual non-invasive conversation check.",
            }
        }


def collect_replay_acceptance_evidence(
    audit: Any,
    *,
    export_dir: str | Path | None = None,
) -> dict[str, dict[str, str]]:
    """Collect A09 evidence by exporting a real sanitized replay ZIP."""

    try:
        if audit is None:
            return {
                "A09": {
                    "status": "fail",
                    "evidence": "Replay export failed because no audit log is available.",
                    "manual_action": "Run `/export-replay <session_id>` or provide an existing audit session.",
                }
            }
        records = audit.read_all()
        if not records:
            return {
                "A09": {
                    "status": "fail",
                    "evidence": _trim(f"Replay export skipped because audit session {getattr(audit, 'session_id', '')} has no records."),
                    "manual_action": "Run acceptance actions first, then export replay evidence from that audit session.",
                }
            }

        replay_path = export_replay_package(audit, output_dir=str(export_dir) if export_dir else None)
        with zipfile.ZipFile(replay_path) as archive:
            names = {name.replace("\\", "/") for name in archive.namelist()}
            lower_names = {name.lower() for name in names}
            has_summary = "summary.md" in lower_names
            has_jsonl = "session.jsonl" in lower_names or "audit.jsonl" in lower_names or any(
                name.rsplit("/", 1)[-1].lower().startswith("audit_") and name.lower().endswith(".jsonl")
                for name in names
            )
            summary = {}
            if "summary.json" in lower_names:
                summary_name = next(name for name in names if name.lower() == "summary.json")
                try:
                    parsed = json.loads(archive.read(summary_name).decode("utf-8", errors="replace"))
                    summary = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    summary = {}

        ok = has_summary and has_jsonl
        evidence = {
            "replay_path": str(replay_path),
            "session_id": getattr(audit, "session_id", ""),
            "files": sorted(names),
            "total_entries": summary.get("total_entries", len(records)),
            "command_count": summary.get("command_count", 0),
            "workflow_step_count": summary.get("workflow_step_count", 0),
            "final_status": summary.get("final_status", "unknown"),
        }
        return {
            "A09": {
                "status": "pass" if ok else "fail",
                "evidence": _trim("A09 replay export collected: " + str(sanitize_value(evidence))),
                "manual_action": "" if ok else "Re-export replay evidence and confirm SUMMARY.md plus JSONL audit data are present.",
            }
        }
    except Exception as exc:
        return {
            "A09": {
                "status": "fail",
                "evidence": _trim(f"A09 replay export failed: {type(exc).__name__}: {exc}"),
                "manual_action": "Run `/export-replay <session_id>` manually and attach the replay ZIP.",
            }
        }


def collect_recovery_acceptance_evidence(controller: Any) -> dict[str, dict[str, str]]:
    """Collect A08 evidence by exercising /next and /abandon on synthetic task state.

    This mutates only SysDialogue's own session/task/lock stores. It never
    dispatches OS-facing tools and refuses to run when the current session
    already has an active task.
    """

    try:
        session_store = getattr(controller, "session_store", None)
        task_store = getattr(controller, "task_store", None)
        lock_store = getattr(controller, "lock_store", None)
        session_id = str(getattr(controller, "session_id", "") or "acceptance_recovery")
        surface = str(getattr(controller, "surface", "") or "acceptance")
        if session_store is None or task_store is None or lock_store is None:
            return {
                "A08": {
                    "status": "fail",
                    "evidence": "Recovery drill could not run because durable session/task/lock stores are unavailable.",
                    "manual_action": "Run `/next` plus `/resume` or `/abandon` manually in a durable SysDialogue session.",
                }
            }

        record = session_store.ensure(session_id, surface=surface)
        active_task_id = str(getattr(record, "active_task_id", "") or "")
        if active_task_id:
            existing = task_store.load(active_task_id)
            if existing is not None and not existing.is_final():
                return {
                    "A08": {
                        "status": "partial",
                        "evidence": _trim(f"Recovery drill skipped because active task {active_task_id} is still {existing.status}."),
                        "manual_action": "Finish, resume, or abandon the current active task before running the A08 recovery drill.",
                    }
                }

        task_id = f"acceptance_recovery_{uuid.uuid4().hex[:10]}"
        goal = "A08 recovery acceptance drill: synthetic interrupted task"
        task_store.create(
            task_id=task_id,
            session_id=session_id,
            surface=surface,
            goal=goal,
            status="interrupted",
            current_phase="resume",
            resume_message="Synthetic A08 recovery drill.",
        )
        task_store.set_steps(
            task_id,
            [
                TaskStepRecord(
                    step_id="recover",
                    status="pending",
                    kind="control",
                    purpose="Verify /next exposes recovery options before /abandon cleanup.",
                )
            ],
        )
        session_store.set_status(
            session_id,
            "interrupted",
            surface=surface,
            active_task_id=task_id,
            technical_details="Synthetic A08 recovery drill interruption.",
        )
        lock_store.acquire(
            f"acceptance:recovery-drill:{task_id}",
            task_id=task_id,
            session_id=session_id,
            surface=surface,
            timeout=0.1,
        )

        registry = CommandRegistry()
        next_output = registry.execute(controller, "/next").output
        abandon_output = registry.execute(controller, "/abandon").output
        task_after = task_store.load(task_id)
        record_after = session_store.load(session_id)
        leaked_locks = [lease for lease in lock_store.list_leases() if lease.task_id == task_id]
        ok = (
            "/resume" in next_output
            and "/abandon" in next_output
            and task_after is not None
            and task_after.status == "cancelled"
            and record_after is not None
            and not record_after.active_task_id
            and not leaked_locks
        )
        evidence = {
            "task_id": task_id,
            "goal": goal,
            "/next": next_output,
            "/abandon": abandon_output,
            "task_status_after": getattr(task_after, "status", ""),
            "active_task_after": getattr(record_after, "active_task_id", ""),
            "locks_released": not leaked_locks,
        }
        return {
            "A08": {
                "status": "pass" if ok else "partial",
                "evidence": _trim("A08 recovery drill collected: " + str(sanitize_value(evidence))),
                "manual_action": "" if ok else "Review the recovery drill result and rerun `/next` plus `/resume` or `/abandon` manually.",
            }
        }
    except Exception as exc:
        return {
            "A08": {
                "status": "fail",
                "evidence": _trim(f"A08 recovery drill failed: {type(exc).__name__}: {exc}"),
                "manual_action": "Run a manual recovery drill and attach `/next` plus `/resume` or `/abandon` evidence.",
            }
        }


def _collect_doctor(controller: Any) -> dict[str, str]:
    try:
        output = CommandRegistry().execute(controller, "/doctor").output
        return {
            "status": "pass",
            "evidence": _trim("Doctor collected successfully.\n" + output),
            "manual_action": "",
        }
    except Exception as exc:
        return {
            "status": "fail",
            "evidence": _trim(f"Doctor collection failed: {type(exc).__name__}: {exc}"),
            "manual_action": "Fix the connected runtime or run `sysdialogue --doctor --remote ...` manually.",
        }


def _collect_security_audit(controller: Any, *, workflows_dir: str | Path | None) -> dict[str, str]:
    try:
        root = Path(workflows_dir) if workflows_dir else Path(__file__).parent.parent / "workflows"
        execution = WorkflowEngine(controller=controller, workflows_dir=root).run("security_audit", {})
        status = "pass" if execution.final_status == "completed" else "partial"
        evidence = {
            "workflow": "security_audit",
            "final_status": execution.final_status,
            "final_message": execution.final_message,
            "audit_session_id": getattr(controller.audit_log, "session_id", ""),
            "steps": {
                step_id: {"status": result.status, "error": result.error}
                for step_id, result in execution.steps_state.items()
            },
        }
        return {
            "status": status,
            "evidence": _trim("Read-only security_audit collected: " + str(sanitize_value(evidence))),
            "manual_action": "" if status == "pass" else "Review workflow result and attach sanitized audit/replay evidence.",
        }
    except Exception as exc:
        return {
            "status": "fail",
            "evidence": _trim(f"Read-only security_audit collection failed: {type(exc).__name__}: {exc}"),
            "manual_action": "Run `sysdialogue --demo --remote ...` manually and attach sanitized evidence.",
        }


def _collect_tui_surface_review() -> dict[str, Any]:
    try:
        from rich.console import Console

        from sysdialogue.ui.tui_app import SysDialogueTUI

        app = SysDialogueTUI(SimpleNamespace(audit_log=SimpleNamespace(), env_profile={}))
        console = Console(file=StringIO(), record=True, width=120)
        console.print(app._build_welcome())
        rendered = console.export_text()
        missing = [command for command in _A04_TUI_COMMANDS if command not in rendered]
        missing_keys = [key for key in ("F2", "F3", "F4") if key not in rendered]
        return {
            "ok": not missing and not missing_keys,
            "commands_visible": [command for command in _A04_TUI_COMMANDS if command not in missing],
            "missing_commands": missing,
            "function_keys_visible": [key for key in ("F2", "F3", "F4") if key not in missing_keys],
            "missing_function_keys": missing_keys,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _collect_web_release_surface_review(web_app_path: str | Path | None) -> dict[str, Any]:
    try:
        path = Path(web_app_path) if web_app_path else Path(__file__).resolve().parents[2] / "web" / "src" / "App.tsx"
        text = path.read_text(encoding="utf-8")
        missing = [label for label in _A04_WEB_RELEASE_CONTROLS if label not in text]
        return {
            "ok": not missing,
            "path": str(path),
            "controls_visible": [label for label in _A04_WEB_RELEASE_CONTROLS if label not in missing],
            "missing_controls": missing,
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(web_app_path or ""),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _trim(value: str) -> str:
    return sanitize_text(value, limit=1200)
