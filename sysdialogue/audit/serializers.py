"""审计日志导出 / 复现包导出。"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.security.output_sanitizer import sanitize_command, sanitize_text, sanitize_value


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
    - SUMMARY.md — 人类可读复盘摘要
    """
    records = [sanitize_value(record) for record in audit.read_all()]
    out_dir = Path(output_dir or os.path.expanduser("~/.sysdialogue/exports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_path = out_dir / f"replay_{audit.session_id}_{ts}.zip"

    env_record = next((r for r in records if r.get("type") == "env_profile"), {})
    cmd_records = [r for r in records if r.get("type") == "command_trace"]
    decision_records = [r for r in records if r.get("type") == "decision"]
    workflow_records = [r for r in records if r.get("type") == "workflow_step"]
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
            "failed_command_count": sum(1 for item in cmd_records if _exit_code(item.get("exit_code")) != 0),
            "decision_count": len(decision_records),
            "workflow_step_count": len(workflow_records),
            "risk_counts": _risk_counts(decision_records),
            "final_status": final_record.get("final_status", "unknown"),
            "exported_at": ts,
        }
        zf.writestr("summary.json", json.dumps(summary, indent=2, ensure_ascii=False))
        zf.writestr(
            "SUMMARY.md",
            _render_replay_summary(
                session_id=audit.session_id,
                exported_at=ts,
                records=records,
                env_record=env_record,
                cmd_records=cmd_records,
                decision_records=decision_records,
                workflow_records=workflow_records,
                final_record=final_record,
            ),
        )

    return zip_path


def _render_replay_summary(
    *,
    session_id: str,
    exported_at: str,
    records: list[dict],
    env_record: dict,
    cmd_records: list[dict],
    decision_records: list[dict],
    workflow_records: list[dict],
    final_record: dict,
) -> str:
    risk_counts = _risk_counts(decision_records)
    failed_commands = [item for item in cmd_records if _exit_code(item.get("exit_code")) != 0]
    blocked_decisions = [
        item for item in decision_records
        if str(item.get("risk_level") or item.get("decision") or "").upper() in {"BLOCK", "WARN-HIGH"}
        or str(item.get("decision") or "").lower() in {"block", "permission_denied", "user_cancelled"}
    ]
    final_status = sanitize_text(final_record.get("final_status") or "unknown", limit=200)
    final_detail = sanitize_text(final_record.get("detail") or "", limit=800)

    lines = [
        "# SysDialogue Replay Summary",
        "",
        f"- Session: `{sanitize_text(session_id, limit=120)}`",
        f"- Exported at: `{exported_at}`",
        f"- Final status: `{final_status}`",
        f"- Total audit entries: {len(records)}",
        f"- Commands: {len(cmd_records)} total, {len(failed_commands)} failed",
        f"- Decisions: {len(decision_records)} total",
        f"- Workflow steps: {len(workflow_records)} total",
    ]
    if final_detail:
        lines.append(f"- Final detail: {final_detail}")

    profile = env_record.get("profile") if isinstance(env_record, dict) else {}
    if isinstance(profile, dict) and profile:
        lines.extend(["", "## Environment"])
        for key in ("os", "distro", "kernel", "arch", "user", "remote_mode", "host", "container_backend"):
            if key in profile:
                lines.append(f"- {key}: {sanitize_text(profile.get(key), limit=300)}")

    lines.extend(["", "## Risk Decisions"])
    if risk_counts:
        for level, count in risk_counts.items():
            lines.append(f"- {level}: {count}")
    else:
        lines.append("- none")

    if blocked_decisions:
        lines.extend(["", "## High-Risk Or Blocked Decisions"])
        for item in blocked_decisions[:10]:
            tool = sanitize_text(item.get("tool") or "unknown", limit=160)
            risk = sanitize_text(item.get("risk_level") or item.get("decision") or "unknown", limit=120)
            reason = sanitize_text(item.get("reason") or "", limit=500)
            rules = ", ".join(str(rule) for rule in (item.get("rule_ids") or [])[:6]) or "-"
            lines.append(f"- `{tool}` [{risk}] rules={rules}: {reason or 'no reason recorded'}")

    lines.extend(["", "## Command Outcomes"])
    if not cmd_records:
        lines.append("- no commands recorded")
    else:
        for item in cmd_records[:20]:
            tool = sanitize_text(item.get("tool") or "unknown", limit=160)
            exit_code = item.get("exit_code", "?")
            cmd = sanitize_command(item.get("cmd") or [])
            rendered = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            rendered = sanitize_text(rendered, limit=500)
            marker = "FAILED" if _exit_code(exit_code) != 0 else "ok"
            lines.append(f"- {marker} `{tool}` exit={exit_code}: `{rendered}`")
        if len(cmd_records) > 20:
            lines.append(f"- ... {len(cmd_records) - 20} more command record(s) in session.jsonl")

    if workflow_records:
        lines.extend(["", "## Workflow Steps"])
        for item in workflow_records[:20]:
            wid = sanitize_text(item.get("workflow_id") or "", limit=80)
            sid = sanitize_text(item.get("step_id") or "", limit=120)
            status = sanitize_text(item.get("status") or "", limit=120)
            lines.append(f"- `{wid}` step `{sid}`: {status}")
        if len(workflow_records) > 20:
            lines.append(f"- ... {len(workflow_records) - 20} more workflow record(s) in session.jsonl")

    lines.extend(
        [
            "",
            "## Included Files",
            "- `session.jsonl`: sanitized full audit log",
            "- `env_profile.json`: sanitized target environment profile",
            "- `commands.txt`: command traces for audit reference only",
            "- `summary.json`: machine-readable aggregate summary",
            "- `SUMMARY.md`: this human-readable overview",
        ]
    )
    return sanitize_text("\n".join(lines), limit=20000)


def _risk_counts(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in records:
        level = str(item.get("risk_level") or "UNKNOWN")
        counts[level] = counts.get(level, 0) + 1
    return dict(sorted(counts.items()))


def _exit_code(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


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
