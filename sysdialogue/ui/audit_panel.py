"""AuditPanel — F3 审计日志侧边栏。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Label, Static

if TYPE_CHECKING:
    from sysdialogue.audit.trace_store import AuditLog


_TYPE_LABELS = {
    "decision":      "安全决策",
    "command_trace": "命令执行",
    "workflow_step": "工作流步骤",
    "env_profile":   "环境快照",
    "final":         "任务收口",
}

_DECISION_STYLES = {
    "allow":   ("✓", "green"),
    "approve": ("✓", "green"),
    "deny":    ("✕", "red"),
    "block":   ("⊘", "red"),
    "warn":    ("⚠", "yellow"),
    "ask":     ("?", "yellow"),
}


class AuditPanel(Vertical):
    """右侧栏 — 最近 60 条审计条目（安全决策 / 命令执行 / 工作流步骤）。"""

    CSS = """
    AuditPanel {
        height: 100%;
        width: 100%;
        padding: 0;
    }

    AuditPanel #audit_header {
        background: $primary 18%;
        padding: 0 2;
        height: 2;
        content-align: left middle;
        text-style: bold;
        border-bottom: solid $primary 20%;
    }

    AuditPanel #audit_empty {
        padding: 2;
        color: $text-muted;
        text-align: center;
    }

    AuditPanel DataTable {
        height: 1fr;
    }

    AuditPanel DataTable > .datatable--header {
        text-style: bold;
        background: $panel;
    }
    """

    def __init__(self, audit_log: "AuditLog"):
        super().__init__()
        self.audit_log = audit_log

    def compose(self) -> ComposeResult:
        yield Static("📋  审计日志  ·  F3 切换", id="audit_header")
        table = DataTable(id="audit_table", zebra_stripes=True, show_cursor=True)
        table.cursor_type = "row"
        yield table

    def on_mount(self) -> None:
        table = self.query_one("#audit_table", DataTable)
        table.add_columns("时间", "类型", "操作 / 步骤", "结果 / 状态", "规则")
        self.refresh_data()

    def refresh_data(self) -> None:
        table = self.query_one("#audit_table", DataTable)
        if not table.columns:
            return
        table.clear()
        records = self.audit_log.read_all()
        shown = records[-60:]
        for rec in shown:
            row = _format_row(rec)
            if row is not None:
                table.add_row(*row)


def _format_row(rec: dict) -> tuple[str, str, str, str, str] | None:
    ts_raw = rec.get("ts", "")
    ts     = ts_raw[11:16] if len(ts_raw) >= 16 else ts_raw

    rtype  = rec.get("type", "")
    label  = _TYPE_LABELS.get(rtype, rtype)

    if rtype == "decision":
        tool     = rec.get("tool", "—")
        decision = rec.get("decision", "")
        icon, _  = _DECISION_STYLES.get(decision.lower(), ("·", ""))
        result   = f"{icon} {decision}" if decision else "—"
        rules    = ", ".join(rec.get("rule_ids", [])) or "—"
        return (ts, label, tool, result, rules)

    if rtype == "command_trace":
        tool      = rec.get("tool", "—")
        exit_code = rec.get("exit_code", "")
        ok        = str(exit_code) == "0"
        result    = f"{'✓' if ok else '✕'} exit={exit_code}"
        return (ts, label, tool, result, "—")

    if rtype == "workflow_step":
        sid    = rec.get("step_id", "")
        stype  = rec.get("step_type", "")
        status = rec.get("status", "—")
        name   = f"{sid}  ({stype})" if stype else sid
        return (ts, label, name, status, "—")

    if rtype == "env_profile":
        return (ts, label, "—", "已采集", "—")

    if rtype == "final":
        status = rec.get("final_status", "—")
        return (ts, label, "—", status, "—")

    # unknown type — still show something
    return (ts, label, "—", "—", "—")
