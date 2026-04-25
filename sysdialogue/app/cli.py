"""CLI 入口 — click 驱动。

用法：
  sysdialogue                        启动 TUI（需 OPENAI_API_KEY + 模型）
  sysdialogue --verify               系统自检（不调 API）
  sysdialogue --demo                 演示 security_audit 工作流（不调 API）
  sysdialogue --remote user@host     远程模式（SSH）
"""

from __future__ import annotations

import os
import sys

import click

from sysdialogue.app.config import load_config
from sysdialogue.app.jobs import run_scheduled_job
from sysdialogue.app.runtime_factory import create_runtime
from sysdialogue.app.simple_cli import run_simple_cli
from sysdialogue.app.verify import run_demo, run_verify
from sysdialogue.audit.serializers import export_audit_jsonl, export_replay_package
from sysdialogue.audit.trace_store import AuditLog


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--verify", is_flag=True, help="系统自检：探测环境 / 列工具 / 检查配置，不调 API")
@click.option("--demo", is_flag=True, help="演示 security_audit 工作流，不调 API")
@click.option("--remote", metavar="USER@HOST[:PORT]", help="远程 SSH 模式")
@click.option("--ssh-key", "ssh_key_file", type=click.Path(exists=True),
              help="SSH 私钥文件路径")
@click.option("--ssh-password", envvar="SYSDIALOGUE_SSH_PASSWORD",
              help="SSH 密码；也可用环境变量 SYSDIALOGUE_SSH_PASSWORD")
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
@click.option("--web", "web_mode", is_flag=True, help="启动轻量 Web 控制台")
@click.option("--host", "web_host", default="127.0.0.1", show_default=True,
              help="Web 控制台监听地址")
@click.option("--port", "web_port", default=8000, show_default=True, type=int,
              help="Web 控制台监听端口")
@click.option("--break-glass", "break_glass", is_flag=True,
              help="Enable the explicit break_glass safety profile for DynTool shell execution.")
def main(verify: bool, demo: bool, remote: str | None,
         ssh_key_file: str | None, ssh_password: str | None,
         model: str | None, env_file: str | None,
         workflows_dir: str | None, scheduled_job_id: str | None,
         export_audit_session: str | None, export_replay_session: str | None,
         export_dir: str | None,
         simple: bool, web_mode: bool, web_host: str, web_port: int,
         break_glass: bool) -> None:
    """SysDialogue v9 — Linux 服务器运维智能代理。"""

    ssh_conf: dict = {}
    remote_mode = False
    if remote:
        remote_mode = True
        # 解析 user@host:port
        parts = remote.split("@", 1)
        if len(parts) == 2:
            user, hostport = parts
        else:
            user, hostport = os.environ.get("USER", "root"), parts[0]
        if ":" in hostport:
            host, port = hostport.rsplit(":", 1)
            ssh_conf = {"user": user, "host": host,
                        "port": int(port), "key_file": ssh_key_file or "",
                        "password": ssh_password or ""}
        else:
            ssh_conf = {"user": user, "host": hostport,
                        "port": 22, "key_file": ssh_key_file or "",
                        "password": ssh_password or ""}

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
    if verify:
        sys.exit(run_verify(config))
    if demo:
        sys.exit(run_demo(config))
    if scheduled_job_id:
        sys.exit(run_scheduled_job(config, scheduled_job_id))
    if simple:
        _require_api_config(config, "Simple CLI")
        sys.exit(run_simple_cli(config))
    if web_mode:
        _require_api_config(config, "Web 控制台")
        from sysdialogue.web.app import run_web_server
        run_web_server(config, host=web_host, port=web_port)
        return

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
    click.secho(
        f"错误：缺少 OpenAI-compatible API 配置，无法启动 {entrypoint}。\n"
        f"  - 缺少：{', '.join(missing)}\n"
        "  - 设置环境变量：export OPENAI_API_KEY=...\n"
        "  - 设置模型：export OPENAI_MODEL=... 或使用 --model\n"
        "  - 可选 base_url：export OPENAI_BASE_URL=https://...\n"
        "  - 或创建 .env 文件并用 --env-file 指定\n"
        "  - 不调 API 可用 --verify、--demo 或 --run-scheduled-job 模式",
        fg="red",
        err=True,
    )
    sys.exit(2)


def _run_tui(config) -> None:
    from sysdialogue.ui.tui_app import run_tui

    runtime = create_runtime(
        config,
        require_api=True,
        surface="tui",
    )
    try:
        run_tui(runtime.controller)
    finally:
        runtime.close()


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
