"""Shared user-facing error presentation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ErrorPresentation:
    user_summary: str
    impact: str
    suggested_next_action: list[str]
    technical_details: str = ""

    @property
    def summary(self) -> str:
        return f"{self.user_summary} {self.impact}".strip()

    @property
    def detail(self) -> str:
        return self.technical_details

    @property
    def suggestions(self) -> list[str]:
        return self.suggested_next_action


def present_error(raw: str) -> ErrorPresentation:
    text = str(raw or "").strip()
    if not text:
        return ErrorPresentation(
            user_summary="任务未完成，但没有收到详细错误。",
            impact="当前执行已停止。",
            suggested_next_action=["查看审计面板", "缩小任务范围后重试"],
        )
    if "Traceback (most recent call last)" in text:
        exc_line = _last_exception_line(text)
        return ErrorPresentation(
            user_summary=f"系统捕获到未处理异常：{exc_line}",
            impact="当前任务已停止。",
            technical_details=text,
            suggested_next_action=["查看技术详情", "检查最近一次工具调用和审计记录"],
        )
    if "LLM 调用失败" in text or "OpenAI-compatible API" in text:
        return ErrorPresentation(
            user_summary="模型服务调用失败。",
            impact="当前任务未能继续执行。",
            technical_details=text,
            suggested_next_action=[
                "检查 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL",
                "确认模型服务支持 Chat Completions tool_calls",
            ],
        )
    if "未按 ReAct 协议" in text or "tool_calls" in text:
        return ErrorPresentation(
            user_summary="模型未按工具协议完成任务收口。",
            impact="当前任务在协议层中止。",
            technical_details=text,
            suggested_next_action=["确认当前模型支持 tool_calls", "将任务拆小后重试"],
        )
    return ErrorPresentation(
        user_summary=_shorten(text.splitlines()[0], 180),
        impact="当前任务未完成。",
        technical_details=text,
        suggested_next_action=["根据错误摘要补充信息", "必要时查看技术详情后重试"],
    )


def format_error_markdown(raw: str) -> str:
    presentation = present_error(raw)
    suggestions = "\n".join(f"- {item}" for item in presentation.suggestions)
    return (
        "### 执行异常\n\n"
        f"{presentation.summary}\n\n"
        f"{suggestions}\n\n"
        "<details>\n"
        "<summary>技术详情</summary>\n\n"
        "```text\n"
        f"{presentation.detail}\n"
        "```\n\n"
        "</details>"
    )


def _last_exception_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "unknown error"


def _shorten(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text)).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)] + "..."
