"""CLI 入口 — click 驱动。

用法：
  sysdialogue                        启动 TUI（需 OPENAI_API_KEY + 模型）
  sysdialogue --verify               系统自检（不调 API）
  sysdialogue --acceptance           生成发布验收清单（不调 API）
  sysdialogue --acceptance-bundle DIR 导出脱敏验收证据包（不调 API）
  sysdialogue --release-readiness DIR 汇总验收产物（不调 API）
  sysdialogue --demo                 演示 security_audit 工作流（不调 API）
  sysdialogue --remote user@host     远程模式（SSH）
  sysdialogue --simple               启动 stdin/stdout 轻量 CLI
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path

import click

from sysdialogue.app.config import AppConfig, load_config
from sysdialogue.app.acceptance_collection import (
    collect_conversation_acceptance_evidence,
    collect_model_diagnostic_acceptance_evidence,
    collect_read_only_acceptance_evidence,
    collect_recovery_acceptance_evidence,
    collect_replay_acceptance_evidence,
    collect_ui_acceptance_evidence,
)
from sysdialogue.app.mutation_drill import collect_operator_approved_mutation_drill_evidence
from sysdialogue.app.jobs import run_scheduled_job
from sysdialogue.app.runtime_factory import RuntimeStartupError, create_runtime
from sysdialogue.app.simple_cli import run_simple_cli
from sysdialogue.app.verify import run_demo, run_verify
from sysdialogue.agent.controller import OpenAIChatClient
from sysdialogue.agent.acceptance_checklist import render_acceptance_checklist
from sysdialogue.agent.acceptance_bundle import write_acceptance_bundle
from sysdialogue.agent.acceptance_runner import render_guided_acceptance
from sysdialogue.agent.evidence_matrix import render_evidence_matrix
from sysdialogue.agent.model_diagnostics import diagnose_tool_call_support
from sysdialogue.agent.release_readiness import render_release_gate_report, render_release_readiness_report
from sysdialogue.audit.serializers import export_audit_jsonl, export_replay_package
from sysdialogue.audit.trace_store import AuditLog


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--evidence", is_flag=True, help="Show product-bar and verification-gate evidence; no API call.")
@click.option("--acceptance", is_flag=True, help="Show release-ready operator acceptance checklist; no API call.")
@click.option("--acceptance-runner", is_flag=True, help="Run safe local acceptance preflight and print a guided A01-A10 artifact; no API call.")
@click.option("--acceptance-runner-mode", type=click.Choice(["safe-preflight", "model-check", "conversation-check", "ui-review", "read-only-collect", "recovery-drill", "replay-export", "operator-approved-drill"]), default="safe-preflight", show_default=True,
              help="Guided acceptance runner mode. model-check and conversation-check call the configured model; ui-review checks command discoverability; read-only-collect may connect to the target and run non-mutating collection; recovery-drill uses only local SysDialogue state; replay-export writes a real replay ZIP for an audit session.")
@click.option("--acceptance-drill-plan", type=click.Path(exists=True, dir_okay=False),
              help="JSON plan for --acceptance-runner-mode operator-approved-drill.")
@click.option("--acceptance-replay-session",
              help="Existing audit session id for --acceptance-runner-mode replay-export.")
@click.option("--acceptance-suite", "acceptance_suite_path", type=click.Path(file_okay=False),
              help="Write a local, non-mutating acceptance evidence kit directory; no API call.")
@click.option("--acceptance-bundle", "acceptance_bundle_path", type=click.Path(exists=True),
              help="Export a sanitized acceptance evidence bundle ZIP; no API call.")
@click.option("--release-readiness", "release_readiness_path", type=click.Path(exists=True),
              help="Summarize completed acceptance artifacts into a release-readiness report; no API call.")
@click.option("--release-gate", "release_gate_path", type=click.Path(exists=True),
              help="Fail with exit code 1 unless acceptance artifacts are release-ready; no API call.")
@click.option("--verify", is_flag=True, help="系统自检：探测环境 / 列工具 / 检查配置，不调 API")
@click.option("--doctor", is_flag=True, help="代理体检：展示会话、工具、记忆、技能、Hooks 和可行动提醒，不调 API")
@click.option("--check-model", is_flag=True, help="模型适配诊断：调用一次模型确认 tool_calls 支持，不执行系统操作")
@click.option("--demo", is_flag=True, help="演示 security_audit 工作流，不调 API")
@click.option("--remote", metavar="USER@HOST[:PORT]", help="远程 SSH 模式")
@click.option("--ssh-key", "ssh_key_file", type=click.Path(exists=True),
              help="SSH 私钥文件路径")
@click.option("--ssh-password", envvar="SYSDIALOGUE_SSH_PASSWORD",
              help="SSH 密码；也可用环境变量 SYSDIALOGUE_SSH_PASSWORD")
@click.option("--ssh-proxy-command", envvar="SYSDIALOGUE_SSH_PROXY_COMMAND",
              help="SSH ProxyCommand for bastion/jump-host access; supports %h/%p/%r placeholders")
@click.option("--model", help="覆盖 OpenAI-compatible 模型（如 gpt-5.4 或你的服务模型名）")
@click.option("--env-file", type=click.Path(), help=".env 配置文件路径")
@click.option("--workflows-dir", type=click.Path(),
              help="工作流 YAML 目录（默认 sysdialogue/workflows/）")
@click.option("--run-scheduled-job", "scheduled_job_id",
              help="执行已注册的计划任务（供 cron 调用）")
@click.option("--export-audit", "export_audit_session",
              help="Export sanitized audit JSONL for a session id")
@click.option("--export-replay", "export_replay_session",
              help="Export sanitized replay ZIP for a session id")
@click.option("--export-dir", "export_dir", type=click.Path(file_okay=False),
              help="Directory for audit/replay exports")
@click.option("--simple", is_flag=True, help="启动 stdin/stdout 轻量 CLI")
@click.option("--break-glass", "break_glass", is_flag=True,
              help="Enable the explicit break_glass safety profile for DynTool shell execution.")
@click.option("--setup", is_flag=True, help="交互式配置向导（设置 API Key、模型等）")
@click.option("--config", "show_config", is_flag=True, help="查看当前配置")
@click.option("--reset", is_flag=True, help="重新配置（配合 --setup 使用）")
def main(evidence: bool, acceptance: bool, acceptance_runner: bool, acceptance_runner_mode: str, acceptance_drill_plan: str | None, acceptance_replay_session: str | None, acceptance_suite_path: str | None, acceptance_bundle_path: str | None, release_readiness_path: str | None, release_gate_path: str | None,
         verify: bool, doctor: bool, check_model: bool, demo: bool, remote: str | None,
         ssh_key_file: str | None, ssh_password: str | None, ssh_proxy_command: str | None,
         model: str | None, env_file: str | None,
         workflows_dir: str | None, scheduled_job_id: str | None,
         export_audit_session: str | None, export_replay_session: str | None,
         export_dir: str | None,
         simple: bool,
         break_glass: bool,
         setup: bool,
         show_config: bool,
         reset: bool) -> None:
    """SysDialogue v9 — Linux 服务器运维智能代理。"""

    # 查看当前配置
    if show_config:
        from sysdialogue.app.setup import show_config
        sys.exit(show_config())

    # 交互式配置向导
    if setup:
        from sysdialogue.app.setup import run_setup
        sys.exit(run_setup(reset=reset))

    remote_mode, ssh_conf = _parse_remote_option(remote, ssh_key_file, ssh_password, ssh_proxy_command)

    config = load_config(
        env_file=env_file,
        model=model,
        remote=remote_mode,
        ssh=ssh_conf if ssh_conf else None,
        safety_profile="break_glass" if break_glass else None,
    )
    if workflows_dir:
        config.workflows_dir = workflows_dir

    if export_audit_session:
        _export_session_artifact(export_audit_session, "audit", export_dir)
        return
    if export_replay_session:
        _export_session_artifact(export_replay_session, "replay", export_dir)
        return
    if evidence:
        click.echo(render_evidence_matrix())
        return
    if acceptance:
        click.echo(render_acceptance_checklist(_acceptance_env_from_config(config)))
        return
    if acceptance_runner:
        click.echo(_render_acceptance_runner(config, acceptance_runner_mode, acceptance_drill_plan, acceptance_replay_session, export_dir))
        return
    if acceptance_suite_path:
        suite_dir = _write_acceptance_suite(config, acceptance_suite_path, acceptance_replay_session)
        click.echo(f"Acceptance suite written: {suite_dir}")
        return
    if acceptance_bundle_path:
        output_path = write_acceptance_bundle(
            acceptance_bundle_path,
            export_dir=export_dir,
            target=_acceptance_target_from_config(config),
        )
        click.echo(f"Acceptance evidence bundle written: {output_path}")
        return
    if release_readiness_path:
        click.echo(render_release_readiness_report(release_readiness_path))
        return
    if release_gate_path:
        report, exit_code = render_release_gate_report(release_gate_path)
        click.echo(report)
        sys.exit(exit_code)
    if doctor:
        sys.exit(_run_doctor(config))
    if check_model:
        _require_api_config(config, "model diagnostic")
        sys.exit(_run_model_check(config))
    if verify:
        sys.exit(run_verify(config))
    if demo:
        try:
            sys.exit(run_demo(config))
        except RuntimeStartupError as exc:
            raise click.ClickException(str(exc)) from exc
    if scheduled_job_id:
        sys.exit(run_scheduled_job(config, scheduled_job_id))
    if simple:
        _require_api_config(config, "Simple CLI")
        try:
            sys.exit(run_simple_cli(config))
        except RuntimeStartupError as exc:
            raise click.ClickException(str(exc)) from exc

    # 启动 TUI
    _require_api_config(config, "TUI")

    _run_tui(config)


def _require_api_config(config, entrypoint: str) -> None:
    missing = []
    if not config.api_key:
        missing.append("OPENAI_API_KEY")
    if not config.model:
        missing.append("OPENAI_MODEL 或 --model")
    if not missing:
        return

    from sysdialogue.app.setup import has_global_config, run_setup

    if not has_global_config():
        # 首次运行，自动引导配置
        click.echo()
        click.secho("欢迎使用 SysDialogue！", fg="cyan", bold=True)
        click.secho("检测到尚未配置 API 连接信息，正在启动配置向导...\n", fg="yellow")
        rc = run_setup()
        if rc != 0:
            sys.exit(2)
        # 重新加载配置
        from sysdialogue.app.setup import load_global_config
        for key, value in load_global_config().items():
            os.environ.setdefault(key, value)
        config.api_key = config.api_key or os.environ.get("OPENAI_API_KEY", "")
        config.base_url = config.base_url or os.environ.get("OPENAI_BASE_URL", "")
        config.model = config.model or os.environ.get("OPENAI_MODEL", "")
        # 再次检查
        if config.api_key and config.model:
            return
    click.secho(
        f"错误：缺少 OpenAI-compatible API 配置，无法启动 {entrypoint}。\n"
        f"  - 缺少：{', '.join(missing)}\n"
        "  - 运行 sysdialogue --setup 交互式配置\n"
        "  - 或设置环境变量：export OPENAI_API_KEY=...\n"
        "  - 或创建 .env 文件并用 --env-file 指定\n"
        "  - 不调 API 可用 --verify、--evidence、--acceptance、--doctor、--demo 模式",
        fg="red",
        err=True,
    )
    sys.exit(2)


def _acceptance_env_from_config(config) -> dict:
    return {
        "remote_mode": config.remote_mode,
        "ssh_user": config.ssh_user,
        "ssh_host": config.ssh_host,
        "host": config.ssh_host,
        "ssh_port": config.ssh_port,
        "ssh_proxy_command_configured": bool(config.ssh_proxy_command),
    }


def _acceptance_target_from_config(config) -> str:
    env = _acceptance_env_from_config(config)
    if env.get("remote_mode"):
        suffix = " via ProxyCommand" if env.get("ssh_proxy_command_configured") else ""
        return f"ssh://{env.get('host') or 'host'}:{env.get('ssh_port') or 22}{suffix}"
    return "local-or-placeholder"


def _render_acceptance_runner(
    config,
    mode: str,
    drill_plan_path: str | None = None,
    replay_session_id: str | None = None,
    export_dir: str | None = None,
) -> str:
    env = _acceptance_env_from_config(config)
    if mode == "safe-preflight":
        return render_guided_acceptance(env)
    if mode == "model-check":
        _require_api_config(config, "acceptance runner model-check")
        client = OpenAIChatClient(
            api_key=config.api_key,
            base_url=config.base_url or None,
            model=config.model,
        )
        collected = collect_model_diagnostic_acceptance_evidence(client)
        return render_guided_acceptance(env, mode="model-check", collected=collected)
    if mode == "conversation-check":
        _require_api_config(config, "acceptance runner conversation-check")
        runtime = create_runtime(
            config,
            require_api=True,
            surface="cli",
        )
        try:
            collected = collect_conversation_acceptance_evidence(runtime.controller)
            return render_guided_acceptance(env, mode="conversation-check", collected=collected)
        finally:
            runtime.close()
    if mode == "ui-review":
        collected = collect_ui_acceptance_evidence()
        return render_guided_acceptance(env, mode="ui-review", collected=collected)
    if mode == "recovery-drill":
        runtime = create_runtime(
            config,
            require_api=False,
            surface="cli",
        )
        try:
            collected = collect_recovery_acceptance_evidence(runtime.controller)
            return render_guided_acceptance(env, mode="recovery-drill", collected=collected)
        finally:
            runtime.close()
    if mode == "replay-export":
        if not replay_session_id:
            raise click.ClickException("--acceptance-replay-session is required for replay-export mode.")
        audit = AuditLog(session_id=replay_session_id)
        if not audit.path.exists():
            raise click.ClickException(f"audit session not found: {replay_session_id}")
        collected = collect_replay_acceptance_evidence(audit, export_dir=export_dir)
        return render_guided_acceptance(env, mode="replay-export", collected=collected)
    runtime = create_runtime(
        config,
        require_api=False,
        surface="cli",
    )
    try:
        if mode == "operator-approved-drill":
            if not drill_plan_path:
                raise click.ClickException("--acceptance-drill-plan is required for operator-approved-drill mode.")
            try:
                with open(drill_plan_path, encoding="utf-8") as file:
                    plan = json.load(file)
            except json.JSONDecodeError as exc:
                raise click.ClickException(f"--acceptance-drill-plan is not valid JSON: {exc}") from exc
            if not isinstance(plan, dict):
                raise click.ClickException("--acceptance-drill-plan must contain a JSON object.")
            collected = collect_operator_approved_mutation_drill_evidence(
                runtime.controller,
                plan,
                workflows_dir=config.workflows_dir,
            )
            return render_guided_acceptance(env, mode="operator-approved-drill", collected=collected)
        collected = collect_read_only_acceptance_evidence(
            runtime.controller,
            workflows_dir=config.workflows_dir,
        )
        return render_guided_acceptance(env, mode="read-only-collect", collected=collected)
    finally:
        runtime.close()


def _write_acceptance_suite(config, output_dir: str, replay_session_id: str | None = None) -> Path:
    """Write a local, non-mutating acceptance evidence kit.

    The suite intentionally avoids model calls, SSH connections, and mutation
    workflows. It packages the automated local checks that are safe to run on a
    release engineer workstation, then emits readiness output that remains
    partial until real staging/disposable-host evidence is added.
    """

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    env = _acceptance_env_from_config(config)

    artifacts: list[tuple[str, str]] = []
    artifacts.append(("acceptance-safe-preflight.md", render_guided_acceptance(env)))
    artifacts.append(("acceptance-ui.md", render_guided_acceptance(env, mode="ui-review", collected=collect_ui_acceptance_evidence())))

    local_config = AppConfig(
        workflows_dir=config.workflows_dir,
        max_iterations=config.max_iterations,
        safety_profile=config.safety_profile,
    )
    runtime = create_runtime(local_config, require_api=False, surface="acceptance-suite")
    try:
        recovery = collect_recovery_acceptance_evidence(runtime.controller)
    finally:
        runtime.close()
    artifacts.append(("acceptance-recovery.md", render_guided_acceptance(env, mode="recovery-drill", collected=recovery)))

    if replay_session_id:
        audit = AuditLog(session_id=replay_session_id)
        if not audit.path.exists():
            raise click.ClickException(f"audit session not found: {replay_session_id}")
        replay = collect_replay_acceptance_evidence(audit, export_dir=out)
        artifacts.append(("acceptance-replay.md", render_guided_acceptance(env, mode="replay-export", collected=replay)))

    for filename, text in artifacts:
        (out / filename).write_text(text, encoding="utf-8")

    (out / "README.md").write_text(_acceptance_suite_readme(env, replay_session_id=bool(replay_session_id)), encoding="utf-8")
    (out / "release-readiness.md").write_text(render_release_readiness_report(out), encoding="utf-8")
    return out


def _acceptance_suite_readme(env: dict, *, replay_session_id: bool) -> str:
    target = _acceptance_target_from_config(SimpleNamespaceFromDict(env))
    lines = [
        "# SysDialogue Acceptance Suite",
        "",
        f"- Target context: {target}",
        "- Scope: local non-mutating evidence only; no model call, no SSH connection, no mutation workflow.",
        "- Generated artifacts: safe preflight, UI-review, recovery drill, README, and release-readiness report.",
    ]
    if replay_session_id:
        lines.append("- Replay export: included from the supplied audit session.")
    else:
        lines.append("- Replay export: not included; add `--acceptance-replay-session <session_id>` when a real audit session exists.")
    lines.extend(
        [
            "",
            "Next actions before release:",
            "- Run model-check and conversation-check with the release model.",
            "- Run read-only collection on the staging or disposable target.",
            "- Run the operator-approved A07 drill only on a disposable or explicitly low-risk target.",
            "- Attach a real replay ZIP if this suite does not already include one.",
            "- Re-run `sysdialogue --release-gate <this-directory>` after adding the missing evidence.",
        ]
    )
    return "\n".join(lines)


class SimpleNamespaceFromDict:
    def __init__(self, data: dict):
        self.remote_mode = bool(data.get("remote_mode"))
        self.ssh_user = str(data.get("ssh_user") or data.get("current_user") or "")
        self.ssh_host = str(data.get("host") or data.get("ssh_host") or "")
        self.ssh_port = int(data.get("ssh_port") or 22)
        self.ssh_proxy_command = "<configured>" if data.get("ssh_proxy_command_configured") else ""


def _parse_remote_option(
    remote: str | None,
    ssh_key_file: str | None,
    ssh_password: str | None,
    ssh_proxy_command: str | None = None,
) -> tuple[bool, dict]:
    if not remote:
        return False, {}
    raw = remote.strip()
    if not raw:
        raise click.BadParameter("remote target cannot be empty", param_hint="--remote")

    parts = raw.split("@", 1)
    if len(parts) == 2:
        user = parts[0].strip()
        hostport = parts[1].strip()
        if not user:
            raise click.BadParameter("remote user cannot be empty", param_hint="--remote")
    else:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "root"
        hostport = parts[0].strip()
    if not hostport:
        raise click.BadParameter("remote host cannot be empty", param_hint="--remote")

    if ":" in hostport:
        host, port_text = hostport.rsplit(":", 1)
        host = host.strip()
        port_text = port_text.strip()
        if not host:
            raise click.BadParameter("remote host cannot be empty", param_hint="--remote")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise click.BadParameter(
                "remote port must be an integer between 1 and 65535",
                param_hint="--remote",
            ) from exc
        if not 1 <= port <= 65535:
            raise click.BadParameter(
                "remote port must be between 1 and 65535",
                param_hint="--remote",
            )
    else:
        host = hostport
        port = 22

    return True, {
        "user": user,
        "host": host,
        "port": port,
        "key_file": ssh_key_file or "",
        "password": ssh_password or "",
        "proxy_command": (ssh_proxy_command or "").strip(),
    }


def _run_tui(config) -> None:
    from sysdialogue.ui.tui_app import run_tui

    try:
        runtime = create_runtime(
            config,
            require_api=True,
            surface="tui",
        )
    except RuntimeStartupError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        run_tui(runtime.controller)
    finally:
        runtime.close()


def _run_doctor(config) -> int:
    try:
        runtime = create_runtime(
            config,
            require_api=False,
            surface="doctor",
        )
    except RuntimeStartupError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        click.echo(runtime.controller.run_turn("/doctor"))
    finally:
        runtime.close()
    return 0


def _run_model_check(config) -> int:
    client = OpenAIChatClient(
        api_key=config.api_key,
        base_url=config.base_url or None,
        model=config.model,
    )
    result = diagnose_tool_call_support(client)
    click.echo(result.to_text())
    return 0 if result.ok else 1


def _export_session_artifact(session_id: str, kind: str, export_dir: str | None) -> None:
    audit = AuditLog(session_id=session_id)
    if not audit.path.exists():
        raise click.ClickException(f"audit session not found: {session_id}")
    if kind == "audit":
        path = export_audit_jsonl(audit, output_dir=export_dir)
    elif kind == "replay":
        path = export_replay_package(audit, output_dir=export_dir)
    else:
        raise click.ClickException(f"unknown export kind: {kind}")
    click.echo(str(path))


if __name__ == "__main__":
    main()
