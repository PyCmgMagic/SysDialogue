"""Shared runtime creation helpers for app entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from sysdialogue.agent.controller import AgentController, OpenAIChatClient
from sysdialogue.agent.command_registry import CommandRegistry
from sysdialogue.agent.memory import MemoryManager
from sysdialogue.agent.permission_policy import PermissionPolicy
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskStore
from sysdialogue.agent.trace_store import TraceStore
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.capability_probe import CapabilityProbe
from sysdialogue.runtime.privilege_manager import PrivilegeManager
from sysdialogue.runtime.secure_runner import LocalExecutor, SafeExecutor
from sysdialogue.runtime.ssh_adapter import RemoteExecutor, SSHConfig
from sysdialogue.tools.dynamic_registry import DynamicToolRegistry
from sysdialogue.tools.registry import default_registry

if TYPE_CHECKING:
    from sysdialogue.app.config import AppConfig


class NullLLMClient:
    def messages_create(self, *, system, messages, tools):
        raise RuntimeError("当前入口不调用 OpenAI-compatible API")


@dataclass
class RuntimeBundle:
    executor: SafeExecutor
    env_profile: dict
    audit_log: AuditLog
    controller: AgentController
    session_store: SessionStore
    task_store: TaskStore
    lock_store: LockStore
    permission_policy: PermissionPolicy
    memory_manager: MemoryManager
    trace_store: TraceStore
    command_registry: CommandRegistry
    privilege_manager: PrivilegeManager

    def close(self) -> None:
        try:
            self.controller.unbind_task()
        except Exception:
            pass
        try:
            self.privilege_manager.clear()
        except Exception:
            pass
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
    llm_client: Any | None = None,
    confirm_callback=None,
    input_callback=None,
    surface: str = "unknown",
) -> RuntimeBundle:
    privilege_manager = PrivilegeManager(input_callback=input_callback)
    if config.remote_mode:
        ssh_cfg = SSHConfig(
            host=config.ssh_host,
            port=config.ssh_port,
            username=config.ssh_user,
            password=config.ssh_password or None,
            key_filename=config.ssh_key_file or None,
            sudo_password=config.ssh_sudo_password or None,
        )
        executor = RemoteExecutor(ssh_cfg)
        executor.connect()
    else:
        executor = LocalExecutor(privilege_manager=privilege_manager)

    probe = CapabilityProbe(
        executor,
        remote_mode=config.remote_mode,
        ssh_port=config.ssh_port,
    )
    env_profile = probe.probe()
    audit = AuditLog(session_id=session_id)
    session_store = SessionStore()
    task_store = TaskStore()
    lock_store = LockStore()
    permission_policy = PermissionPolicy()
    memory_manager = MemoryManager()
    trace_store = TraceStore()
    command_registry = CommandRegistry()

    if llm_client is None:
        if require_api:
            if not config.api_key:
                raise RuntimeError("OPENAI_API_KEY is required for this entrypoint")
            if not config.model:
                raise RuntimeError("OPENAI_MODEL or --model is required for this entrypoint")
            llm_client = OpenAIChatClient(
                api_key=config.api_key,
                base_url=config.base_url or None,
                model=config.model,
            )
        else:
            llm_client = NullLLMClient()

    controller = AgentController(
        executor=executor,
        env_profile=env_profile,
        audit_log=audit,
        registry=default_registry(),
        llm_client=llm_client,
        dynamic_registry=DynamicToolRegistry(),
        max_iterations=config.max_iterations,
        workflows_dir=Path(config.workflows_dir) if config.workflows_dir else None,
        surface=surface,
        session_store=session_store,
        task_store=task_store,
        lock_store=lock_store,
        permission_policy=permission_policy,
        memory_manager=memory_manager,
        trace_store=trace_store,
        command_registry=command_registry,
    )
    try:
        existing = session_store.load(controller.session_id)
        if existing is not None and (existing.history or existing.context):
            session_store.restore_to_manager(controller.session_id, controller.conversation_manager)
    except Exception:
        pass
    if confirm_callback is not None:
        controller.confirm_callback = confirm_callback
    if input_callback is not None:
        controller.input_callback = input_callback
        privilege_manager.set_input_callback(input_callback)
    else:
        privilege_manager.set_input_callback(controller.input_callback)

    return RuntimeBundle(
        executor=executor,
        env_profile=env_profile,
        audit_log=audit,
        controller=controller,
        session_store=session_store,
        task_store=task_store,
        lock_store=lock_store,
        permission_policy=permission_policy,
        memory_manager=memory_manager,
        trace_store=trace_store,
        command_registry=command_registry,
        privilege_manager=privilege_manager,
    )
