"""Release acceptance evidence bundle generation."""

from __future__ import annotations

import base64
import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from sysdialogue.agent.acceptance_checklist import render_acceptance_checklist
from sysdialogue.agent.release_readiness import (
    ReleaseReadiness,
    analyze_release_readiness,
    analyze_release_readiness_text,
    evaluate_release_gate,
    release_gate_to_dict,
    release_readiness_to_dict,
    render_release_readiness,
)
from sysdialogue.security.output_sanitizer import sanitize_text, sanitize_value


@dataclass(frozen=True)
class AcceptanceBundle:
    """In-memory release acceptance bundle ready for CLI or Web download."""

    file_name: str
    content: bytes
    readiness: ReleaseReadiness
    report: str
    manifest: tuple[str, ...]


def build_acceptance_bundle_from_text(
    content: str,
    *,
    source: str = "submitted-content",
    target: str = "",
    checklist_text: str = "",
) -> AcceptanceBundle:
    """Build a sanitized evidence ZIP from a submitted acceptance artifact."""

    source = sanitize_text(source or "submitted-content", limit=120)
    readiness = analyze_release_readiness_text(content or "", source=source)
    report = render_release_readiness(readiness)
    manifest = _manifest(readiness, target=target, source=source)
    files = {
        "README.md": _bundle_readme(readiness, manifest),
        "acceptance-artifact.md": sanitize_text(content or "", limit=200_000),
        "acceptance-guide.md": sanitize_text(checklist_text or render_acceptance_checklist(), limit=200_000),
        "release-gate.json": _json(release_gate_to_dict(evaluate_release_gate(readiness))),
        "release-readiness.md": report,
        "release-readiness.json": _json(release_readiness_to_dict(readiness)),
        "acceptance-checks.jsonl": _checks_jsonl(readiness),
        "manifest.txt": "\n".join(manifest) + "\n",
    }
    return AcceptanceBundle(
        file_name=_bundle_name(readiness.source),
        content=_zip_bytes(files),
        readiness=readiness,
        report=report,
        manifest=manifest,
    )


def build_acceptance_bundle_from_path(path: str | Path, *, target: str = "") -> AcceptanceBundle:
    """Build a sanitized evidence ZIP from a completed artifact file or directory."""

    root = Path(path)
    readiness = analyze_release_readiness(root)
    report = render_release_readiness(readiness)
    manifest = _manifest(readiness, target=target, source=str(root))
    files: dict[str, str] = {
        "README.md": _bundle_readme(readiness, manifest),
        "acceptance-guide.md": render_acceptance_checklist(),
        "release-gate.json": _json(release_gate_to_dict(evaluate_release_gate(readiness))),
        "release-readiness.md": report,
        "release-readiness.json": _json(release_readiness_to_dict(readiness)),
        "acceptance-checks.jsonl": _checks_jsonl(readiness),
        "manifest.txt": "\n".join(manifest) + "\n",
    }
    files.update(_sanitized_source_files(root))
    return AcceptanceBundle(
        file_name=_bundle_name(root.name or "acceptance"),
        content=_zip_bytes(files),
        readiness=readiness,
        report=report,
        manifest=manifest,
    )


def write_acceptance_bundle(
    path: str | Path,
    *,
    export_dir: str | Path | None = None,
    target: str = "",
) -> Path:
    """Build and write a sanitized acceptance evidence ZIP."""

    bundle = build_acceptance_bundle_from_path(path, target=target)
    output_dir = Path(export_dir) if export_dir else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / bundle.file_name
    output_path.write_bytes(bundle.content)
    return output_path


def acceptance_bundle_to_web_payload(bundle: AcceptanceBundle) -> dict[str, Any]:
    """Serialize a bundle for the Web API without exposing raw bytes as text."""

    return {
        "fileName": bundle.file_name,
        "content": base64.b64encode(bundle.content).decode("ascii"),
        "encoding": "base64",
        "report": bundle.report,
        "readiness": release_readiness_to_dict(bundle.readiness),
        "manifest": list(bundle.manifest),
    }


def _manifest(readiness: ReleaseReadiness, *, target: str, source: str) -> tuple[str, ...]:
    payload = release_readiness_to_dict(readiness)
    counts = payload["counts"]
    gate = payload["releaseGate"]
    lines = [
        "SysDialogue release acceptance bundle",
        f"Source: {sanitize_text(source or readiness.source, limit=240)}",
        f"Target: {sanitize_text(target or 'not recorded', limit=240)}",
        f"Overall: {readiness.overall}",
        f"Release gate: {'pass' if gate['passed'] else 'blocked'}",
        f"Release gate exit code: {gate['exitCode']}",
        (
            "Counts: "
            f"pass={counts['pass']}, partial={counts['partial']}, "
            f"fail={counts['fail']}, missing={counts['missing']}, unknown={counts['unknown']}"
        ),
        f"Artifacts detected: {len(readiness.artifacts)}",
    ]
    if readiness.notes:
        lines.append("Notes:")
        lines.extend(f"- {note}" for note in readiness.notes)
    if gate["blockingReasons"]:
        lines.append("Blocking reasons:")
        lines.extend(f"- {reason}" for reason in gate["blockingReasons"][:10])
        if len(gate["blockingReasons"]) > 10:
            lines.append(f"- ... {len(gate['blockingReasons']) - 10} additional blocking reason(s) in release-gate.json")
    if gate.get("nextActions"):
        lines.append("Next actions:")
        lines.extend(f"- {action}" for action in gate["nextActions"][:8])
        if len(gate["nextActions"]) > 8:
            lines.append(f"- ... {len(gate['nextActions']) - 8} additional next action(s) in release-gate.json")
    return tuple(lines)


def _bundle_readme(readiness: ReleaseReadiness, manifest: tuple[str, ...]) -> str:
    return "\n".join(
        [
            "# SysDialogue Release Acceptance Bundle",
            "",
            "This ZIP contains sanitized release acceptance evidence.",
            "Attach it to release notes only after reviewing `release-readiness.md` and the source artifacts.",
            "To independently re-check the package, run `sysdialogue --release-gate <this-zip>`; the gate is recomputed from structured evidence in the bundle.",
            "",
            "## Summary",
            "",
            *[f"- {line}" for line in manifest[1:]],
            "",
            "## Files",
            "",
            "- `acceptance-guide.md`: clean A01-A10 runbook template.",
            "- `release-gate.json`: machine-readable pass/blocked status, expected exit code, blocking reasons, and next actions.",
            "- `release-readiness.md`: human-readable pass/fail/partial summary.",
            "- `release-readiness.json`: structured readiness result.",
            "- `acceptance-checks.jsonl`: one sanitized check record per line.",
            "- `source-artifacts/`: sanitized text copies or manifests for source artifacts.",
            "",
            f"Overall readiness: `{readiness.overall}`",
            f"Release gate: `{'pass' if evaluate_release_gate(readiness).passed else 'blocked'}`",
        ]
    )


def _checks_jsonl(readiness: ReleaseReadiness) -> str:
    lines = []
    for check in release_readiness_to_dict(readiness)["checks"]:
        lines.append(json.dumps(sanitize_value(check), ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + ("\n" if lines else "")


def _json(value: Any) -> str:
    return json.dumps(sanitize_value(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, sanitize_text(content, limit=250_000))
    return buffer.getvalue()


def _sanitized_source_files(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    files = [root] if root.is_file() else sorted(item for item in root.rglob("*") if item.is_file())
    rendered: dict[str, str] = {}
    for file_path in files[:80]:
        rel = _display_path(file_path, root)
        archive_name = "source-artifacts/" + _safe_archive_name(rel)
        suffix = file_path.suffix.lower()
        try:
            if suffix in {".md", ".txt", ".json", ".jsonl", ".log"}:
                rendered[archive_name] = sanitize_text(file_path.read_text(encoding="utf-8", errors="replace"), limit=200_000)
            elif suffix == ".zip":
                rendered[archive_name + ".manifest.txt"] = _zip_manifest(file_path)
            else:
                rendered[archive_name + ".manifest.txt"] = f"Omitted non-text artifact: {sanitize_text(rel, limit=240)}\n"
        except (OSError, zipfile.BadZipFile) as exc:
            rendered[archive_name + ".error.txt"] = f"Could not read sanitized source artifact: {type(exc).__name__}\n"
    if len(files) > 80:
        rendered["source-artifacts/_truncated.txt"] = f"Omitted {len(files) - 80} additional files from sanitized bundle.\n"
    return rendered


def _zip_manifest(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    lines = ["ZIP artifact manifest:"]
    lines.extend(f"- {sanitize_text(name, limit=240)}" for name in names[:200])
    if len(names) > 200:
        lines.append(f"- ... {len(names) - 200} additional entries omitted")
    return "\n".join(lines) + "\n"


def _display_path(path: Path, root: Path) -> str:
    try:
        if root.is_dir():
            return str(path.relative_to(root))
    except ValueError:
        pass
    return path.name


def _safe_archive_name(value: str) -> str:
    clean = value.replace("\\", "/").strip("/ ") or "artifact"
    clean = re.sub(r"[^A-Za-z0-9._/-]+", "_", clean)
    clean = "/".join(part for part in clean.split("/") if part not in {"", ".", ".."})
    return clean[:180] or "artifact"


def _bundle_name(source: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.strip() or "acceptance").strip(".-")
    return f"sysdialogue-acceptance-bundle-{(stem or 'artifact')[:80]}.zip"
