from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

import sysdialogue.app.cli as cli_module
from sysdialogue.app.cli import _require_api_config, main
from sysdialogue.app.config import AppConfig, load_config
from sysdialogue.app.runtime_factory import create_runtime
from sysdialogue.agent.conversation import ConversationManager
from sysdialogue.agent.state_store import SessionStore
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.tools.dynamic_registry import DynamicToolRegistry


def test_require_api_config_exits_with_clear_message(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _require_api_config(AppConfig(api_key="", model=""), "Web 控制台")

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "OPENAI_API_KEY" in captured.err
    assert "OPENAI_MODEL" in captured.err
    assert "Web 控制台" in captured.err


def test_load_config_prefers_cli_model_over_openai_and_legacy_env(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-env-model")
    monkeypatch.setenv("SYSDIALOGUE_MODEL", "legacy-model")

    config = load_config(model="cli-model")

    assert config.api_key == "sk-test"
    assert config.base_url == "https://api.example.test/v1"
    assert config.model == "cli-model"


def test_load_config_falls_back_to_openai_model_then_legacy(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_MODEL", "openai-env-model")
    monkeypatch.setenv("SYSDIALOGUE_MODEL", "legacy-model")
    assert load_config().model == "openai-env-model"

    monkeypatch.delenv("OPENAI_MODEL")
    assert load_config().model == "legacy-model"


def test_load_config_clamps_max_iterations(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_config().max_iterations == 160

    monkeypatch.setenv("SYSDIALOGUE_MAX_ITER", "999")
    assert load_config().max_iterations == 300

    monkeypatch.setenv("SYSDIALOGUE_MAX_ITER", "1")
    assert load_config().max_iterations == 20

    monkeypatch.setenv("SYSDIALOGUE_MAX_ITER", "not-an-int")
    assert load_config().max_iterations == 160


def test_load_config_reads_ssh_password_from_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SYSDIALOGUE_SSH_PASSWORD", "secret")
    monkeypatch.setenv("SYSDIALOGUE_SUDO_PASSWORD", "sudo-secret")

    config = load_config(
        remote=True,
        ssh={"host": "example.test", "port": 2222, "user": "alice", "key_file": ""},
    )

    assert config.remote_mode is True
    assert config.ssh_host == "example.test"
    assert config.ssh_password == "secret"
    assert config.ssh_sudo_password == "sudo-secret"


def test_tui_remote_cli_accepts_ssh_password_option(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_run_tui(config):
        captured["config"] = config

    monkeypatch.setattr(cli_module, "_run_tui", fake_run_tui)
    result = CliRunner().invoke(
        main,
        [
            "--remote",
            "alice@example.test:2222",
            "--ssh-password",
            "secret",
            "--model",
            "model",
        ],
        env={"OPENAI_API_KEY": "key"},
    )

    assert result.exit_code == 0
    config = captured["config"]
    assert config.remote_mode is True
    assert config.ssh_user == "alice"
    assert config.ssh_host == "example.test"
    assert config.ssh_port == 2222
    assert config.ssh_password == "secret"


def test_cli_help_no_longer_exposes_dev_mode() -> None:
    result = CliRunner().invoke(main, ["--help"])
    removed_option = "-" + "-dev"

    assert result.exit_code == 0
    assert removed_option not in result.output
    assert "competition" not in result.output.lower()
    assert "竞赛" not in result.output

def test_cli_exports_sanitized_audit_and_replay(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    audit = AuditLog(session_id="cli_export_session")
    audit.log_command("demo", ["echo", "OPENAI_API_KEY=sk-live-secret"], 0, "Bearer abc.def")
    export_dir = tmp_path / "exports"

    audit_result = CliRunner().invoke(
        main,
        ["--export-audit", "cli_export_session", "--export-dir", str(export_dir)],
    )
    replay_result = CliRunner().invoke(
        main,
        ["--export-replay", "cli_export_session", "--export-dir", str(export_dir)],
    )

    assert audit_result.exit_code == 0
    assert replay_result.exit_code == 0
    audit_path = Path(audit_result.output.strip())
    replay_path = Path(replay_result.output.strip())
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "sk-live-secret" not in audit_text
    assert "abc.def" not in audit_text
    with zipfile.ZipFile(replay_path) as zf:
        combined = "\n".join(zf.read(name).decode("utf-8") for name in zf.namelist())
    assert "sk-live-secret" not in combined
    assert "abc.def" not in combined


def test_cli_export_missing_session_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    result = CliRunner().invoke(main, ["--export-audit", "missing_session"])

    assert result.exit_code != 0
    assert "audit session not found" in result.output


def test_default_runtime_injects_executable_dynamic_registry() -> None:
    runtime = create_runtime(AppConfig(), require_api=False)
    try:
        assert isinstance(runtime.controller.dynamic_registry, DynamicToolRegistry)
        removed_attr = "competition" + "_mode"
        assert not hasattr(runtime.controller.dynamic_registry, removed_attr)
    finally:
        runtime.close()


def test_create_runtime_hydrates_persisted_conversation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    manager = ConversationManager()
    manager.context["service_name"] = "nginx"
    manager.history = [
        {"role": "user", "content": "检查 nginx"},
        {"role": "assistant", "content": [{"type": "text", "text": "检查完成。"}]},
    ]
    SessionStore().sync_manager("session_a", manager, surface="web")

    runtime = create_runtime(AppConfig(), session_id="session_a", require_api=False)
    try:
        assert runtime.controller.conversation_manager.context == {"service_name": "nginx"}
        assert runtime.controller.conversation_manager.history[-1]["content"] == [
            {"type": "text", "text": "检查完成。"}
        ]
    finally:
        runtime.close()
