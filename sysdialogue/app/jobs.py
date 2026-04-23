"""Scheduled job runner entrypoints."""

from __future__ import annotations

import json
import uuid

from sysdialogue.app.runtime_factory import create_runtime
from sysdialogue.security.risk_classifier import classify
from sysdialogue.tools.cron_jobs import get_cron_job


def run_scheduled_job(config, job_id: str) -> int:
    session_id = f"cron_{job_id}_{uuid.uuid4().hex[:6]}"
    runtime = create_runtime(config, session_id=session_id, require_api=False)
    controller = runtime.controller
    try:
        job = get_cron_job(runtime.executor, job_id)
        if job is None:
            print(f"计划任务不存在：{job_id}")
            runtime.audit_log.log_final(final_status="failed", detail=f"cron job not found: {job_id}")
            return 1

        target = job.get("job_target") or {}
        kind = target.get("kind")
        name = target.get("name", "")
        args = target.get("args") or {}

        if kind == "tool":
            decision = classify(
                name,
                args,
                controller.env_profile,
                session_counters=controller._session_counters,
            )
            if decision.level in ("WARN-HIGH", "BLOCK"):
                controller.audit_log.log_decision(
                    tool=name,
                    args=args,
                    risk_level=decision.level,
                    rule_ids=decision.rule_ids,
                    reason=decision.reason,
                    decision="scheduled_job_rejected",
                    env_profile_id=controller._env_profile_id,
                )
                runtime.audit_log.log_final(final_status="failed", detail="scheduled job rejected by risk gate")
                print(f"计划任务已拒绝：{decision.level} {decision.reason}")
                return 2
            block = controller._dispatch_tool(name, args, f"scheduled:{job_id}")
        elif kind == "workflow":
            block = controller._handle_set_execution_mode(
                {
                    "mode": "workflow",
                    "workflow_name": name,
                    "workflow_params": args,
                },
                f"scheduled:{job_id}",
            )
        else:
            print(f"不支持的计划任务类型：{kind}")
            runtime.audit_log.log_final(final_status="failed", detail=f"unsupported scheduled job kind: {kind}")
            return 1

        print(block["content"])
        runtime.audit_log.log_final(
            final_status="completed" if not block.get("is_error") else "failed",
            detail=f"scheduled job {job_id} completed",
        )
        return 0 if not block.get("is_error") else 1
    finally:
        runtime.close()
