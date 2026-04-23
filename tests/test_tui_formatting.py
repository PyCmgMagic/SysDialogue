from __future__ import annotations

from sysdialogue.ui.tui_app import (
    _event_style,
    _format_error_markdown,
    _format_event_message,
    _looks_like_failure_reply,
)


def test_tui_event_messages_hide_raw_model_internals() -> None:
    assert _format_event_message("task_started", "raw", {}) == "已接收需求，正在建立任务上下文。"
    assert _format_event_message("model_response", "Model requested tools: x", {"tool_count": 2}) == (
        "已规划下一步动作：2 个工具/流程调用。"
    )
    assert "ReAct" in _format_event_message("correction", "raw correction", {})


def test_tui_event_styles_reflect_failures() -> None:
    assert _event_style("tool_finished", {"success": False}) == "red"
    assert _event_style("workflow_finished", {"success": True}) == "green"
    assert _event_style("task_failed", {"status": "cancelled"}) == "yellow"
    assert _event_style("task_finished", {}) == "bold green"


def test_tui_failure_reply_detection_and_markdown_wrapper() -> None:
    assert _looks_like_failure_reply("LLM 调用失败：401")
    assert not _looks_like_failure_reply("## 完成\n\n- 已验证")

    formatted = _format_error_markdown("Traceback (most recent call last):\nboom")
    assert formatted.startswith("### 执行异常")
    assert "```text" in formatted
    assert "boom" in formatted
