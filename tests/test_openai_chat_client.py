from __future__ import annotations

from types import SimpleNamespace

from sysdialogue.agent.controller import (
    _from_openai_message,
    _to_openai_messages,
    _to_openai_tools,
)


def test_openai_tool_schema_conversion_uses_function_tools() -> None:
    tools = [
        {
            "name": "get_system_info",
            "description": "获取系统信息",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]

    converted = _to_openai_tools(tools)

    assert converted == [
        {
            "type": "function",
            "function": {
                "name": "get_system_info",
                "description": "获取系统信息",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_openai_tool_calls_convert_to_internal_tool_use_blocks() -> None:
    message = SimpleNamespace(
        content="",
        tool_calls=[
            SimpleNamespace(
                id="call_123",
                function=SimpleNamespace(
                    name="get_system_info",
                    arguments='{"detail": true}',
                ),
            )
        ],
    )

    response = _from_openai_message(message, "tool_calls")

    assert response.stop_reason == "tool_use"
    assert response.content == [
        {
            "type": "tool_use",
            "id": "call_123",
            "name": "get_system_info",
            "input": {"detail": True},
        }
    ]


def test_internal_tool_results_convert_to_openai_tool_messages() -> None:
    messages = [
        {"role": "user", "content": "检查系统"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_123",
                    "name": "get_system_info",
                    "input": {},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_123",
                    "content": '{"success": true}',
                    "is_error": False,
                }
            ],
        },
    ]

    converted = _to_openai_messages("system prompt", messages)

    assert converted == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "检查系统"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "get_system_info", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": '{"success": true}',
        },
    ]
