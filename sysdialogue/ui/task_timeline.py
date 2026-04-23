"""Collapsible ReAct timeline cards for the Textual TUI."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Static


@dataclass
class ErrorPresentation:
    summary: str
    detail: str
    suggestions: list[str]


class TaskTimelineCard(Vertical):
    """One compact, collapsible card for a single ReAct task."""

    DEFAULT_CSS = """
    TaskTimelineCard {
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
        border: round $primary 35%;
        background: $boost 4%;
    }
    TaskTimelineCard.running {
        border: round $accent 55%;
    }
    TaskTimelineCard.failed {
        border: round $error 60%;
    }
    TaskTimelineCard.cancelled {
        border: round $warning 60%;
    }
    TaskTimelineCard.completed {
        border: round $success 55%;
    }
    TaskTimelineCard .task_header {
        text-style: bold;
        color: $text;
    }
    TaskTimelineCard .task_status {
        color: $text-muted;
        margin: 0 0 1 0;
    }
    TaskTimelineCard Collapsible {
        margin: 0;
        padding: 0;
    }
    TaskTimelineCard Collapsible > Contents {
        padding: 0 0 0 2;
    }
    """

    def __init__(self, goal: str):
        super().__init__(classes="running")
        self.goal = goal
        self.started_at = time.monotonic()
        self.status = "运行中"
        self._thinking: list[str] = []
        self._tools: list[str] = []
        self._verification: list[str] = []
        self._results: list[str] = []
        self._errors: list[str] = []
        self._details: list[str] = []
        self._correction_count = 0
        self._correction_detail = ""
        self._header = Static(classes="task_header")
        self._status_line = Static(classes="task_status")
        self._thinking_body = Static()
        self._tools_body = Static()
        self._verification_body = Static()
        self._result_body = Static()
        self._error_body = Static()
        self._detail_body = Static()
        self._thinking_section = Collapsible(
            self._thinking_body, title="思考", collapsed=True
        )
        self._tools_section = Collapsible(
            self._tools_body, title="工具执行", collapsed=False
        )
        self._verification_section = Collapsible(
            self._verification_body, title="验证", collapsed=True
        )
        self._result_section = Collapsible(
            self._result_body, title="结果", collapsed=False
        )
        self._error_section = Collapsible(
            self._error_body, title="错误详情", collapsed=True
        )
        self._detail_section = Collapsible(
            self._detail_body, title="技术详情", collapsed=True
        )
        self._refresh()

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._status_line
        yield self._thinking_section
        yield self._tools_section
        yield self._verification_section
        yield self._result_section
        yield self._error_section
        yield self._detail_section

    def apply_event(self, stage: str, message: str, data: dict[str, Any]) -> None:
        if stage == "task_started":
            self._add_unique(self._thinking, "已接收需求，正在建立任务上下文。")
        elif stage == "model_response":
            self._add_unique(
                self._thinking,
                data.get("analysis_summary") or _fallback_model_summary(data),
            )
            if data.get("visible_text_preview"):
                self._details.append(
                    _detail_block("模型可见文本", data["visible_text_preview"])
                )
        elif stage == "correction":
            self._correction_count = int(data.get("correction_count") or (self._correction_count + 1))
            errors = data.get("errors") or []
            detail = "模型输出未满足工具协议，系统已自动要求改用工具或 finish_task 收口。"
            if errors:
                detail += "\n" + "\n".join(map(str, errors))
            self._correction_detail = _detail_block(
                "ReAct 纠偏记录",
                f"已自动纠偏 {self._correction_count} 次。\n{detail}",
            )
        elif stage == "tool_started":
            tool = data.get("tool") or message
            self._tools.append(f"开始调用 `{tool}`。")
            if data.get("args_preview"):
                self._details.append(_detail_block(f"{tool} 参数", data["args_preview"]))
        elif stage == "tool_finished":
            self._record_tool_result(data.get("tool") or message, data)
        elif stage == "workflow_started":
            name = data.get("workflow_name") or message
            self._tools.append(f"开始执行工作流 `{name}`。")
            if data.get("args_preview"):
                self._details.append(_detail_block(f"{name} 参数", data["args_preview"]))
        elif stage == "workflow_finished":
            self._record_tool_result(data.get("workflow_name") or message, data, workflow=True)
        elif stage == "confirmation_requested":
            tool = data.get("tool") or "当前操作"
            risk = data.get("risk_level") or "WARN-HIGH"
            self._tools.append(f"`{tool}` 需要用户批阅（{risk}）。")
            if data.get("reason"):
                self._details.append(_detail_block("批阅原因", data["reason"]))
        elif stage == "verification":
            self._verification.append(message or data.get("verification") or "验证已记录。")
        elif stage == "task_finished":
            self._record_finish(data)
        elif stage == "task_failed":
            self._record_failure(message, data)
        self._refresh()

    def add_review_result(self, renderable: Text) -> None:
        self._tools.append(renderable.plain)
        self._refresh()

    def add_notice(self, message: str) -> None:
        self._thinking.append(message)
        self._refresh()

    def finish_with_reply(self, reply: str, *, is_error: bool, cancelled: bool = False) -> None:
        if is_error:
            self._record_failure("Unhandled TUI turn error.", {
                "status": "failed",
                "error_detail": reply,
            })
        elif cancelled:
            self.status = "已取消"
            self._results = self._results or [reply or "当前任务已取消。"]
        elif not self._results:
            self.status = "已完成"
            self._results.append(reply or "（无输出）")
        self._collapse_after_finish()
        self._refresh()

    def snapshot(self) -> dict[str, list[str] | str]:
        """Return a lightweight rendering state for tests and diagnostics."""
        return {
            "status": self.status,
            "thinking": list(self._thinking),
            "tools": list(self._tools),
            "verification": list(self._verification),
            "results": list(self._results),
            "errors": list(self._errors),
            "details": [*self._details, *([self._correction_detail] if self._correction_detail else [])],
            "correction_count": str(self._correction_count),
        }

    def _record_tool_result(self, name: str, data: dict[str, Any], *, workflow: bool = False) -> None:
        kind = "工作流" if workflow else "工具"
        if data.get("success") is False:
            summary = data.get("error_summary") or f"{kind}返回失败。"
            self._tools.append(f"{kind} `{name}` 失败：{summary}")
            self._errors.append(f"{kind} `{name}` 失败：{summary}")
        else:
            output = data.get("output_preview")
            suffix = f" 输出摘要：{output}" if output else ""
            self._tools.append(f"{kind} `{name}` 完成。{suffix}")
        if data.get("raw_result_preview"):
            self._details.append(_detail_block(f"{name} 原始结果", data["raw_result_preview"]))

    def _record_finish(self, data: dict[str, Any]) -> None:
        status = data.get("status") or "completed"
        self.status = _status_label(status)
        summary = data.get("summary") or f"任务已收口：{status}。"
        result_lines = [summary]
        if data.get("verification"):
            result_lines.append(f"验证：{data['verification']}")
        evidence = data.get("evidence") or []
        if evidence:
            result_lines.append("证据：" + "；".join(str(item) for item in evidence))
        remaining = data.get("remaining_risks") or []
        if remaining:
            result_lines.append("剩余风险：" + "；".join(str(item) for item in remaining))
        next_steps = data.get("next_steps") or []
        if next_steps:
            result_lines.append("下一步：" + "；".join(str(item) for item in next_steps))
        if data.get("no_action_reason"):
            result_lines.append(f"未执行系统操作：{data['no_action_reason']}")
        self._results = result_lines
        if status == "completed":
            self.remove_class("running")
            self.add_class("completed")
        self._collapse_after_finish()

    def _record_failure(self, message: str, data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "cancelled":
            self.status = "已取消"
            self.remove_class("running")
            self.add_class("cancelled")
            self._results = ["任务已取消，未继续执行后续工具。"]
            self._collapse_after_finish()
            return
        presentation = present_error(data.get("error_detail") or data.get("error_summary") or message or "")
        self.status = "未完成"
        self.remove_class("running")
        self.add_class("failed")
        self._results = [
            presentation.summary,
            "建议：" + "；".join(presentation.suggestions),
        ]
        self._errors.append(presentation.summary)
        if presentation.detail:
            self._details.append(_detail_block("错误技术详情", presentation.detail))
        self._collapse_after_finish()

    def _collapse_after_finish(self) -> None:
        self._tools_section.collapsed = True
        self._error_section.collapsed = True
        self._detail_section.collapsed = True

    def _refresh(self) -> None:
        elapsed = max(0.0, time.monotonic() - self.started_at)
        symbol, color = _status_symbol(self.status)
        header = Text()
        header.append(symbol, style=f"bold {color}")
        header.append("  SysDialogue", style=f"bold {color}")
        self._header.update(header)
        status_line = Text()
        status_line.append(self.status, style=color)
        status_line.append("  ·  ", style="dim")
        status_line.append(f"{elapsed:.1f}s", style="dim")
        if self._correction_count:
            status_line.append("  ·  ", style="dim")
            status_line.append(f"纠偏 {self._correction_count}", style="yellow dim")
        self._status_line.update(status_line)
        self._thinking_body.update(Markdown(_markdown_list(self._thinking, "等待模型分析。")))
        self._tools_body.update(Markdown(_markdown_list(self._tools, "尚未调用工具。")))
        self._verification_body.update(Markdown(_markdown_list(self._verification, "尚未记录验证。")))
        self._result_body.update(Markdown(_markdown_list(self._results, "任务仍在进行。")))
        self._error_body.update(Markdown(_markdown_list(self._errors, "没有错误。")))
        details = [*self._details]
        if self._correction_detail:
            details.append(self._correction_detail)
        self._detail_body.update(Markdown("\n\n".join(details) or "_暂无技术详情。_"))

    @staticmethod
    def _add_unique(items: list[str], value: str) -> None:
        if value and value not in items:
            items.append(value)


def present_error(raw: str) -> ErrorPresentation:
    text = (raw or "").strip()
    if not text:
        return ErrorPresentation(
            summary="任务未完成，但没有收到详细错误。",
            detail="",
            suggestions=["查看审计面板", "缩小任务范围后重试"],
        )
    if "Traceback (most recent call last)" in text:
        exc_line = _last_exception_line(text)
        return ErrorPresentation(
            summary=f"系统捕获到未处理异常，任务已停止：{exc_line}",
            detail=text,
            suggestions=["查看技术详情", "检查最近一次工具调用和右侧审计记录"],
        )
    if "LLM 调用失败" in text or "OpenAI-compatible API" in text:
        return ErrorPresentation(
            summary="模型服务调用失败，任务已停止。",
            detail=text,
            suggestions=[
                "检查 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL",
                "确认模型服务支持 Chat Completions tool_calls",
            ],
        )
    if "未按 ReAct 协议" in text or "tool_calls" in text:
        return ErrorPresentation(
            summary="模型未按工具协议完成任务收口。",
            detail=text,
            suggestions=["确认当前模型支持 tool_calls", "将任务拆小后重试"],
        )
    return ErrorPresentation(
        summary=_shorten(text.splitlines()[0], 180),
        detail=text,
        suggestions=["根据错误摘要补充信息", "必要时查看技术详情后重试"],
    )


def _fallback_model_summary(data: dict[str, Any]) -> str:
    count = data.get("tool_count", 0)
    if count:
        return f"模型选择下一步调用 {count} 个工具或工作流。"
    return "模型正在整理回复，等待工具调用或 finish_task 收口。"


def _markdown_list(items: list[str], empty: str) -> str:
    if not items:
        return f"_ {empty} _"
    return "\n".join(f"- {_escape_newlines(item)}" for item in items)


def _detail_block(title: str, body: str) -> str:
    return f"**{title}**\n\n```text\n{body}\n```"


def _escape_newlines(text: str) -> str:
    return str(text).replace("\n", "\n  ")


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _last_exception_line(traceback_text: str) -> str:
    lines = [line.strip() for line in traceback_text.splitlines() if line.strip()]
    if not lines:
        return "未知异常"
    for line in reversed(lines):
        if re.match(r"^[A-Za-z_][\w.]*Error:|^[A-Za-z_][\w.]*Exception:", line):
            return _shorten(line, 160)
    return _shorten(lines[-1], 160)


def _status_symbol(status: str) -> tuple[str, str]:
    mapping = {
        "运行中": ("◐", "cyan"),
        "已完成": ("✓", "green"),
        "部分完成": ("◑", "yellow"),
        "失败": ("✕", "red"),
        "未完成": ("✕", "red"),
        "已阻止": ("⊘", "red"),
        "需要补充信息": ("?", "yellow"),
        "已取消": ("◦", "yellow"),
    }
    return mapping.get(status, ("●", "magenta"))


def _status_label(status: str) -> str:
    return {
        "completed": "已完成",
        "partial": "部分完成",
        "failed": "失败",
        "blocked": "已阻止",
        "need_info": "需要补充信息",
        "cancelled": "已取消",
    }.get(status, status)
