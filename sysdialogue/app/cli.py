"""CLI 入口 — click 驱动。

用法：
  sysdialogue                        启动 TUI（需 ANTHROPIC_API_KEY）
  sysdialogue --verify               系统自检（不调 API）
  sysdialogue --demo                 演示 security_audit 工作流（不调 API）
  sysdialogue --remote user@host     远程模式（SSH）
  sysdialogue --dev                  关闭竞赛模式（开启 DynTool）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from sysdialogue.app.config import load_config
from sysdialogue.app.verify import run_demo, run_verify


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--verify", is_flag=True, help="系统自检：探测环境 / 列工具 / 检查配置，不调 API")
@click.option("--demo", is_flag=True, help="演示 security_audit 工作流，不调 API")
@click.option("--remote", metavar="USER@HOST[:PORT]", help="远程 SSH 模式")
@click.option("--ssh-key", "ssh_key_file", type=click.Path(exists=True),
              help="SSH 私钥文件路径")
@click.option("--dev", is_flag=True, help="关闭竞赛模式（开启 DynTool）")
@click.option("--model", help="覆盖默认模型（如 claude-opus-4-7）")
@click.option("--env-file", type=click.Path(), help=".env 配置文件路径")
@click.option("--workflows-dir", type=click.Path(),
              help="工作流 YAML 目录（默认 sysdialogue/workflows/）")
def main(verify: bool, demo: bool, remote: str | None,
         ssh_key_file: str | None, dev: bool,
         model: str | None, env_file: str | None,
         workflows_dir: str | None) -> None:
    """SysDialogue v6 — Linux 服务器运维智能代理。"""

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
                        "port": int(port), "key_file": ssh_key_file or ""}
        else:
            ssh_conf = {"user": user, "host": hostport,
                        "port": 22, "key_file": ssh_key_file or ""}

    config = load_config(
        env_file=env_file,
        competition_mode=(not dev),
        model=model,
        remote=remote_mode,
        ssh=ssh_conf if ssh_conf else None,
    )
    if workflows_dir:
        config.workflows_dir = workflows_dir

    if verify:
        sys.exit(run_verify(config))
    if demo:
        sys.exit(run_demo(config))

    # 启动 TUI
    if not config.api_key:
        click.secho(
            "错误：未配置 ANTHROPIC_API_KEY，无法启动 TUI。\n"
            "  - 设置环境变量：export ANTHROPIC_API_KEY=...\n"
            "  - 或创建 .env 文件并 --env-file 指定\n"
            "  - 不调 API 可用 --verify 或 --demo 模式",
            fg="red", err=True,
        )
        sys.exit(2)

    _run_tui(config)


def _run_tui(config) -> None:
    from sysdialogue.agent.controller import AgentController, ClaudeClient
    from sysdialogue.audit.trace_store import AuditLog
    from sysdialogue.runtime.capability_probe import CapabilityProbe
    from sysdialogue.runtime.secure_runner import LocalExecutor
    from sysdialogue.tools.registry import default_registry
    from sysdialogue.ui.tui_app import run_tui

    if config.remote_mode:
        from sysdialogue.runtime.ssh_adapter import RemoteExecutor, SSHConfig
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

    probe = CapabilityProbe(executor,
                            remote_mode=config.remote_mode,
                            ssh_port=config.ssh_port)
    env_profile = probe.probe()

    audit = AuditLog()
    claude = ClaudeClient(api_key=config.api_key, model=config.model)
    controller = AgentController(
        executor=executor,
        env_profile=env_profile,
        audit_log=audit,
        registry=default_registry(),
        claude_client=claude,
        competition_mode=config.competition_mode,
        max_iterations=config.max_iterations,
        workflows_dir=Path(config.workflows_dir) if config.workflows_dir else None,
    )
    try:
        run_tui(controller)
    finally:
        if config.remote_mode and hasattr(executor, "disconnect"):
            executor.disconnect()


if __name__ == "__main__":
    main()
