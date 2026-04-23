"""Self-check (--verify) and demo (--demo) entrypoints."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sysdialogue.app.runtime_factory import create_runtime

if TYPE_CHECKING:
    from sysdialogue.app.config import AppConfig


def _safe_print(text: str = "") -> None:
    """Write console output without crashing on GBK/legacy terminals."""
    stream = sys.stdout
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        data = (text + "\n").encode(encoding, errors="backslashreplace")
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(data)
            buffer.flush()
        else:
            stream.write(data.decode(encoding, errors="ignore"))


def run_verify(config: "AppConfig") -> int:
    """Run a no-API readiness check for the local or remote runtime."""
    _safe_print("=" * 60)
    _safe_print(" SysDialogue v6 - Self-check (--verify)")
    _safe_print("=" * 60)

    issues: list[str] = []

    # 1. EnvProfile probing
    try:
        from sysdialogue.runtime.capability_probe import (
            CapabilityProbe,
            EnvProfileSanitizer,
        )
        from sysdialogue.runtime.secure_runner import LocalExecutor

        probe = CapabilityProbe(
            LocalExecutor(),
            remote_mode=config.remote_mode,
            ssh_port=config.ssh_port,
        )
        profile = probe.probe()
        sanitized = EnvProfileSanitizer.sanitize(profile)
        _safe_print("\n[1/5] Sanitized environment profile:")
        for key, value in sanitized.items():
            _safe_print(f"  {key}: {value}")
    except Exception as exc:
        issues.append(f"EnvProfile probe failed: {exc}")
        _safe_print(f"  [ERROR] {exc}")

    # 2. Tool registry
    try:
        from sysdialogue.tools.meta_tools import META_TOOL_SCHEMAS
        from sysdialogue.tools.registry import default_registry

        reg = default_registry()
        _safe_print(
            f"\n[2/5] Registered tools: {len(reg.all_schemas())} static"
            f" + {len(META_TOOL_SCHEMAS)} meta"
        )
        for name, desc in reg.describe()[:5]:
            head = desc.split("。")[0] if desc else ""
            _safe_print(f"  - {name}: {head}")
        _safe_print(f"  ... total {len(reg.names())}")
    except Exception as exc:
        issues.append(f"ToolRegistry load failed: {exc}")

    # 3. Built-in workflows
    try:
        workflows_dir = (
            Path(config.workflows_dir)
            if config.workflows_dir
            else Path(__file__).parent.parent / "workflows"
        )
        yamls = sorted(workflows_dir.glob("*.yaml"))
        _safe_print(f"\n[3/5] Built-in workflows: {len(yamls)}")
        for workflow in yamls:
            _safe_print(f"  - {workflow.stem}")
        if len(yamls) != 10:
            issues.append(f"Workflow count mismatch: expected 10, got {len(yamls)}")
    except Exception as exc:
        issues.append(f"Workflow directory scan failed: {exc}")

    # 4. Security rules
    try:
        from sysdialogue.security import risk_classifier as rc

        _safe_print("\n[4/5] Security rules:")
        _safe_print(f"  - RiskClassifier coverage: {len(rc._CLASSIFIERS)} tools")
        _safe_print("  - CommandSafetyChecker: CS001-CS009")
        _safe_print("  - RemoteLockoutChecker: B010 / B015-B017 / WH023")
    except Exception as exc:
        issues.append(f"Security rule modules failed to load: {exc}")

    # 5. Runtime config
    _safe_print("\n[5/5] Config:")
    _safe_print(f"  - model: {config.model}")
    _safe_print(f"  - base_url: {config.base_url or '(OpenAI SDK default)'}")
    _safe_print(f"  - competition_mode: {config.competition_mode}")
    _safe_print(f"  - deployment_mode: {'remote' if config.remote_mode else 'local'}")
    if config.api_key:
        _safe_print(f"  - OPENAI_API_KEY: configured ({config.api_key[:8]}...)")
    else:
        _safe_print("  - OPENAI_API_KEY: missing (required for TUI/simple/web)")
        issues.append("OPENAI_API_KEY is not configured")
    if not config.model:
        _safe_print("  - OPENAI_MODEL / --model: missing (required for TUI/simple/web)")
        issues.append("OPENAI_MODEL or --model is not configured")

    _safe_print("\n" + "=" * 60)
    if issues:
        _safe_print(f"[WARN] Self-check found {len(issues)} issue(s):")
        for index, message in enumerate(issues, 1):
            _safe_print(f"  {index}. {message}")
        _safe_print("=" * 60)
        return 1

    _safe_print("[OK] Self-check passed.")
    _safe_print("=" * 60)
    return 0


def run_demo(config: "AppConfig") -> int:
    """Run the built-in security_audit workflow without calling the LLM API."""
    _safe_print("=" * 60)
    _safe_print(" SysDialogue v6 - Demo mode (--demo)")
    _safe_print(" Scenario: security_audit workflow (read-only inspection)")
    _safe_print("=" * 60)

    runtime = create_runtime(
        config,
        session_id="demo",
        require_api=False,
        confirm_callback=lambda req: True,
    )
    try:
        profile = runtime.env_profile
        if not config.remote_mode and not sys.platform.startswith("linux"):
            _safe_print(
                "\n[UNSUPPORTED] Local demo requires a Linux host. "
                "Use --remote against a Linux machine or run the demo on Linux."
            )
            runtime.audit_log.log_final(
                final_status="unsupported_host",
                detail="local demo requires Linux host",
            )
            return 2

        if not config.remote_mode and profile.get("distro_id") == "unknown":
            _safe_print(
                "\n[UNSUPPORTED] The local environment does not look like a supported Linux runtime."
            )
            runtime.audit_log.log_final(
                final_status="unsupported_host",
                detail="unable to identify a supported Linux distribution",
            )
            return 2

        from sysdialogue.agent.workflow_engine import WorkflowEngine

        workflows_dir = (
            Path(config.workflows_dir)
            if config.workflows_dir
            else Path(__file__).parent.parent / "workflows"
        )
        engine = WorkflowEngine(
            controller=runtime.controller,
            workflows_dir=workflows_dir,
        )

        _safe_print("\n[RUN] security_audit.yaml ...")
        execution = engine.run("security_audit", {})

        _safe_print(f"\n[RESULT] final_status = {execution.final_status}")
        _safe_print(f"[RESULT] message = {execution.final_message}")
        _safe_print("\n[STEP STATUS]")
        for step_id, result in execution.steps_state.items():
            suffix = f" - {result.error}" if result.error else ""
            _safe_print(f"  {step_id}: {result.status}{suffix}")

        _safe_print(f"\n[AUDIT] session_id: {runtime.audit_log.session_id}")
        _safe_print(f"[AUDIT] log_path: {runtime.audit_log.path}")
        _safe_print("\n" + "=" * 60)

        if execution.final_status in ("completed", "rolled_back"):
            return 0

        _safe_print(
            "[ERROR] Demo workflow reached a failure state. "
            "This indicates an engine/runtime problem rather than an unsupported host."
        )
        return 1
    finally:
        runtime.close()
