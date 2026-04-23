from __future__ import annotations

import pytest

from sysdialogue.app.cli import _require_api_config
from sysdialogue.app.config import AppConfig, load_config


def test_require_api_config_exits_with_clear_message(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _require_api_config(AppConfig(api_key="", model=""), "Web 控制台")

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "OPENAI_API_KEY" in captured.err
    assert "OPENAI_MODEL" in captured.err
    assert "Web 控制台" in captured.err


def test_load_config_prefers_cli_model_over_openai_and_legacy_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-env-model")
    monkeypatch.setenv("SYSDIALOGUE_MODEL", "legacy-model")

    config = load_config(model="cli-model")

    assert config.api_key == "sk-test"
    assert config.base_url == "https://api.example.test/v1"
    assert config.model == "cli-model"


def test_load_config_falls_back_to_openai_model_then_legacy(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "openai-env-model")
    monkeypatch.setenv("SYSDIALOGUE_MODEL", "legacy-model")
    assert load_config().model == "openai-env-model"

    monkeypatch.delenv("OPENAI_MODEL")
    assert load_config().model == "legacy-model"
