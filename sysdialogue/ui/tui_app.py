"""SysDialogue TUI main interface."""

from __future__ import annotations

import threading
import traceback
from typing import TYPE_CHECKING, Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Static

from sysdialogue.agent.conversation_store import ConversationStore
from sysdialogue.agent.error_presentation import format_error_markdown
from sysdialogue.ui.audit_panel import AuditPanel
from sysdialogue.ui.confirm_modal import ConfirmModal
from sysdialogue.ui.env_panel import EnvPanel
from sysdialogue.ui.history_modal import HistoryModal
from sysdialogue.ui.input_modal import InputModal
from sysdialogue.ui.task_timeline import TaskTimelineCard

if TYPE_CHECKING:
    from sysdialogue.agent.controller import AgentController
    from sysdialogue.security.approval_rules import ConfirmationRequest


_EVENT_LABELS = {
    "task_started": "开始",
    "model_response": "分析",
    "correction": "调整",
    "tool_started": "工具",
    "tool_finished": "结果",
    "workflow_started": "流程",
    "workflow_finished": "流程",
    "confirmation_requested": "确认",
    "verification": "验证",
    "task_finished": "完成",
    "task_failed": "结束",
}


_EVENT_STYLES = {
    "task_started": "bold cyan",
    "model_response": "cyan",
    "correction": "yellow",
    "tool_started": "blue",
    "tool_finished": "green",
    "workflow_started": "blue",
    "workflow_finished": "green",
    "confirmation_requested": "bold yellow",
    "verification": "bold green",
    "task_finished": "bold green",
    "task_failed": "bold red",
}


class SysDialogueTUI(App):
    """SysDialogue v9 Textual application."""

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }
    #main_layout {
        height: 1fr;
        layout: horizontal;
        padding: 0;
    }
    #left_pane {
        width: 65%;
        height: 100%;
        layout: vertical;
        padding: 0 1 0 1;
    }
    #right_pane {
        width: 35%;
        height: 100%;
        background: $panel;
        border-left: tall $primary 30%;
        padding: 0 1;
    }
    #conversation {
        height: 1fr;
        padding: 1 2;
        scrollbar-size: 1 1;
    }
    #choice_bar {
        height: auto;
        min-height: 3;
        padding: 1 2;
        margin: 0 0 1 0;
        background: $accent 8%;
        border: round $accent 35%;
    }
    #choice_bar.hidden {
        display: none;
    }
    #choice_bar Button {
        margin: 0 1 0 0;
        max-width: 32;
    }
    #user_input {
        margin: 0 0 1 0;
        border: round $primary 45%;
        padding: 0 1;
    }
    #user_input:focus {
        border: round $accent 70%;
    }
    #status_bar {
        height: 1;
        background: $primary 18%;
        padding: 0 2;
        color: $text-muted;
    }
    .log_line {
        margin: 0 0 1 0;
    }
    """

    BINDINGS = [
        Binding("f2", "show_history", "历史"),
        Binding("f3", "toggle_audit", "审计"),
        Binding("f4", "toggle_env", "环境"),
        Binding("ctrl+c", "cancel_current", "取消"),
        Binding("ctrl+d", "quit", "退出"),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]

    def __init__(self, controller: "AgentController"):
        super().__init__()
        self.controller = controller
        controller.confirm_callback = self._confirm_callback
        controller.input_callback = self._input_callback
        controller.event_callback = self._event_callback
        self._right_panel_mode = "audit"
        self._worker: threading.Thread | None = None
        self._confirm_state: dict[str, Any] | None = None
        self._input_state: dict[str, Any] | None = None
        self._turn_failed = False
        self._turn_cancelled = False
        self._choice_values: list[str] = []
        self._current_card: TaskTimelineCard | None = None
        self._current_goal = ""
        self._history_store = ConversationStore()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Container(
            Vertical(
                VerticalScroll(id="conversation"),
                Horizontal(id="choice_bar", classes="hidden"),
                Input(
                    placeholder="输入运维需求（例：查看系统版本 / 重启 nginx）…",
                    id="user_input",
                ),
                id="left_pane",
            ),
            Vertical(
                AuditPanel(self.controller.audit_log),
                id="right_pane",
            ),
            id="main_layout",
        )
        yield Static("状态：就绪", id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "SysDialogue v9"
        self.sub_title = "Linux 运维智能代理"
        welcome = Text()
        welcome.append("SysDialogue v9", style="bold cyan")
        welcome.append("  ·  ", style="dim")
        welcome.append("Linux 运维智能代理", style="cyan")
        welcome.append("\n\n")
        welcome.append("输入自然语言运维需求，", style="")
        welcome.append("Enter", style="bold")
        welcome.append(" 发送。\n", style="")
        welcome.append(
            "F2 历史  ·  F3 审计  ·  F4 环境  ·  Ctrl+C 取消  ·  Ctrl+D 退出",
            style="dim",
        )
        self._write_log(
            Panel(
                welcome,
                border_style="dim cyan",
                padding=(1, 2),
                title_align="left",
            )
        )
        self.query_one("#user_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "user_input":
            return

        text = (event.value or "").strip()
        if not text:
            return
        event.input.value = ""
        self._hide_choice_bar()

        self._start_turn(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("choice_"):
            return
        try:
            index = int(button_id.split("_", 1)[1])
            text = self._choice_values[index]
        except (IndexError, ValueError):
            return
        input_box = self.query_one("#user_input", Input)
        if input_box.disabled:
            return
        self._hide_choice_bar()
        self._start_turn(text)

    def _start_turn(self, text: str) -> None:
        self._turn_failed = False
        self._turn_cancelled = False
        self._current_goal = text
        self._write_user_bubble(text)
        self._begin_task_card(text)
        input_box = self.query_one("#user_input", Input)
        input_box.disabled = True
        self._set_status("思考中…")

        def worker() -> None:
            try:
                reply = self.controller.run_turn(text)
                is_error = False
            except Exception:
                reply = traceback.format_exc()
                is_error = True
            self.call_from_thread(self._on_turn_done, reply, is_error)

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_turn_done(self, reply: str, is_error: bool = False) -> None:
        self._worker = None
        if is_error or _looks_like_failure_reply(reply) or self._turn_failed:
            self._finish_current_card(reply, is_error=True)
            status = "failed"
        elif self._turn_cancelled:
            self._finish_current_card(reply, is_error=False, cancelled=True)
            if reply and reply.strip():
                self._write_warning(reply, title="任务已取消")
            status = "cancelled"
        else:
            self._finish_current_card(reply, is_error=False)
            if reply and reply.strip():
                self._write_assistant(reply)
            status = "completed"
        self._persist_history(reply, status)
        self._refresh_audit_panel()
        input_box = self.query_one("#user_input", Input)
        input_box.disabled = False
        input_box.focus()
        self._set_status("就绪")

    def _write_log(self, renderable) -> None:
        self.query_one("#conversation", VerticalScroll).mount(
            Static(renderable, classes="log_line")
        )

    def _write_user_bubble(self, text: str) -> None:
        body = Text()
        body.append("你  ", style="bold cyan")
        body.append("·  刚刚", style="dim")
        body.append("\n")
        body.append(text)
        self._write_log(
            Panel(
                body,
                border_style="dim cyan",
                padding=(0, 2),
                title_align="left",
            )
        )

    def _begin_task_card(self, goal: str) -> None:
        if self._current_card is not None:
            self._current_card._collapse_after_finish()
        card = TaskTimelineCard(goal)
        self._current_card = card
        self.query_one("#conversation", VerticalScroll).mount(card)

    def _finish_current_card(
        self,
        reply: str,
        *,
        is_error: bool,
        cancelled: bool = False,
    ) -> None:
        if self._current_card is None:
            if is_error:
                self._write_error(reply)
            elif cancelled:
                self._write_warning(reply, title="任务已取消")
            else:
                self._write_assistant(reply)
            return
        self._current_card.finish_with_reply(reply, is_error=is_error, cancelled=cancelled)

    def _persist_history(self, reply: str, status: str) -> None:
        if not self._current_goal:
            return
        try:
            snapshot = self._current_card.snapshot() if self._current_card else {}
            self.controller.session_store.sync_manager(
                self.controller.session_id,
                self.controller.conversation_manager,
                surface=self.controller.surface,
                events_summary={**snapshot, "status": status},
            )
        except Exception:
            pass

    def _write_assistant(self, reply: str) -> None:
        self._write_log(
            Panel(
                Markdown(reply or "（无输出）"),
                title="● SysDialogue",
                title_align="left",
                border_style="magenta",
                padding=(1, 2),
            )
        )

    def _write_error(self, reply: str) -> None:
        self._write_log(
            Panel(
                Markdown(format_error_markdown(reply)),
                title="✕ 执行遇到问题",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )
        )

    def _write_warning(self, reply: str, *, title: str = "提示") -> None:
        self._write_log(
            Panel(
                Markdown(reply or "当前任务已停止。"),
                title=f"◐ {title}",
                title_align="left",
                border_style="yellow",
                padding=(1, 2),
            )
        )

    def _event_callback(self, event) -> None:
        stage = getattr(event, "stage", "event")
        message = getattr(event, "message", "")
        data = getattr(event, "data", {}) or {}
        if stage == "task_started":
            self.call_from_thread(self._hide_choice_bar)
        if stage == "task_failed":
            if data.get("status") == "cancelled":
                self._turn_cancelled = True
            else:
                self._turn_failed = True
        if stage == "task_finished":
            choices = _choices_from_task_event(data)
            if choices:
                self.call_from_thread(lambda: self._show_choice_bar(choices))

        def write() -> None:
            self._write_event(stage, message, data)

        self.call_from_thread(write)

    def _write_event(self, stage: str, message: str, data: dict[str, Any]) -> None:
        if self._current_card is not None:
            self._current_card.apply_event(stage, message, data)
            return
        label = _EVENT_LABELS.get(stage, "事件")
        style = _event_style(stage, data)
        text = Text()
        text.append(f"{label:<4}", style=style)
        text.append(" | ", style="dim")
        text.append(_format_event_message(stage, message, data), style=style if stage in {"task_failed", "correction"} else "")
        self._write_log(text)

    def _refresh_audit_panel(self) -> None:
        try:
            panel = self.query_one(AuditPanel)
            panel.refresh_data()
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status_bar", Static).update(f"状态：{text}")
        except Exception:
            pass

    def _refresh_runtime_status(self) -> None:
        if self.controller.is_cancel_requested():
            self._set_status("取消中…")
        elif self._confirm_state is not None:
            self._set_status("等待确认")
        elif self._input_state is not None:
            self._set_status("等待输入")
        elif self._worker is not None and self._worker.is_alive():
            self._set_status("思考中…")
        else:
            self._set_status("就绪")

    def _confirm_callback(self, req: "ConfirmationRequest") -> bool:
        event = threading.Event()
        result: dict[str, bool] = {"ok": False}
        state: dict[str, Any] = {
            "event": event,
            "result": result,
            "resolved": False,
            "screen": None,
            "request": req,
        }
        self._confirm_state = state
        try:
            self.controller.session_store.set_status(
                self.controller.session_id,
                "waiting_confirm",
                surface=self.controller.surface,
                pending_confirmation={
                    "tool": req.tool,
                    "reason": req.risk.reason,
                    "risk_level": req.risk.level,
                    "rollback_hint": req.rollback_hint or req.risk.rollback_hint,
                },
            )
        except Exception:
            pass

        def show() -> None:
            if state["resolved"]:
                return
            if self._current_card is not None:
                self._current_card.add_notice(f"等待用户批阅：{req.tool} ({req.risk.level})")
            else:
                self._write_log(f"[yellow]需要确认:[/yellow] {req.tool} ({req.risk.level})")
            self._refresh_runtime_status()
            modal = ConfirmModal(req)
            state["screen"] = modal

            def on_close(approved: bool | None) -> None:
                self._resolve_confirm_state(bool(approved), state=state)

            self.push_screen(modal, on_close)

        self.call_from_thread(show)
        event.wait()
        return result["ok"]

    def _input_callback(self, prompt: str, multiline: bool) -> str:
        event = threading.Event()
        result: dict[str, str] = {"value": ""}
        state: dict[str, Any] = {
            "event": event,
            "result": result,
            "resolved": False,
            "screen": None,
        }
        self._input_state = state
        try:
            self.controller.session_store.set_status(
                self.controller.session_id,
                "waiting_input",
                surface=self.controller.surface,
                pending_input={"prompt": prompt, "multiline": multiline},
            )
        except Exception:
            pass

        def show() -> None:
            if state["resolved"]:
                return
            mode = "多行" if multiline else "单行"
            if self._current_card is not None:
                self._current_card.add_notice(f"需要补充输入：{prompt}（{mode}）")
            else:
                self._write_log(f"[yellow]需要输入:[/yellow] {prompt} ({mode})")
            self._refresh_runtime_status()
            modal = InputModal(prompt=prompt, multiline=multiline)
            state["screen"] = modal

            def on_close(value: str | None) -> None:
                self._resolve_input_state(value or "", state=state)

            self.push_screen(modal, on_close)

        self.call_from_thread(show)
        if not event.wait(timeout=300):
            self.call_from_thread(lambda: self._resolve_input_state("", state=state, dismiss=True))
        return result["value"]

    def _resolve_confirm_state(
        self,
        approved: bool,
        *,
        state: dict[str, Any] | None = None,
        dismiss: bool = False,
    ) -> None:
        current = state or self._confirm_state
        if current is None or current["resolved"]:
            return
        current["resolved"] = True
        current["result"]["ok"] = bool(approved)
        req = current.get("request")
        if dismiss and current.get("screen") is not None:
            try:
                current["screen"].dismiss(bool(approved))
            except Exception:
                pass
        current["event"].set()
        if self._confirm_state is current:
            self._confirm_state = None
        try:
            self.controller.session_store.set_status(
                self.controller.session_id,
                "running",
                surface=self.controller.surface,
                pending_confirmation=None,
            )
        except Exception:
            pass
        if req is not None:
            result_text = _format_confirmation_result(req, approved)
            if self._current_card is not None:
                self._current_card.add_review_result(result_text)
            else:
                self._write_log(result_text)
        self._refresh_runtime_status()

    def _resolve_input_state(
        self,
        value: str,
        *,
        state: dict[str, Any] | None = None,
        dismiss: bool = False,
    ) -> None:
        current = state or self._input_state
        if current is None or current["resolved"]:
            return
        current["resolved"] = True
        current["result"]["value"] = value
        if dismiss and current.get("screen") is not None:
            try:
                current["screen"].dismiss(value)
            except Exception:
                pass
        current["event"].set()
        if self._input_state is current:
            self._input_state = None
        try:
            self.controller.session_store.set_status(
                self.controller.session_id,
                "running",
                surface=self.controller.surface,
                pending_input=None,
            )
        except Exception:
            pass
        self._refresh_runtime_status()

    def action_toggle_audit(self) -> None:
        self._switch_right_panel("audit")

    def action_show_history(self) -> None:
        busy = (
            (self._worker is not None and self._worker.is_alive())
            or self._confirm_state is not None
            or self._input_state is not None
        )
        if busy:
            self._write_log(Panel("任务执行中，暂时不能恢复历史。", border_style="yellow", title="历史"))
            return
        summaries = self._history_store.list_summaries(limit=30)
        if not summaries:
            self._write_log(Panel("还没有可恢复的历史对话。", border_style="yellow", title="历史"))
            return

        def on_close(session_id: str | None) -> None:
            if session_id:
                self._restore_history(session_id)

        self.push_screen(HistoryModal(summaries), on_close)

    def _restore_history(self, session_id: str) -> None:
        try:
            record = self._history_store.restore_to_manager(
                session_id,
                self.controller.conversation_manager,
            )
        except Exception as exc:
            self._write_log(Panel(f"恢复历史失败：{exc}", border_style="red", title="历史"))
            return
        try:
            if hasattr(self.controller, "switch_session"):
                self.controller.switch_session(record.session_id)
            task_store = getattr(self.controller, "task_store", None)
            if task_store is not None:
                self.controller.session_store.recover_interrupted(
                    record.session_id,
                    task_store,
                    surface=self.controller.surface,
                )
            self.controller.session_store.sync_manager(
                record.session_id,
                self.controller.conversation_manager,
                surface=self.controller.surface,
            )
        except Exception:
            pass
        self._write_log(
            Panel(
                Markdown(
                    f"已恢复历史对话：**{record.title}**\n\n"
                    "后续输入会复用该对话的上下文；不会重放历史工具执行。"
                ),
                title="历史已恢复",
                border_style="cyan",
                padding=(0, 1),
            )
        )

    def action_toggle_env(self) -> None:
        self._switch_right_panel("env")

    def action_cancel_current(self) -> None:
        busy = (
            (self._worker is not None and self._worker.is_alive())
            or self._confirm_state is not None
            or self._input_state is not None
        )
        if not busy:
            self._write_log(Panel("当前没有正在执行的任务。", border_style="yellow", title="提示"))
            return

        self.controller.request_cancel()
        if self._current_card is not None:
            self._current_card.add_notice("已请求取消当前执行。")
        else:
            self._write_log(Panel("已请求取消当前执行。", border_style="yellow", title="提示"))
        self._set_status("取消中…")
        self._resolve_confirm_state(False, dismiss=True)
        self._resolve_input_state("", dismiss=True)

    def _switch_right_panel(self, mode: str) -> None:
        right = self.query_one("#right_pane", Vertical)
        right.remove_children()
        if mode == "env":
            right.mount(EnvPanel(self.controller.env_profile))
            self._right_panel_mode = "env"
            return

        panel = AuditPanel(self.controller.audit_log)
        right.mount(panel)
        self.call_later(panel.refresh_data)
        self._right_panel_mode = "audit"

    def action_clear_log(self) -> None:
        self.query_one("#conversation", VerticalScroll).remove_children()
        self._current_card = None

    def action_quit(self) -> None:
        self.exit()

    def _show_choice_bar(self, choices: list[str]) -> None:
        choice_bar = self.query_one("#choice_bar", Horizontal)
        choice_bar.remove_children()
        self._choice_values = choices[:3]
        choice_bar.mount(Static("需要补充？", classes="choice_label"))
        for index, choice in enumerate(self._choice_values):
            choice_bar.mount(
                Button(
                    _choice_button_label(index, choice),
                    id=f"choice_{index}",
                    variant="primary" if index == 0 else "default",
                )
            )
        choice_bar.remove_class("hidden")

    def _hide_choice_bar(self) -> None:
        try:
            choice_bar = self.query_one("#choice_bar", Horizontal)
            choice_bar.add_class("hidden")
            choice_bar.remove_children()
        except Exception:
            pass
        self._choice_values = []


def _event_style(stage: str, data: dict[str, Any]) -> str:
    if stage == "task_failed" and data.get("status") == "cancelled":
        return "yellow"
    if stage in {"tool_finished", "workflow_finished"} and data.get("success") is False:
        return "red"
    return _EVENT_STYLES.get(stage, "dim")


def _format_event_message(stage: str, message: str, data: dict[str, Any]) -> str:
    if stage == "task_started":
        return "已接收需求，正在建立任务上下文。"
    if stage == "model_response":
        count = data.get("tool_count", 0)
        if count:
            return f"已规划下一步动作：{count} 个工具/流程调用。"
        return "正在整理回复格式，等待 ReAct 收口。"
    if stage == "correction":
        count = data.get("correction_count")
        suffix = f"（累计 {count} 次）" if count else ""
        return f"ReAct 协议纠偏已记录到技术详情{suffix}。"
    if stage == "tool_started":
        return f"正在调用工具：{data.get('tool') or message}"
    if stage == "tool_finished":
        tool = data.get("tool") or message
        return f"工具完成：{tool}" if data.get("success") is not False else f"工具失败：{tool}"
    if stage == "workflow_started":
        return f"开始执行工作流：{data.get('workflow_name') or message}"
    if stage == "workflow_finished":
        name = data.get("workflow_name") or message
        return f"工作流完成：{name}" if data.get("success") is not False else f"工作流失败：{name}"
    if stage == "confirmation_requested":
        tool = data.get("tool") or "当前操作"
        risk = data.get("risk_level") or "WARN-HIGH"
        reason = data.get("reason") or message
        return f"{tool} 需要确认（{risk}）：{reason}"
    if stage == "verification":
        return message or "验证已记录。"
    if stage == "task_finished":
        return f"任务已收口：{data.get('status') or 'completed'}。"
    if stage == "task_failed":
        if data.get("status") == "cancelled":
            return "任务已取消，未继续执行后续工具。"
        return message or "任务未完成。"
    return message or stage


def _format_confirmation_result(req: "ConfirmationRequest", approved: bool) -> Text:
    text = Text()
    if approved:
        text.append("批阅 ", style="bold green")
        text.append("| ", style="dim")
        text.append(f"已批准 {req.tool}，继续执行。", style="green")
    else:
        text.append("批阅 ", style="bold yellow")
        text.append("| ", style="dim")
        text.append(f"已拒绝 {req.tool}，相关操作不会继续。", style="yellow")
    return text


def _choices_from_task_event(data: dict[str, Any]) -> list[str]:
    status = data.get("status")
    if status not in {"need_info", "blocked"}:
        return []
    raw_choices = data.get("choices") or data.get("next_steps") or []
    choices: list[str] = []
    for item in raw_choices:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in choices:
            continue
        choices.append(text)
        if len(choices) >= 3:
            break
    return choices


def _choice_button_label(index: int, choice: str) -> str:
    normalized = " ".join(choice.split())
    if len(normalized) > 24:
        normalized = normalized[:23] + "…"
    return f"{index + 1}. {normalized}"


def _looks_like_failure_reply(reply: str) -> bool:
    text = (reply or "").strip()
    failure_markers = (
        "LLM 调用失败",
        "未按 ReAct 协议",
        "达到最大 ReAct",
        "Traceback (most recent call last)",
    )
    return any(marker in text for marker in failure_markers)


def _format_error_markdown(reply: str) -> str:
    return format_error_markdown(reply)


def run_tui(controller: "AgentController") -> None:
    """Launch the TUI with the provided controller."""
    app = SysDialogueTUI(controller)
    app.run()
