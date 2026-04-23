"""元工具 Schema — set_execution_mode / propose_dynamic_tool。

这些工具不进入 ToolRegistry；AgentController 按名字拦截并路由到
PlanningEngine / WorkflowEngine / DynamicToolRegistry。
"""

from __future__ import annotations

META_SET_EXECUTION_MODE = "set_execution_mode"
META_PROPOSE_DYNAMIC_TOOL = "propose_dynamic_tool"


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
        "当现有 37 个静态工具和内置 workflow 均无法满足用户需求时调用。"
        "提出新工具方案供用户审批，不自动执行。"
        "竞赛模式下本工具关闭；严禁用于已有静态工具可表达的能力。"
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


META_TOOL_SCHEMAS: list[dict] = [
    SET_EXECUTION_MODE_SCHEMA,
    PROPOSE_DYNAMIC_TOOL_SCHEMA,
]

META_TOOL_NAMES = {META_SET_EXECUTION_MODE, META_PROPOSE_DYNAMIC_TOOL}
