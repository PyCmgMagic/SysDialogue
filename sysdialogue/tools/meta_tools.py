"""元工具 Schema — set_execution_mode / propose_dynamic_tool。

这些工具不进入 ToolRegistry；AgentController 按名字拦截并路由到
PlanningEngine / WorkflowEngine / DynamicToolRegistry。
"""

from __future__ import annotations

META_SET_EXECUTION_MODE = "set_execution_mode"
META_PROPOSE_DYNAMIC_TOOL = "propose_dynamic_tool"
META_EXECUTE_DYNAMIC_TOOL = "execute_dynamic_tool"
META_FINISH_TASK = "finish_task"
META_ACTIVATE_SKILL = "activate_skill"
META_HANDOFF_TO_ROLE = "handoff_to_role"


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
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "continue_on_failure": {"type": "boolean"},
                        "finding_id": {"type": "string"},
                        "severity": {"type": "string"},
                        "blocking": {"type": "boolean"},
                        "source_ref": {"type": "string"},
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
        "Call only when the existing 37 static tools and built-in workflows cannot satisfy the user request, "
        "and the capability is worth reusing across future turns. Proposes or reuses a registered DynTool for controlled registration; "
        "it does not execute automatically. For one-off ad-hoc commands, prefer execute_dynamic_tool directly with inline cmd_template + args. "
        "DynTool remains a last resort and is still subject to safety checks, confirmation, audit, and ReAct completion gates."
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
            "execution_mode": {
                "type": "string",
                "enum": ["argv", "shell"],
                "default": "argv",
            },
            "shell_command": {
                "type": "string",
                "description": "Reusable shell template for execution_mode=shell.",
            },
            "cwd": {
                "type": "string",
                "description": "Optional absolute working directory. Use it for project commands instead of shell cd.",
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
            "consequences",
            "risk_assessment",
            "estimated_risk",
        ],
    },
}


EXECUTE_DYNAMIC_TOOL_SCHEMA: dict = {
    "name": META_EXECUTE_DYNAMIC_TOOL,
    "description": (
        "Execute a one-off dynamic command by default. Provide cmd_template (argv list) plus optional args/cwd/metadata. "
        "Aliases command/cmd/argv are accepted and normalized to cmd_template. "
        "Only use tool_id + args when reusing a previously registered dyn_* tool returned by propose_dynamic_tool. "
        "Execution always passes through CommandSafetyChecker, static semantic risk mapping, user confirmation, audit, and ReAct completion gates. "
        "Do not call this tool with only args; it must include either cmd_template/command/argv or a dyn_* tool_id."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tool_id": {"type": "string", "description": "已注册 DynTool 的 dyn_* ID；registered mode 使用"},
            "tool_name": {"type": "string", "description": "inline mode 显示名称；省略时自动从命令推断"},
            "cmd_template": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 10,
                "description": "一次性动态命令的 subprocess argv 模板，用 {param_name} 表示参数占位符",
            },
            "execution_mode": {
                "type": "string",
                "enum": ["argv", "shell"],
                "default": "argv",
                "description": "argv uses cmd_template. shell uses shell_command and requires operator or break_glass safety profile.",
            },
            "shell_command": {
                "type": "string",
                "description": "Shell command string for execution_mode=shell; supports pipes, redirects, and compound commands in break_glass.",
            },
            "privileged": {
                "type": "boolean",
                "default": False,
                "description": "Run through the controlled privileged executor; sudo passwords never appear in argv or audit command text.",
            },
            "command": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": "cmd_template 的兼容别名；字符串会按 shell-like argv 拆分但不会通过 shell 执行",
            },
            "cmd": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": "cmd_template 的兼容别名",
            },
            "argv": {
                "type": "array",
                "items": {"type": "string"},
                "description": "cmd_template 的兼容别名",
            },
            "cwd": {
                "type": "string",
                "description": "Optional absolute working directory. Use it for Maven/Gradle/npm project commands.",
            },
            "args": {
                "type": "object",
                "description": "DynTool 参数值；registered mode 按已注册 params 传入，inline mode 按 cmd_template 占位符传入",
            },
            "params": {
                "type": "object",
                "description": "inline mode 可选参数定义：{param_name: {type, description, required}}",
            },
            "intent_summary": {"type": "string", "description": "inline mode 的意图摘要"},
            "consequences": {"type": "string", "description": "inline mode 的影响说明"},
            "risk_assessment": {"type": "string", "description": "inline mode 的风险评估"},
            "estimated_risk": {
                "type": "string",
                "enum": ["WARN-LOW", "WARN-HIGH", "UNKNOWN"],
                "description": "inline mode 预估风险等级",
            },
            "changes_state": {
                "type": "boolean",
                "default": True,
                "description": "inline mode 是否会修改目标系统状态；只读诊断应设置为 false",
            },
            "reversible": {"type": "boolean", "description": "inline mode 是否易于回滚"},
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 300,
                "default": 30,
            },
        },
        "required": [],
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


ACTIVATE_SKILL_SCHEMA: dict = {
    "name": META_ACTIVATE_SKILL,
    "description": (
        "Activate a local Markdown skill/playbook when it is relevant to the task. "
        "This only injects skill instructions and context; it never executes OS operations and does not bypass safety gates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name from the [Skills] prompt section."},
            "args": {"type": "object", "description": "Structured skill arguments for this invocation."},
            "reason": {"type": "string", "description": "Why this skill is useful now."},
        },
        "required": ["name"],
    },
}


HANDOFF_TO_ROLE_SCHEMA: dict = {
    "name": META_HANDOFF_TO_ROLE,
    "description": (
        "Ask a built-in constrained role (planner, executor, verifier, risk_reviewer, toolsmith) "
        "for structured guidance. Handoff is serial and advisory; execution remains in the main ReAct loop."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "role": {
                "type": "string",
                "enum": ["planner", "executor", "verifier", "risk_reviewer", "toolsmith"],
            },
            "objective": {"type": "string"},
            "constraints": {"type": "object"},
        },
        "required": ["role", "objective"],
    },
}


META_TOOL_SCHEMAS: list[dict] = [
    SET_EXECUTION_MODE_SCHEMA,
    PROPOSE_DYNAMIC_TOOL_SCHEMA,
    EXECUTE_DYNAMIC_TOOL_SCHEMA,
    ACTIVATE_SKILL_SCHEMA,
    HANDOFF_TO_ROLE_SCHEMA,
    FINISH_TASK_SCHEMA,
]

META_TOOL_NAMES = {
    META_SET_EXECUTION_MODE,
    META_PROPOSE_DYNAMIC_TOOL,
    META_EXECUTE_DYNAMIC_TOOL,
    META_ACTIVATE_SKILL,
    META_HANDOFF_TO_ROLE,
    META_FINISH_TASK,
}
