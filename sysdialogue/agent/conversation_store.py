"""Compatibility wrapper around the shared SessionStore."""

from __future__ import annotations

from sysdialogue.agent.state_store import (
    SessionRecord as ConversationRecord,
    SessionStore,
    SessionSummary as ConversationSummary,
)


class ConversationStore:
    """Backwards-compatible history API backed by SessionStore."""

    def __init__(self, storage_dir: str | None = None):
        self._store = SessionStore(storage_dir=storage_dir)

    def save_turn(self, **kwargs) -> ConversationRecord:
        return self._store.save_turn(**kwargs)

    def load(self, session_id: str) -> ConversationRecord | None:
        return self._store.load(session_id)

    def list_summaries(self, limit: int = 30) -> list[ConversationSummary]:
        return self._store.list_summaries(limit=limit)

    def restore_to_manager(self, session_id: str, manager) -> ConversationRecord:
        return self._store.restore_to_manager(session_id, manager)
