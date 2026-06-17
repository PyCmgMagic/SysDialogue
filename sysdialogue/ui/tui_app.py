"""SysDialogue TUI main interface with the compact origin/ui layout."""

from __future__ import annotations

import threading
import traceback
from typing import TYPE_CHECKING, Any

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
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
from sysdialogue.ui.remote_modal import RemoteModal
from sysdialogue.ui.status_panel import StatusPanel
from sysdialogue.ui.task_timeline import TaskTimelineCard
from sysdialogue.ui.theme import get_glyphs, get_theme

if TYPE_CHECKING:
    from sysdialogue.agent.controller import AgentController
    from sysdialogue.security.approval_rules import ConfirmationRequest


_ASCII_LOGO = r"""
  ___         ___  _      _
 / __|_  _ __|   \(_)__ _| |___  __ _ _  _ ___
 \__ \ || (_-< |) | / _` | / _ \/ _` | || / -_)
 |___/\_, /__/___/|_\__,_|_\___/\__, |\_,_\___|
      |__/                      |___/
"""


class SysDialogueTUI(App):
    """SysDialogue Textual application."""

    CSS = """
    Screen { layout: vertical; background: $surface; }

    #main_layout { height: 1fr; layout: horizontal; }
    #left_pane   { width: 64%; height: 100%; layout: vertical; padding: 0 1; }
    #right_pane  { width: 36%; height: 100%; background: $panel;
                   border-left: vkey $primary 20%; padding: 0; layout: vertical; }

    #right_pane StatusPanel { height: auto; }
    #right_pane AuditPanel  { height: 1fr; }

    #conversation { height: 1fr; padding: 1; scrollbar-size: 1 1; scrollbar-color: $primary 25%; }
    .log_line { margin: 0 0 1 0; }

    #choice_bar { height: auto; min-height: 3; padding: 0 2 1 2;
                  background: $accent 5%; border-top: dashed $accent 25%; }
    #choice_bar.hidden { display: none; }
    #choice_label { color: $accent; text-style: bold; padding: 1 0 0 0; height: auto; }
    #choice_bar Button { margin: 0 1 0 0; max-width: 36; border: tall $accent 35%; }
    #choice_bar Button:hover { background: $accent 18%; }

    #input_area { height: auto; border-top: solid $primary 12%; }
    #user_input { margin: 1 0; border: round $primary 35%; padding: 0 1; height: 3; }
    #user_input:focus { border: round $accent 75%; }
    #input_hint { height: 1; padding: 0 1; color: $text-muted; text-style: italic; }

    #status_bar { height: 1; background: $primary 10%; padding: 0 2;
                  color: $text-muted; border-top: solid $primary 12%; }
    """

    BINDINGS = [
        Binding("f2", "show_history", "历史"),
        Binding("f3", "toggle_audit", "审计"),
        Binding("f4", "toggle_env", "环境"),
        Binding("f5", "connect_remote", "远程"),
        Binding("ctrl+c", "cancel_current", "取消"),
        Binding("ctrl+l", "clear_log", "清屏"),
        Binding("ctrl+d", "quit", "退出"),
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
                Horizontal(
                    Static("或者试试：", id="choice_label"),
                    id="choice_bar",
                    classes="hidden",
                ),
                Vertical(
                    Input(
                        placeholder="描述运维需求 - 例如：查看磁盘使用率 / 重启 nginx",
                        id="user_input",
                    ),
                    Static(
                        "/help  /examples  /playbooks  /evidence  /acceptance  /doctor  /check-model  /next",
                        id="input_hint",
                    ),
                    id="input_area",
                ),
                id="left_pane",
            ),
            Vertical(
                StatusPanel(getattr(self.controller, "env_profile", None)),
                AuditPanel(self.controller.audit_log),
                id="right_pane",
            ),
            id="main_layout",
        )
        yield Static("", id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "SysDialogue"
        self.sub_title = "Linux 运维智能代理"
        self._write_log(self._build_welcome())
        self._set_status("就绪")
        self.query_one("#user_input", Input).focus()

    def _build_welcome(self):
        theme = get_theme()
        glyphs = get_glyphs()
        logo = Text(_ASCII_LOGO, style=f"bold {theme.banner_fg}")
        tagline = Text()
        tagline.append("Linux 运维智能代理", style="bold")
        tagline.append("  ·  ", style="dim")
        tagline.append("自然语言 -> 意图解析 -> 安全执行 -> 反馈核查", style="dim")
        line1 = Text.from_markup(
            f"[dim]{glyphs.bullet}[/dim]  直接输入需求回车即可；[bold]/examples[/bold] 看任务样例，[bold]/playbooks[/bold] 看生产工作流，[bold]/evidence[/bold] 看完成证据，[bold]/acceptance[/bold] 看发布验收。"
        )
        line2 = Text.from_markup(
            f"[dim]{glyphs.bullet}[/dim]  [bold]/doctor[/bold] 检查代理状态；[bold]/check-model[/bold] 验证模型工具调用能力。"
        )
        line_ssh = Text.from_markup(
            f"[dim]{glyphs.bullet}[/dim]  [bold]F5[/bold] 连接远程服务器（SSH），所有操作将在目标机器上执行。"
        )
        line3 = Text.from_markup(
            f"[dim]{glyphs.bullet}[/dim]  高风险操作会主动拦截，请求二次确认。"
        )
        line4 = Text.from_markup(
            f"[dim]{glyphs.bullet}[/dim]  "
            "[bold]F2[/bold] 历史  [bold]F3[/bold] 审计  [bold]F4[/bold] 环境  "
            "[bold]F5[/bold] 远程  "
            "[bold]^C[/bold] 取消  [bold]^L[/bold] 清屏  [bold]^D[/bold] 退出"
        )
        return Panel(
            Group(logo, tagline, Rule(style="dim"), line1, line2, line_ssh, line3, line4),
            border_style=f"dim {theme.banner_fg}",
            padding=(1, 3),
            title_align="left",
        )

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
        if self.query_one("#user_input", Input).disabled:
            return
        self._hide_choice_bar()
        self._start_turn(text)

    def _start_turn(self, text: str) -> None:
        self._turn_failed = False
        self._turn_cancelled = False
        self._current_goal = text
        self._write_user_bubble(text)
        self._begin_task_card(text)
        self.query_one("#user_input", Input).disabled = True
        self._set_status("推理中...")

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
        input_widget = self.query_one("#user_input", Input)
        input_widget.disabled = False
        input_widget.focus()
        self._set_status("就绪")

    def _write_log(self, renderable) -> None:
        scroll = self.query_one("#conversation", VerticalScroll)
        scroll.mount(Static(renderable, classes="log_line"))
        scroll.scroll_end(animate=False)

    def _write_user_bubble(self, text: str) -> None:
        glyphs = get_glyphs()
        body = Text()
        body.append(f"{glyphs.bullet} 你", style="bold cyan")
        body.append("   刚刚\n", style="dim")
        body.append(text)
        self._write_log(Panel(body, border_style="dim cyan", padding=(0, 2)))

    def _begin_task_card(self, goal: str) -> None:
        if self._current_card is not None:
            self._current_card._collapse_after_finish()
        card = TaskTimelineCard(goal)
        self._current_card = card
        scroll = self.query_one("#conversation", VerticalScroll)
        scroll.mount(card)
        scroll.scroll_end(animate=False)

    def _finish_current_card(self, reply: str, *, is_error: bool, cancelled: bool = False) -> None:
        if self._current_card is None:
            if is_error:
                self._write_error(reply)
            elif cancelled:
                self._write_warning(reply, title="任务已取消")
            else:
                self._write_assistant(reply)
            return
        self._current_card.finish_with_reply(reply, is_error=is_error, cancelled=cancelled)

    def _write_assistant(self, reply: str) -> None:
        self._write_log(
            Panel(
                Markdown(reply or "（无输出）"),
                title="[bold magenta]SysDialogue[/bold magenta]",
                title_align="left",
                border_style="dim magenta",
                padding=(1, 2),
            )
        )

    def _write_error(self, reply: str) -> None:
        glyphs = get_glyphs()
        self._write_log(
            Panel(
                Markdown(format_error_markdown(reply)),
                title=f"[bold red]{glyphs.fail} 执行遇到问题[/bold red]",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )
        )

    def _write_warning(self, reply: str, *, title: str = "提示") -> None:
        glyphs = get_glyphs()
        self._write_log(
            Panel(
                Markdown(reply or "当前任务已停止。"),
                title=f"[bold yellow]{glyphs.warn} {title}[/bold yellow]",
                title_align="left",
                border_style="dim yellow",
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
        self.call_from_thread(lambda: self._write_event(stage, message, data))

    def _write_event(self, stage: str, message: str, data: dict[str, Any]) -> None:
        if self._current_card is not None:
            self._current_card.apply_event(stage, message, data)

    def _refresh_audit_panel(self) -> None:
        try:
            self.query_one(AuditPanel).refresh_data()
        except Exception:
            pass

    def _switch_right_panel(self, mode: str) -> None:
        right = self.query_one("#right_pane", Vertical)
        right.remove_children()
        status_panel = StatusPanel(getattr(self.controller, "env_profile", None))
        if mode == "env":
            right.mount(status_panel)
            right.mount(EnvPanel(self.controller.env_profile))
            self._right_panel_mode = "env"
        else:
            right.mount(status_panel)
            audit = AuditPanel(self.controller.audit_log)
            right.mount(audit)
            self.call_later(audit.refresh_data)
            self._right_panel_mode = "audit"

    def _set_status(self, text: str) -> None:
        glyphs = get_glyphs()
        try:
            session_id = getattr(self.controller, "session_id", "") or ""
            model = _controller_model_name(self.controller)
            parts = [f"{glyphs.bullet} {text}"]
            # 显示远程连接信息
            executor = getattr(self.controller, "executor", None)
            if hasattr(executor, "_config"):
                ssh_cfg = executor._config
                parts.append(f"{ssh_cfg.username}@{ssh_cfg.host}")
            else:
                parts.append("本地")
            if session_id:
                parts.append(f"会话 #{session_id[:6]}")
            if model:
                parts.append(model)
            self.query_one("#status_bar", Static).update(f"  {glyphs.sep}  ".join(parts))
        except Exception:
            pass

    def _refresh_runtime_status(self) -> None:
        if self.controller.is_cancel_requested():
            self._set_status("取消中...")
        elif self._confirm_state is not None:
            self._set_status("等待确认")
        elif self._input_state is not None:
            self._set_status("等待输入")
        elif self._worker is not None and self._worker.is_alive():
            self._set_status("推理中...")
        else:
            self._set_status("就绪")

    def _confirm_callback(self, req: "ConfirmationRequest") -> bool | dict[str, Any]:
        event = threading.Event()
        result: dict[str, Any] = {
            "decision": {"approved": False, "decision": "deny"},
        }
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
            notice = f"需要审批高风险操作：{req.tool}（{req.risk.level}）"
            if self._current_card is not None:
                self._current_card.add_notice(notice)
            else:
                self._write_warning(notice, title="等待审批")
            self._refresh_runtime_status()
            modal = ConfirmModal(req)
            state["screen"] = modal
            self.push_screen(modal, lambda decision: self._resolve_confirm_state(decision, state=state))

        self.call_from_thread(show)
        event.wait()
        return result["decision"]

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
            notice = f"需要补充信息：{prompt}（{mode}输入）"
            if self._current_card is not None:
                self._current_card.add_notice(notice)
            else:
                self._write_warning(notice, title="需要输入")
            self._refresh_runtime_status()
            modal = InputModal(prompt=prompt, multiline=multiline)
            state["screen"] = modal
            self.push_screen(modal, lambda value: self._resolve_input_state(value or "", state=state))

        self.call_from_thread(show)
        if not event.wait(timeout=300):
            self.call_from_thread(lambda: self._resolve_input_state("", state=state, dismiss=True))
        return result["value"]

    def _resolve_confirm_state(
        self,
        approved: bool | dict | None,
        *,
        state: dict[str, Any] | None = None,
        dismiss: bool = False,
    ) -> None:
        current = state or self._confirm_state
        if current is None or current["resolved"]:
            return
        decision = _normalize_confirmation_decision(approved)
        current["resolved"] = True
        current["result"]["decision"] = decision
        req = current.get("request")
        if dismiss and current.get("screen") is not None:
            try:
                current["screen"].dismiss(decision)
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
            text = _format_confirmation_result(
                req,
                bool(decision["approved"]),
                str(decision["decision"]),
            )
            if self._current_card is not None:
                self._current_card.add_review_result(text)
            else:
                self._write_log(text)
        self._refresh_runtime_status()

    def _resolve_input_state(self, value: str, *, state=None, dismiss=False) -> None:
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

    def _show_choice_bar(self, choices: list[str]) -> None:
        bar = self.query_one("#choice_bar", Horizontal)
        for button in bar.query(Button):
            button.remove()
        self._choice_values = choices[:3]
        for index, choice in enumerate(self._choice_values):
            bar.mount(
                Button(
                    _choice_button_label(index, choice),
                    id=f"choice_{index}",
                    variant="primary" if index == 0 else "default",
                )
            )
        bar.remove_class("hidden")

    def _hide_choice_bar(self) -> None:
        try:
            bar = self.query_one("#choice_bar", Horizontal)
            bar.add_class("hidden")
            for button in bar.query(Button):
                button.remove()
        except Exception:
            pass
        self._choice_values = []

    def action_toggle_audit(self) -> None:
        self._switch_right_panel("audit")

    def action_toggle_env(self) -> None:
        self._switch_right_panel("env")

    def action_connect_remote(self) -> None:
        """F5: 打开 SSH 远程连接弹窗。"""
        busy = (
            (self._worker is not None and self._worker.is_alive())
            or self._confirm_state is not None
            or self._input_state is not None
        )
        if busy:
            self._write_warning("任务执行中，请等待完成后再连接远程服务器。", title="远程连接")
            return

        # 当前远程连接信息
        current_remote: str | None = None
        executor = getattr(self.controller, "executor", None)
        if hasattr(executor, "_config"):
            ssh_cfg = executor._config
            current_remote = f"{ssh_cfg.username}@{ssh_cfg.host}:{ssh_cfg.port}"

        self.push_screen(
            RemoteModal(current_remote),
            callback=self._on_remote_submitted,
        )

    def _on_remote_submitted(self, info) -> None:
        if info is None:
            return  # 用户取消

        from rich.panel import Panel as RichPanel
        from rich.text import Text as RichText

        self._set_status(f"正在连接 {info.username}@{info.host}...")
        self._write_log(
            RichPanel(
                f"正在连接 [bold]{info.username}@{info.host}:{info.port}[/bold]...",
                title="[bold cyan]远程连接[/bold cyan]",
                border_style="dim cyan",
                padding=(0, 1),
            )
        )

        import threading

        def worker() -> None:
            ok, message = self._do_connect_remote(info)
            self.call_from_thread(lambda: self._on_remote_done(info, ok, message))

        threading.Thread(target=worker, daemon=True).start()

    def _do_connect_remote(self, info) -> tuple[bool, str]:
        """在后台线程执行 SSH 连接。"""
        try:
            from sysdialogue.runtime.ssh_adapter import SSHConfig, RemoteExecutor
            from sysdialogue.runtime.capability_probe import CapabilityProbe
            from sysdialogue.runtime.local_adapter import LocalExecutor
        except ImportError as exc:
            return False, f"缺少依赖: {exc}"

        ssh_cfg = SSHConfig(
            host=info.host,
            port=info.port,
            username=info.username,
            password=info.password,
            key_filename=info.key_filename,
        )
        try:
            new_executor = RemoteExecutor(ssh_cfg)
            new_executor.connect()
            # 重探针环境
            probe = CapabilityProbe(new_executor, remote_mode=True, ssh_port=info.port)
            env_profile = probe.probe()
            env_profile["host"] = info.host
            env_profile["ssh_port"] = info.port
            # 替换 executor 和 env_profile
            old_executor = self.controller.executor
            self.controller.executor = new_executor
            self.controller.env_profile = env_profile
            # 断开旧连接
            if hasattr(old_executor, "disconnect"):
                try:
                    old_executor.disconnect()
                except Exception:
                    pass
            return True, f"已连接到 {info.username}@{info.host}:{info.port}"
        except Exception as exc:
            return False, f"连接失败: {exc}"

    def _on_remote_done(self, info, ok: bool, message: str) -> None:
        from rich.panel import Panel as RichPanel

        if ok:
            self._write_log(
                RichPanel(
                    f"[green]✓[/green] {message}\n"
                    f"后续所有运维操作将在 [bold]{info.host}[/bold] 上执行。",
                    title="[bold green]远程连接成功[/bold green]",
                    border_style="green",
                    padding=(0, 1),
                )
            )
            # 刷新右侧状态面板
            self._switch_right_panel(self._right_panel_mode)
        else:
            self._write_log(
                RichPanel(
                    f"[red]✗[/red] {message}\n"
                    "请检查主机地址、用户名、密钥或密码是否正确。",
                    title="[bold red]远程连接失败[/bold red]",
                    border_style="red",
                    padding=(0, 1),
                )
            )
        self._set_status("就绪")

    def action_show_history(self) -> None:
        busy = (
            (self._worker is not None and self._worker.is_alive())
            or self._confirm_state is not None
            or self._input_state is not None
        )
        if busy:
            self._write_warning("任务执行中，请等待完成后再查看历史。", title="历史会话")
            return
        summaries = self._history_store.list_summaries(limit=30)
        if not summaries:
            self._write_log(
                Panel("还没有可恢复的历史对话。", border_style="dim", title="历史", padding=(0, 1))
            )
            return
        self.push_screen(HistoryModal(summaries), lambda sid: sid and self._restore_history(sid))

    def _restore_history(self, session_id: str) -> None:
        try:
            record = self._history_store.restore_to_manager(
                session_id, self.controller.conversation_manager
            )
        except Exception as exc:
            self._write_error(f"恢复历史失败：{exc}")
            return
        try:
            if hasattr(self.controller, "switch_session"):
                self.controller.switch_session(record.session_id)
            task_store = getattr(self.controller, "task_store", None)
            if task_store is not None:
                self.controller.session_store.recover_interrupted(
                    record.session_id, task_store, surface=self.controller.surface
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
                    f"已恢复历史会话：**{record.title}**\n\n"
                    "后续输入复用该对话上下文，历史工具不会重放。"
                ),
                title="[bold cyan]历史已恢复[/bold cyan]",
                border_style="dim cyan",
                padding=(0, 1),
            )
        )

    def action_cancel_current(self) -> None:
        busy = (
            (self._worker is not None and self._worker.is_alive())
            or self._confirm_state is not None
            or self._input_state is not None
        )
        if not busy:
            self._write_log(
                Panel("当前没有正在执行的任务。", border_style="dim", title="提示", padding=(0, 1))
            )
            return
        self.controller.request_cancel()
        if self._current_card is not None:
            self._current_card.add_notice("已发出取消请求，等待当前步骤退出...")
        else:
            self._write_warning("已发出取消请求，等待当前步骤退出...", title="取消中")
        self._set_status("取消中...")
        self._resolve_confirm_state(False, dismiss=True)
        self._resolve_input_state("", dismiss=True)

    def action_clear_log(self) -> None:
        self.query_one("#conversation", VerticalScroll).remove_children()
        self._current_card = None

    def action_quit(self) -> None:
        self.exit()


def _format_confirmation_result(
    req: "ConfirmationRequest",
    approved: bool,
    decision: str = "once",
) -> Text:
    glyphs = get_glyphs()
    text = Text()
    if approved:
        text.append(f"{glyphs.ok} 批阅通过  ", style="bold green")
        if decision == "always_this_session":
            text.append(f"已批准 {req.tool}，本会话后续同类请求将自动允许。", style="green")
        else:
            text.append(f"已批准 {req.tool}，继续执行。", style="green")
    else:
        text.append(f"{glyphs.fail} 批阅拒绝  ", style="bold yellow")
        text.append(f"已拒绝 {req.tool}，相关操作不会继续。", style="yellow")
    return text


def _normalize_confirmation_decision(value: bool | dict | None) -> dict[str, Any]:
    if isinstance(value, dict):
        approved = bool(value.get("approved"))
        decision = str(value.get("decision") or ("once" if approved else "deny"))
    else:
        approved = bool(value)
        decision = "once" if approved else "deny"
    if decision not in {"once", "always_this_session", "deny"}:
        decision = "once" if approved else "deny"
    if not approved:
        decision = "deny"
    return {"approved": approved, "decision": decision}


def _choices_from_task_event(data: dict[str, Any]) -> list[str]:
    if data.get("status") not in {"need_info", "blocked"}:
        return []
    raw: list = data.get("choices") or data.get("next_steps") or []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= 3:
            break
    return out


def _choice_button_label(index: int, choice: str) -> str:
    label = " ".join(choice.split())
    if len(label) > 26:
        label = label[:25] + "…"
    return f"{index + 1}. {label}"


def _looks_like_failure_reply(reply: str) -> bool:
    markers = (
        "LLM 调用失败",
        "未按 ReAct 协议",
        "达到最大 ReAct",
        "Traceback (most recent call last)",
    )
    return any(marker in (reply or "") for marker in markers)


def _controller_model_name(controller: Any) -> str:
    direct = str(getattr(controller, "model", "") or "")
    if direct:
        return direct.split("/")[-1]
    llm_client = getattr(controller, "llm_client", None)
    model = str(getattr(llm_client, "model", "") or "")
    return model.split("/")[-1] if model else ""


def _format_error_markdown(reply: str) -> str:
    return format_error_markdown(reply)


def _format_event_message(stage: str, message: str, data: dict[str, Any]) -> str:
    if stage == "task_started":
        return "已接收需求，正在建立任务上下文。"
    if stage == "model_response":
        count = int(data.get("tool_count") or 0)
        if count:
            return f"已规划下一步动作：{count} 个工具/流程调用。"
        return data.get("analysis_summary") or "模型正在整理下一步。"
    if stage == "correction":
        return "ReAct 协议纠偏已记录在技术详情中。"
    if stage == "tool_started":
        return f"开始执行工具：{data.get('tool') or message}"
    if stage == "tool_finished":
        return f"工具执行完成：{data.get('tool') or message}"
    if stage == "workflow_started":
        return f"开始执行工作流：{data.get('workflow_name') or message}"
    if stage == "workflow_finished":
        return f"工作流执行完成：{data.get('workflow_name') or message}"
    if stage == "task_finished":
        return data.get("summary") or "任务已完成。"
    if stage == "task_failed":
        return data.get("error_summary") or "任务未完成。"
    return message or stage


def _event_style(stage: str, data: dict[str, Any]) -> str:
    if stage in {"task_failed"}:
        return "yellow" if data.get("status") == "cancelled" else "red"
    if stage in {"tool_finished", "workflow_finished"}:
        return "green" if data.get("success") is not False else "red"
    if stage == "task_finished":
        return "bold green"
    if stage == "confirmation_requested":
        return "yellow"
    return "dim"


def run_tui(controller: "AgentController") -> None:
    SysDialogueTUI(controller).run()
