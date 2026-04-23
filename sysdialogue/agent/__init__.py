"""agent 层 — AgentController 主控、PlanningEngine、WorkflowEngine（Task 11 接入）。"""

from sysdialogue.agent.controller import AgentController, OpenAIChatClient
from sysdialogue.agent.prompt import build_system_prompt

__all__ = ["AgentController", "OpenAIChatClient", "build_system_prompt"]
