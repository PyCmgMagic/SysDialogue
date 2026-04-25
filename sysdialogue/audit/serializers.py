"""审计日志导出 / 复现包导出。"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.security.output_sanitizer import sanitize_command, sanitize_value


def export_audit_jsonl(audit: AuditLog, output_dir: str | None = None) -> Path:
    """Export a sanitized audit JSONL for a session."""
    records = [sanitize_value(record) for record in audit.read_all()]
    out_dir = Path(output_dir or os.path.expanduser("~/.sysdialogue/exports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"audit_{audit.session_id}_{ts}.jsonl"
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )
    return path


def export_replay_package(audit: AuditLog, output_dir: str | None = None) -> Path:
    """将会话审计日志打包为 ZIP 复现包。

    包含：
    - session.jsonl  — 完整审计 JSONL
    - env_profile.json — 环境画像（第一条 env_profile 条目）
    - commands.txt — 可读命令列表（仅供审计参考，不作为用户侧命令建议）
    - summary.json — 最终状态摘要
    """
    records = [sanitize_value(record) for record in audit.read_all()]
    out_dir = Path(output_dir or os.path.expanduser("~/.sysdialogue/exports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_path = out_dir / f"replay_{audit.session_id}_{ts}.zip"

    env_record = next((r for r in records if r.get("type") == "env_profile"), {})
    cmd_records = [r for r in records if r.get("type") == "command_trace"]
    final_record = next((r for r in records if r.get("type") == "final"), {})

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # session.jsonl
        jsonl_content = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
        zf.writestr("session.jsonl", jsonl_content)

        # env_profile.json
        zf.writestr(
            "env_profile.json",
            json.dumps(env_record.get("profile", {}), indent=2, ensure_ascii=False),
        )

        # commands.txt
        lines = ["# 以下命令仅供审计复现参考，不作为操作建议"]
        for r in cmd_records:
            tool = r.get("tool", "")
            cmd = sanitize_command(r.get("cmd", []))
            code = r.get("exit_code", "?")
            rendered_cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            lines.append(f"[{tool}] exit={code}  $ {rendered_cmd}")
        zf.writestr("commands.txt", "\n".join(lines))

        # summary.json
        summary = {
            "session_id": audit.session_id,
            "total_entries": len(records),
            "command_count": len(cmd_records),
            "final_status": final_record.get("final_status", "unknown"),
            "exported_at": ts,
        }
        zf.writestr("summary.json", json.dumps(summary, indent=2, ensure_ascii=False))

    return zip_path


def format_audit_table(records: list[dict]) -> str:
    """将审计记录格式化为可读文本表（用于 TUI 审计面板）。"""
    lines = []
    for r in records:
        ts = r.get("ts", "")[:19].replace("T", " ")
        rtype = r.get("type", "")
        if rtype == "decision":
            level = r.get("risk_level", "?")
            decision = r.get("decision", "?")
            tool = r.get("tool", "?")
            rules = ",".join(r.get("rule_ids") or [])
            lines.append(f"[{ts}] DECISION {level:9s} {decision:15s} {tool}  {rules}")
        elif rtype == "command_trace":
            tool = r.get("tool", "?")
            code = r.get("exit_code", "?")
            cmd = " ".join(r.get("cmd") or [])[:80]
            lines.append(f"[{ts}] CMD      exit={code}  {tool}  $ {cmd}")
        elif rtype == "workflow_step":
            wid = r.get("workflow_id", "?")[:8]
            sid = r.get("step_id", "?")
            status = r.get("status", "?")
            lines.append(f"[{ts}] WORKFLOW {wid}  step={sid}  {status}")
        elif rtype == "final":
            fs = r.get("final_status", "?")
            lines.append(f"[{ts}] FINAL    {fs}")
    return "\n".join(lines)
