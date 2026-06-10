from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from sysdialogue.agent.acceptance_checklist import render_acceptance_checklist
from sysdialogue.agent.command_registry import CommandRegistry
from sysdialogue.app.cli import main


def test_acceptance_checklist_renders_release_ready_remote_runbook() -> None:
    text = render_acceptance_checklist(
        {
            "remote_mode": True,
            "ssh_user": "alice",
            "host": "example.test",
            "ssh_port": 2222,
            "ssh_proxy_command_configured": True,
        }
    )

    assert "Operator acceptance checklist:" in text
    assert "Target: ssh://example.test:2222 via ProxyCommand" in text
    assert "sysdialogue --doctor --remote alice@example.test:2222 --ssh-proxy-command <proxy-command>" in text
    assert "sysdialogue --demo --remote alice@example.test:2222 --ssh-proxy-command <proxy-command>" in text
    assert "Release notes attachment template:" in text
    assert "/export-replay" in text
    assert "A10 evidence matrix attached" in text


def test_slash_acceptance_command_and_alias_use_current_target() -> None:
    controller = SimpleNamespace(
        env_profile={
            "remote_mode": True,
            "current_user": "deploy",
            "host": "prod.example.test",
            "ssh_port": 2200,
        }
    )
    registry = CommandRegistry()

    reply = registry.execute(controller, "/acceptance").output
    alias_reply = registry.execute(controller, "/release-checklist").output
    help_reply = registry.execute(controller, "/help").output

    assert "Operator acceptance checklist:" in reply
    assert "sysdialogue --doctor --remote deploy@prod.example.test:2200" in reply
    assert "A07 mutation safety gate passed" in reply
    assert alias_reply == reply
    assert "/acceptance" in help_reply


def test_cli_acceptance_runs_without_api_config_and_uses_remote_option(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        main,
        ["--acceptance", "--remote", "alice@example.test:2222"],
        env={},
    )

    assert result.exit_code == 0
    assert "Operator acceptance checklist:" in result.output
    assert "ssh://example.test:2222" in result.output
    assert "alice@example.test:2222" in result.output
    assert "OPENAI_API_KEY" not in result.output
