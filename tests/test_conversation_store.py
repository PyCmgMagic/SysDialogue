from __future__ import annotations

import json

from sysdialogue.agent.conversation import ConversationManager
from sysdialogue.agent.conversation_store import ConversationStore


def test_conversation_store_saves_sanitized_history_and_restores_context(tmp_path) -> None:
    manager = ConversationManager()
    manager.context["service_name"] = "nginx"
    manager.history = [
        {"role": "user", "content": "检查 nginx"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tool_1", "name": "read_log", "input": {}},
                {"type": "text", "text": "我会先查看状态。"},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool_1",
                    "content": "raw output should not persist",
                }
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "检查完成。"}]},
    ]
    store = ConversationStore(storage_dir=str(tmp_path))

    record = store.save_turn(
        session_id="session_a",
        manager=manager,
        user_message="检查 nginx",
        final_reply="检查完成。",
        status="completed",
        events_summary={
            "status": "已完成",
            "thinking": ["模型选择下一步调用：read_log。"],
            "details": ["raw hidden detail"],
            "results": ["检查完成。"],
        },
    )

    assert record.session_id == "session_a"
    assert record.context == {"service_name": "nginx"}
    assert "raw output should not persist" not in (tmp_path / "session_a.json").read_text(encoding="utf-8")

    restored = ConversationManager()
    loaded = store.restore_to_manager("session_a", restored)

    assert loaded.title == "检查 nginx"
    assert restored.context == {"service_name": "nginx"}
    assert all("tool_result" not in str(message) for message in restored.history)
    assert any(message["role"] == "assistant" for message in restored.history)
    assistant_messages = [message for message in restored.history if message["role"] == "assistant"]
    assert assistant_messages[0]["content"] == [{"type": "text", "text": "我会先查看状态。"}]


def test_conversation_store_lists_recent_summaries(tmp_path) -> None:
    store = ConversationStore(storage_dir=str(tmp_path))
    manager = ConversationManager()

    for index in range(35):
        store.save_turn(
            session_id=f"s_{index}",
            manager=manager,
            user_message=f"request {index}",
            final_reply="done",
            status="completed",
        )

    summaries = store.list_summaries(limit=30)

    assert len(summaries) == 30
    assert summaries[0].last_user_message.startswith("request")


def test_conversation_store_restores_legacy_assistant_string_as_typed_text(tmp_path) -> None:
    (tmp_path / "legacy.json").write_text(
        json.dumps(
            {
                "session_id": "legacy",
                "title": "legacy",
                "created_at": "2026-04-23T00:00:00+00:00",
                "updated_at": "2026-04-23T00:00:00+00:00",
                "status": "completed",
                "history": [
                    {"role": "user", "content": "上一轮问题"},
                    {"role": "assistant", "content": "上一轮回答"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = ConversationStore(storage_dir=str(tmp_path))
    manager = ConversationManager()

    store.restore_to_manager("legacy", manager)

    assert manager.history[-1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "上一轮回答"}],
    }
