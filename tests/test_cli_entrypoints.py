from __future__ import annotations

import pytest
from click.testing import CliRunner

from sysdialogue.app.cli import _require_api_config, main
from sysdialogue.app.config import AppConfig, load_config
from sysdialogue.app.runtime_factory import create_runtime
from sysdialogue.agent.conversation import ConversationManager
from sysdialogue.agent.state_store import SessionStore
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


def test_cli_help_no_longer_exposes_dev_mode() -> None:
    result = CliRunner().invoke(main, ["--help"])
    removed_option = "-" + "-dev"

    assert result.exit_code == 0
    assert removed_option not in result.output
    assert "competition" not in result.output.lower()
    assert "竞赛" not in result.output


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
