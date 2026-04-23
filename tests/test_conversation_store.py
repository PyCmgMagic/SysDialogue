from __future__ import annotations

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
