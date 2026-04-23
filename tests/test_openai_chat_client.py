from __future__ import annotations

from types import SimpleNamespace

import pytest

from sysdialogue.agent.controller import (
    LLMClientError,
    _from_openai_response,
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


def test_openai_response_accepts_plain_string_content() -> None:
    response = _from_openai_response("你好，我可以帮你检查服务器状态。")

    assert response.stop_reason == "stop"
    assert response.content == [
        {"type": "text", "text": "你好，我可以帮你检查服务器状态。"}
    ]


def test_openai_response_accepts_json_string_with_choices() -> None:
    response = _from_openai_response(
        '{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'
    )

    assert response.stop_reason == "stop"
    assert response.content == [{"type": "text", "text": "ok"}]


def test_openai_response_rejects_unknown_shape_with_clear_error() -> None:
    with pytest.raises(LLMClientError, match="无法识别的响应结构"):
        _from_openai_response({"unexpected": True})


def test_openai_response_rejects_html_console_page() -> None:
    with pytest.raises(LLMClientError, match="HTML 页面"):
        _from_openai_response("<!doctype html><html><title>New API</title></html>")


def test_openai_response_rejects_error_payload() -> None:
    with pytest.raises(LLMClientError, match="返回错误"):
        _from_openai_response({"error": {"message": "invalid api key"}})


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


def test_legacy_assistant_string_messages_remain_visible_to_openai() -> None:
    converted = _to_openai_messages(
        "system prompt",
        [{"role": "assistant", "content": "历史回复"}],
    )

    assert converted == [
        {"role": "system", "content": "system prompt"},
        {"role": "assistant", "content": "历史回复"},
    ]
