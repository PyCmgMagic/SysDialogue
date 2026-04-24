"""PlanningEngine — plan 声明冻结 + 预判定 + 审计。

与 WorkflowEngine 不同：PlanningEngine 不"执行"计划步骤，
而是把 set_execution_mode(mode=plan, plan_steps=[...]) 中的 plan 进行：
  1. 参数结构校验
  2. 对每步预调用 RiskClassifier 做 expected_risk 核对
  3. 冻结 plan_id 并写入 AuditLog
  4. 返回一段 UI 可展示的计划文本

随后 LLM 按 plan 依次调用 tool_use，AgentController 常规路径处理。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sysdialogue.security.risk_classifier import classify

if TYPE_CHECKING:
    from sysdialogue.agent.controller import AgentController


@dataclass
class PlanStep:
    step_id: str
    tool: str
    args: dict
    purpose: str
    expected_risk: str = "UNKNOWN"
    confirm_required: bool = False
    # 预判定补充字段
    actual_risk: str = "UNKNOWN"
    risk_match: bool = True
    rule_ids: list[str] = field(default_factory=list)
    reason: str = ""
    depends_on: list[str] = field(default_factory=list)
    finding_id: str = ""
    severity: str = ""
    blocking: bool = False
    source_ref: str = ""


@dataclass
class FrozenPlan:
    plan_id: str
    steps: list[PlanStep]
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "step_count": len(self.steps),
            "steps": [
                {
                    "step_id": s.step_id, "tool": s.tool,
                    "purpose": s.purpose,
                    "expected_risk": s.expected_risk,
                    "actual_risk": s.actual_risk,
                    "risk_match": s.risk_match,
                    "rule_ids": s.rule_ids,
                    "depends_on": s.depends_on,
                    "finding_id": s.finding_id,
                    "severity": s.severity,
                    "blocking": s.blocking,
                    "source_ref": s.source_ref,
                } for s in self.steps
            ],
            "warnings": self.warnings,
        }

    def display_text(self) -> str:
        lines = [f"📋 执行计划 {self.plan_id}（{len(self.steps)} 步）："]
        for i, s in enumerate(self.steps, 1):
            warn = " ⚠️" if not s.risk_match else ""
            lines.append(
                f"  {i}. [{s.actual_risk}] {s.tool} — {s.purpose}{warn}"
            )
            if s.rule_ids:
                lines.append(f"     规则：{', '.join(s.rule_ids)}")
        if self.warnings:
            lines.append("")
            lines.append("风险提示：")
            for w in self.warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)


class PlanningEngine:
    """plan_steps 冻结 + 预判定。"""

    def __init__(self, *, controller: "AgentController"):
        self.controller = controller

    def freeze(self, plan_steps: list[dict]) -> FrozenPlan:
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        frozen: list[PlanStep] = []
        warnings: list[str] = []

        if not isinstance(plan_steps, list):
            warnings.append("plan_steps must be a list; ignoring invalid plan payload")
            plan_steps = []

        for index, raw in enumerate(plan_steps or [], 1):
            if not isinstance(raw, dict):
                step = PlanStep(
                    step_id=f"step_{index}",
                    tool="",
                    args={},
                    purpose=str(raw),
                    actual_risk="UNKNOWN",
                )
                warnings.append(f"{step.step_id}: invalid plan step format; expected object")
                frozen.append(step)
                continue
            step = PlanStep(
                step_id=raw.get("step_id", ""),
                tool=raw.get("tool", ""),
                args=raw.get("args") or {},
                purpose=raw.get("purpose", ""),
                expected_risk=raw.get("expected_risk", "UNKNOWN"),
                confirm_required=raw.get("confirm_required", False),
                depends_on=list(raw.get("depends_on") or []),
                finding_id=str(raw.get("finding_id") or ""),
                severity=str(raw.get("severity") or ""),
                blocking=bool(raw.get("blocking", False)),
                source_ref=str(raw.get("source_ref") or ""),
            )

            if not step.tool:
                warnings.append(f"{step.step_id or '?'}：未指定 tool")
                step.actual_risk = "UNKNOWN"
                frozen.append(step)
                continue

            # 注册表存在性检查
            if not self.controller.registry.has(step.tool):
                warnings.append(f"{step.step_id}：工具 {step.tool} 未注册")
                step.actual_risk = "UNKNOWN"
                frozen.append(step)
                continue

            # 风险预判定
            decision = classify(
                step.tool,
                step.args,
                self.controller.env_profile,
                session_counters=self.controller._session_counters,
            )
            step.actual_risk = decision.level
            step.rule_ids = decision.rule_ids
            step.reason = decision.reason
            step.risk_match = (step.expected_risk == decision.level
                               or step.expected_risk == "UNKNOWN")
            if not step.risk_match:
                warnings.append(
                    f"{step.step_id}：预估 {step.expected_risk} 与实际 {decision.level} 不一致"
                )
            if decision.level == "BLOCK":
                warnings.append(
                    f"{step.step_id}：命中 BLOCK（{', '.join(decision.rule_ids)}），执行将被拒绝"
                )
            frozen.append(step)

        plan = FrozenPlan(plan_id=plan_id, steps=frozen, warnings=warnings)

        # 审计
        self.controller.audit_log.log_decision(
            tool="__plan__", args={"plan_id": plan_id, "step_count": len(frozen)},
            risk_level="SAFE", rule_ids=[],
            reason="plan freeze", decision="plan_frozen",
            plan_id=plan_id,
            env_profile_id=self.controller._env_profile_id,
        )
        return plan
