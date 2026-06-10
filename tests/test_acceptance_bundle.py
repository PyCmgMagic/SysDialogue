from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from click.testing import CliRunner

from sysdialogue.agent.acceptance_bundle import build_acceptance_bundle_from_text
from sysdialogue.app.cli import main


def _acceptance_text() -> str:
    lines = ["Operator acceptance checklist:", "Runbook:"]
    for index in range(1, 11):
        step_id = f"A{index:02d}"
        evidence = "token=sk-secret1234" if step_id == "A03" else f"{step_id} completed"
        lines.append(f"- [x] {step_id} acceptance item")
        lines.append(f"  Evidence: {evidence}")
    lines.append("Replay package: SUMMARY.md and audit.jsonl attached")
    return "\n".join(lines)


def test_acceptance_bundle_contains_sanitized_readiness_and_sources() -> None:
    bundle = build_acceptance_bundle_from_text(
        _acceptance_text(),
        source="release-note Authorization: Bearer secret-token",
        target="ssh://prod.example.test:22",
    )

    with zipfile.ZipFile(BytesIO(bundle.content)) as archive:
        names = set(archive.namelist())
        rendered = "\n".join(archive.read(name).decode("utf-8") for name in sorted(names))

    assert "README.md" in names
    assert "release-gate.json" in names
    assert "release-readiness.md" in names
    assert "release-readiness.json" in names
    assert "acceptance-checks.jsonl" in names
    assert bundle.readiness.overall == "partial"
    assert "sk-secret1234" not in rendered
    assert "secret-token" not in rendered
    assert "<redacted>" in rendered
    assert "Release gate: blocked" in rendered
    readiness = json.loads(zipfile.ZipFile(BytesIO(bundle.content)).read("release-readiness.json"))
    gate = json.loads(zipfile.ZipFile(BytesIO(bundle.content)).read("release-gate.json"))
    assert readiness["counts"]["pass"] == 10
    assert gate["passed"] is False
    assert gate["exitCode"] == 1
    assert gate["blockingReasons"]
    assert gate["nextActions"]
    assert "Next actions:" in rendered
    assert any("--release-gate" in action for action in gate["nextActions"])


def test_cli_acceptance_bundle_writes_zip_without_api_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "acceptance.md"
    export_dir = tmp_path / "bundle"
    source.write_text(_acceptance_text(), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["--acceptance-bundle", str(source), "--export-dir", str(export_dir)],
        env={},
    )

    assert result.exit_code == 0
    assert "Acceptance evidence bundle written:" in result.output
    bundle_path = next(export_dir.glob("sysdialogue-acceptance-bundle-*.zip"))
    with zipfile.ZipFile(bundle_path) as archive:
        assert "release-readiness.md" in archive.namelist()
        assert "release-gate.json" in archive.namelist()
        rendered = archive.read("acceptance-checks.jsonl").decode("utf-8")
        gate = json.loads(archive.read("release-gate.json"))
    assert "sk-secret1234" not in rendered
    assert "<redacted>" in rendered
    assert gate["passed"] is False
    assert gate["nextActions"]
    assert "OPENAI_API_KEY" not in result.output
