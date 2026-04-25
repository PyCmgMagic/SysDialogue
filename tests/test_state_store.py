from __future__ import annotations

import json
from pathlib import Path

from sysdialogue.agent.conversation import ConversationManager
from sysdialogue.agent.conversation_store import ConversationStore
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskStore


def test_session_store_recovers_interrupted_stale_task(tmp_path: Path) -> None:
    session_store = SessionStore(str(tmp_path / "sessions"))
    task_store = TaskStore(str(tmp_path / "tasks"), stale_after=1)

    task = task_store.create(
        task_id="task_a",
        session_id="session_a",
        surface="tui",
        goal="check nginx",
        status="running",
    )
    task_store.update(task.task_id, heartbeat_ts="2000-01-01T00:00:00+00:00")
    session_store.ensure("session_a", surface="tui")
    session_store.set_status(
        "session_a",
        "running",
        surface="tui",
        active_task_id=task.task_id,
        pending_confirmation={"tool": "manage_service"},
    )

    record = session_store.recover_interrupted("session_a", task_store, surface="tui")
    recovered_task = task_store.load(task.task_id)

    assert record.status == "interrupted"
    assert record.pending_confirmation is None
    assert recovered_task is not None
    assert recovered_task.status == "interrupted"


def test_session_store_restores_manager_context(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "sessions"))
    manager = ConversationManager()
    manager.context["service_name"] = "nginx"
    manager.history = [
        {"role": "user", "content": "检查 nginx"},
        {"role": "assistant", "content": [{"type": "text", "text": "检查完成。"}]},
    ]
    store.sync_manager("session_a", manager, surface="web")

    restored = ConversationManager()
    store.restore_to_manager("session_a", restored)

    assert restored.context == {"service_name": "nginx"}
    assert restored.history[-1]["content"] == [{"type": "text", "text": "检查完成。"}]


def test_session_store_tags_entries_with_active_task_id(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "sessions"))

    store.append_user_turn("session_a", "check load", surface="web", active_task_id="task_a")
    store.append_entry("session_a", "assistant", "done", surface="web", task_id="task_a")
    record = store.load("session_a")

    assert record is not None
    assert record.entries[-2]["task_id"] == "task_a"
    assert record.entries[-1]["task_id"] == "task_a"


def test_conversation_store_defaults_to_shared_sessions_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    store = ConversationStore()

    assert store._store.storage_dir == tmp_path / ".sysdialogue" / "sessions"


def test_lock_store_reclaims_stale_lease(tmp_path: Path) -> None:
    store = LockStore(str(tmp_path / "locks"), stale_after=1)

    first = store.acquire(
        "file:/etc/nginx/nginx.conf",
        task_id="task_1",
        session_id="session_1",
        surface="tui",
        timeout=0.5,
    )
    assert first is not None

    path = next((tmp_path / "locks").glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["heartbeat_ts"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    reclaimed: list[str] = []
    second = store.acquire(
        "file:/etc/nginx/nginx.conf",
        task_id="task_2",
        session_id="session_2",
        surface="web",
        timeout=0.5,
        on_stale_reclaim=lambda lease: reclaimed.append(lease.task_id),
    )

    assert second is not None
    assert second.task_id == "task_2"
    assert reclaimed == ["task_1"]


def test_task_store_updates_steps_and_events(tmp_path: Path) -> None:
    store = TaskStore(str(tmp_path / "tasks"))
    task = store.create(
        task_id="task_steps",
        session_id="session_steps",
        surface="simple",
        goal="run a plan",
        mode="plan",
    )
    store.set_steps(
        task.task_id,
        [
            {
                "step_id": "step_1",
                "status": "pending",
                "kind": "plan_step",
                "tool": "get_system_info",
                "purpose": "observe",
                "args": {},
            }
        ],
    )
    store.update_step(task.task_id, "step_1", status="completed")
    store.append_event(
        task.task_id,
        {
            "ts": "2026-04-23T00:00:00+00:00",
            "stage": "task_started",
            "message": "started",
            "data": {"mode": "plan"},
        },
    )

    loaded = store.load(task.task_id)

    assert loaded is not None
    assert loaded.steps[0].status == "completed"
    assert loaded.events[-1].stage == "task_started"
