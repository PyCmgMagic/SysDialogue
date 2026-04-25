"""FastAPI web app entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from sysdialogue.web.service import WebSessionStore


def create_web_app(config) -> FastAPI:
    app = FastAPI(title="SysDialogue Web Console")
    store = WebSessionStore(config)
    base_dir = Path(__file__).parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        session = store.session_store.ensure("default", surface="web", title="默认 Web 会话")
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"session_id": session.session_id},
        )

    @app.get("/api/sessions")
    async def list_sessions():
        return {"sessions": store.list_sessions()}

    @app.post("/api/sessions")
    async def create_session():
        return {"ok": True, "session": store.create_session()}

    @app.get("/api/session/{session_id}/state")
    async def get_state(session_id: str):
        return store.get(session_id).state()

    @app.post("/api/session/{session_id}/turn")
    async def submit_turn(session_id: str, payload: dict):
        session = store.get(session_id)
        message = (payload.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message cannot be empty")
        try:
            if session.needs_input_response():
                session.submit_turn_input(message)
            else:
                session.start_turn(message)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/session/{session_id}/command")
    async def submit_command(session_id: str, payload: dict):
        command = (payload.get("command") or payload.get("message") or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="command cannot be empty")
        try:
            reply = store.get(session_id).run_command(command)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "reply": reply}

    @app.get("/api/session/{session_id}/traces")
    async def get_traces(session_id: str):
        session = store.get(session_id)
        return {"spans": [span.__dict__ for span in session.runtime.trace_store.list_spans(session_id, limit=200)]}

    @app.get("/api/session/{session_id}/memory")
    async def get_memory(session_id: str):
        session = store.get(session_id)
        return {"records": [record.__dict__ for record in session.runtime.memory_manager.list_records(limit=100)]}

    @app.get("/api/session/{session_id}/skills")
    async def get_skills(session_id: str):
        session = store.get(session_id)
        return {"skills": [skill.__dict__ for skill in session.runtime.skill_manager.list_skills()]}

    @app.post("/api/session/{session_id}/skill")
    async def activate_skill(session_id: str, payload: dict):
        name = (payload.get("name") or "").strip()
        args = payload.get("args") or {}
        if not name:
            raise HTTPException(status_code=400, detail="skill name cannot be empty")
        if not isinstance(args, dict):
            raise HTTPException(status_code=400, detail="args must be an object")
        try:
            reply = store.get(session_id).activate_skill(name, args)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "reply": reply}

    @app.get("/api/session/{session_id}/hooks")
    async def get_hooks(session_id: str):
        session = store.get(session_id)
        return {"hooks": [hook.__dict__ for hook in session.runtime.hook_manager.list_rules()]}

    @app.get("/api/session/{session_id}/permissions/explain")
    async def explain_permissions(session_id: str, tool: str = "*", risk_level: str = "SAFE"):
        session = store.get(session_id)
        target = str(session.runtime.env_profile.get("host") or session.runtime.env_profile.get("hostname") or "")
        return session.runtime.permission_policy.explain_tool(
            tool=tool,
            args={},
            risk_level=risk_level,
            target=target,
        )

    @app.get("/api/session/{session_id}/tasks")
    async def list_tasks(session_id: str):
        return {"tasks": store.get(session_id).list_tasks()}

    @app.get("/api/session/{session_id}/tasks/{task_id}")
    async def task_detail(session_id: str, task_id: str):
        try:
            return store.get(session_id).task_detail(task_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/session/{session_id}/audit")
    async def get_audit(session_id: str):
        return store.get(session_id).audit_summary()

    @app.post("/api/session/{session_id}/audit/export")
    async def export_audit(session_id: str):
        return {"ok": True, **store.get(session_id).export_audit()}

    @app.post("/api/session/{session_id}/target")
    async def configure_target(session_id: str, payload: dict):
        try:
            summary = store.get(session_id).configure_target(payload)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "summary": summary}

    @app.get("/api/locks")
    async def list_locks():
        return {"locks": store.list_locks()}

    @app.get("/api/targets")
    async def list_targets():
        return {"targets": store.list_targets()}

    @app.post("/api/targets/test")
    async def test_target(payload: dict):
        return store.test_target(payload)

    @app.post("/api/session/{session_id}/confirm")
    async def submit_confirm(session_id: str, payload: dict):
        approved = bool(payload.get("approved"))
        decision = str(payload.get("decision") or ("once" if approved else "deny"))
        try:
            store.get(session_id).submit_confirmation(approved, decision=decision)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/session/{session_id}/cancel")
    async def cancel(session_id: str):
        store.get(session_id).cancel()
        return {"ok": True}

    @app.post("/api/session/{session_id}/resume")
    async def resume(session_id: str):
        try:
            store.get(session_id).resume()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    return app


def run_web_server(config, host: str = "127.0.0.1", port: int = 8000) -> None:
    uvicorn.run(create_web_app(config), host=host, port=port)
