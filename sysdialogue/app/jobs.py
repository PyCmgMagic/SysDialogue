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
            decision = _scheduled_tool_rejection(controller, name, args)
            if decision is not None:
                _log_scheduled_rejection(runtime, controller, name, args, decision)
                return 2
            block = controller._dispatch_tool(name, args, f"scheduled:{job_id}")
        elif kind == "workflow":
            decision = _scheduled_workflow_rejection(controller, name, args, job_id)
            if decision is not None:
                _log_scheduled_rejection(
                    runtime,
                    controller,
                    f"workflow:{name}",
                    args,
                    decision,
                    workflow_id=f"scheduled_preflight:{job_id}",
                )
                return 2
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


def _scheduled_tool_rejection(controller, name: str, args: dict):
    decision = classify(
        name,
        args,
        controller.env_profile,
        session_counters=controller._session_counters,
    )
    if decision.level in ("WARN-HIGH", "BLOCK"):
        return decision
    return None


def _scheduled_workflow_rejection(controller, workflow_name: str, workflow_args: dict, job_id: str):
    from sysdialogue.agent.workflow_engine import WorkflowExecution
    from sysdialogue.security.risk_classifier import RiskDecision

    engine = controller._get_workflow_engine()
    try:
        workflow, resolved_params = engine._load_with_params(workflow_name, workflow_args)
    except Exception as exc:
        return RiskDecision(
            level="BLOCK",
            rule_ids=["B026"],
            reason=f"计划任务 workflow 预检加载失败：{exc}",
        )

    execution = WorkflowExecution(
        workflow_id=f"scheduled_preflight:{job_id}",
        workflow_name=workflow_name,
        params=resolved_params,
    )
    steps = [*(workflow.get("steps") or []), *(workflow.get("rollback") or [])]
    for step in steps:
        step_type = step.get("type", "tool_call")
        step_id = step.get("id", "<unknown>")
        if step_type in {"confirm", "approval", "input"}:
            return RiskDecision(
                level="WARN-HIGH",
                rule_ids=["WH015"],
                reason=f"计划任务 workflow 包含非交互模式无法处理的 {step_type} 步骤：{step_id}",
                requires_confirmation=True,
            )
        if step_type != "tool_call":
            continue
        tool = step.get("tool", "")
        try:
            rendered_args = engine._render_args(step.get("args") or {}, execution, resolved_params)
        except ValueError as exc:
            return RiskDecision(
                level="BLOCK",
                rule_ids=["B026"],
                reason=f"计划任务 workflow 步骤 {step_id} 预检模板渲染失败：{exc}",
            )
        decision = classify(
            tool,
            rendered_args,
            controller.env_profile,
            session_counters=controller._session_counters,
        )
        if decision.level in ("WARN-HIGH", "BLOCK"):
            decision.reason = f"计划任务 workflow 步骤 {step_id} 被风险门拒绝：{decision.reason}"
            return decision
    return None


def _log_scheduled_rejection(
    runtime,
    controller,
    name: str,
    args: dict,
    decision,
    workflow_id: str | None = None,
) -> None:
    controller.audit_log.log_decision(
        tool=name,
        args=args,
        risk_level=decision.level,
        rule_ids=decision.rule_ids,
        reason=decision.reason,
        decision="scheduled_job_rejected",
        workflow_id=workflow_id,
        env_profile_id=controller._env_profile_id,
    )
    runtime.audit_log.log_final(
        workflow_id=workflow_id,
        final_status="failed",
        detail="scheduled job rejected by risk gate",
    )
    print(f"计划任务已拒绝：{decision.level} {decision.reason}")
