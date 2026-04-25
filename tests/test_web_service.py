from __future__ import annotations

import os
import threading
from types import SimpleNamespace

from sysdialogue.app.config import AppConfig
from sysdialogue.agent.state_store import SessionStore, TaskStore
from sysdialogue.web.service import WebSession, _process_alive, _target_config_from_payload


def _fake_web_session(session_id: str, session_store: SessionStore, task_store: TaskStore) -> WebSession:
    session = WebSession.__new__(WebSession)
    session.runtime = SimpleNamespace(
        session_store=session_store,
        task_store=task_store,
        controller=SimpleNamespace(is_cancel_requested=lambda: False),
    )
    session.session_id = session_id
    session.pending_confirmation = None
    session.pending_input = None
    session._worker = None
    session._lock = threading.Lock()
    return session


def test_process_alive_current_process_is_safe_on_windows() -> None:
    assert _process_alive(os.getpid()) is True


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

    session = _fake_web_session("web_a", session_store, task_store)
    session._recover_unowned_pending()
    state = session_store.load("web_a")

    assert state is not None
    assert state.status == "interrupted"
    assert state.pending_confirmation is None
    assert task_store.load(task.task_id).status == "interrupted"


def test_web_session_can_resolve_persisted_confirmation_from_another_session(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    session_store = SessionStore()
    session_store.ensure("web_confirm", surface="web")
    session_store.set_status(
        "web_confirm",
        "waiting_confirm",
        surface="web",
        pending_confirmation={
            "tool": "manage_service",
            "reason": "restart nginx",
            "risk_level": "WARN-HIGH",
            "owner_pid": os.getpid(),
            "request_id": "confirm_live",
            "resolved": False,
        },
    )

    session = _fake_web_session("web_confirm", session_store, TaskStore())
    session.submit_confirmation(True, decision="always_this_session")

    record = session_store.load("web_confirm")
    assert record is not None
    assert record.status == "running"
    assert record.pending_confirmation["resolved"] is True
    assert record.pending_confirmation["approved"] is True
    assert record.pending_confirmation["decision"] == "always_this_session"


def test_web_session_can_resolve_persisted_input_from_another_session(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    session_store = SessionStore()
    session_store.ensure("web_input", surface="web")
    session_store.set_status(
        "web_input",
        "waiting_input",
        surface="web",
        pending_input={
            "prompt": "service name",
            "multiline": False,
            "owner_pid": os.getpid(),
            "request_id": "input_live",
            "resolved": False,
        },
    )

    session = _fake_web_session("web_input", session_store, TaskStore())
    assert session.needs_input_response() is True
    session.submit_turn_input("nginx")

    record = session_store.load("web_input")
    assert record is not None
    assert record.status == "running"
    assert record.pending_input["resolved"] is True
    assert record.pending_input["value"] == "nginx"


def test_web_target_config_payload_supports_local_and_ssh(tmp_path) -> None:
    key = tmp_path / "id_ed25519"
    key.write_text("not-a-real-key", encoding="utf-8")
    base = AppConfig(
        api_key="key",
        base_url="https://example.test/v1",
        model="model",
        remote_mode=False,
    )

    remote = _target_config_from_payload(
        base,
        {
            "mode": "ssh",
            "host": "example.com",
            "user": "root",
            "port": "2222",
            "ssh_key_file": str(key),
        },
    )
    local = _target_config_from_payload(remote, {"mode": "local"})

    assert remote.remote_mode is True
    assert remote.ssh_host == "example.com"
    assert remote.ssh_port == 2222
    assert remote.ssh_user == "root"
    assert remote.ssh_key_file == str(key)
    assert local.remote_mode is False
    assert local.ssh_host == ""


def test_web_target_config_rejects_incomplete_ssh_payload() -> None:
    base = AppConfig(api_key="key", model="model")

    try:
        _target_config_from_payload(base, {"mode": "ssh", "host": "example.com"})
    except RuntimeError as exc:
        assert "SSH 用户名不能为空" in str(exc)
    else:
        raise AssertionError("expected incomplete SSH target to fail")
