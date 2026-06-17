"""HTTP bridge for the SysDialogue web console.

The browser cannot safely open SSH sockets by itself. This module exposes a
small FastAPI surface that binds the web UI to the existing SysDialogue runtime:
SSH/local connections, terminal execution, agent turns, tools, workflows, and
audit export all flow through the same executor and safety stack used by the CLI.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal

import yaml
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sysdialogue.app.acceptance_collection import (
    collect_conversation_acceptance_evidence,
    collect_model_diagnostic_acceptance_evidence,
    collect_read_only_acceptance_evidence,
    collect_recovery_acceptance_evidence,
    collect_ui_acceptance_evidence,
)
from sysdialogue.app.mutation_drill import collect_operator_approved_mutation_drill_evidence
from sysdialogue.agent.acceptance_checklist import render_acceptance_checklist
from sysdialogue.agent.acceptance_bundle import (
    acceptance_bundle_to_web_payload,
    build_acceptance_bundle_from_text,
)
from sysdialogue.agent.acceptance_runner import (
    guided_acceptance_to_dict,
    render_guided_acceptance_run,
    run_guided_acceptance,
)
from sysdialogue.agent.controller import OpenAIChatClient
from sysdialogue.agent.release_readiness import (
    analyze_release_readiness_text,
    release_readiness_to_dict,
    render_release_readiness,
)
from sysdialogue.app.config import AppConfig, load_config
from sysdialogue.app.runtime_factory import RuntimeBundle, RuntimeStartupError, create_runtime
from sysdialogue.audit.serializers import export_audit_jsonl, export_replay_package
from sysdialogue.security.approval_rules import ConfirmationRequest
from sysdialogue.security.output_sanitizer import sanitize_text, sanitize_value
from sysdialogue.tools.registry import default_registry

SafetyProfile = Literal["standard", "operator", "break_glass"]
ConnectionMode = Literal["local", "ssh"]
TaskSource = Literal["agent", "tool", "workflow", "terminal"]
TaskStatus = Literal["running", "waiting_approval", "completed", "failed", "cancelled"]

_DEFAULT_WORKFLOWS_DIR = Path(__file__).parent.parent / "workflows"
_READ_ONLY_TOOLS = {
    "get_system_info",
    "get_disk_usage",
    "find_files",
    "list_processes",
    "get_port_status",
    "get_network_info",
    "read_log",
    "read_file",
    "get_resource_stats",
    "list_directory",
    "stat_path",
    "search_file_content",
    "validate_config",
    "resolve_dns",
    "check_endpoint",
}
_HIGH_RISK_TOOLS = {
    "delete_path",
    "kill_process",
    "manage_power",
    "manage_firewall",
    "manage_mount",
    "manage_service",
    "manage_container",
    "manage_cron",
    "manage_authorized_keys",
    "delete_user",
    "write_file",
    "replace_in_file",
}


class RuntimeConfigIn(BaseModel):
    apiUrl: str = ""
    model: str = ""
    openaiBaseUrl: str = ""
    maxIterations: int = 160
    workflowsDir: str = ""
    safetyProfile: SafetyProfile = "standard"
    streamEvents: bool = True


class ServerConnectionIn(BaseModel):
    id: str = ""
    name: str = ""
    mode: ConnectionMode = "ssh"
    host: str = ""
    port: int = 22
    user: str = ""
    keyFile: str = ""
    password: str = ""
    sudoPassword: str = ""
    fingerprint: str = ""
    status: str = "offline"
    latencyMs: int = 0
    distro: str = ""
    kernel: str = ""
    safetyProfile: SafetyProfile = "standard"


class ConnectionRequest(BaseModel):
    connection: ServerConnectionIn
    runtimeConfig: RuntimeConfigIn = Field(default_factory=RuntimeConfigIn)


class TaskRequest(BaseModel):
    serverId: str
    goal: str
    runtimeConfig: RuntimeConfigIn = Field(default_factory=RuntimeConfigIn)


class ApprovalRequestIn(BaseModel):
    approved: bool
    runtimeConfig: RuntimeConfigIn = Field(default_factory=RuntimeConfigIn)


class CommandRequest(BaseModel):
    serverId: str
    command: str
    runtimeConfig: RuntimeConfigIn = Field(default_factory=RuntimeConfigIn)


class NamedRunRequest(BaseModel):
    serverId: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    runtimeConfig: RuntimeConfigIn = Field(default_factory=RuntimeConfigIn)


class ReleaseReadinessRequest(BaseModel):
    content: str = ""
    source: str = "web-submission"


class AcceptanceBundleRequest(BaseModel):
    content: str = ""
    source: str = "web-submission"
    serverId: str = ""


class AcceptanceMutationDrillRequest(BaseModel):
    serverId: str = ""
    workflowName: str = "service_restart"
    args: dict[str, Any] = Field(default_factory=dict)
    approvalPhrase: str = ""
    impact: str = ""
    rollback: str = ""
    verification: str = ""
    disposableTarget: bool = False


@dataclass
class PendingApproval:
    id: str
    server_id: str
    task_id: str
    source: TaskSource
    title: str
    tool: str
    reason: str
    risk: str
    rollback: str
    request: dict[str, Any]


@dataclass
class WebSession:
    server_id: str
    bundle: RuntimeBundle
    connection: ServerConnectionIn
    runtime_config: RuntimeConfigIn
    server_payload: dict[str, Any]
    lock: RLock = field(default_factory=RLock)
    pending_approval_id: str = ""
    terminal_cwd: str = ""

    def close(self) -> None:
        self.bundle.close()


class WebSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, WebSession] = {}
        self._pending: dict[str, PendingApproval] = {}
        self._latest_server_id = ""
        self._lock = RLock()

    def connect(self, connection: ServerConnectionIn, runtime_config: RuntimeConfigIn) -> dict[str, Any]:
        server_id = connection.id or f"srv_{uuid.uuid4().hex[:10]}"
        connection = connection.model_copy(update={"id": server_id})
        app_config = _build_app_config(connection, runtime_config)

        def confirm_callback(req: ConfirmationRequest) -> bool:
            approval = self._store_pending_approval(
                server_id=server_id,
                task_id=f"task_{uuid.uuid4().hex[:10]}",
                source="agent",
                title=f"审批 {req.tool}",
                request={"kind": "agent", "goal": ""},
                req=req,
            )
            with self._lock:
                session = self._sessions.get(server_id)
                if session is not None:
                    session.pending_approval_id = approval.id
            return False

        started = time.perf_counter()
        try:
            bundle = create_runtime(
                app_config,
                require_api=_has_llm_config(app_config),
                confirm_callback=confirm_callback,
                surface="web",
            )
        except RuntimeStartupError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        payload = _server_payload(connection, bundle, latency_ms)
        session = WebSession(
            server_id=server_id,
            bundle=bundle,
            connection=connection,
            runtime_config=runtime_config,
            server_payload=payload,
        )
        with self._lock:
            previous = self._sessions.get(server_id)
            self._sessions[server_id] = session
            self._latest_server_id = server_id
        if previous is not None:
            previous.close()
        return payload

    def latest(self) -> WebSession | None:
        with self._lock:
            if self._latest_server_id:
                return self._sessions.get(self._latest_server_id)
            return next(reversed(self._sessions.values()), None) if self._sessions else None

    def get(self, server_id: str) -> WebSession:
        with self._lock:
            session = self._sessions.get(server_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Server session is not connected.")
        return session

    def overview(self) -> dict[str, Any]:
        session = self.latest()
        workflows_dir = (
            Path(session.runtime_config.workflowsDir)
            if session and session.runtime_config.workflowsDir
            else _DEFAULT_WORKFLOWS_DIR
        )
        return {
            "tools": _tool_catalog(),
            "workflows": _workflow_catalog(workflows_dir),
            "metrics": _metrics_from_session(session),
            "audit": _audit_records(session),
        }

    def run_task(self, request: TaskRequest) -> dict[str, Any]:
        session = self.get(request.serverId)
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        title = request.goal.strip() or "SysDialogue task"
        if not request.goal.strip():
            raise HTTPException(status_code=400, detail="goal is required")
        self._refresh_llm_client(session, request.runtimeConfig)
        with session.lock:
            self._clear_session_pending(session)
            try:
                reply = session.bundle.controller.run_turn(request.goal)
            except Exception as exc:
                return _task_response(
                    task_id=task_id,
                    title=title,
                    source="agent",
                    status="failed",
                    reply=str(exc),
                    audit=_audit_records(session),
                    events=[_event("agent_error", str(exc), "danger")],
                )
            pending = self._consume_pending(session, task_id=task_id, source="agent", title=title, request={"kind": "agent", "goal": request.goal})
            if pending is not None:
                return _approval_response(pending, audit=_audit_records(session))
            return _task_response(
                task_id=task_id,
                title=title,
                source="agent",
                status="completed",
                reply=reply,
                audit=_audit_records(session),
                events=[_event("agent_reply", _preview(reply), "success")],
            )

    def run_command(self, request: CommandRequest) -> dict[str, Any]:
        session = self.get(request.serverId)
        command = request.command.strip()
        if not command:
            raise HTTPException(status_code=400, detail="command is required")
        with session.lock:
            wrapped_command = _terminal_command(command, session.connection.mode)
            output, exit_code = session.bundle.executor.run_shell(wrapped_command, timeout=60, cwd=session.terminal_cwd or None)
            output, cwd = _extract_terminal_cwd(output)
            if cwd:
                session.terminal_cwd = cwd
            session.bundle.audit_log.log_command(
                tool="terminal",
                cmd=["shell", command],
                exit_code=exit_code,
                output_preview=output,
            )
        lines = output.splitlines() if output else []
        if exit_code != 0:
            lines.append(f"[exit {exit_code}]")
        return {"lines": lines, "cwd": session.terminal_cwd, "audit": _audit_records(session)}

    def run_tool(self, request: NamedRunRequest) -> dict[str, Any]:
        session = self.get(request.serverId)
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        title = f"工具 {request.name}"
        with session.lock:
            self._clear_session_pending(session)
            block = session.bundle.controller._dispatch_tool(  # noqa: SLF001 - web bridge to existing safety dispatcher.
                request.name,
                request.args,
                f"web_tool_{uuid.uuid4().hex[:8]}",
            )
            pending = self._consume_pending(
                session,
                task_id=task_id,
                source="tool",
                title=title,
                request={"kind": "tool", "name": request.name, "args": request.args},
            )
            if pending is not None:
                return _approval_response(pending, audit=_audit_records(session))
        content = str(block.get("content") or "")
        is_error = bool(block.get("is_error"))
        return _task_response(
            task_id=task_id,
            title=title,
            source="tool",
            status="failed" if is_error else "completed",
            reply=content,
            audit=_audit_records(session),
            events=[_event("tool_result", _preview(content), "danger" if is_error else "success")],
        )

    def run_workflow(self, request: NamedRunRequest) -> dict[str, Any]:
        session = self.get(request.serverId)
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        title = f"工作流 {request.name}"
        with session.lock:
            self._clear_session_pending(session)
            controller = session.bundle.controller
            controller.bind_task(task_id)
            try:
                execution = controller._get_workflow_engine().run(request.name, request.args)  # noqa: SLF001
            except Exception as exc:
                controller.unbind_task()
                return _task_response(
                    task_id=task_id,
                    title=title,
                    source="workflow",
                    status="failed",
                    reply=str(exc),
                    audit=_audit_records(session),
                    events=[_event("workflow_error", str(exc), "danger")],
                )
            finally:
                controller.unbind_task()
            pending = self._consume_pending(
                session,
                task_id=task_id,
                source="workflow",
                title=title,
                request={"kind": "workflow", "name": request.name, "args": request.args},
            )
            if pending is not None:
                return _approval_response(pending, audit=_audit_records(session))
        status: TaskStatus = "completed" if execution.final_status == "completed" else "failed"
        return _task_response(
            task_id=task_id,
            title=title,
            source="workflow",
            status=status,
            reply=execution.final_message,
            audit=_audit_records(session),
            events=[
                _event(
                    "workflow_finished",
                    f"{execution.workflow_name}: {execution.final_status}",
                    "success" if status == "completed" else "danger",
                )
            ],
        )

    def resolve_approval(self, approval_id: str, request: ApprovalRequestIn) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.pop(approval_id, None)
        if pending is None:
            raise HTTPException(status_code=404, detail="Approval request was not found.")
        session = self.get(pending.server_id)
        if not request.approved:
            return _task_response(
                task_id=pending.task_id,
                title=pending.title,
                source=pending.source,
                status="cancelled",
                reply="审批已拒绝，未执行该操作。",
                audit=_audit_records(session),
                events=[_event("approval_denied", pending.reason, "warning")],
            )
        target = str(session.bundle.env_profile.get("host") or session.bundle.env_profile.get("hostname") or "")
        session.bundle.permission_policy.grant_for_session(kind="tool", value=pending.tool, target=target)
        kind = pending.request.get("kind")
        if kind == "tool":
            return self.run_tool(
                NamedRunRequest(
                    serverId=pending.server_id,
                    name=str(pending.request.get("name") or ""),
                    args=dict(pending.request.get("args") or {}),
                    runtimeConfig=request.runtimeConfig,
                )
            )
        if kind == "workflow":
            return self.run_workflow(
                NamedRunRequest(
                    serverId=pending.server_id,
                    name=str(pending.request.get("name") or ""),
                    args=dict(pending.request.get("args") or {}),
                    runtimeConfig=request.runtimeConfig,
                )
            )
        return self.run_task(
            TaskRequest(
                serverId=pending.server_id,
                goal=str(pending.request.get("goal") or ""),
                runtimeConfig=request.runtimeConfig,
            )
        )

    def export_audit(self, format_name: str) -> dict[str, str]:
        session = self.latest()
        if session is None:
            raise HTTPException(status_code=404, detail="No connected session to export.")
        if format_name == "jsonl":
            path = export_audit_jsonl(session.bundle.audit_log)
            return {"fileName": path.name, "content": path.read_text(encoding="utf-8"), "encoding": "utf-8"}
        if format_name == "replay":
            path = export_replay_package(session.bundle.audit_log)
            return {
                "fileName": path.name,
                "content": base64.b64encode(path.read_bytes()).decode("ascii"),
                "encoding": "base64",
            }
        raise HTTPException(status_code=400, detail="format must be jsonl or replay")

    def acceptance_checklist(self, server_id: str = "") -> dict[str, Any]:
        session = self.get(server_id) if server_id else self.latest()
        env = _release_env(session)
        text = render_acceptance_checklist(env)
        return {
            "text": text,
            "target": _release_target(env),
            "connected": session is not None,
        }

    def acceptance_runner(self, server_id: str = "", mode: str = "safe-preflight") -> dict[str, Any]:
        session = self.get(server_id) if server_id else self.latest()
        if mode not in {"safe-preflight", "model-check", "conversation-check", "ui-review", "read-only-collect", "recovery-drill"}:
            raise HTTPException(status_code=400, detail="mode must be safe-preflight, model-check, conversation-check, ui-review, read-only-collect, or recovery-drill")
        if mode == "read-only-collect" and session is None:
            raise HTTPException(status_code=404, detail="A connected session is required for read-only collection.")
        if mode == "model-check" and session is None:
            raise HTTPException(status_code=404, detail="A connected session with model configuration is required for model-check collection.")
        if mode == "conversation-check" and session is None:
            raise HTTPException(status_code=404, detail="A connected session with model configuration is required for conversation-check collection.")
        if mode == "recovery-drill" and session is None:
            raise HTTPException(status_code=404, detail="A connected session is required for recovery-drill collection.")
        env = _release_env(session)
        collected = None
        if mode == "read-only-collect" and session is not None:
            collected = collect_read_only_acceptance_evidence(
                session.bundle.controller,
                workflows_dir=session.runtime_config.workflowsDir or None,
            )
        elif mode == "model-check" and session is not None:
            llm_client = getattr(session.bundle.controller, "llm_client", None)
            if llm_client is None:
                config = _build_app_config(session.connection, session.runtime_config)
                if not _has_llm_config(config):
                    raise HTTPException(status_code=400, detail="Model-check requires OPENAI_API_KEY and a configured model.")
                llm_client = OpenAIChatClient(
                    api_key=config.api_key,
                    base_url=config.base_url or None,
                    model=config.model,
                )
            collected = collect_model_diagnostic_acceptance_evidence(llm_client)
        elif mode == "conversation-check" and session is not None:
            collected = collect_conversation_acceptance_evidence(session.bundle.controller)
        elif mode == "ui-review":
            collected = collect_ui_acceptance_evidence(session.bundle.controller if session is not None else None)
        elif mode == "recovery-drill" and session is not None:
            collected = collect_recovery_acceptance_evidence(session.bundle.controller)
        run = run_guided_acceptance(env, mode=mode, collected=collected)
        artifact = render_guided_acceptance_run(run)
        readiness = analyze_release_readiness_text(artifact, source="web-acceptance-runner")
        return {
            "artifact": artifact,
            "target": _release_target(env),
            "connected": session is not None,
            "run": guided_acceptance_to_dict(run),
            "readiness": release_readiness_to_dict(readiness),
            "report": render_release_readiness(readiness),
        }

    def release_readiness(self, request: ReleaseReadinessRequest) -> dict[str, Any]:
        readiness = analyze_release_readiness_text(
            request.content or "",
            source=sanitize_text(request.source or "web-submission", limit=120),
        )
        return {
            "report": render_release_readiness(readiness),
            "readiness": release_readiness_to_dict(readiness),
        }

    def acceptance_bundle(self, request: AcceptanceBundleRequest) -> dict[str, Any]:
        session = self.get(request.serverId) if request.serverId else self.latest()
        env = _release_env(session)
        bundle = build_acceptance_bundle_from_text(
            request.content or "",
            source=sanitize_text(request.source or "web-submission", limit=120),
            target=_release_target(env),
            checklist_text=render_acceptance_checklist(env),
        )
        return acceptance_bundle_to_web_payload(bundle)

    def acceptance_mutation_drill(self, request: AcceptanceMutationDrillRequest) -> dict[str, Any]:
        session = self.get(request.serverId) if request.serverId else self.latest()
        if session is None:
            raise HTTPException(status_code=404, detail="A connected session is required for an operator-approved mutation drill.")
        env = _release_env(session)
        collected = collect_operator_approved_mutation_drill_evidence(
            session.bundle.controller,
            {
                "workflow_name": request.workflowName,
                "args": request.args,
                "approval_phrase": request.approvalPhrase,
                "impact": request.impact,
                "rollback": request.rollback,
                "verification": request.verification,
                "disposable_target": request.disposableTarget,
            },
            workflows_dir=session.runtime_config.workflowsDir or None,
        )
        run = run_guided_acceptance(env, mode="operator-approved-drill", collected=collected)
        artifact = render_guided_acceptance_run(run)
        readiness = analyze_release_readiness_text(artifact, source="web-acceptance-mutation-drill")
        return {
            "artifact": artifact,
            "target": _release_target(env),
            "connected": True,
            "run": guided_acceptance_to_dict(run),
            "readiness": release_readiness_to_dict(readiness),
            "report": render_release_readiness(readiness),
        }

    def _store_pending_approval(
        self,
        *,
        server_id: str,
        task_id: str,
        source: TaskSource,
        title: str,
        request: dict[str, Any],
        req: ConfirmationRequest,
    ) -> PendingApproval:
        risk = getattr(req.risk, "level", "WARN-HIGH")
        approval = PendingApproval(
            id=f"approval_{uuid.uuid4().hex[:10]}",
            server_id=server_id,
            task_id=task_id,
            source=source,
            title=title,
            tool=req.tool,
            reason=getattr(req.risk, "reason", "") or "该操作需要审批。",
            risk=_risk_level(risk),
            rollback=req.rollback_hint or "由对应工具或 workflow 的后置校验/回滚策略处理。",
            request=request,
        )
        with self._lock:
            self._pending[approval.id] = approval
        return approval

    def _clear_session_pending(self, session: WebSession) -> None:
        session.pending_approval_id = ""

    def _consume_pending(
        self,
        session: WebSession,
        *,
        task_id: str,
        source: TaskSource,
        title: str,
        request: dict[str, Any],
    ) -> PendingApproval | None:
        pending_id = session.pending_approval_id
        if not pending_id:
            return None
        with self._lock:
            pending = self._pending.get(pending_id)
            if pending is None:
                return None
            pending.task_id = task_id
            pending.source = source
            pending.title = title
            pending.request = request
        session.pending_approval_id = ""
        return pending

    def _refresh_llm_client(self, session: WebSession, runtime_config: RuntimeConfigIn) -> None:
        config = _build_app_config(session.connection, runtime_config)
        session.bundle.controller.max_iterations = config.max_iterations
        session.bundle.controller.safety_profile = config.safety_profile
        if _has_llm_config(config):
            session.bundle.controller.llm_client = OpenAIChatClient(
                api_key=config.api_key,
                base_url=config.base_url or None,
                model=config.model,
            )


manager = WebSessionManager()
router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "sysdialogue-web"}


@router.get("/overview")
def overview() -> dict[str, Any]:
    return manager.overview()


@router.post("/connections")
def connect(request: ConnectionRequest) -> dict[str, Any]:
    return manager.connect(request.connection, request.runtimeConfig)


@router.post("/tasks")
def run_task(request: TaskRequest) -> dict[str, Any]:
    return manager.run_task(request)


@router.post("/approvals/{approval_id}")
def resolve_approval(approval_id: str, request: ApprovalRequestIn) -> dict[str, Any]:
    return manager.resolve_approval(approval_id, request)


@router.post("/terminal/exec")
def run_command(request: CommandRequest) -> dict[str, Any]:
    return manager.run_command(request)


@router.post("/tools/run")
def run_tool(request: NamedRunRequest) -> dict[str, Any]:
    return manager.run_tool(request)


@router.post("/workflows/run")
def run_workflow(request: NamedRunRequest) -> dict[str, Any]:
    return manager.run_workflow(request)


@router.get("/audit/export")
def export_audit(format: Literal["jsonl", "replay"] = "jsonl") -> dict[str, str]:
    return manager.export_audit(format)


@router.get("/release/acceptance")
def acceptance_checklist(serverId: str = "") -> dict[str, Any]:
    return manager.acceptance_checklist(serverId)


@router.get("/release/acceptance-runner")
def acceptance_runner(serverId: str = "", mode: str = "safe-preflight") -> dict[str, Any]:
    return manager.acceptance_runner(serverId, mode)


@router.post("/release/readiness")
def release_readiness(request: ReleaseReadinessRequest) -> dict[str, Any]:
    return manager.release_readiness(request)


@router.post("/release/acceptance-bundle")
def acceptance_bundle(request: AcceptanceBundleRequest) -> dict[str, Any]:
    return manager.acceptance_bundle(request)


@router.post("/release/mutation-drill")
def acceptance_mutation_drill(request: AcceptanceMutationDrillRequest) -> dict[str, Any]:
    return manager.acceptance_mutation_drill(request)


def create_app() -> FastAPI:
    app = FastAPI(title="SysDialogue Web API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/health")
    def root_health() -> dict[str, str]:
        return {"status": "ok", "service": "sysdialogue-web"}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("SYSDIALOGUE_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("SYSDIALOGUE_WEB_PORT", "8000"))
    uvicorn.run("sysdialogue.app.web_api:app", host=host, port=port, reload=False)


def _build_app_config(connection: ServerConnectionIn, runtime_config: RuntimeConfigIn) -> AppConfig:
    if connection.mode == "ssh" and not connection.host.strip():
        raise HTTPException(status_code=400, detail="SSH host is required.")
    ssh = None
    if connection.mode == "ssh":
        ssh = {
            "host": connection.host.strip(),
            "port": int(connection.port or 22),
            "user": connection.user.strip() or "root",
            "key_file": os.path.expanduser(connection.keyFile.strip()) if connection.keyFile.strip() else "",
            "password": connection.password or "",
            "sudo_password": connection.sudoPassword or "",
        }
    config = load_config(
        model=runtime_config.model.strip() or None,
        remote=connection.mode == "ssh",
        ssh=ssh,
        safety_profile=runtime_config.safetyProfile,
    )
    if runtime_config.openaiBaseUrl.strip():
        config.base_url = runtime_config.openaiBaseUrl.strip()
    if runtime_config.model.strip():
        config.model = runtime_config.model.strip()
    if runtime_config.workflowsDir.strip():
        config.workflows_dir = runtime_config.workflowsDir.strip()
    config.max_iterations = max(20, min(300, int(runtime_config.maxIterations or 160)))
    config.safety_profile = runtime_config.safetyProfile
    return config


def _has_llm_config(config: AppConfig) -> bool:
    return bool((config.api_key or os.environ.get("OPENAI_API_KEY")) and config.model)


def _server_payload(connection: ServerConnectionIn, bundle: RuntimeBundle, latency_ms: int) -> dict[str, Any]:
    env = bundle.env_profile
    if connection.mode == "local":
        host = "localhost"
        port = 0
        user = str(env.get("current_user") or connection.user or "")
        key_file = ""
    else:
        host = str(env.get("host") or connection.host or "localhost")
        port = int(env.get("ssh_port") or connection.port or 22)
        user = connection.user or str(env.get("current_user") or "")
        key_file = connection.keyFile
    return {
        "id": connection.id,
        "name": "Local executor" if connection.mode == "local" else f"{user or 'root'}@{host}",
        "mode": connection.mode,
        "host": host,
        "port": port,
        "user": user,
        "keyFile": key_file,
        "fingerprint": _ssh_fingerprint(bundle),
        "status": "online",
        "latencyMs": latency_ms,
        "distro": str(env.get("os_release") or env.get("distro_id") or "unknown"),
        "kernel": str(env.get("kernel_version") or "unknown"),
        "safetyProfile": connection.safetyProfile,
        "lastSeen": _now(),
    }


def _release_target(env: dict[str, Any]) -> str:
    if env.get("remote_mode"):
        host = str(env.get("host") or env.get("ssh_host") or env.get("hostname") or "host").strip() or "host"
        port = str(env.get("ssh_port") or "22").strip() or "22"
        suffix = " via ProxyCommand" if env.get("ssh_proxy_command_configured") else ""
        return f"ssh://{host}:{port}{suffix}"
    return "local-or-placeholder"


def _release_env(session: WebSession | None) -> dict[str, Any]:
    env = dict(session.bundle.env_profile) if session is not None else {}
    if session is not None:
        env.setdefault("ssh_user", session.connection.user)
        env.setdefault("ssh_host", session.connection.host)
        env.setdefault("host", session.connection.host or env.get("host"))
        env.setdefault("ssh_port", session.connection.port)
        env.setdefault("remote_mode", session.connection.mode == "ssh")
    return env


def _ssh_fingerprint(bundle: RuntimeBundle) -> str:
    client = getattr(bundle.executor, "_client", None)
    if client is None:
        return ""
    try:
        transport = client.get_transport()
        key = transport.get_remote_server_key() if transport is not None else None
        raw = key.get_fingerprint() if key is not None else b""
    except Exception:
        return ""
    return ":".join(f"{byte:02x}" for byte in raw)


def _tool_catalog() -> list[dict[str, Any]]:
    tools = []
    for schema in default_registry().all_schemas():
        name = str(schema.get("name") or "")
        input_schema = schema.get("input_schema") or {}
        properties = input_schema.get("properties") if isinstance(input_schema, dict) else {}
        required = input_schema.get("required") if isinstance(input_schema, dict) else []
        args = sorted(list((properties or {}).keys()))
        tools.append(
            {
                "name": name,
                "category": _tool_category(name),
                "description": str(schema.get("description") or ""),
                "risk": _tool_risk(name),
                "readOnly": name in _READ_ONLY_TOOLS,
                "args": args,
                "inputSchema": _field_schema(properties or {}, required if isinstance(required, list) else []),
            }
        )
    return tools


def _workflow_catalog(workflows_dir: Path) -> list[dict[str, Any]]:
    if not workflows_dir.exists():
        return []
    workflows: list[dict[str, Any]] = []
    for path in sorted(workflows_dir.glob("*.yaml")):
        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(re.sub(r"\{\{[^}]*\}\}", "__PH__", raw)) or {}
        except Exception:
            continue
        params = data.get("parameters") or []
        steps = data.get("steps") or []
        inputs = []
        input_schema = []
        for item in params:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            label = name
            required = bool(item.get("required"))
            if required:
                label = f"{label}*"
            if label:
                inputs.append(label)
            if name:
                field: dict[str, Any] = {
                    "name": name,
                    "label": name,
                    "type": str(item.get("type") or "string"),
                    "required": required,
                    "description": str(item.get("description") or ""),
                }
                if "default" in item:
                    field["default"] = item.get("default")
                input_schema.append(field)
        workflows.append(
            {
                "name": path.stem,
                "label": str(data.get("name") or path.stem),
                "description": str(data.get("description") or ""),
                "risk": _workflow_risk(steps),
                "steps": len(steps),
                "inputs": inputs,
                "inputSchema": input_schema,
            }
        )
    return workflows


def _field_schema(properties: dict[str, Any], required: list[Any]) -> list[dict[str, Any]]:
    required_names = {str(item) for item in required}
    fields: list[dict[str, Any]] = []
    for name in sorted(properties.keys()):
        spec = properties.get(name) if isinstance(properties.get(name), dict) else {}
        field: dict[str, Any] = {
            "name": str(name),
            "label": str(name),
            "type": str(spec.get("type") or "string"),
            "required": str(name) in required_names,
            "description": str(spec.get("description") or ""),
        }
        if "default" in spec:
            field["default"] = spec.get("default")
        fields.append(field)
    return fields


def _metrics_from_session(session: WebSession | None) -> list[dict[str, Any]]:
    if session is None:
        return []
    env = session.bundle.env_profile
    return [
        _metric("sudo", bool(env.get("has_sudo")), "可提权" if env.get("has_sudo") else "无 sudo"),
        _metric("service", str(env.get("service_manager") or "unknown") != "unknown", str(env.get("service_manager") or "unknown")),
        _metric("package", str(env.get("package_manager") or "unknown") != "unknown", str(env.get("package_manager") or "unknown")),
        _metric("audit", True, f"{len(session.bundle.audit_log.read_all())} records"),
    ]


def _metric(label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"label": label, "value": 100 if ok else 32, "detail": detail, "tone": "success" if ok else "warning"}


def _terminal_command(command: str, mode: ConnectionMode) -> str:
    marker = "__SYSDIALOGUE_CWD__"
    if mode == "local" and os.name == "nt":
        return (
            f"{command}\n"
            "$__sysdialogue_code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }\n"
            f"Write-Output \"{marker}$((Get-Location).Path)\"\n"
            "exit $__sysdialogue_code"
        )
    return f"{command}\n_code=$?\nprintf '\\n{marker}%s\\n' \"$PWD\"\nexit $_code"


def _extract_terminal_cwd(output: str) -> tuple[str, str]:
    marker = "__SYSDIALOGUE_CWD__"
    if not output:
        return "", ""
    lines = output.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith(marker):
            cwd = lines[index][len(marker):].strip()
            return "\n".join(lines[:index]).strip(), cwd
    return output, ""


def _audit_records(session: WebSession | None) -> list[dict[str, Any]]:
    if session is None:
        return []
    records = [_audit_record(record, session) for record in reversed(session.bundle.audit_log.read_all()[-80:])]
    seen: dict[str, int] = {}
    for index, item in enumerate(records):
        base_id = str(item.get("id") or f"audit-{index}")
        count = seen.get(base_id, 0)
        seen[base_id] = count + 1
        item["id"] = base_id if count == 0 else f"{base_id}-{count + 1}"
    return records


def _audit_record(record: dict[str, Any], session: WebSession) -> dict[str, Any]:
    record_type = str(record.get("type") or "decision")
    if record_type not in {"decision", "command_trace", "workflow_step", "env_profile", "final"}:
        record_type = "decision"
    target = (
        record.get("tool")
        or record.get("workflow_id")
        or record.get("env_profile_id")
        or session.server_payload.get("name")
        or session.server_id
    )
    result = (
        record.get("decision")
        or record.get("status")
        or record.get("final_status")
        or (f"exit {record.get('exit_code')}" if "exit_code" in record else "")
        or "recorded"
    )
    return {
        "id": str(record.get("id") or uuid.uuid4().hex[:8]),
        "time": record.get("ts") or _now(),
        "type": record_type,
        "target": sanitize_text(str(target), limit=180),
        "result": sanitize_text(str(result), limit=240),
        "risk": _risk_level(str(record.get("risk_level") or "")),
        "ruleIds": [str(item) for item in (record.get("rule_ids") or [])],
    }


def _task_response(
    *,
    task_id: str,
    title: str,
    source: TaskSource,
    status: TaskStatus,
    reply: str = "",
    audit: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now = _now()
    task_events = events or [_event("completed", title, "success" if status == "completed" else "danger")]
    return {
        "task": {
            "id": task_id,
            "title": title,
            "source": source,
            "status": status,
            "startedAt": now,
            "finishedAt": now if status != "waiting_approval" else None,
            "events": task_events,
        },
        "messages": ([{"id": f"msg_{uuid.uuid4().hex[:8]}", "role": "assistant", "text": reply, "at": now, "taskId": task_id}] if reply else []),
        "events": task_events,
        "reply": reply,
        "audit": audit or [],
        "approval": None,
    }


def _approval_response(pending: PendingApproval, *, audit: list[dict[str, Any]]) -> dict[str, Any]:
    event = _event("approval_required", pending.reason, "warning")
    return {
        "task": {
            "id": pending.task_id,
            "title": pending.title,
            "source": pending.source,
            "status": "waiting_approval",
            "startedAt": _now(),
            "events": [event],
        },
        "events": [event],
        "messages": [],
        "reply": "",
        "audit": audit,
        "approval": {
            "id": pending.id,
            "taskId": pending.task_id,
            "tool": pending.tool,
            "reason": pending.reason,
            "risk": pending.risk,
            "rollback": pending.rollback,
        },
    }


def _event(stage: str, message: str, tone: str) -> dict[str, Any]:
    return {"id": f"ev_{uuid.uuid4().hex[:8]}", "stage": stage, "message": message, "tone": tone, "at": _now()}


def _tool_category(name: str) -> str:
    if name in _READ_ONLY_TOOLS:
        return "观测"
    if any(token in name for token in ("file", "path", "archive", "config")):
        return "文件"
    if any(token in name for token in ("user", "group", "authorized")):
        return "权限"
    if any(token in name for token in ("service", "process", "package", "container", "power")):
        return "运行时"
    if any(token in name for token in ("endpoint", "dns", "firewall", "hosts", "port", "network")):
        return "网络"
    return "自动化"


def _tool_risk(name: str) -> str:
    if name in _READ_ONLY_TOOLS:
        return "SAFE"
    if name in _HIGH_RISK_TOOLS:
        return "WARN-HIGH"
    return "LOW"


def _workflow_risk(steps: list[dict[str, Any]]) -> str:
    tool_names = [str(step.get("tool") or "") for step in steps if isinstance(step, dict)]
    if tool_names and all(name in _READ_ONLY_TOOLS for name in tool_names if name):
        return "SAFE"
    if any(name in _HIGH_RISK_TOOLS for name in tool_names):
        return "WARN-HIGH"
    return "LOW"


def _risk_level(value: str) -> str:
    normalized = str(value or "").upper().replace("_", "-")
    if normalized in {"SAFE", "LOW", "WARN-HIGH", "HARD-BLOCK"}:
        return normalized
    if normalized in {"BLOCK", "DENY", "DENIED"}:
        return "HARD-BLOCK"
    if normalized.startswith("WARN"):
        return "WARN-HIGH"
    return "LOW"


def _preview(value: object, limit: int = 320) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(sanitize_value(value), ensure_ascii=False)
    else:
        text = str(value or "")
    return sanitize_text(text, limit=limit)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
