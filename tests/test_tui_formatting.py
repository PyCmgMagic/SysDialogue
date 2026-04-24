from __future__ import annotations

from types import SimpleNamespace

from sysdialogue.agent.conversation import ConversationManager
from sysdialogue.agent.conversation_store import ConversationStore
from sysdialogue.ui.tui_app import (
    SysDialogueTUI,
    _choice_button_label,
    _choices_from_task_event,
    _event_style,
    _format_confirmation_result,
    _format_error_markdown,
    _format_event_message,
    _looks_like_failure_reply,
)
from sysdialogue.ui.task_timeline import TaskTimelineCard, present_error
from sysdialogue.security.approval_rules import ConfirmationRequest
from sysdialogue.security.risk_classifier import RiskDecision


def test_tui_event_messages_hide_raw_model_internals() -> None:
    assert _format_event_message("task_started", "raw", {}) == "已接收需求，正在建立任务上下文。"
    assert _format_event_message("model_response", "Model requested tools: x", {"tool_count": 2}) == (
        "已规划下一步动作：2 个工具/流程调用。"
    )
    assert "ReAct" in _format_event_message("correction", "raw correction", {})
    assert "输出未满足" not in _format_event_message("correction", "raw correction", {})


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
                None,
                42,
                {"label": "对象不应显示"},
                "提供配置文件路径",
                "   ",
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


def test_tui_error_presentation_keeps_technical_details_foldable() -> None:
    traceback_text = "Traceback (most recent call last):\nValueError: boom"
    presentation = present_error(traceback_text)

    assert "未处理异常" in presentation.summary
    assert "ValueError: boom" in presentation.summary
    assert presentation.detail == traceback_text
    assert presentation.suggestions


def test_task_timeline_card_groups_react_events() -> None:
    card = TaskTimelineCard("检查系统版本和负载")
    card.apply_event("task_started", "raw", {})
    card.apply_event(
        "model_response",
        "raw model response",
        {
            "analysis_summary": "模型选择下一步调用：get_system_info。",
            "visible_text_preview": "我会先观察系统。",
        },
    )
    card.apply_event("tool_started", "raw", {"tool": "get_system_info", "args_preview": "{}"})
    card.apply_event(
        "tool_finished",
        "raw",
        {"tool": "get_system_info", "success": True, "output_preview": "hostname=testbox"},
    )
    card.apply_event("verification", "只读系统信息已返回。", {})
    card.apply_event(
        "task_finished",
        "raw",
        {
            "status": "completed",
            "summary": "系统信息检查完成。",
            "evidence": ["hostname=testbox"],
            "verification": "只读系统信息已返回。",
        },
    )

    state = card.snapshot()
    assert state["status"] == "已完成"
    assert any("模型选择下一步调用" in item for item in state["thinking"])
    assert any("get_system_info" in item for item in state["tools"])
    assert any("只读系统信息" in item for item in state["verification"])
    assert any("系统信息检查完成" in item for item in state["results"])


def test_task_timeline_corrections_are_debug_details_not_main_thinking() -> None:
    card = TaskTimelineCard("检查系统")
    card.apply_event("task_started", "raw", {})
    card.apply_event(
        "correction",
        "raw correction",
        {"display_level": "debug", "correction_count": 1},
    )
    card.apply_event(
        "correction",
        "raw correction",
        {"display_level": "debug", "correction_count": 2},
    )

    state = card.snapshot()
    assert not any("输出未满足" in item for item in state["thinking"])
    assert state["correction_count"] == "2"
    assert sum("ReAct 纠偏记录" in item for item in state["details"]) == 1


def test_tui_persists_history_to_shared_runtime_session(tmp_path) -> None:
    from sysdialogue.agent.state_store import SessionStore

    controller = SimpleNamespace(
        audit_log=SimpleNamespace(session_id="current_session"),
        session_id="current_session",
        surface="tui",
        session_store=SessionStore(str(tmp_path)),
        conversation_manager=ConversationManager(),
    )
    app = SysDialogueTUI(controller)
    app._history_store = ConversationStore(storage_dir=str(tmp_path))
    app._current_goal = "继续检查"

    app._persist_history("完成", "completed")

    assert (tmp_path / "current_session.json").exists()
    assert not (tmp_path / "restored_session.json").exists()
