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
            "index.html",
            {
                "request": request,
                "session_id": session.session_id,
            },
        )

    @app.get("/api/session/{session_id}/state")
    async def get_state(session_id: str):
        return store.get(session_id).state()

    @app.post("/api/session/{session_id}/turn")
    async def submit_turn(session_id: str, payload: dict):
        session = store.get(session_id)
        message = (payload.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message 不能为空")
        try:
            if session.pending_input is not None:
                session.submit_turn_input(message)
            else:
                session.start_turn(message)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

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

    return app


def run_web_server(config, host: str = "127.0.0.1", port: int = 8000) -> None:
    uvicorn.run(create_web_app(config), host=host, port=port)
