"""AuditPanel — F3 审计日志面板。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Label

if TYPE_CHECKING:
    from sysdialogue.audit.trace_store import AuditLog


class AuditPanel(Vertical):
    """在右侧栏显示最近的审计条目（decision / command_trace / workflow_step）。"""

    CSS = """
    AuditPanel {
        height: 100%;
        width: 100%;
        padding: 0;
    }
    AuditPanel Label {
        background: $primary 20%;
        padding: 0 1;
        text-style: bold;
    }
    AuditPanel DataTable {
        height: 1fr;
    }
    """

    def __init__(self, audit_log: "AuditLog"):
        super().__init__()
        self.audit_log = audit_log

    def compose(self) -> ComposeResult:
        yield Label("📋 审计日志 (F3 切换)")
        table = DataTable(id="audit_table", zebra_stripes=True)
        table.cursor_type = "row"
        yield table

    def on_mount(self) -> None:
        table = self.query_one("#audit_table", DataTable)
        table.add_columns("时间", "类型", "工具/步骤", "结果/等级", "规则")
        self.refresh_data()

    def refresh_data(self) -> None:
        table = self.query_one("#audit_table", DataTable)
        table.clear()
        records = self.audit_log.read_all()
        # 只显示最近 50 条
        for rec in records[-50:]:
            ts = rec.get("ts", "")[-9:-4]  # 显示 HH:MM
            rtype = rec.get("type", "")
            if rtype == "decision":
                tool = rec.get("tool", "")
                decision = rec.get("decision", "")
                rules = ", ".join(rec.get("rule_ids", []))
                table.add_row(ts, "decision", tool, decision, rules)
            elif rtype == "command_trace":
                tool = rec.get("tool", "")
                exit_code = rec.get("exit_code", "")
                table.add_row(ts, "cmd", tool, f"exit={exit_code}", "")
            elif rtype == "workflow_step":
                sid = rec.get("step_id", "")
                status = rec.get("status", "")
                stype = rec.get("step_type", "")
                table.add_row(ts, "wf_step", f"{sid} ({stype})", status, "")
            elif rtype == "env_profile":
                table.add_row(ts, "env_profile", "-", "snapshot", "")
            elif rtype == "final":
                status = rec.get("final_status", "")
                table.add_row(ts, "final", "-", status, "")
