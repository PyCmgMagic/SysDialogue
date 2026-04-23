from __future__ import annotations

import pytest

from sysdialogue.app.cli import _require_api_key
from sysdialogue.app.config import AppConfig


def test_require_api_key_exits_with_clear_message(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _require_api_key(AppConfig(api_key=""), "Web 控制台")

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY" in captured.err
    assert "Web 控制台" in captured.err
