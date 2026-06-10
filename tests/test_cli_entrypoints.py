from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

import sysdialogue.app.cli as cli_module
import sysdialogue.app.runtime_factory as runtime_factory_module
from sysdialogue.app.cli import _parse_remote_option, _require_api_config, main
from sysdialogue.app.config import AppConfig, load_config
from sysdialogue.app.runtime_factory import RuntimeStartupError, create_runtime
from sysdialogue.app.verify import run_verify
from sysdialogue.agent.conversation import ConversationManager
from sysdialogue.agent.controller import LLMResponse
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
    assert "--doctor" in captured.err


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


def test_load_config_reads_safety_profile_and_operator_compat(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SYSDIALOGUE_SAFETY_PROFILE", "break-glass")
    assert load_config().safety_profile == "break_glass"

    monkeypatch.delenv("SYSDIALOGUE_SAFETY_PROFILE")
    monkeypatch.setenv("SYSDIALOGUE_OPERATOR_MODE", "1")
    assert load_config().safety_profile == "operator"

    assert load_config(safety_profile="break_glass").safety_profile == "break_glass"


def test_load_config_reads_ssh_password_from_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SYSDIALOGUE_SSH_PASSWORD", "secret")
    monkeypatch.setenv("SYSDIALOGUE_SUDO_PASSWORD", "sudo-secret")
    monkeypatch.setenv("SYSDIALOGUE_SSH_PROXY_COMMAND", "ssh -W %h:%p bastion")

    config = load_config(
        remote=True,
        ssh={"host": "example.test", "port": 2222, "user": "alice", "key_file": ""},
    )

    assert config.remote_mode is True
    assert config.ssh_host == "example.test"
    assert config.ssh_password == "secret"
    assert config.ssh_sudo_password == "sudo-secret"
    assert config.ssh_proxy_command == "ssh -W %h:%p bastion"


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
            "--ssh-proxy-command",
            "ssh -W %h:%p bastion.example.com",
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
    assert config.ssh_proxy_command == "ssh -W %h:%p bastion.example.com"


def test_remote_option_rejects_invalid_targets() -> None:
    with pytest.raises(Exception, match="remote port"):
        _parse_remote_option("alice@example.test:notaport", None, None)
    with pytest.raises(Exception, match="remote host"):
        _parse_remote_option("alice@", None, None)
    with pytest.raises(Exception, match="remote user"):
        _parse_remote_option("@example.test", None, None)


def test_remote_option_preserves_proxy_command() -> None:
    remote_mode, ssh_conf = _parse_remote_option(
        "alice@example.test:2222",
        None,
        None,
        "ssh -W %h:%p bastion.example.com",
    )

    assert remote_mode is True
    assert ssh_conf["proxy_command"] == "ssh -W %h:%p bastion.example.com"


def test_cli_remote_invalid_port_is_clear_usage_error(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main,
        ["--remote", "alice@example.test:notaport", "--model", "model"],
        env={"OPENAI_API_KEY": "key"},
    )

    assert result.exit_code == 2
    assert "remote port must be an integer" in result.output


def test_cli_break_glass_sets_safety_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_run_tui(config):
        captured["config"] = config

    monkeypatch.setattr(cli_module, "_run_tui", fake_run_tui)
    result = CliRunner().invoke(
        main,
        ["--break-glass", "--model", "model"],
        env={"OPENAI_API_KEY": "key"},
    )

    assert result.exit_code == 0
    assert captured["config"].safety_profile == "break_glass"


def test_default_tui_requires_api_config_with_clear_message(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, [], env={})

    assert result.exit_code == 2
    assert "无法启动 TUI" in result.output
    assert "OPENAI_API_KEY" in result.output
    assert "--verify" in result.output


def test_simple_cli_requires_api_config_with_clear_message(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--simple"], env={})

    assert result.exit_code == 2
    assert "无法启动 Simple CLI" in result.output
    assert "OPENAI_MODEL" in result.output
    assert "--doctor" in result.output


def test_simple_cli_invokes_runner_with_loaded_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_simple(config):
        captured["config"] = config
        return 0

    monkeypatch.setattr(cli_module, "run_simple_cli", fake_simple)
    result = CliRunner().invoke(
        main,
        ["--simple", "--model", "model"],
        env={"OPENAI_API_KEY": "key"},
    )

    assert result.exit_code == 0
    assert captured["config"].model == "model"
    assert captured["config"].api_key == "key"
    assert captured["config"].remote_mode is False


def test_cli_doctor_runs_without_api_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--doctor"], env={})

    assert result.exit_code == 0
    assert "SysDialogue doctor:" in result.output
    assert "Tools:" in result.output


def test_cli_check_model_runs_tool_call_diagnostic(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeOpenAIChatClient:
        def __init__(self, *, api_key, base_url, model):
            self.api_key = api_key
            self.base_url = base_url or ""
            self.model = model

        def messages_create(self, *, system, messages, tools):
            return LLMResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "call_diag",
                        "name": "diagnostic_ping",
                        "input": {"ok": True},
                    }
                ],
                stop_reason="tool_use",
            )

    monkeypatch.setattr(cli_module, "OpenAIChatClient", FakeOpenAIChatClient)

    result = CliRunner().invoke(
        main,
        ["--check-model", "--model", "model"],
        env={"OPENAI_API_KEY": "key", "OPENAI_BASE_URL": "https://api.example.test/v1"},
    )

    assert result.exit_code == 0
    assert "Model tool-call diagnostic:" in result.output
    assert "Status: ok" in result.output
    assert "model" in result.output


def test_cli_check_model_requires_api_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--check-model"], env={})

    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_verify_does_not_require_api_config(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    code = run_verify(AppConfig(api_key="", model=""))
    captured = capsys.readouterr()

    assert code == 0
    assert "non-blocking notice" in captured.out
    assert "OPENAI_API_KEY" in captured.out


def test_verify_remote_connection_failure_reports_recovery_without_api_config(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    class FailingRemoteExecutor:
        def __init__(self, config):
            self.config = config

        def connect(self):
            raise TimeoutError("timed out password=secret-value")

    monkeypatch.setattr(runtime_factory_module, "RemoteExecutor", FailingRemoteExecutor)

    code = run_verify(
        AppConfig(
            api_key="",
            model="",
            remote_mode=True,
            ssh_host="example.test",
            ssh_port=2222,
            ssh_user="alice",
            ssh_password="secret-value",
        )
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "Remote SSH connection failed for alice@example.test:2222" in captured.out
    assert "sysdialogue --doctor --remote alice@example.test:2222" in captured.out
    assert "/next" in captured.out
    assert "secret-value" not in captured.out


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


def test_create_runtime_remote_connection_failure_is_actionable(monkeypatch) -> None:
    class FailingRemoteExecutor:
        def __init__(self, config):
            self.config = config

        def connect(self):
            raise TimeoutError("timed out")

    monkeypatch.setattr(runtime_factory_module, "RemoteExecutor", FailingRemoteExecutor)
    config = AppConfig(
        remote_mode=True,
        ssh_host="example.test",
        ssh_port=2222,
        ssh_user="alice",
        ssh_password="secret",
        ssh_proxy_command="ssh -W %h:%p bastion.example.com",
    )

    with pytest.raises(RuntimeStartupError) as exc_info:
        create_runtime(config, require_api=False)

    message = str(exc_info.value)
    assert "alice@example.test:2222" in message
    assert "timed out" in message
    assert "secret" not in message
    assert "--ssh-password" in message
    assert "ssh -p 2222 alice@example.test 'uname -a'" in message
    assert "sysdialogue --doctor --remote alice@example.test:2222" in message
    assert "/next" in message
    assert "/abandon" in message
    assert "ProxyCommand is configured" in message


def test_create_runtime_passes_proxy_command_to_remote_executor(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    captured = {}

    class FakeRemoteExecutor:
        def __init__(self, config):
            captured["ssh_config"] = config

        def connect(self):
            pass

        def disconnect(self):
            pass

    class FakeCapabilityProbe:
        def __init__(self, executor, remote_mode=False, ssh_port=22):
            self.remote_mode = remote_mode
            self.ssh_port = ssh_port

        def probe(self):
            return {
                "remote_mode": self.remote_mode,
                "hostname": "remote-box",
                "ssh_port": self.ssh_port,
                "os_release": "Ubuntu 24.04",
                "distro_id": "ubuntu",
                "distro_family": "debian",
                "kernel_version": "6.8.0",
                "architecture": "x86_64",
                "current_user": "alice",
                "container_backend": "none",
            }

    monkeypatch.setattr(runtime_factory_module, "RemoteExecutor", FakeRemoteExecutor)
    monkeypatch.setattr(runtime_factory_module, "CapabilityProbe", FakeCapabilityProbe)

    runtime = create_runtime(
        AppConfig(
            remote_mode=True,
            ssh_host="example.test",
            ssh_port=2222,
            ssh_user="alice",
            ssh_proxy_command="ssh -W %h:%p bastion.example.com",
        ),
        require_api=False,
    )
    try:
        assert captured["ssh_config"].proxy_command == "ssh -W %h:%p bastion.example.com"
        assert runtime.env_profile["ssh_proxy_command_configured"] is True
        assert "Remote access: ssh://example.test:2222 via ProxyCommand" in runtime.controller.run_turn("/doctor")
    finally:
        runtime.close()


def test_create_runtime_remote_connection_failure_redacts_exception_secret(monkeypatch) -> None:
    class FailingRemoteExecutor:
        def __init__(self, config):
            self.config = config

        def connect(self):
            raise RuntimeError("auth failed password=secret-value")

    monkeypatch.setattr(runtime_factory_module, "RemoteExecutor", FailingRemoteExecutor)
    config = AppConfig(
        remote_mode=True,
        ssh_host="example.test",
        ssh_port=2222,
        ssh_user="alice",
        ssh_password="secret-value",
    )

    with pytest.raises(RuntimeStartupError) as exc_info:
        create_runtime(config, require_api=False)

    message = str(exc_info.value)
    assert "secret-value" not in message
    assert "password=<redacted>" in message


def test_cli_remote_doctor_connection_failure_is_click_error(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    class FailingRemoteExecutor:
        def __init__(self, config):
            self.config = config

        def connect(self):
            raise TimeoutError("timed out")

    monkeypatch.setattr(runtime_factory_module, "RemoteExecutor", FailingRemoteExecutor)

    result = CliRunner().invoke(
        main,
        ["--doctor", "--remote", "alice@example.test:2222"],
        env={},
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Remote SSH connection failed for alice@example.test:2222" in result.output
    assert "timed out" in result.output


def test_create_runtime_remote_profile_uses_connection_target_for_doctor(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    class FakeRemoteExecutor:
        def __init__(self, config):
            self.config = config
            self.connected = False

        def connect(self):
            self.connected = True

        def disconnect(self):
            self.connected = False

    class FakeCapabilityProbe:
        def __init__(self, executor, remote_mode=False, ssh_port=22):
            self.executor = executor
            self.remote_mode = remote_mode
            self.ssh_port = ssh_port

        def probe(self):
            return {
                "remote_mode": self.remote_mode,
                "hostname": "remote-box",
                "ssh_port": self.ssh_port,
                "os_release": "Ubuntu 24.04",
                "distro_id": "ubuntu",
                "distro_family": "debian",
                "kernel_version": "6.8.0",
                "architecture": "x86_64",
                "current_user": "alice",
                "container_backend": "none",
            }

    monkeypatch.setattr(runtime_factory_module, "RemoteExecutor", FakeRemoteExecutor)
    monkeypatch.setattr(runtime_factory_module, "CapabilityProbe", FakeCapabilityProbe)

    runtime = create_runtime(
        AppConfig(
            remote_mode=True,
            ssh_host="example.test",
            ssh_port=2222,
            ssh_user="alice",
        ),
        require_api=False,
    )
    try:
        assert runtime.env_profile["host"] == "example.test"
        assert runtime.env_profile["hostname"] == "remote-box"
        assert runtime.env_profile["ssh_port"] == 2222
        doctor = runtime.controller.run_turn("/doctor")
        assert "Target profile: ssh-example.test-2222" in doctor
    finally:
        runtime.close()
