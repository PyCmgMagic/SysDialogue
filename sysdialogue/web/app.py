"""FastAPI web app entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn

from sysdialogue.web.service import WebSessionStore


def create_web_app(config) -> FastAPI:
    app = FastAPI(title="SysDialogue Web Console")
    store = WebSessionStore(config)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        session = store.get("default")
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"session_id": session.session_id},
        )

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

    @app.post("/api/session/{session_id}/confirm")
    async def submit_confirm(session_id: str, payload: dict):
        approved = bool(payload.get("approved"))
        try:
            store.get(session_id).submit_confirmation(approved)
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
