"""Shared runtime creation helpers for app entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from sysdialogue.agent.controller import AgentController, ClaudeClient
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.capability_probe import CapabilityProbe
from sysdialogue.runtime.secure_runner import LocalExecutor, SafeExecutor
from sysdialogue.runtime.ssh_adapter import RemoteExecutor, SSHConfig
from sysdialogue.tools.registry import default_registry

if TYPE_CHECKING:
    from sysdialogue.app.config import AppConfig


class NullClaudeClient:
    def messages_create(self, *, system, messages, tools):
        raise RuntimeError("当前入口不调用 Claude API")


@dataclass
class RuntimeBundle:
    executor: SafeExecutor
    env_profile: dict
    audit_log: AuditLog
    controller: AgentController

    def close(self) -> None:
        if hasattr(self.executor, "disconnect"):
            try:
                self.executor.disconnect()  # type: ignore[attr-defined]
            except Exception:
                pass


def create_runtime(
    config: "AppConfig",
    *,
    session_id: str | None = None,
    require_api: bool = False,
    claude_client: Any | None = None,
    confirm_callback=None,
    input_callback=None,
) -> RuntimeBundle:
    if config.remote_mode:
        ssh_cfg = SSHConfig(
            host=config.ssh_host,
            port=config.ssh_port,
            username=config.ssh_user,
            key_filename=config.ssh_key_file or None,
        )
        executor = RemoteExecutor(ssh_cfg)
        executor.connect()
    else:
        executor = LocalExecutor()

    probe = CapabilityProbe(
        executor,
        remote_mode=config.remote_mode,
        ssh_port=config.ssh_port,
    )
    env_profile = probe.probe()
    audit = AuditLog(session_id=session_id)

    if claude_client is None:
        if require_api:
            claude_client = ClaudeClient(api_key=config.api_key, model=config.model)
        else:
            claude_client = NullClaudeClient()

    controller = AgentController(
        executor=executor,
        env_profile=env_profile,
        audit_log=audit,
        registry=default_registry(),
        claude_client=claude_client,
        competition_mode=config.competition_mode,
        max_iterations=config.max_iterations,
        workflows_dir=Path(config.workflows_dir) if config.workflows_dir else None,
    )
    if confirm_callback is not None:
        controller.confirm_callback = confirm_callback
    if input_callback is not None:
        controller.input_callback = input_callback

    return RuntimeBundle(
        executor=executor,
        env_profile=env_profile,
        audit_log=audit,
        controller=controller,
    )
