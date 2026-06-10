"""Built-in production playbook catalog for users and the system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlaybookEntry:
    workflow_name: str
    title: str
    use_when: str
    params: str
    task: str
    safety: str


PRODUCTION_PLAYBOOKS: tuple[PlaybookEntry, ...] = (
    PlaybookEntry(
        workflow_name="security_audit",
        title="Read-only security audit",
        use_when="baseline triage before changes or after an incident",
        params="none",
        task="Run the built-in security_audit workflow on this target and summarize users, ports, processes, and network findings.",
        safety="read-only evidence collection",
    ),
    PlaybookEntry(
        workflow_name="port_scan",
        title="Listening port inventory",
        use_when="confirm exposed TCP/UDP listeners before firewall or service work",
        params="none",
        task="Run the built-in port_scan workflow and explain which listeners need follow-up.",
        safety="read-only evidence collection",
    ),
    PlaybookEntry(
        workflow_name="disk_cleanup",
        title="Disk pressure triage",
        use_when="find large files without deleting anything",
        params="none",
        task="Run the built-in disk_cleanup workflow and identify safe cleanup candidates without deleting files.",
        safety="read-only; deletion requires a separate approved task",
    ),
    PlaybookEntry(
        workflow_name="service_restart",
        title="Safe service restart",
        use_when="restart a known service with pre/post status checks",
        params='{"service_name":"nginx"}',
        task="Safely restart nginx using the built-in service_restart workflow, then report pre-check and post-check evidence.",
        safety="requires confirmation and a service lock",
    ),
    PlaybookEntry(
        workflow_name="safe_config_patch",
        title="Patch config with rollback",
        use_when="make a precise text replacement with backup, validation, optional reload, and rollback",
        params='{"file_path":"/etc/nginx/nginx.conf","search_text":"old","replace_text":"new","validator":"nginx","service_name":"nginx"}',
        task="Use the built-in safe_config_patch workflow to replace 'old' with 'new' in /etc/nginx/nginx.conf, validate nginx, reload nginx, and roll back if validation fails.",
        safety="dry-run preview, approval, backup, validation, rollback",
    ),
    PlaybookEntry(
        workflow_name="rollback_config",
        title="Rollback a config backup",
        use_when="restore a previous backup after a bad change",
        params='{"file_path":"/etc/nginx/nginx.conf","validator":"nginx"}',
        task="Use the built-in rollback_config workflow to restore /etc/nginx/nginx.conf from a selected backup and validate it.",
        safety="requires confirmation and validation after restore",
    ),
    PlaybookEntry(
        workflow_name="container_rollout",
        title="Container rollout",
        use_when="pull, run, check endpoint, inspect logs, and roll back failed starts",
        params='{"image":"nginx:stable","container_name":"web","host_port":8080,"container_port":80}',
        task="Roll out nginx:stable as container web on host port 8080 using the built-in container_rollout workflow, check TCP readiness, and roll back on failure.",
        safety="requires confirmation; stop/remove rollback on failed readiness",
    ),
    PlaybookEntry(
        workflow_name="scheduled_health_check",
        title="Scheduled endpoint health check",
        use_when="create a managed cron job that calls a static check_endpoint tool",
        params='{"endpoint_host":"127.0.0.1","endpoint_port":8080,"schedule":"*/5 * * * *"}',
        task="Create a scheduled health check for 127.0.0.1:8080 every five minutes using the built-in scheduled_health_check workflow.",
        safety="requires confirmation; cron target is restricted to static tool/workflow jobs",
    ),
    PlaybookEntry(
        workflow_name="new_user",
        title="Developer account setup",
        use_when="create a local account and optional supplemental groups",
        params='{"username":"deploy","groups":["docker"]}',
        task="Create developer user deploy with docker group using the built-in new_user workflow and verify the result.",
        safety="mutating account operation with user lock and verification",
    ),
)


def render_playbook_command_output(env: dict[str, Any] | None = None) -> str:
    env = env or {}
    remote_note = ""
    if env.get("remote_mode"):
        host = str(env.get("host") or env.get("hostname") or "remote")
        port = str(env.get("ssh_port") or "22")
        route = " via ProxyCommand" if env.get("ssh_proxy_command_configured") else ""
        remote_note = f"\nTarget: ssh://{host}:{port}{route}"

    lines = [
        "Production playbooks:",
        "Copy a task line into the input box, then adjust names, paths, ports, and schedules.",
        "The agent should route matching requests through set_execution_mode(mode=\"workflow\").",
    ]
    if remote_note:
        lines.append(remote_note.strip())
    for entry in PRODUCTION_PLAYBOOKS:
        lines.append("")
        lines.append(f"- {entry.title} (`{entry.workflow_name}`)")
        lines.append(f"  Task: {entry.task}")
        lines.append(f"  Params: {entry.params}")
        lines.append(f"  Safety: {entry.safety}")
    return "\n".join(lines)


def render_prompt_workflow_catalog() -> str:
    lines = [
        "[Built-in Production Workflows]",
        "Prefer these workflows over dynamic commands whenever the user request matches their use case.",
    ]
    for entry in PRODUCTION_PLAYBOOKS:
        lines.append(
            f"- {entry.workflow_name}: {entry.use_when}; params={entry.params}; safety={entry.safety}"
        )
    return "\n".join(lines)
