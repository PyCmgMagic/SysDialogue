"""Conversation history and reusable execution context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_CANONICAL_KEYS = {
    "service_name",
    "backup_id",
    "username",
    "host",
    "port",
    "file_path",
    "container_name",
}

_ALIASES = {
    "name": "service_name",
    "endpoint_host": "host",
    "endpoint_port": "port",
}


@dataclass
class ConversationManager:
    history: list[dict] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    max_messages: int = 50

    def prepare_turn(self, user_message: str) -> list[dict]:
        return [*self.history, {"role": "user", "content": user_message}]

    def commit_turn(self, messages: list[dict]) -> None:
        self.history = messages[-self.max_messages :]

    def render_context(self) -> str:
        if not self.context:
            return "暂无已知的跨轮上下文。"
        lines = []
        for key in sorted(self.context):
            value = self.context[key]
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def observe_tool_success(self, tool: str, args: dict, result: Any) -> None:
        self._observe_args(args)
        data = getattr(result, "data", None)
        self._observe_data(data)
        if tool == "manage_service" and args.get("name"):
            self.context["service_name"] = args["name"]
        if tool == "manage_container" and args.get("name"):
            self.context["container_name"] = args["name"]

    def observe_workflow(self, workflow_name: str, params: dict, execution: Any) -> None:
        self._observe_args(params)
        steps_state = getattr(execution, "steps_state", {}) or {}
        for step in steps_state.values():
            if getattr(step, "status", "") in ("completed", "rolled_back"):
                self._observe_data(getattr(step, "data", None))
        self.context["last_workflow"] = workflow_name

    def _observe_args(self, args: dict | None) -> None:
        if not isinstance(args, dict):
            return
        for key, value in args.items():
            canonical = _ALIASES.get(key, key)
            if canonical in _CANONICAL_KEYS and value not in (None, ""):
                self.context[canonical] = value

    def _observe_data(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            canonical = _ALIASES.get(key, key)
            if canonical in _CANONICAL_KEYS and value not in (None, ""):
                self.context[canonical] = value

