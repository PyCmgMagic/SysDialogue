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
from textual.containers import Container, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from sysdialogue.ui.audit_panel import AuditPanel
from sysdialogue.ui.confirm_modal import ConfirmModal
from sysdialogue.ui.env_panel import EnvPanel
from sysdialogue.ui.input_modal import InputModal

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
    """SysDialogue v6 Textual application."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #main_layout {
        height: 1fr;
        layout: horizontal;
    }
    #left_pane {
        width: 65%;
        height: 100%;
        layout: vertical;
    }
    #right_pane {
        width: 35%;
        height: 100%;
        background: $panel;
        border-left: tall $primary 40%;
    }
    #conversation {
        height: 1fr;
        border: round $primary 40%;
        padding: 0 1;
    }
    #status_bar {
        height: 1;
        background: $primary 20%;
        padding: 0 1;
    }
    """

    BINDINGS = [
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Container(
            Vertical(
                RichLog(id="conversation", highlight=True, markup=True, wrap=True),
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
        self.title = "SysDialogue v6"
        self.sub_title = "Linux 运维智能代理"
        self._write_log(
            "[bold cyan]欢迎使用 SysDialogue v6[/bold cyan]\n"
            "输入自然语言运维需求，Enter 发送。\n"
            "F3 切换审计面板 / F4 切换环境画像 / Ctrl+C 取消当前执行 / Ctrl+D 退出。\n"
        )
        self.query_one("#user_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "user_input":
            return

        text = (event.value or "").strip()
        if not text:
            return
        event.input.value = ""
        self._write_log(f"[bold green]> 你:[/bold green] {text}")

        self._turn_failed = False
        self._turn_cancelled = False
        event.input.disabled = True
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
            self._write_error(reply)
        elif self._turn_cancelled:
            self._write_warning(reply, title="任务已取消")
        else:
            self._write_assistant(reply)
        self._refresh_audit_panel()
        input_box = self.query_one("#user_input", Input)
        input_box.disabled = False
        input_box.focus()
        self._set_status("就绪")

    def _write_log(self, renderable) -> None:
        self.query_one("#conversation", RichLog).write(renderable)

    def _write_assistant(self, reply: str) -> None:
        self._write_log(
            Panel(
                Markdown(reply or "（无输出）"),
                title="SysDialogue",
                border_style="magenta",
                padding=(0, 1),
            )
        )

    def _write_error(self, reply: str) -> None:
        self._write_log(
            Panel(
                Markdown(_format_error_markdown(reply)),
                title="执行遇到问题",
                border_style="red",
                padding=(0, 1),
            )
        )

    def _write_warning(self, reply: str, *, title: str = "提示") -> None:
        self._write_log(
            Panel(
                Markdown(reply or "当前任务已停止。"),
                title=title,
                border_style="yellow",
                padding=(0, 1),
            )
        )

    def _event_callback(self, event) -> None:
        stage = getattr(event, "stage", "event")
        message = getattr(event, "message", "")
        data = getattr(event, "data", {}) or {}
        if stage == "task_failed":
            if data.get("status") == "cancelled":
                self._turn_cancelled = True
            else:
                self._turn_failed = True

        def write() -> None:
            self._write_event(stage, message, data)

        self.call_from_thread(write)

    def _write_event(self, stage: str, message: str, data: dict[str, Any]) -> None:
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
        }
        self._confirm_state = state

        def show() -> None:
            if state["resolved"]:
                return
            self._write_log(
                f"[yellow]需要确认:[/yellow] {req.tool} ({req.risk.level})"
            )
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

        def show() -> None:
            if state["resolved"]:
                return
            mode = "多行" if multiline else "单行"
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
        if dismiss and current.get("screen") is not None:
            try:
                current["screen"].dismiss(bool(approved))
            except Exception:
                pass
        current["event"].set()
        if self._confirm_state is current:
            self._confirm_state = None
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
        self._refresh_runtime_status()

    def action_toggle_audit(self) -> None:
        self._switch_right_panel("audit")

    def action_toggle_env(self) -> None:
        self._switch_right_panel("env")

    def action_cancel_current(self) -> None:
        busy = (
            (self._worker is not None and self._worker.is_alive())
            or self._confirm_state is not None
            or self._input_state is not None
        )
        if not busy:
            self._write_log("[yellow]当前没有正在执行的任务。[/yellow]")
            return

        self.controller.request_cancel()
        self._write_log("[yellow]已请求取消当前执行。[/yellow]")
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
        self.query_one("#conversation", RichLog).clear()

    def action_quit(self) -> None:
        self.exit()


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
        return "输出未满足 ReAct 协议，已自动要求模型改用工具或 finish_task 收口。"
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
    text = (reply or "").strip() or "未知错误。"
    if "Traceback (most recent call last)" in text:
        return (
            "### 执行异常\n\n"
            "系统捕获到未处理异常，任务已经停止。\n\n"
            "```text\n"
            f"{text}\n"
            "```\n\n"
            "请先查看上方流程事件和右侧审计面板，再决定是否重试。"
        )
    return (
        "### 任务未完成\n\n"
        f"{text}\n\n"
        "可以根据上方流程事件继续补充信息、修正配置，或重新发起更小范围的任务。"
    )


def run_tui(controller: "AgentController") -> None:
    """Launch the TUI with the provided controller."""
    app = SysDialogueTUI(controller)
    app.run()
