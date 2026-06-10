"""Release readiness report built from completed acceptance artifacts."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sysdialogue.agent.acceptance_checklist import _steps
from sysdialogue.security.output_sanitizer import sanitize_text


STATUS_ORDER = {"pass": 0, "partial": 1, "fail": 2, "missing": 3, "unknown": 4}
STATUS_ALIASES = {
    "": "missing",
    "x": "pass",
    "done": "pass",
    "ok": "pass",
    "passed": "pass",
    "pass": "pass",
    "true": "pass",
    "yes": "pass",
    "~": "partial",
    "-": "partial",
    "partial": "partial",
    "warn": "partial",
    "warning": "partial",
    "!": "fail",
    "failed": "fail",
    "fail": "fail",
    "false": "fail",
    "no": "fail",
    "blocked": "fail",
    " ": "missing",
    "todo": "missing",
    "missing": "missing",
    "pending": "missing",
    "?": "unknown",
    "unknown": "unknown",
}
ZIP_TEXT_MEMBER_LIMIT = 2_000_000


@dataclass(frozen=True)
class ReadinessCheck:
    step_id: str
    title: str
    gate: str
    status: str = "missing"
    evidence: str = ""
    source: str = ""


@dataclass(frozen=True)
class ArtifactFinding:
    kind: str
    path: str
    detail: str


@dataclass(frozen=True)
class ReleaseReadiness:
    source: str
    overall: str
    checks: tuple[ReadinessCheck, ...]
    artifacts: tuple[ArtifactFinding, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseGate:
    passed: bool
    exit_code: int
    blocking_reasons: tuple[str, ...]
    next_actions: tuple[str, ...] = ()


def render_release_readiness_report(path: str | Path | None = None) -> str:
    readiness = analyze_release_readiness(path)
    return render_release_readiness(readiness)


def render_release_readiness(readiness: ReleaseReadiness) -> str:
    counts = _status_counts(readiness.checks)
    gate = evaluate_release_gate(readiness)
    lines = [
        "Release readiness report:",
        f"Source: {readiness.source}",
        f"Overall: {readiness.overall}",
        f"Release gate: {'pass' if gate.passed else 'blocked'}",
        (
            "Summary: "
            f"pass={counts['pass']}, partial={counts['partial']}, "
            f"fail={counts['fail']}, missing={counts['missing']}, unknown={counts['unknown']}"
        ),
        "",
        "Acceptance checks:",
    ]
    for check in readiness.checks:
        evidence = f" - {check.evidence}" if check.evidence else ""
        source = f" ({check.source})" if check.source else ""
        lines.append(f"- {check.step_id} [{check.status}] {check.title} [{check.gate}]{evidence}{source}")

    lines.append("")
    lines.append("Artifacts:")
    if readiness.artifacts:
        for artifact in readiness.artifacts:
            lines.append(f"- {artifact.kind}: {artifact.detail} ({artifact.path})")
    else:
        lines.append("- none detected")

    lines.append("")
    lines.append("Release-note summary:")
    lines.append(f"- Acceptance result: {readiness.overall}")
    lines.append(
        f"- Checks: {counts['pass']} passed, {counts['partial']} partial, "
        f"{counts['fail']} failed, {counts['missing']} missing, {counts['unknown']} unknown."
    )
    if readiness.artifacts:
        lines.append(f"- Attached artifacts: {len(readiness.artifacts)} sanitized evidence artifact(s) detected.")
    else:
        lines.append("- Attached artifacts: none detected; attach `/export-replay` output before release.")

    if readiness.notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"- {note}" for note in readiness.notes)
    if not gate.passed:
        lines.append("")
        lines.append("Blocking reasons:")
        lines.extend(f"- {reason}" for reason in gate.blocking_reasons)
        if gate.next_actions:
            lines.append("")
            lines.append("Next actions:")
            lines.extend(f"- {action}" for action in gate.next_actions)
    return "\n".join(lines)


def render_release_gate_report(path: str | Path | None = None) -> tuple[str, int]:
    readiness = analyze_release_readiness(path)
    report = render_release_readiness(readiness)
    gate = evaluate_release_gate(readiness)
    return report, gate.exit_code


def analyze_release_readiness_text(text: str, *, source: str = "submitted-content") -> ReleaseReadiness:
    base_checks = {
        step.step_id: ReadinessCheck(step.step_id, step.title, step.gate)
        for step in _steps("user@host:22", "")
    }
    artifacts = _text_artifacts(text, source)
    _merge_checks(base_checks, _parse_text_checks(text, source))
    return _finalize(source, base_checks, artifacts, [])


def analyze_release_readiness(path: str | Path | None = None) -> ReleaseReadiness:
    base_checks = {
        step.step_id: ReadinessCheck(step.step_id, step.title, step.gate)
        for step in _steps("user@host:22", "")
    }
    artifacts: list[ArtifactFinding] = []
    notes: list[str] = []
    source = "(no artifact path supplied)"

    if path is None:
        notes.append("Run `/acceptance`, mark A01-A10 as pass/fail/partial, then pass the file or directory here.")
        return _finalize(source, base_checks, artifacts, notes)

    root = Path(path)
    source = str(root)
    if not root.exists():
        notes.append("Artifact path does not exist.")
        return _finalize(source, base_checks, artifacts, notes)

    files = [root] if root.is_file() else sorted(item for item in root.rglob("*") if item.is_file())
    if not files:
        notes.append("Artifact directory is empty.")

    for file_path in files:
        rel = _display_path(file_path, root)
        suffix = file_path.suffix.lower()
        try:
            if suffix in {".md", ".txt"}:
                text = file_path.read_text(encoding="utf-8", errors="replace")
                _merge_checks(base_checks, _parse_text_checks(text, rel))
                artifacts.extend(_text_artifacts(text, rel))
            elif suffix == ".json":
                data = json.loads(file_path.read_text(encoding="utf-8"))
                _merge_checks(base_checks, _parse_json_checks(data, rel))
            elif suffix == ".jsonl":
                text = file_path.read_text(encoding="utf-8", errors="replace")
                _merge_checks(base_checks, _parse_jsonl_checks(text, rel))
                if "\"type\": \"final\"" in text or '"final_status"' in text:
                    artifacts.append(ArtifactFinding("audit-jsonl", rel, "audit JSONL with final-status records"))
            elif suffix == ".zip":
                zip_checks, zip_artifacts, zip_notes = _zip_evidence(file_path, rel)
                _merge_checks(base_checks, zip_checks)
                artifacts.extend(zip_artifacts)
                notes.extend(zip_notes)
        except (OSError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            notes.append(f"Could not parse {sanitize_text(rel, limit=200)}: {type(exc).__name__}")

    return _finalize(source, base_checks, artifacts, notes)


def release_readiness_to_dict(readiness: ReleaseReadiness) -> dict[str, Any]:
    counts = _status_counts(readiness.checks)
    gate = evaluate_release_gate(readiness)
    return {
        "source": readiness.source,
        "overall": readiness.overall,
        "counts": counts,
        "checks": [
            {
                "stepId": check.step_id,
                "title": check.title,
                "gate": check.gate,
                "status": check.status,
                "evidence": check.evidence,
                "source": check.source,
            }
            for check in readiness.checks
        ],
        "artifacts": [
            {"kind": artifact.kind, "path": artifact.path, "detail": artifact.detail}
            for artifact in readiness.artifacts
        ],
        "notes": list(readiness.notes),
        "releaseGate": release_gate_to_dict(gate),
    }


def evaluate_release_gate(readiness: ReleaseReadiness) -> ReleaseGate:
    reasons: list[str] = []
    if readiness.overall != "pass":
        reasons.append(f"Overall readiness is {readiness.overall}, not pass.")
    for check in readiness.checks:
        if check.status != "pass":
            detail = f"{check.step_id} is {check.status}: {check.title}"
            if check.evidence:
                detail += f" ({check.evidence})"
            reasons.append(detail)
    reasons.extend(readiness.notes)
    sanitized = tuple(dict.fromkeys(sanitize_text(reason, limit=500) for reason in reasons if reason))
    next_actions = () if not sanitized else _release_next_actions(readiness)
    return ReleaseGate(
        passed=not sanitized,
        exit_code=0 if not sanitized else 1,
        blocking_reasons=sanitized,
        next_actions=next_actions,
    )


def release_gate_to_dict(gate: ReleaseGate) -> dict[str, Any]:
    return {
        "passed": gate.passed,
        "exitCode": gate.exit_code,
        "blockingReasons": list(gate.blocking_reasons),
        "nextActions": list(gate.next_actions),
    }


def _parse_text_checks(text: str, source: str) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    checkbox_re = re.compile(
        r"^\s*[-*]\s*\[(?P<mark>[^\]]*)\]\s*(?P<id>A\d{2})\b(?P<title>.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    status_re = re.compile(
        r"^\s*(?P<id>A\d{2})\s*[:|-]\s*(?P<status>pass|passed|fail|failed|partial|missing|unknown|blocked)\b(?P<rest>.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in checkbox_re.finditer(text):
        step_id = match.group("id").upper()
        status = _normalize_status(match.group("mark"))
        evidence = _nearby_evidence(text, match.end())
        checks.append(ReadinessCheck(step_id, "", "", status, evidence, source))
    for match in status_re.finditer(text):
        step_id = match.group("id").upper()
        status = _normalize_status(match.group("status"))
        evidence = _trim(match.group("rest").lstrip(" -:"))
        checks.append(ReadinessCheck(step_id, "", "", status, evidence, source))
    return checks


def _parse_json_checks(data: Any, source: str) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    if isinstance(data, dict) and isinstance(data.get("checks"), list):
        candidates = data["checks"]
    elif isinstance(data, dict):
        candidates = [
            {"id": key, **value} if isinstance(value, dict) else {"id": key, "status": value}
            for key, value in data.items()
            if re.fullmatch(r"A\d{2}", str(key), flags=re.IGNORECASE)
        ]
    elif isinstance(data, list):
        candidates = data
    else:
        candidates = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("id") or item.get("step_id") or item.get("stepId") or "").upper()
        if not re.fullmatch(r"A\d{2}", step_id):
            continue
        status = _normalize_status(str(item.get("status") or "unknown"))
        evidence = _trim(str(item.get("evidence") or item.get("note") or item.get("notes") or ""))
        checks.append(ReadinessCheck(step_id, "", "", status, evidence, source))
    return checks


def _parse_json_artifacts(data: Any, source: str) -> list[ArtifactFinding]:
    if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
        return []
    findings: list[ArtifactFinding] = []
    for item in data["artifacts"]:
        if not isinstance(item, dict):
            continue
        kind = _normalize_artifact_kind(item.get("kind"))
        if not kind:
            continue
        path = _trim(str(item.get("path") or ""), limit=240)
        detail = _trim(str(item.get("detail") or "embedded readiness artifact"), limit=240)
        embedded_path = f"{source}!{path}" if path else source
        findings.append(ArtifactFinding(kind, embedded_path, detail))
    return findings


def _parse_jsonl_checks(text: str, source: str) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            checks.extend(_parse_json_checks(json.loads(line), source))
        except json.JSONDecodeError:
            continue
    return checks


def _text_artifacts(text: str, source: str) -> list[ArtifactFinding]:
    findings: list[ArtifactFinding] = []
    if "Completion evidence matrix:" in text:
        findings.append(ArtifactFinding("evidence-matrix", source, "completion evidence matrix output"))
    if "Operator acceptance checklist:" in text:
        findings.append(ArtifactFinding("acceptance-checklist", source, "operator acceptance checklist output"))
    target_match = re.search(r"^\s*Target:\s*(?P<target>.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    if target_match:
        target = _trim(target_match.group("target"), limit=180)
        kind = "target-placeholder" if _is_placeholder_target(target) else "acceptance-target"
        findings.append(ArtifactFinding(kind, source, target))
    if "SUMMARY.md" in text or "Replay package" in text:
        findings.append(ArtifactFinding("replay-reference", source, "replay evidence referenced"))
    return findings


def _zip_evidence(path: Path, source: str) -> tuple[list[ReadinessCheck], list[ArtifactFinding], list[str]]:
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = {info.filename for info in infos}
        checks: list[ReadinessCheck] = []
        findings = _zip_artifacts_from_names(names, source)
        notes: list[str] = []
        for info in infos:
            name = info.filename.replace("\\", "/")
            lower_name = name.lower()
            member_source = _zip_member_source(source, name)
            try:
                if lower_name.endswith("release-readiness.json"):
                    data = json.loads(_read_zip_text(archive, info))
                    checks.extend(_parse_json_checks(data, member_source))
                    findings.extend(_parse_json_artifacts(data, member_source))
                elif lower_name.endswith("acceptance-checks.jsonl"):
                    checks.extend(_parse_jsonl_checks(_read_zip_text(archive, info), member_source))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                notes.append(f"Could not parse {sanitize_text(member_source, limit=200)}: {type(exc).__name__}")
    return checks, findings, notes


def _zip_artifacts(path: Path, source: str) -> list[ArtifactFinding]:
    _, findings, _ = _zip_evidence(path, source)
    return findings


def _zip_artifacts_from_names(names: set[str], source: str) -> list[ArtifactFinding]:
    normalized_names = {name.replace("\\", "/") for name in names}
    lower_names = {name.lower() for name in normalized_names}
    findings: list[ArtifactFinding] = []
    if "summary.md" in lower_names or any(name.endswith("/summary.md") for name in lower_names):
        findings.append(ArtifactFinding("replay-summary", source, "replay ZIP contains SUMMARY.md"))
    if any(_is_audit_jsonl_name(name) for name in normalized_names):
        findings.append(ArtifactFinding("audit-jsonl", source, "replay ZIP contains JSONL audit data"))
    if any(name.endswith("release-readiness.json") for name in lower_names) and any(
        name.endswith("acceptance-checks.jsonl") for name in lower_names
    ):
        findings.append(ArtifactFinding("acceptance-bundle", source, "acceptance evidence bundle ZIP"))
    if any(item.kind in {"replay-summary", "audit-jsonl"} for item in findings):
        findings.insert(0, ArtifactFinding("replay-zip", source, "sanitized replay ZIP"))
    elif not findings:
        findings.append(ArtifactFinding("zip-manifest", source, "ZIP artifact present but no replay or acceptance-bundle markers detected"))
    return findings


def _read_zip_text(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    if info.file_size > ZIP_TEXT_MEMBER_LIMIT:
        raise ValueError("ZIP member is too large to parse as readiness text")
    return archive.read(info).decode("utf-8", errors="replace")


def _zip_member_source(source: str, member_name: str) -> str:
    source = sanitize_text(source, limit=160)
    member_name = sanitize_text(member_name, limit=160)
    return f"{source}!{member_name}"


def _merge_checks(current: dict[str, ReadinessCheck], parsed: list[ReadinessCheck]) -> None:
    for item in parsed:
        existing = current.get(item.step_id)
        if existing is None:
            continue
        status = item.status if _status_rank(item.status) <= _status_rank(existing.status) else existing.status
        evidence = item.evidence or existing.evidence
        source = item.source or existing.source
        current[item.step_id] = ReadinessCheck(
            existing.step_id,
            existing.title,
            existing.gate,
            status,
            evidence,
            source,
        )


def _finalize(
    source: str,
    checks_by_id: dict[str, ReadinessCheck],
    artifacts: list[ArtifactFinding],
    notes: list[str],
) -> ReleaseReadiness:
    checks = tuple(checks_by_id[key] for key in sorted(checks_by_id))
    counts = _status_counts(checks)
    has_replay_artifact = any(item.kind in {"replay-zip", "replay-summary", "audit-jsonl"} for item in artifacts)
    proof_gaps = _release_proof_gaps(checks, artifacts)
    if counts["fail"]:
        overall = "fail"
    elif counts["missing"] or counts["unknown"] or counts["partial"]:
        overall = "partial"
    else:
        overall = "pass"
    if counts["missing"]:
        notes.append("Missing acceptance results remain; run `/acceptance` and mark every A01-A10 line.")
    if not has_replay_artifact:
        if overall == "pass":
            overall = "partial"
        notes.append("No replay/audit artifact detected; attach sanitized `/export-replay` output before release.")
    if proof_gaps:
        if overall == "pass":
            overall = "partial"
        notes.extend(proof_gaps)
    deduped_artifacts = tuple(dict.fromkeys(artifacts))
    deduped_notes = tuple(dict.fromkeys(sanitize_text(note, limit=400) for note in notes if note))
    return ReleaseReadiness(source, overall, checks, deduped_artifacts, deduped_notes)


def _has_a04_command_surface_evidence(evidence: str) -> bool:
    text = evidence.lower()
    has_surface_review = "command-surface" in text or "command surface" in text or "ui-review" in text
    has_slash_surface = "/help" in text or "slash" in text
    has_tui_surface = "tui" in text
    has_web_surface = "web_release_controls" in text or "web release" in text or ("web" in text and "control" in text)
    return has_surface_review and has_slash_surface and has_tui_surface and has_web_surface


def _has_a05_no_command_trace_evidence(evidence: str) -> bool:
    text = evidence.lower()
    compact = re.sub(r"\s+", "", text)
    normalized = re.sub(r"[\s_-]+", "", text)
    no_command_trace_count = bool(re.search(r"command_trace(?:_count)?[^0-9a-z]{0,8}0\b", text))
    return "command_trace" in text and (no_command_trace_count or "nocommand" in normalized)


def _release_proof_gaps(
    checks: tuple[ReadinessCheck, ...],
    artifacts: list[ArtifactFinding],
) -> list[str]:
    by_id = {check.step_id: check for check in checks}
    gaps: list[str] = []
    target_artifacts = [item for item in artifacts if item.kind in {"acceptance-target", "target-placeholder"}]
    if not any(item.kind == "acceptance-target" for item in target_artifacts):
        if any(item.kind == "target-placeholder" for item in target_artifacts):
            gaps.append("Acceptance target is still a placeholder; run acceptance against a concrete staging or disposable host.")
        elif all(check.status == "pass" for check in checks):
            gaps.append("No concrete acceptance target detected; include the real staging/disposable host target in the artifact.")

    a02 = by_id.get("A02")
    if a02 and a02.status == "pass" and "tool call" not in a02.evidence.lower():
        gaps.append("A02 is marked pass but does not include model tool-call diagnostic evidence.")

    a04 = by_id.get("A04")
    if a04 and a04.status == "pass" and not _has_a04_command_surface_evidence(a04.evidence):
        gaps.append("A04 is marked pass but does not include slash, TUI, and Web command-surface evidence.")

    a05 = by_id.get("A05")
    if a05 and a05.status == "pass" and not _has_a05_no_command_trace_evidence(a05.evidence):
        gaps.append("A05 is marked pass but does not include no-command-trace conversation evidence.")

    a07 = by_id.get("A07")
    if a07 and a07.status == "pass":
        evidence = a07.evidence.lower()
        if not ("operator-approved" in evidence and "mutation drill" in evidence and "verification" in evidence):
            gaps.append("A07 is marked pass but does not include operator-approved mutation drill and post-change verification evidence.")

    a08 = by_id.get("A08")
    if a08 and a08.status == "pass":
        evidence = a08.evidence.lower()
        if "/next" not in evidence or not ("/resume" in evidence or "/abandon" in evidence):
            gaps.append("A08 is marked pass but does not show /next plus /resume or /abandon recovery evidence.")

    return gaps


def _release_next_actions(readiness: ReleaseReadiness) -> tuple[str, ...]:
    checks = readiness.checks
    artifacts = list(readiness.artifacts)
    by_id = {check.step_id: check for check in checks}
    actions: list[str] = []

    missing_or_weak = [check.step_id for check in checks if check.status != "pass"]
    if missing_or_weak:
        actions.append(
            "Complete "
            + _compact_step_list(missing_or_weak)
            + " in the A01-A10 artifact with concrete pass/fail/partial evidence."
        )

    target_artifacts = [item for item in artifacts if item.kind in {"acceptance-target", "target-placeholder"}]
    if not any(item.kind == "acceptance-target" for item in target_artifacts):
        if any(item.kind == "target-placeholder" for item in target_artifacts):
            actions.append("Re-run the checklist or runner with `--remote user@host:22` against the staging or disposable target.")
        elif all(check.status == "pass" for check in checks):
            actions.append("Record the concrete staging or disposable target in the acceptance artifact before claiming release readiness.")

    a02 = by_id.get("A02")
    if a02 and (a02.status != "pass" or "tool call" not in a02.evidence.lower()):
        actions.append(
            "Run `sysdialogue --acceptance-runner --acceptance-runner-mode model-check` "
            "or `sysdialogue --check-model` / `/check-model`, then attach the model tool-call diagnostic evidence to A02."
        )

    a04 = by_id.get("A04")
    if a04 and (a04.status != "pass" or not _has_a04_command_surface_evidence(a04.evidence)):
        actions.append(
            "Run `sysdialogue --acceptance-runner --acceptance-runner-mode ui-review` "
            "to collect slash, TUI welcome, and Web Release command-surface evidence for A04."
        )

    a07 = by_id.get("A07")
    if a07:
        a07_evidence = a07.evidence.lower()
        if a07.status != "pass" or not ("operator-approved" in a07_evidence and "mutation drill" in a07_evidence and "verification" in a07_evidence):
            actions.append(
                "Run `sysdialogue --acceptance-runner --acceptance-runner-mode operator-approved-drill` "
                "with the fixed approval phrase, disposable-target assertion, rollback, and verification text."
            )

    a05 = by_id.get("A05")
    if a05 and (a05.status != "pass" or not _has_a05_no_command_trace_evidence(a05.evidence)):
        actions.append(
            "Run `sysdialogue --acceptance-runner --acceptance-runner-mode conversation-check` "
            "to verify ordinary conversation does not produce OS command traces."
        )

    a08 = by_id.get("A08")
    if a08:
        a08_evidence = a08.evidence.lower()
        if a08.status != "pass" or "/next" not in a08_evidence or not ("/resume" in a08_evidence or "/abandon" in a08_evidence):
            actions.append(
                "Run `sysdialogue --acceptance-runner --acceptance-runner-mode recovery-drill` "
                "or a manual recovery drill that records `/next` plus either `/resume` or `/abandon` evidence for A08."
            )

    has_replay_artifact = any(item.kind in {"replay-zip", "replay-summary", "audit-jsonl"} for item in artifacts)
    if not has_replay_artifact:
        actions.append(
            "Export sanitized replay evidence with `/export-replay` or "
            "`sysdialogue --acceptance-runner --acceptance-runner-mode replay-export --acceptance-replay-session <session_id>`, "
            "then place the replay ZIP or audit JSONL beside the acceptance artifact."
        )

    if any(check.status == "fail" for check in checks):
        actions.append("Resolve failed checks first, then rebuild the readiness report from the corrected evidence.")

    actions.append("Re-run `sysdialogue --release-gate <artifact-dir-or-bundle.zip>` after updating the evidence.")
    return tuple(dict.fromkeys(sanitize_text(action, limit=500) for action in actions if action))


def _status_counts(checks: tuple[ReadinessCheck, ...]) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_ORDER}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def _compact_step_list(step_ids: list[str]) -> str:
    if len(step_ids) <= 5:
        return ", ".join(step_ids)
    return ", ".join(step_ids[:5]) + f", and {len(step_ids) - 5} more check(s)"


def _normalize_status(raw: str) -> str:
    key = str(raw or "").strip().lower()
    return STATUS_ALIASES.get(key, "unknown")


def _normalize_artifact_kind(raw: Any) -> str:
    kind = str(raw or "").strip().lower()
    kind = re.sub(r"[^a-z0-9_-]+", "-", kind).strip("-")
    return kind[:80]


def _status_rank(status: str) -> int:
    return STATUS_ORDER.get(status, STATUS_ORDER["unknown"])


def _nearby_evidence(text: str, offset: int) -> str:
    tail = text[offset:].splitlines()[:4]
    evidence_lines = []
    for line in tail:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"[-*]\s*\[[^\]]*\]\s*A\d{2}\b", stripped, flags=re.IGNORECASE):
            break
        if stripped.lower().startswith(("evidence:", "expected evidence:", "release-note line:", "note:", "notes:")):
            evidence_lines.append(stripped)
    return _trim(" ".join(evidence_lines))


def _trim(value: str, *, limit: int = 240) -> str:
    return sanitize_text(str(value or "").strip(), limit=limit)


def _display_path(path: Path, root: Path) -> str:
    try:
        if root.is_dir():
            return str(path.relative_to(root))
    except ValueError:
        pass
    return str(path)


def _is_placeholder_target(target: str) -> bool:
    normalized = target.lower()
    return any(
        marker in normalized
        for marker in (
            "placeholder",
            "local-or-placeholder",
            "local controller",
            "user@host",
            "ssh://host",
            "no connected target",
        )
    )


def _is_audit_jsonl_name(name: str) -> bool:
    normalized = name.replace("\\", "/").lower().rsplit("/", 1)[-1]
    return normalized in {"session.jsonl", "audit.jsonl"} or normalized.startswith("audit_") and normalized.endswith(".jsonl")
