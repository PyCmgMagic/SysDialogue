from __future__ import annotations

from sysdialogue.agent.state_store import SessionStore, TaskStore
from sysdialogue.app.config import AppConfig
from sysdialogue.web.service import WebSession


def test_web_session_marks_unowned_pending_interaction_interrupted(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    task_store = TaskStore()
    session_store = SessionStore()
    task = task_store.create(
        task_id="task_pending",
        session_id="web_a",
        surface="web",
        goal="restart nginx",
        status="running",
    )
    session_store.ensure("web_a", surface="web")
    session_store.set_status(
        "web_a",
        "waiting_confirm",
        surface="web",
        active_task_id=task.task_id,
        pending_confirmation={
            "tool": "manage_service",
            "reason": "restart nginx",
            "risk_level": "WARN-HIGH",
            "owner_pid": 0,
        },
    )

    session = WebSession(AppConfig(api_key="sk-test", model="test-model"), "web_a")
    state = session.state()
    session.close()

    assert state["status"] == "interrupted"
    assert state["pending_confirmation"] is None
    assert task_store.load(task.task_id).status == "interrupted"
