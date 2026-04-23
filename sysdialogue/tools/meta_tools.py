"""元工具 Schema — set_execution_mode / propose_dynamic_tool。

这些工具不进入 ToolRegistry；AgentController 按名字拦截并路由到
PlanningEngine / WorkflowEngine / DynamicToolRegistry。
"""

from __future__ import annotations

META_SET_EXECUTION_MODE = "set_execution_mode"
META_PROPOSE_DYNAMIC_TOOL = "propose_dynamic_tool"
META_EXECUTE_DYNAMIC_TOOL = "execute_dynamic_tool"
META_FINISH_TASK = "finish_task"


SET_EXECUTION_MODE_SCHEMA: dict = {
    "name": META_SET_EXECUTION_MODE,
    "description": (
        "在调用 OS 工具之前声明执行模式，用于触发 plan 或 workflow。"
        "用户请求需要 3 步以上操作时 mode=plan；命中内置 workflow 时 mode=workflow；"
        "单步直接执行可用 mode=direct 或不调用本元工具。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["plan", "workflow", "direct"]},
            "plan_steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string"},
                        "tool": {"type": "string"},
                        "args": {"type": "object"},
                        "purpose": {"type": "string"},
                        "expected_risk": {
                            "type": "string",
                            "enum": ["SAFE", "WARN-LOW", "WARN-HIGH", "BLOCK", "UNKNOWN"],
                        },
                        "confirm_required": {"type": "boolean"},
                    },
                    "required": ["step_id", "tool", "args", "purpose"],
                },
            },
            "workflow_name": {"type": "string"},
            "workflow_params": {"type": "object"},
        },
        "required": ["mode"],
    },
}


PROPOSE_DYNAMIC_TOOL_SCHEMA: dict = {
    "name": META_PROPOSE_DYNAMIC_TOOL,
    "description": (
        "Call only when the existing 37 static tools and built-in workflows cannot satisfy the user request. "
        "Proposes a new DynTool for controlled registration; it does not execute automatically. "
        "DynTool is always available but must remain a last resort and is still subject to safety checks, confirmation, audit, and ReAct completion gates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intent_summary": {"type": "string"},
            "proposed_tool_name": {"type": "string"},
            "cmd_template": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 10,
                "description": "subprocess argv，用 {param_name} 表示参数占位符，每元素 ≤ 256 字符",
            },
            "params": {
                "type": "object",
                "description": "参数定义：{param_name: {type, description, required}}",
            },
            "consequences": {"type": "string"},
            "risk_assessment": {"type": "string"},
            "estimated_risk": {
                "type": "string",
                "enum": ["WARN-LOW", "WARN-HIGH", "UNKNOWN"],
            },
            "changes_state": {
                "type": "boolean",
                "default": True,
                "description": "该工具是否会修改目标系统状态；只读诊断工具应设置为 false",
            },
            "reversible": {"type": "boolean"},
        },
        "required": [
            "intent_summary",
            "proposed_tool_name",
            "cmd_template",
            "consequences",
            "risk_assessment",
            "estimated_risk",
        ],
    },
}


EXECUTE_DYNAMIC_TOOL_SCHEMA: dict = {
    "name": META_EXECUTE_DYNAMIC_TOOL,
    "description": (
        "Execute a registered DynTool. Execution always passes through CommandSafetyChecker, "
        "static semantic risk mapping, user confirmation, audit, and ReAct completion gates. "
        "Usually call propose_dynamic_tool first, read the returned tool_id, then call this tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tool_id": {"type": "string", "description": "propose_dynamic_tool 返回的 dyn_* ID"},
            "args": {
                "type": "object",
                "description": "DynTool 参数值，按注册时 params 定义传入",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 300,
                "default": 30,
            },
        },
        "required": ["tool_id", "args"],
    },
}


FINISH_TASK_SCHEMA: dict = {
    "name": META_FINISH_TASK,
    "description": (
        "ReAct 任务收口工具。所有用户输入都必须通过本工具结束；不能直接用自然语言结束。"
        "用于报告完成状态、证据、验证结论、剩余风险和下一步。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["completed", "partial", "failed", "blocked", "need_info", "cancelled"],
            },
            "summary": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "verification": {"type": "string"},
            "changed_state": {"type": "boolean", "default": False},
            "remaining_risks": {"type": "array", "items": {"type": "string"}},
            "next_steps": {"type": "array", "items": {"type": "string"}},
            "no_action_reason": {"type": "string"},
        },
        "required": ["status", "summary"],
    },
}


META_TOOL_SCHEMAS: list[dict] = [
    SET_EXECUTION_MODE_SCHEMA,
    PROPOSE_DYNAMIC_TOOL_SCHEMA,
    EXECUTE_DYNAMIC_TOOL_SCHEMA,
    FINISH_TASK_SCHEMA,
]

META_TOOL_NAMES = {
    META_SET_EXECUTION_MODE,
    META_PROPOSE_DYNAMIC_TOOL,
    META_EXECUTE_DYNAMIC_TOOL,
    META_FINISH_TASK,
}
