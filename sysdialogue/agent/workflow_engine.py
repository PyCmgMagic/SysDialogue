"""WorkflowEngine — YAML 工作流加载 / Jinja2 插值 / 5 种 step 类型 / 资源锁 / rollback。

参考 claudeplan7.md 的 workflow schema 与跨进程资源 lease 语义。
"""

from __future__ import annotations

import ast
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import jinja2
import yaml

from sysdialogue.agent.state_store import TaskStepRecord
from sysdialogue.tools.base import ToolResult

if TYPE_CHECKING:
    from sysdialogue.agent.controller import AgentController


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class StepResult:
    step_id: str
    status: str  # completed | skipped | failed | rolled_back
    data: Any = None
    error: str = ""
    result: Any = None  # 给 Jinja2 引用 {{s1.result}}

    @property
    def failed(self) -> bool:
        return self.status == "failed"


class WorkflowTemplateError(ValueError):
    """Raised when workflow Jinja interpolation cannot be rendered safely."""


@dataclass
class WorkflowExecution:
    workflow_id: str
    workflow_name: str
    params: dict
    steps_state: dict[str, StepResult] = field(default_factory=dict)
    rolled_back: bool = False
    rollback_failed: bool = False
    cancelled: bool = False
    final_status: str = "pending"  # pending|completed|rolled_back|failed|rollback_failed
    final_message: str = ""

    def summary(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "final_status": self.final_status,
            "final_message": self.final_message,
            "cancelled": self.cancelled,
            "steps": {
                sid: {"status": r.status, "error": r.error}
                for sid, r in self.steps_state.items()
            },
        }


# --------------------------------------------------------------------------
# 资源锁管理器
# --------------------------------------------------------------------------

class ResourceLockManager:
    """进程内资源锁。scope 格式：file:<path> / service:<name> / user:<name> / cron:<id>。"""

    def __init__(self, controller: "AgentController" | None = None) -> None:
        self.controller = controller
        self._locks: dict[str, threading.Lock] = {}
        self._mu = threading.Lock()

    def _lock_for(self, scope: str) -> threading.Lock:
        with self._mu:
            lk = self._locks.get(scope)
            if lk is None:
                lk = threading.Lock()
                self._locks[scope] = lk
            return lk

    def acquire(self, scope: str, timeout: float = 30.0) -> bool:
        """非阻塞轮询 + 超时。成功返回 True，超时返回 False。"""
        if self.controller is not None and self.controller.lock_store is not None and self.controller.current_task_id:
            lease = self.controller.lock_store.acquire(
                scope,
                task_id=self.controller.current_task_id,
                session_id=self.controller.session_id,
                surface=self.controller.surface,
                timeout=timeout,
                on_stale_reclaim=lambda previous: self.controller.mark_stale_task_interrupted(
                    previous.task_id,
                    detail=f"Resource lock {scope} was reclaimed after stale heartbeat.",
                ),
            )
            if lease is not None:
                self.controller.register_lock_scope(scope)
                return True
            return False
        lk = self._lock_for(scope)
        deadline = time.monotonic() + timeout
        while True:
            if lk.acquire(blocking=False):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def release(self, scope: str) -> None:
        if self.controller is not None and self.controller.lock_store is not None and self.controller.current_task_id:
            self.controller.lock_store.release(scope, task_id=self.controller.current_task_id)
            self.controller.unregister_lock_scope(scope)
            return
        lk = self._locks.get(scope)
        if lk is not None:
            try:
                lk.release()
            except RuntimeError:
                pass


# --------------------------------------------------------------------------
# WorkflowEngine
# --------------------------------------------------------------------------

class WorkflowEngine:
    """工作流执行引擎。复用 AgentController 的 registry/executor/audit/confirm_callback。"""

    def __init__(
        self,
        *,
        controller: "AgentController",
        workflows_dir: Path | str,
        input_callback: Callable[[str, bool], str] | None = None,
        lock_timeout: float = 30.0,
    ):
        self.controller = controller
        self.workflows_dir = Path(workflows_dir)
        self.input_callback = input_callback or (lambda prompt, multiline: "")
        self.lock_timeout = lock_timeout
        self.locks = ResourceLockManager(controller=controller)
        self._jinja = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )

    # ------------------------------------------------------------------
    # 加载 + 参数校验
    # ------------------------------------------------------------------

    def load_raw(self, name: str) -> str:
        p = self.workflows_dir / f"{name}.yaml"
        if not p.exists():
            raise FileNotFoundError(f"工作流不存在：{name}.yaml")
        return p.read_text(encoding="utf-8")

    def load(self, name: str) -> dict:
        """解析一个无变量替换的骨架（所有 {{..}} 替换成占位标量），用于读取 parameters 定义。"""
        raw = self.load_raw(name)
        # 裸字符串占位符：既能嵌入已带引号的值 "{{var}}" → "__PH__"，
        # 也能嵌入无引号的整数位 port: {{var}} → port: __PH__（被 YAML 解析为 str）。
        placeholder = re.sub(r"\{\{[^}]*\}\}", "__PH__", raw)
        return yaml.safe_load(placeholder)

    def list_workflows(self) -> list[str]:
        if not self.workflows_dir.exists():
            return []
        return sorted([p.stem for p in self.workflows_dir.glob("*.yaml")])

    def _validate_and_defaults(self, wf: dict, params: dict) -> dict:
        resolved: dict = {}
        for pdef in wf.get("parameters") or []:
            name = pdef["name"]
            required = pdef.get("required", False)
            default = pdef.get("default")
            if name in params:
                resolved[name] = params[name]
            elif required:
                raise ValueError(f"参数缺失：{name}")
            else:
                resolved[name] = default
        return resolved

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def _pre_render_params(self, raw: str, params: dict) -> str:
        """对 raw YAML 字符串做参数级 regex 替换；step 引用 {{s1.result.xxx}} 保持原样。

        - integer/float/bool/None 按 YAML 字面量格式替换（保证类型正确）
        - 字符串对内嵌反斜杠和双引号做 YAML 双引号转义
        - list 使用 YAML flow 格式内联
        注意：传入 re.sub 的 repl 必须通过 lambda 包装，否则其中的 `\\` 会被
        re 模块解释为反向引用而不是字面反斜杠。
        """
        for name, value in params.items():
            pattern = r"\{\{\s*" + re.escape(name) + r"\s*\}\}"
            if isinstance(value, bool):
                repl = "true" if value else "false"
            elif value is None:
                repl = "null"
            elif isinstance(value, (int, float)):
                repl = str(value)
            elif isinstance(value, list):
                repl = yaml.safe_dump(value, default_flow_style=True).strip()
            else:
                # YAML 双引号字符串转义：\ → \\，" → \"
                repl = str(value).replace("\\", "\\\\").replace('"', '\\"')
            # lambda 包装避免 re.sub 对 \ 做反向引用解释
            raw = re.sub(pattern, lambda m, r=repl: r, raw)
        return raw

    def _load_with_params(self, name: str, params: dict) -> tuple[dict, dict]:
        raw = self.load_raw(name)
        skeleton = self.load(name)
        resolved = self._validate_and_defaults(skeleton, params)
        rendered_raw = self._pre_render_params(raw, resolved)
        wf = yaml.safe_load(rendered_raw)
        return wf, resolved

    def run(self, name: str, params: dict) -> WorkflowExecution:
        wf, resolved_params = self._load_with_params(name, params)
        wf_id = f"wf_{uuid.uuid4().hex[:8]}"
        execution = WorkflowExecution(
            workflow_id=wf_id,
            workflow_name=name,
            params=resolved_params,
        )
        self.controller.audit_log.log_workflow_step(
            workflow_id=wf_id, step_id="__start__", step_type="workflow",
            status="started", detail={"name": name, "params": resolved_params},
        )

        steps = wf.get("steps") or []
        rollback_steps = wf.get("rollback") or []
        final_section = wf.get("final") or {}
        self._sync_workflow_task(name, steps, rollback_steps)

        triggered_rollback = False
        acquired_locks: list[str] = []

        try:
            ordered = self._topo_sort(steps)
            for step in ordered:
                if self.controller.is_cancel_requested():
                    execution.cancelled = True
                    triggered_rollback = True
                    break
                sid = step["id"]
                # 依赖短路：任何依赖标记为 failed → 本步标记为 failed（级联），
                # 区别于 condition=false 的 skipped（后者视为"已成功完成"）
                if not self._deps_satisfied(step, execution):
                    execution.steps_state[sid] = StepResult(
                        step_id=sid, status="failed",
                        error="依赖步骤失败，级联终止",
                    )
                    self._log_step(wf_id, sid, step.get("type", "tool_call"), "failed",
                                   detail={"reason": "dep_failed"})
                    continue

                # condition 计算
                try:
                    condition_ok = self._eval_condition(step, execution, resolved_params)
                except WorkflowTemplateError as e:
                    result = StepResult(step_id=sid, status="failed", error=str(e))
                    execution.steps_state[sid] = result
                    self._log_step(
                        wf_id, sid, step.get("type", "tool_call"), "failed",
                        detail={"error": result.error},
                    )
                    if step.get("on_fail") == "rollback":
                        triggered_rollback = True
                        break
                    continue
                if not condition_ok:
                    execution.steps_state[sid] = StepResult(
                        step_id=sid, status="skipped", error="condition=false 跳过",
                    )
                    self._log_step(wf_id, sid, step.get("type", "tool_call"), "skipped")
                    continue

                # lock_scope 申请
                try:
                    lock_scope = self._render(
                        step.get("lock_scope", ""), execution, resolved_params,
                    ).strip() if step.get("lock_scope") else ""
                except WorkflowTemplateError as e:
                    result = StepResult(step_id=sid, status="failed", error=str(e))
                    execution.steps_state[sid] = result
                    self._log_step(
                        wf_id, sid, step.get("type", "tool_call"), "failed",
                        detail={"error": result.error},
                    )
                    if step.get("on_fail") == "rollback":
                        triggered_rollback = True
                        break
                    continue
                if lock_scope:
                    ok = self.locks.acquire(lock_scope, timeout=self.lock_timeout)
                    if not ok:
                        execution.steps_state[sid] = StepResult(
                            step_id=sid, status="failed",
                            error=f"resource_locked: {lock_scope}",
                        )
                        self._log_step(wf_id, sid, step.get("type", "tool_call"), "failed",
                                       detail={"reason": "resource_locked", "scope": lock_scope})
                        if step.get("on_fail") == "rollback":
                            triggered_rollback = True
                        break
                    acquired_locks.append(lock_scope)

                try:
                    result = self._execute_step(step, execution, resolved_params)
                except WorkflowTemplateError as e:
                    result = StepResult(step_id=sid, status="failed", error=str(e))
                execution.steps_state[sid] = result
                self._log_step(
                    wf_id, sid, step.get("type", "tool_call"), result.status,
                    detail={"error": result.error} if result.error else None,
                )

                if result.status == "failed":
                    if step.get("on_fail") == "rollback":
                        triggered_rollback = True
                        break
                    # 默认失败策略：continue（后续 depends_on 短路会自然跳过）
                if self.controller.is_cancel_requested():
                    execution.cancelled = True
                    triggered_rollback = True
                    break

            # 回滚
            if triggered_rollback and rollback_steps:
                self._run_rollback(rollback_steps, execution, resolved_params, wf_id)

        finally:
            for scope in acquired_locks:
                self.locks.release(scope)

        # 终态决定
        try:
            if execution.rollback_failed:
                execution.final_status = "rollback_failed"
                execution.final_message = self._render(
                    final_section.get("rollback_failed_template", "自动回滚失败，请人工介入。"),
                    execution, resolved_params,
                )
            elif execution.rolled_back:
                execution.final_status = "rolled_back"
                if execution.cancelled:
                    execution.final_message = self._render(
                        final_section.get("cancel_template", "当前工作流已取消，并已按回滚策略处理。"),
                        execution, resolved_params,
                    )
                else:
                    execution.final_message = self._render(
                        final_section.get("rollback_template", "已回滚到变更前状态。"),
                        execution, resolved_params,
                    )
            elif any(r.status == "failed" for r in execution.steps_state.values()):
                execution.final_status = "failed"
                execution.final_message = "工作流失败，部分步骤未完成。"
            else:
                execution.final_status = "completed"
                execution.final_message = self._render(
                    final_section.get("success_template", "工作流执行完成。"),
                    execution, resolved_params,
                )
        except WorkflowTemplateError as e:
            execution.final_status = "failed"
            execution.final_message = str(e)

        self.controller.audit_log.log_final(
            workflow_id=wf_id,
            final_status=execution.final_status,
            detail=execution.final_message,
        )
        return execution

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def _run_rollback(
        self,
        rollback_steps: list[dict],
        execution: WorkflowExecution,
        params: dict,
        wf_id: str,
    ) -> None:
        execution.rolled_back = True
        ordered = self._topo_sort(rollback_steps)
        any_failed = False
        for step in ordered:
            sid = step["id"]
            if not self._deps_satisfied(step, execution):
                execution.steps_state[sid] = StepResult(
                    step_id=sid, status="skipped", error="回滚依赖未满足",
                )
                self._log_step(wf_id, sid, step.get("type", "tool_call"), "skipped")
                continue
            try:
                condition_ok = self._eval_condition(step, execution, params)
            except WorkflowTemplateError as e:
                result = StepResult(step_id=sid, status="failed", error=str(e))
                execution.steps_state[sid] = result
                self._log_step(
                    wf_id, sid, step.get("type", "tool_call"), "failed",
                    detail={"error": result.error},
                )
                any_failed = True
                if step.get("on_fail") != "continue":
                    break
                continue
            if not condition_ok:
                execution.steps_state[sid] = StepResult(
                    step_id=sid, status="skipped", error="condition=false 跳过",
                )
                self._log_step(wf_id, sid, step.get("type", "tool_call"), "skipped")
                continue

            try:
                result = self._execute_step(step, execution, params)
            except WorkflowTemplateError as e:
                result = StepResult(step_id=sid, status="failed", error=str(e))
            # 回滚步骤状态统一标记为 rolled_back（若成功）或保留 failed
            if result.status == "completed":
                result.status = "rolled_back"
            else:
                any_failed = True
            execution.steps_state[sid] = result
            self._log_step(
                wf_id, sid, step.get("type", "tool_call"), result.status,
                detail={"error": result.error} if result.error else None,
            )
            if result.status == "failed" and step.get("on_fail") != "continue":
                break

        if any_failed:
            execution.rollback_failed = True

    # ------------------------------------------------------------------
    # 单步执行
    # ------------------------------------------------------------------

    def _execute_step(self, step: dict, execution: WorkflowExecution, params: dict) -> StepResult:
        t = step.get("type", "tool_call")
        sid = step["id"]

        if t == "tool_call":
            return self._step_tool_call(step, execution, params)
        if t == "confirm":
            return self._step_confirm(step, execution, params)
        if t == "approval":
            return self._step_approval(step, execution, params)
        if t == "display":
            return self._step_display(step, execution, params)
        if t == "input":
            return self._step_input(step, execution, params)
        return StepResult(step_id=sid, status="failed", error=f"未知 step type: {t}")

    def _step_tool_call(self, step: dict, execution: WorkflowExecution, params: dict) -> StepResult:
        sid = step["id"]
        tool = step.get("tool", "")
        raw_args = step.get("args") or {}
        rendered_args = self._render_args(raw_args, execution, params)

        # 通过 controller 的安全门 + 工具执行
        from sysdialogue.security.risk_classifier import classify
        decision = classify(
            tool,
            rendered_args,
            self.controller.env_profile,
            session_counters=self.controller._session_counters,
        )
        self.controller.audit_log.log_decision(
            tool=tool, args=rendered_args,
            risk_level=decision.level, rule_ids=decision.rule_ids,
            reason=decision.reason, decision=decision.level,
            workflow_id=execution.workflow_id,
            env_profile_id=self.controller._env_profile_id,
        )

        if decision.level == "BLOCK":
            return StepResult(
                step_id=sid, status="failed",
                error=f"BLOCK（{', '.join(decision.rule_ids)}）：{decision.reason}",
            )

        if decision.requires_confirmation:
            from sysdialogue.security.approval_rules import ConfirmationRequest
            req = ConfirmationRequest(
                tool=tool, args=rendered_args, risk=decision,
                rollback_hint=decision.rollback_hint,
            )
            try:
                ok = self.controller.confirm_callback(req)
            except Exception as e:
                ok = False
                self.controller.audit_log.log_decision(
                    tool=tool, args=rendered_args,
                    risk_level=decision.level, rule_ids=decision.rule_ids,
                    reason=f"confirm_callback 异常：{e}",
                    decision="confirm_error",
                    workflow_id=execution.workflow_id,
                    env_profile_id=self.controller._env_profile_id,
                )
            if not ok:
                self.controller.audit_log.log_decision(
                    tool=tool, args=rendered_args,
                    risk_level=decision.level, rule_ids=decision.rule_ids,
                    reason=decision.reason, decision="user_cancelled",
                    workflow_id=execution.workflow_id,
                    env_profile_id=self.controller._env_profile_id,
                )
                return StepResult(step_id=sid, status="failed", error="用户已取消")

        lock_error = self.controller._acquire_direct_tool_locks(
            tool,
            rendered_args if isinstance(rendered_args, dict) else {},
            f"workflow:{execution.workflow_id}:{sid}",
        )
        if lock_error is not None:
            return StepResult(
                step_id=sid,
                status="failed",
                error=str(lock_error.get("content") or "resource_locked"),
            )

        result: ToolResult = self.controller.registry.call(
            tool, rendered_args,
            executor=self.controller.executor,
            session_counters=self.controller._session_counters,
            env_profile=self.controller.env_profile,
        )
        self.controller.audit_log.log_command(
            tool=tool, cmd=result.cmd_trace, exit_code=result.exit_code,
            output_preview=str(result.data or result.error)[:1024],
            workflow_id=execution.workflow_id,
        )
        if result.success:
            return StepResult(step_id=sid, status="completed",
                              data=result.data, result=result.data)
        return StepResult(step_id=sid, status="failed",
                          error=result.error or "工具执行失败",
                          data=result.data, result=result.data)

    def _step_confirm(self, step: dict, execution: WorkflowExecution, params: dict) -> StepResult:
        msg = self._render(step.get("message", ""), execution, params)
        from sysdialogue.security.approval_rules import ConfirmationRequest
        from sysdialogue.security.risk_classifier import RiskDecision
        req = ConfirmationRequest(
            tool=f"workflow:confirm:{step['id']}",
            args={"message": msg},
            risk=RiskDecision(level="WARN-HIGH", reason=msg, requires_confirmation=True),
        )
        ok = False
        try:
            ok = self.controller.confirm_callback(req)
        except Exception as e:
            return StepResult(step_id=step["id"], status="failed",
                              error=f"confirm_callback 异常：{e}")
        if not ok:
            return StepResult(step_id=step["id"], status="failed", error="用户未确认")
        return StepResult(step_id=step["id"], status="completed", data=msg)

    def _step_approval(self, step: dict, execution: WorkflowExecution, params: dict) -> StepResult:
        # approval 与 confirm 共用 confirm_callback，但 template 可包含多行详情
        template = self._render(step.get("template", ""), execution, params)
        from sysdialogue.security.approval_rules import ConfirmationRequest
        from sysdialogue.security.risk_classifier import RiskDecision
        req = ConfirmationRequest(
            tool=f"workflow:approval:{step['id']}",
            args={"detail": template},
            risk=RiskDecision(level="WARN-HIGH", reason=template, requires_confirmation=True),
        )
        try:
            ok = self.controller.confirm_callback(req)
        except Exception as e:
            return StepResult(step_id=step["id"], status="failed",
                              error=f"confirm_callback 异常：{e}")
        if not ok:
            return StepResult(step_id=step["id"], status="failed", error="用户未批准")
        return StepResult(step_id=step["id"], status="completed", data=template)

    def _step_display(self, step: dict, execution: WorkflowExecution, params: dict) -> StepResult:
        template = self._render(step.get("template", ""), execution, params)
        return StepResult(step_id=step["id"], status="completed",
                          data=template, result=template)

    def _step_input(self, step: dict, execution: WorkflowExecution, params: dict) -> StepResult:
        prompt = self._render(step.get("prompt", ""), execution, params)
        multiline = step.get("multiline", False)
        param_name = step.get("param")
        try:
            value = self.input_callback(prompt, multiline)
        except Exception as e:
            return StepResult(step_id=step["id"], status="failed",
                              error=f"input_callback 异常：{e}")
        if param_name:
            params[param_name] = value
        return StepResult(step_id=step["id"], status="completed",
                          data=value, result=value)

    # ------------------------------------------------------------------
    # 拓扑排序 + 依赖检查
    # ------------------------------------------------------------------

    def _topo_sort(self, steps: list[dict]) -> list[dict]:
        """按 depends_on 做拓扑排序；无环假设。"""
        by_id = {s["id"]: s for s in steps}
        visited: set[str] = set()
        result: list[dict] = []

        def visit(sid: str) -> None:
            if sid in visited:
                return
            visited.add(sid)
            step = by_id.get(sid)
            if step is None:
                return
            for dep in step.get("depends_on") or []:
                visit(dep)
            result.append(step)

        for s in steps:
            visit(s["id"])
        return result

    def _deps_satisfied(self, step: dict, execution: WorkflowExecution) -> bool:
        for dep in step.get("depends_on") or []:
            dr = execution.steps_state.get(dep)
            if dr is None:
                return False
            # skipped 视为已成功完成（§9.1 明文）
            if dr.status in ("failed",):
                return False
        return True

    # ------------------------------------------------------------------
    # 插值与条件
    # ------------------------------------------------------------------

    def _build_context(self, execution: WorkflowExecution, params: dict) -> dict:
        ctx: dict = dict(params)
        # step 结果注入：{{s1.result}} {{s1.failed}}
        for sid, r in execution.steps_state.items():
            ctx[sid] = {
                "status": r.status,
                "result": r.result,
                "data": r.data,
                "failed": r.failed,
                "error": r.error,
            }
        return ctx

    def _render(self, template: str, execution: WorkflowExecution, params: dict) -> str:
        if template is None:
            return ""
        if not isinstance(template, str):
            return template
        try:
            tpl = self._jinja.from_string(template)
            return tpl.render(**self._build_context(execution, params))
        except Exception as e:
            preview = template.replace("\n", "\\n")[:160]
            raise WorkflowTemplateError(f"模板插值失败：{e}；template={preview!r}") from e

    def _render_args(self, args: Any, execution: WorkflowExecution, params: dict) -> Any:
        """递归插值 args 结构，保留非字符串类型原样。"""
        if isinstance(args, str):
            rendered = self._render(args, execution, params)
            return _coerce_scalar(rendered, args)
        if isinstance(args, dict):
            return {k: self._render_args(v, execution, params) for k, v in args.items()}
        if isinstance(args, list):
            return [self._render_args(v, execution, params) for v in args]
        return args

    def _eval_condition(self, step: dict, execution: WorkflowExecution, params: dict) -> bool:
        cond = step.get("condition")
        if cond is None or cond == "":
            return True
        rendered = self._render(cond, execution, params).strip()
        if rendered == "":
            return False
        low = rendered.lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no", "none"):
            return False
        # 尝试 literal_eval
        try:
            val = ast.literal_eval(rendered)
            return bool(val)
        except Exception:
            return bool(rendered)

    # ------------------------------------------------------------------
    # 审计日志
    # ------------------------------------------------------------------

    def _log_step(self, wf_id: str, sid: str, stype: str, status: str,
                  detail: Any = None) -> None:
        self.controller.audit_log.log_workflow_step(
            workflow_id=wf_id, step_id=sid, step_type=stype,
            status=status, detail=detail,
        )
        task_id = self.controller.current_task_id
        if task_id and self.controller.task_store is not None:
            changes = {
                "status": status,
                "workflow_step_type": stype,
            }
            if isinstance(detail, dict) and detail.get("error"):
                changes["error"] = str(detail["error"])
            self.controller.task_store.update_step(task_id, sid, **changes)

    def _sync_workflow_task(self, workflow_name: str, steps: list[dict], rollback_steps: list[dict]) -> None:
        task_id = self.controller.current_task_id
        if not task_id or self.controller.task_store is None:
            return
        step_records: list[TaskStepRecord] = []
        for step in [*steps, *rollback_steps]:
            step_records.append(
                TaskStepRecord(
                    step_id=step.get("id", ""),
                    kind="workflow_step",
                    tool=step.get("tool", ""),
                    purpose=str(step.get("message") or step.get("template") or step.get("tool") or step.get("type", "")),
                    args=step.get("args") or {},
                    workflow_step_type=str(step.get("type", "tool_call")),
                    lock_scope=str(step.get("lock_scope") or ""),
                )
            )
        self.controller.task_store.update(
            task_id,
            mode="workflow",
            workflow_name=workflow_name,
            current_phase="act",
        )
        self.controller.task_store.set_steps(task_id, step_records)


def _coerce_scalar(rendered: str, original_template: str) -> Any:
    """字符串模板插值后尝试还原标量类型。

    规则：仅当原模板 "=={{var}}" 整体是一个 Jinja 变量表达（无额外文字）时
    才尝试 int/float/bool 还原；否则保留为字符串以保持模板拼接语义。
    """
    t = original_template.strip()
    if not (t.startswith("{{") and t.endswith("}}")):
        return rendered
    low = rendered.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("none", "null", ""):
        return None
    try:
        if "." in rendered:
            return float(rendered)
        return int(rendered)
    except ValueError:
        return rendered
