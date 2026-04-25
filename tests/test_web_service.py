from __future__ import annotations

import os
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from sysdialogue.app.config import AppConfig
from sysdialogue.agent.state_store import SessionStore, TaskStore
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.web.app import create_web_app
from sysdialogue.web.service import (
    WebSession,
    WebSessionStore,
    _api_config_from_payload,
    _api_config_payload,
    _process_alive,
    _target_config_from_payload,
    _target_payload,
)


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
            "password": "secret",
            "ssh_key_file": str(key),
        },
    )
    local = _target_config_from_payload(remote, {"mode": "local"})

    assert remote.remote_mode is True
    assert remote.ssh_host == "example.com"
    assert remote.ssh_port == 2222
    assert remote.ssh_user == "root"
    assert remote.ssh_password == "secret"
    assert remote.ssh_sudo_password == "secret"
    assert remote.ssh_key_file == str(key)
    assert local.remote_mode is False
    assert local.ssh_host == ""
    assert local.ssh_password == ""
    assert local.ssh_sudo_password == ""


def test_web_target_config_rejects_incomplete_ssh_payload() -> None:
    base = AppConfig(api_key="key", model="model")

    try:
        _target_config_from_payload(base, {"mode": "ssh", "host": "example.com"})
    except RuntimeError as exc:
        assert "SSH 用户名不能为空" in str(exc)
    else:
        raise AssertionError("expected incomplete SSH target to fail")


def test_web_target_payload_does_not_expose_ssh_password() -> None:
    config = AppConfig(
        remote_mode=True,
        ssh_host="example.com",
        ssh_port=22,
        ssh_user="root",
        ssh_password="secret",
    )

    payload = _target_payload(config, {"os": "linux"})

    assert payload["password_configured"] is True
    assert "password" not in payload
    assert "secret" not in str(payload)


def test_web_api_config_payload_preserves_secret_and_updates_model() -> None:
    base = AppConfig(api_key="old-key", base_url="https://old.example/v1", model="old-model")

    updated = _api_config_from_payload(
        base,
        {"base_url": "https://new.example/v1", "model": "new-model", "api_key": ""},
    )
    with_key = _api_config_from_payload(updated, {"api_key": "new-secret"})
    payload = _api_config_payload(with_key)

    assert updated.api_key == "old-key"
    assert updated.base_url == "https://new.example/v1"
    assert updated.model == "new-model"
    assert with_key.api_key == "new-secret"
    assert payload == {
        "base_url": "https://new.example/v1",
        "model": "new-model",
        "api_key": "new-secret",
        "api_key_configured": True,
    }


def test_web_api_config_rejects_missing_key_or_model() -> None:
    try:
        _api_config_from_payload(AppConfig(api_key="", model="m"), {"api_key": ""})
    except RuntimeError as exc:
        assert "API Key" in str(exc)
    else:
        raise AssertionError("expected missing API key to fail")

    try:
        _api_config_from_payload(AppConfig(api_key="key", model="m"), {"model": ""})
    except RuntimeError as exc:
        assert "模型" in str(exc)
    else:
        raise AssertionError("expected missing model to fail")


def test_web_session_store_can_create_and_list_sessions(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    store = WebSessionStore(AppConfig(api_key="key", model="model"))

    created = store.create_session()
    sessions = store.list_sessions()

    assert created["session_id"].startswith("web_")
    assert any(item["session_id"] == created["session_id"] for item in sessions)


def test_web_api_config_updates_future_session_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    store = WebSessionStore(AppConfig(api_key="old-key", base_url="old-url", model="old-model"))

    current = store.get("default")
    current.configure_api(
        {
            "api_key": "new-key",
            "base_url": "https://new.example/v1",
            "model": "new-model",
        }
    )
    created = store.create_session()
    next_session = store.get(created["session_id"])

    assert next_session.config.api_key == "new-key"
    assert next_session.config.base_url == "https://new.example/v1"
    assert next_session.config.model == "new-model"


def test_web_session_list_includes_target_group_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    store = WebSessionStore(AppConfig(api_key="key", model="model"))
    store.session_store.ensure("local_session", surface="web")
    store.session_store.mutate(
        "ssh_session",
        lambda record: record.context.update(
            {"target": "ssh://root@example.com:10022", "target_mode": "remote"}
        ),
        surface="web",
    )

    sessions = {item["session_id"]: item for item in store.list_sessions()}

    assert sessions["local_session"]["target_mode"] == "local"
    assert sessions["local_session"]["target_group"]
    assert sessions["ssh_session"]["target_mode"] == "ssh"
    assert sessions["ssh_session"]["target_group"] == "SSH root@example.com:10022"


def test_web_target_test_rejects_bad_payload_without_switching_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    store = WebSessionStore(AppConfig(api_key="key", model="model"))

    result = store.test_target({"mode": "ssh", "host": "example.com"})

    assert result["ok"] is False
    assert "SSH 用户名不能为空" in result["message"]


def test_web_target_test_success_does_not_create_visible_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    store = WebSessionStore(AppConfig(api_key="key", model="model"))

    result = store.test_target({"mode": "local"})
    sessions = store.list_sessions()

    assert result["ok"] is True
    assert all(not item["session_id"].startswith("target_test_") for item in sessions)


def test_web_target_management_saves_password_without_api_echo(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / "id_ed25519"
    key.write_text("not-a-real-key", encoding="utf-8")
    store = WebSessionStore(AppConfig(api_key="key", model="model"))

    profile = store.save_target(
        {
            "mode": "ssh",
            "label": "prod",
            "host": "example.com",
            "user": "root",
            "port": "2222",
            "password": "secret",
            "ssh_key_file": str(key),
        }
    )

    assert profile["label"] == "prod"
    assert profile["facts"]["host"] == "example.com"
    assert profile["facts"]["port"] == 2222
    assert profile["facts"]["user"] == "root"
    assert profile["facts"]["ssh_key_file"] == str(key)
    assert profile["facts"]["password_configured"] is True
    assert "secret" not in str(profile)
    stored = store.target_profile_store.load(profile["target_id"])
    assert stored is not None
    assert stored.facts["ssh_password"] == "secret"
    restored = _target_config_from_payload(
        AppConfig(api_key="key", model="model"),
        {
            "mode": "ssh",
            "target_id": profile["target_id"],
            "host": "example.com",
            "user": "root",
            "port": "2222",
        },
        store.target_profile_store,
    )
    assert restored.ssh_password == "secret"
    assert restored.ssh_sudo_password == "secret"
    assert any(item["target_id"] == profile["target_id"] for item in store.list_targets())
    assert store.delete_target(profile["target_id"]) is True
    assert all(item["target_id"] != profile["target_id"] for item in store.list_targets())


def test_task_and_audit_serializers_use_session_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    session_store = SessionStore()
    task_store = TaskStore()
    task = task_store.create(
        task_id="task_web",
        session_id="web_tasks",
        surface="web",
        goal="检查系统",
        status="running",
    )
    session_store.ensure("web_tasks", surface="web")
    session = _fake_web_session("web_tasks", session_store, task_store)

    tasks = session.list_tasks()
    detail = session.task_detail(task.task_id)

    assert tasks[0]["task_id"] == "task_web"
    assert detail["task_id"] == "task_web"
    assert detail["summary"]["goal"] == "检查系统"


def test_web_app_exposes_console_routes_without_requiring_target_switch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    client = TestClient(create_web_app(AppConfig(api_key="key", model="model")))

    root = client.get("/")
    sessions = client.get("/api/sessions")
    created = client.post("/api/sessions", json={})
    api_config = client.post(
        "/api/session/default/api-config",
        json={"base_url": "https://new.example/v1", "model": "new-model", "api_key": ""},
    )
    locks = client.get("/api/locks")
    target_save = client.post(
        "/api/targets",
        json={"mode": "ssh", "label": "prod", "host": "example.com", "user": "root", "port": 22, "password": "secret"},
    )
    target_test = client.post("/api/targets/test", json={"mode": "ssh", "host": "example.com"})

    assert root.status_code == 200
    assert "/static/web.css" in root.text
    assert sessions.status_code == 200
    assert created.status_code == 200
    assert created.json()["session"]["session_id"].startswith("web_")
    assert api_config.status_code == 200
    assert api_config.json()["api_config"]["model"] == "new-model"
    assert locks.status_code == 200
    assert target_save.status_code == 200
    saved_id = target_save.json()["target"]["target_id"]
    assert target_save.json()["target"]["facts"]["password_configured"] is True
    assert "secret" not in str(target_save.json())
    assert client.delete(f"/api/targets/{saved_id}").status_code == 200
    assert target_test.status_code == 200
    assert target_test.json()["ok"] is False
    assert "SSH 用户名不能为空" in target_test.json()["message"]


def test_web_app_serializes_tasks_and_audit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    TaskStore().create(
        task_id="task_route",
        session_id="web_route",
        surface="web",
        goal="检查负载",
        status="running",
    )
    AuditLog(session_id="web_route").log_final(final_status="completed")
    client = TestClient(create_web_app(AppConfig(api_key="key", model="model")))

    tasks = client.get("/api/session/web_route/tasks")
    audit = client.get("/api/session/web_route/audit")
    exported = client.post("/api/session/web_route/audit/export", json={})

    assert tasks.status_code == 200
    assert tasks.json()["tasks"][0]["task_id"] == "task_route"
    assert audit.status_code == 200
    assert audit.json()["count"] >= 1
    assert exported.status_code == 200
    assert exported.json()["path"].endswith(".zip")
