from __future__ import annotations

from sysdialogue.ui.tui_app import (
    _choice_button_label,
    _choices_from_task_event,
    _event_style,
    _format_confirmation_result,
    _format_error_markdown,
    _format_event_message,
    _looks_like_failure_reply,
)
from sysdialogue.security.approval_rules import ConfirmationRequest
from sysdialogue.security.risk_classifier import RiskDecision


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


def test_tui_need_info_choices_are_limited_and_labelled() -> None:
    choices = _choices_from_task_event(
        {
            "status": "need_info",
            "next_steps": [
                "提供服务名",
                "提供配置文件路径",
                "说明希望检查的端口",
                "第四个不会展示",
            ],
        }
    )

    assert choices == ["提供服务名", "提供配置文件路径", "说明希望检查的端口"]
    assert _choice_button_label(0, "提供服务名") == "1. 提供服务名"
    assert _choice_button_label(1, "x" * 40).endswith("…")
    assert _choices_from_task_event({"status": "completed", "next_steps": ["x"]}) == []


def test_tui_confirmation_result_is_user_visible() -> None:
    req = ConfirmationRequest(
        tool="manage_service",
        args={"name": "nginx", "action": "restart"},
        risk=RiskDecision(level="WARN-HIGH", rule_ids=["WH001"], reason="restart"),
    )

    approved = _format_confirmation_result(req, True).plain
    denied = _format_confirmation_result(req, False).plain

    assert "批阅" in approved
    assert "已批准 manage_service" in approved
    assert "已拒绝 manage_service" in denied
