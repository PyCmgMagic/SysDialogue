"""TaskTimelineCard — ReAct 执行过程可折叠卡片（密度优化版）。"""

from __future__ import annotations

import time
from typing import Any

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Static

from sysdialogue.agent.error_presentation import present_error
from sysdialogue.ui.theme import get_glyphs, get_theme


class TaskTimelineCard(Vertical):
    """单次 ReAct 任务卡片。默认只展开「工具调用」+「执行摘要」。"""

    DEFAULT_CSS = """
    TaskTimelineCard {
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
        border: round $primary 30%;
        background: $boost 3%;
    }
    TaskTimelineCard.running   { border: round $accent 60%;  background: $accent 3%; }
    TaskTimelineCard.failed    { border: round $error 65%;   background: $error 3%; }
    TaskTimelineCard.cancelled { border: round $warning 55%; background: $warning 3%; }
    TaskTimelineCard.completed { border: round $success 50%; background: $success 2%; }

    TaskTimelineCard .card_header { text-style: bold; color: $text; }
    TaskTimelineCard .card_meta   { color: $text-muted; margin: 0 0 1 0; }

    TaskTimelineCard Collapsible {
        margin: 0;
        padding: 0;
        border: none;
        background: transparent;
    }
    TaskTimelineCard CollapsibleTitle {
        background: transparent;
        color: $text-muted;
        padding: 0 1;
    }
    TaskTimelineCard CollapsibleTitle:hover { background: $boost 6%; color: $text; }
    TaskTimelineCard CollapsibleTitle:focus { color: $accent; text-style: bold; }
    TaskTimelineCard Collapsible.-expanded > CollapsibleTitle { color: $text; text-style: bold; }
    TaskTimelineCard Contents { padding: 0 0 0 3; background: transparent; }
    """

    def __init__(self, goal: str):
        super().__init__(classes="running")
        self.goal       = goal
        self.started_at = time.monotonic()
        self.status     = "运行中"

        self._thinking:     list[str] = []
        self._tools:        list[str] = []
        self._verification: list[str] = []
        self._results:      list[str] = []
        self._errors:       list[str] = []
        self._details:      list[str] = []
        self._correction_count  = 0
        self._correction_detail = ""
        self._tool_call_count   = 0
        self._tool_ok_count     = 0

        g = get_glyphs()
        self._header            = Static(classes="card_header")
        self._meta              = Static(classes="card_meta")
        self._thinking_body     = Static()
        self._tools_body        = Static()
        self._verification_body = Static()
        self._result_body       = Static()
        self._error_body        = Static()
        self._detail_body       = Static()

        # 默认折叠策略：
        #   运行中：工具调用展开，其他折叠
        #   完成后：执行摘要自动展开（finish 时触发）
        self._thinking_section     = Collapsible(self._thinking_body,     title=f"{g.sec_think}  推理过程", collapsed=True)
        self._tools_section        = Collapsible(self._tools_body,        title=f"{g.sec_tool}  工具调用", collapsed=False)
        self._verification_section = Collapsible(self._verification_body, title=f"{g.sec_verify}  结果核查", collapsed=True)
        self._result_section       = Collapsible(self._result_body,       title=f"{g.sec_result}  执行摘要", collapsed=True)
        self._error_section        = Collapsible(self._error_body,        title=f"{g.sec_error}  异常记录", collapsed=True)
        self._detail_section       = Collapsible(self._detail_body,       title=f"{g.sec_debug}  调试信息", collapsed=True)

        self._refresh()

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._meta
        yield self._thinking_section
        yield self._tools_section
        yield self._verification_section
        yield self._result_section
        yield self._error_section
        yield self._detail_section

    # ─────────────────────────────── event dispatch ─────────────────────────

    def apply_event(self, stage: str, message: str, data: dict[str, Any]) -> None:
        g = get_glyphs()

        if stage == "task_started":
            self._add_unique(self._thinking, "已接收请求，解析意图、建立执行计划。")

        elif stage == "model_response":
            self._add_unique(
                self._thinking,
                data.get("analysis_summary") or _fallback_model_summary(data),
            )

        elif stage == "correction":
            self._correction_count = int(
                data.get("correction_count") or (self._correction_count + 1)
            )
            errors = data.get("errors") or []
            detail = f"模型输出未满足 ReAct 协议，已自动重试（{self._correction_count} 次）。"
            if errors:
                detail += "\n" + "\n".join(map(str, errors))
            self._correction_detail = _detail_block("ReAct 纠偏日志", detail)

        elif stage == "tool_started":
            self._tool_call_count += 1
            tool = data.get("tool") or message
            self._tools.append(f"{g.arrow_run} `{tool}`  执行中...")
            if data.get("args_preview"):
                self._details.append(_detail_block(f"{tool} — 调用参数", data["args_preview"]))

        elif stage == "tool_finished":
            self._record_tool_result(data.get("tool") or message, data)

        elif stage == "workflow_started":
            self._tool_call_count += 1
            name = data.get("workflow_name") or message
            self._tools.append(f"{g.arrow_run} 工作流 `{name}`  启动...")
            if data.get("args_preview"):
                self._details.append(_detail_block(f"{name} — 参数", data["args_preview"]))

        elif stage == "workflow_finished":
            self._record_tool_result(
                data.get("workflow_name") or message, data, workflow=True
            )

        elif stage == "confirmation_requested":
            tool = data.get("tool") or "当前操作"
            risk = data.get("risk_level") or "WARN-HIGH"
            self._tools.append(f"{g.warn} `{tool}`  触发安全规则（{risk}），等待审批...")
            if data.get("reason"):
                self._details.append(_detail_block("安全规则触发原因", data["reason"]))

        elif stage == "verification":
            self._verification.append(message or data.get("verification") or "核查已记录。")

        elif stage == "task_finished":
            self._record_finish(data)

        elif stage == "task_failed":
            self._record_failure(message, data)

        self._refresh()

    # ─────────────────────────────── public mutators ────────────────────────

    def add_review_result(self, renderable: Text) -> None:
        self._tools.append(renderable.plain)
        self._refresh()

    def add_notice(self, message: str) -> None:
        self._thinking.append(message)
        self._refresh()

    def finish_with_reply(self, reply: str, *, is_error: bool, cancelled: bool = False) -> None:
        if is_error:
            self._record_failure("Unhandled TUI turn error.", {
                "status": "failed", "error_detail": reply,
            })
        elif cancelled:
            self.status = "已取消"
            self.remove_class("running")
            self.add_class("cancelled")
        elif not self.has_class("completed"):
            self.status = "已完成"
            self.remove_class("running")
            self.add_class("completed")
        self._collapse_after_finish()
        self._refresh()

    def snapshot(self) -> dict[str, list[str] | str]:
        return {
            "status":           self.status,
            "thinking":         list(self._thinking),
            "tools":            list(self._tools),
            "verification":     list(self._verification),
            "results":          list(self._results),
            "errors":           list(self._errors),
            "details":          [*self._details,
                                  *([self._correction_detail] if self._correction_detail else [])],
            "correction_count": str(self._correction_count),
        }

    # ─────────────────────────────── internals ──────────────────────────────

    def _record_tool_result(
        self, name: str, data: dict[str, Any], *, workflow: bool = False
    ) -> None:
        g = get_glyphs()
        kind = "工作流" if workflow else "工具"
        ok   = data.get("success") is not False
        if ok:
            self._tool_ok_count += 1
            output = data.get("output_preview")
            suffix = f" — {output}" if output else ""
            self._tools.append(f"{g.ok} `{name}`  完成{suffix}")
        else:
            summary = data.get("error_summary") or f"{kind}调用失败。"
            self._tools.append(f"{g.fail} `{name}`  失败：{summary}")
            self._errors.append(f"{kind} `{name}` 失败：{summary}")
        if data.get("raw_result_preview"):
            self._details.append(_detail_block(f"{name} — 原始输出", data["raw_result_preview"]))

    def _record_finish(self, data: dict[str, Any]) -> None:
        status = data.get("status") or "completed"
        self.status = _status_label(status)
        lines: list[str] = []
        if data.get("summary"):
            lines.append(str(data["summary"]))
        if data.get("verification"):
            lines.append(f"核查：{data['verification']}")
        evidence = data.get("evidence") or []
        if evidence:
            lines.append("证据：" + "；".join(str(i) for i in evidence))
        remaining = data.get("remaining_risks") or []
        if remaining:
            lines.append("剩余风险：" + "；".join(str(i) for i in remaining))
        next_steps = data.get("next_steps") or []
        if next_steps:
            lines.append("建议后续：" + "；".join(str(i) for i in next_steps))
        if data.get("no_action_reason"):
            lines.append(f"未执行系统操作：{data['no_action_reason']}")
        self._results = lines
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
            self._results = ["任务已取消，后续工具调用已跳过。"]
            self._collapse_after_finish()
            return
        presentation = present_error(
            data.get("error_detail") or data.get("error_summary") or message or ""
        )
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
        """完成后的智能折叠：摘要展开，工具/异常按是否有内容决定。"""
        self._thinking_section.collapsed     = True
        self._verification_section.collapsed = True
        self._detail_section.collapsed       = True
        # 摘要：只要有内容就展开
        self._result_section.collapsed       = not bool(self._results)
        # 工具调用：成功时折叠；失败或仍在进行时展开
        if self.has_class("failed") or self.has_class("cancelled"):
            self._tools_section.collapsed  = False
            self._error_section.collapsed  = bool(not self._errors)
        else:
            self._tools_section.collapsed  = True
            self._error_section.collapsed  = True

    def _refresh(self) -> None:
        g = get_glyphs()
        elapsed = max(0.0, time.monotonic() - self.started_at)
        symbol, color = _status_symbol(self.status)

        header = Text()
        header.append(f"{symbol} ", style=f"bold {color}")
        header.append("SysDialogue", style=f"bold {color}")
        self._header.update(header)

        meta = Text()
        meta.append(self.status, style=color)
        meta.append(f"  {g.sep}  ", style="dim")
        meta.append(f"{elapsed:.1f}s", style="dim")
        if self._tool_call_count:
            meta.append(f"  {g.sep}  ", style="dim")
            meta.append(f"{self._tool_ok_count}/{self._tool_call_count} 工具", style="dim")
        if self._correction_count:
            meta.append(f"  {g.sep}  ", style="dim")
            meta.append(f"纠偏×{self._correction_count}", style="yellow dim")
        goal_preview = self.goal if len(self.goal) <= 40 else self.goal[:39] + "…"
        meta.append(f"  {g.sep}  {goal_preview}", style="dim italic")
        self._meta.update(meta)

        self._thinking_body.update(Markdown(_md_list(self._thinking, "等待模型推理...")))
        self._tools_body.update(Markdown(_md_list(self._tools, "尚未发起工具调用。")))
        self._verification_body.update(Markdown(_md_list(self._verification, "尚未执行结果核查。")))
        self._result_body.update(Markdown(_md_list(self._results, "暂无结构化摘要。")))
        self._error_body.update(Markdown(_md_list(self._errors, "本次任务未发生异常。")))
        details = [*self._details]
        if self._correction_detail:
            details.append(self._correction_detail)
        self._detail_body.update(Markdown("\n\n".join(details) or "_暂无调试信息。_"))

        self._thinking_section.display     = bool(self._thinking)
        self._tools_section.display        = bool(self._tools)
        self._verification_section.display = bool(self._verification)
        self._result_section.display       = bool(self._results)
        self._error_section.display        = bool(self._errors)
        self._detail_section.display       = bool(details)

    @staticmethod
    def _add_unique(lst: list[str], value: str) -> None:
        if value and value not in lst:
            lst.append(value)


# ─────────────────────────────── helpers ────────────────────────────────────

def _fallback_model_summary(data: dict[str, Any]) -> str:
    count = data.get("tool_count", 0)
    if count:
        return f"模型决策：调用 {count} 个工具 / 工作流。"
    return "模型整理回复，准备以 finish_task 收口。"


def _md_list(items: list[str], empty: str) -> str:
    if not items:
        return f"_{empty}_"
    return "\n".join(f"- {str(i).replace(chr(10), chr(10) + '  ')}" for i in items)


def _detail_block(title: str, body: str) -> str:
    return f"**{title}**\n\n```text\n{body}\n```"


def _status_symbol(status: str) -> tuple[str, str]:
    g = get_glyphs()
    mapping = {
        "运行中":       (g.running,   "cyan"),
        "已完成":       (g.ok,        "green"),
        "部分完成":     (g.warn,      "yellow"),
        "失败":         (g.fail,      "red"),
        "未完成":       (g.fail,      "red"),
        "已阻止":       (g.blocked,   "red"),
        "需要补充信息": (g.pending,   "yellow"),
        "已取消":       (g.cancelled, "yellow"),
    }
    return mapping.get(status, (g.info, "magenta"))


def _status_label(status: str) -> str:
    return {
        "completed": "已完成",
        "partial":   "部分完成",
        "failed":    "失败",
        "blocked":   "已阻止",
        "need_info": "需要补充信息",
        "cancelled": "已取消",
    }.get(status, status)
