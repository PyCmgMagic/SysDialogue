"""Completion evidence matrix for the product bar and verification gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvidenceReference:
    """Concrete evidence pointer used by docs, CLI, and self-checks."""

    label: str
    path: str = ""
    command: str = ""


@dataclass(frozen=True)
class EvidenceItem:
    item_id: str
    group: str
    requirement: str
    status: str
    evidence: tuple[str, ...]
    references: tuple[EvidenceReference, ...]


PRODUCT_BAR_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("PB01", "The first screen must make the next action obvious."),
    ("PB02", "Every risky step must explain impact, rollback, and approval state."),
    (
        "PB03",
        "The agent should recover from interruption without losing context or repeating successful side effects.",
    ),
    (
        "PB04",
        'The user should be able to ask "why did you do that?" and get audit evidence, not guesswork.',
    ),
    (
        "PB05",
        "Memory, skills, hooks, and target facts must improve future runs without leaking secrets.",
    ),
    (
        "PB06",
        "Dynamic commands must be available for real work but boxed in by safety, audit, and verification.",
    ),
)


VERIFICATION_GATES: tuple[tuple[str, str], ...] = (
    (
        "VG01",
        "Startup: --verify, --doctor, TUI startup, simple CLI startup, and remote config errors are clear and non-crashing.",
    ),
    (
        "VG02",
        "Conversation: casual chat finishes without OS access; operational tasks observe before completion.",
    ),
    (
        "VG03",
        "Mutation safety: file, service, cron, package, firewall, user, SSH key, sysctl, container, and DynTool mutations require the right confirmation and verification.",
    ),
    (
        "VG04",
        "Recovery: interrupted tasks can be discovered, resumed, inspected with /next, or explicitly abandoned.",
    ),
    (
        "VG05",
        "Audit: decisions, tool calls, failures, approvals, and final outcomes are sanitized and exportable.",
    ),
    (
        "VG06",
        "Usability: /help, /examples, /playbooks, /doctor, /status, /plan, /memory, /target, /skills, /hooks, and /why are understandable in TUI and simple CLI.",
    ),
    (
        "VG07",
        "Security hygiene: persisted memory, target facts, traces, audit exports, hook context, and skill args do not store raw credentials.",
    ),
)


EVIDENCE_MATRIX: tuple[EvidenceItem, ...] = (
    EvidenceItem(
        item_id="PB01",
        group="Product Bar",
        requirement=PRODUCT_BAR_REQUIREMENTS[0][1],
        status="covered",
        evidence=(
            "TUI welcome and input hint expose examples, production playbooks, doctor, model checks, recovery, and abandon commands.",
            "A04 UI-review collection checks slash help, command outputs, TUI welcome controls, and Web Release controls.",
            "CLI startup failures point users at no-API diagnostic modes instead of dead-ending.",
        ),
        references=(
            EvidenceReference(
                "TUI welcome commands",
                path="tests/test_tui_formatting.py::test_tui_welcome_and_bindings_expose_recovery_and_diagnostic_commands",
            ),
            EvidenceReference(
                "A04 UI-review collection",
                path="tests/test_acceptance_runner.py::test_ui_acceptance_collection_checks_operator_surfaces",
            ),
            EvidenceReference(
                "CLI API-config guidance",
                path="tests/test_cli_entrypoints.py::test_default_tui_requires_api_config_with_clear_message",
            ),
            EvidenceReference("Playbook onboarding doc", path="docs/PRODUCTION_PLAYBOOKS.md"),
            EvidenceReference(
                "Operator acceptance checklist",
                path="docs/OPERATOR_ACCEPTANCE_CHECKLIST.md",
            ),
        ),
    ),
    EvidenceItem(
        item_id="PB02",
        group="Product Bar",
        requirement=PRODUCT_BAR_REQUIREMENTS[1][1],
        status="covered",
        evidence=(
            "Golden mutation paths require confirmation before execution and verify state after service/config changes.",
            "Workflow rollback paths prove failed verification produces recovery action instead of silent success.",
            "Operator-approved A07 mutation drills require a fixed approval phrase, impact, rollback, disposable-target assertion, and post-change verification evidence.",
        ),
        references=(
            EvidenceReference(
                "Service restart confirmation and verification",
                path="tests/test_golden_scenarios.py::test_golden_service_restart_requires_approval_then_status_verification",
            ),
            EvidenceReference(
                "Config patch approval, backup, validation",
                path="tests/test_golden_scenarios.py::test_golden_safe_config_patch_workflow_runs_approval_backup_patch_and_validate",
            ),
            EvidenceReference(
                "Rollback after failed verification",
                path="tests/test_golden_scenarios.py::test_golden_workflow_rollback_runs_after_failed_verification",
            ),
            EvidenceReference(
                "A07 mutation drill approval and evidence",
                path="tests/test_acceptance_mutation_drill.py::test_operator_approved_mutation_drill_runs_constrained_workflow",
            ),
            EvidenceReference(
                "A07 mutation drill rejects missing approval phrase",
                path="tests/test_acceptance_mutation_drill.py::test_operator_approved_mutation_drill_rejects_missing_phrase",
            ),
        ),
    ),
    EvidenceItem(
        item_id="PB03",
        group="Product Bar",
        requirement=PRODUCT_BAR_REQUIREMENTS[2][1],
        status="covered",
        evidence=(
            "Interrupted task resume preserves the original command and completes through the same durable task.",
            "/next and /abandon expose explicit recovery and cleanup paths for stale work.",
            "The guided recovery drill fills A08 by exercising /next plus /abandon on synthetic durable task state.",
        ),
        references=(
            EvidenceReference(
                "Resume after interruption",
                path="tests/test_golden_scenarios.py::test_golden_resume_interrupted_task_keeps_resume_turn_and_completes",
            ),
            EvidenceReference(
                "Resume command persistence",
                path="tests/test_react_runner.py::test_resume_command_persists_original_user_command",
            ),
            EvidenceReference(
                "/next and /abandon recovery",
                path="tests/test_agent_upgrade_features.py::test_slash_next_recommends_resume_and_abandon_for_interrupted_task",
            ),
            EvidenceReference(
                "Guided A08 recovery drill",
                path="tests/test_acceptance_runner.py::test_recovery_acceptance_collection_exercises_next_and_abandon",
            ),
        ),
    ),
    EvidenceItem(
        item_id="PB04",
        group="Product Bar",
        requirement=PRODUCT_BAR_REQUIREMENTS[3][1],
        status="covered",
        evidence=(
            "/why explains permission decisions with matching candidates and suggestions.",
            "Replay exports include a human-readable summary so audit evidence is reviewable without raw JSONL spelunking.",
            "Acceptance bundles package readiness, JSON, JSONL, and sanitized source evidence for release review.",
            "The guided acceptance runner creates a parseable A01-A10 artifact with automated safe preflight and explicit manual gates.",
            "The acceptance suite writes a local non-mutating evidence kit with safe preflight, UI-review, recovery drill, README, and readiness output.",
            "Opt-in model-check collection can fill A02 from the synthetic model tool-call diagnostic without dispatching OS-facing tools.",
            "Opt-in ui-review collection can fill A04 from slash, TUI, and Web Release surface checks.",
            "Opt-in conversation-check collection can fill A05 from a plain chat turn that creates no command traces.",
            "Opt-in read-only collection can fill A03 doctor and A06 security_audit evidence without running mutation or model-call gates.",
            "Opt-in recovery-drill collection can fill A08 from /next plus /abandon durable-state behavior without dispatching OS-facing tools.",
            "Opt-in replay-export collection can fill A09 only after writing a real replay ZIP with SUMMARY.md and JSONL audit data.",
            "Release readiness distinguishes strong release proof from weak all-pass checkboxes and non-replay ZIP attachments.",
            "Release gate mode gives CI and release scripts a non-zero exit code until strict readiness passes, while slash and Web surfaces expose the same blocking reasons to operators.",
            "Acceptance bundles include README and manifest gate summaries plus release-gate.json so reviewers and scripts see the same pass/blocked state.",
            "Blocked release gates include next actions so operators know whether to collect model diagnostics, A07 drill evidence, recovery proof, replay exports, or rerun the gate.",
        ),
        references=(
            EvidenceReference(
                "Permission explanation",
                path="tests/test_agent_upgrade_features.py::test_permission_explain_includes_candidates_and_suggestion",
            ),
            EvidenceReference(
                "Replay package summary",
                path="tests/test_agent_upgrade_features.py::test_replay_package_includes_human_readable_summary",
            ),
            EvidenceReference(
                "Sanitized audit and replay export",
                path="tests/test_cli_entrypoints.py::test_cli_exports_sanitized_audit_and_replay",
            ),
            EvidenceReference(
                "Release readiness summary",
                path="tests/test_release_readiness.py::test_release_readiness_report_summarizes_completed_artifacts",
            ),
            EvidenceReference(
                "Web release readiness summary",
                path="tests/test_web_api.py::test_web_release_readiness_route_summarizes_submitted_text_and_redacts",
            ),
            EvidenceReference(
                "Acceptance bundle export",
                path="tests/test_acceptance_bundle.py::test_acceptance_bundle_contains_sanitized_readiness_and_sources",
            ),
            EvidenceReference(
                "Guided acceptance runner artifact",
                path="tests/test_acceptance_runner.py::test_guided_acceptance_artifact_feeds_release_readiness",
            ),
            EvidenceReference(
                "Local acceptance suite",
                path="tests/test_acceptance_runner.py::test_cli_acceptance_suite_writes_local_evidence_kit",
            ),
            EvidenceReference(
                "Guided acceptance model-check collection",
                path="tests/test_acceptance_runner.py::test_guided_acceptance_model_check_marks_a02_collected",
            ),
            EvidenceReference(
                "Guided acceptance UI-review collection",
                path="tests/test_acceptance_runner.py::test_ui_acceptance_collection_checks_operator_surfaces",
            ),
            EvidenceReference(
                "Guided acceptance conversation collection",
                path="tests/test_acceptance_runner.py::test_conversation_acceptance_collection_runs_without_command_trace",
            ),
            EvidenceReference(
                "Guided acceptance read-only collection",
                path="tests/test_acceptance_runner.py::test_guided_acceptance_read_only_collect_marks_collected_gates",
            ),
            EvidenceReference(
                "Guided acceptance recovery collection",
                path="tests/test_acceptance_runner.py::test_recovery_acceptance_collection_exercises_next_and_abandon",
            ),
            EvidenceReference(
                "Guided acceptance replay collection",
                path="tests/test_acceptance_runner.py::test_replay_acceptance_collection_exports_real_replay_zip",
            ),
            EvidenceReference(
                "Weak all-pass release proof stays partial",
                path="tests/test_release_readiness.py::test_release_readiness_keeps_weak_all_pass_artifact_partial",
            ),
            EvidenceReference(
                "Release gate blocks partial evidence",
                path="tests/test_release_readiness.py::test_cli_release_gate_exits_nonzero_until_ready",
            ),
            EvidenceReference(
                "Release gate next actions",
                path="tests/test_release_readiness.py::test_release_readiness_keeps_weak_all_pass_artifact_partial",
            ),
            EvidenceReference(
                "Release gate passes strong evidence",
                path="tests/test_release_readiness.py::test_cli_release_gate_exits_zero_for_strong_pass",
            ),
            EvidenceReference(
                "Release gate slash command",
                path="tests/test_release_readiness.py::test_slash_release_gate_command_and_alias",
            ),
        ),
    ),
    EvidenceItem(
        item_id="PB05",
        group="Product Bar",
        requirement=PRODUCT_BAR_REQUIREMENTS[4][1],
        status="covered",
        evidence=(
            "Memory, skill activation, hook context, and target facts persist useful context while redacting secrets.",
            "Environment profile sanitization retains target identity and remote traits without leaking credentials.",
        ),
        references=(
            EvidenceReference(
                "Memory redaction and summaries",
                path="tests/test_agent_upgrade_features.py::test_memory_manager_redacts_secret_and_renders_summary",
            ),
            EvidenceReference(
                "Skill argument redaction",
                path="tests/test_agent_upgrade_features.py::test_skill_activation_redacts_secret_arguments",
            ),
            EvidenceReference(
                "Hook payload redaction",
                path="tests/test_agent_upgrade_features.py::test_hook_template_redacts_secret_payload_values",
            ),
            EvidenceReference(
                "Target fact redaction",
                path="tests/test_agent_upgrade_features.py::test_target_profile_store_redacts_secret_facts",
            ),
        ),
    ),
    EvidenceItem(
        item_id="PB06",
        group="Product Bar",
        requirement=PRODUCT_BAR_REQUIREMENTS[5][1],
        status="covered",
        evidence=(
            "Dynamic tools can be proposed, reused, and executed for real tasks through the normal approval system.",
            "Shell mode, privilege escalation, password pipes, invalid cwd, and misdeclared mutating commands are constrained.",
        ),
        references=(
            EvidenceReference(
                "Dynamic tool proposal and execution",
                path="tests/test_react_runner.py::test_dynamic_tool_can_be_proposed_then_executed_by_default",
            ),
            EvidenceReference(
                "Standard profile shell rejection",
                path="tests/test_react_runner.py::test_execute_dynamic_tool_standard_profile_rejects_shell_mode",
            ),
            EvidenceReference(
                "Password pipe hard block",
                path="tests/test_react_runner.py::test_execute_dynamic_tool_break_glass_keeps_password_pipe_hard_blocked",
            ),
            EvidenceReference(
                "Misdeclared mutating command stays mutating",
                path="tests/test_react_runner.py::test_dynamic_tool_maven_build_stays_mutating_even_if_misdeclared",
            ),
        ),
    ),
    EvidenceItem(
        item_id="VG01",
        group="Verification Gate",
        requirement=VERIFICATION_GATES[0][1],
        status="covered",
        evidence=(
            "--verify and --doctor run without API configuration.",
            "TUI/simple startup and remote target parsing failures return clear, actionable errors.",
        ),
        references=(
            EvidenceReference(
                "Verify no API",
                path="tests/test_cli_entrypoints.py::test_verify_does_not_require_api_config",
                command="python -m pytest tests/test_cli_entrypoints.py::test_verify_does_not_require_api_config -q",
            ),
            EvidenceReference(
                "Doctor no API",
                path="tests/test_cli_entrypoints.py::test_cli_doctor_runs_without_api_config",
            ),
            EvidenceReference(
                "Simple CLI config guidance",
                path="tests/test_cli_entrypoints.py::test_simple_cli_requires_api_config_with_clear_message",
            ),
            EvidenceReference(
                "Invalid remote target parsing",
                path="tests/test_cli_entrypoints.py::test_remote_option_rejects_invalid_targets",
            ),
        ),
    ),
    EvidenceItem(
        item_id="VG02",
        group="Verification Gate",
        requirement=VERIFICATION_GATES[1][1],
        status="covered",
        evidence=(
            "Non-operational conversation finishes without exposing or executing OS tools.",
            "Guided A05 conversation-check collection verifies a plain chat turn produces no command traces.",
            "Operational tasks cannot finish before an observation, and post-tool evidence completes the task.",
        ),
        references=(
            EvidenceReference(
                "Greeting finishes without system action",
                path="tests/test_react_runner.py::test_greeting_can_finish_without_system_action",
            ),
            EvidenceReference(
                "Hallucinated OS tool is not executed",
                path="tests/test_react_runner.py::test_non_operational_hallucinated_os_tool_is_not_executed",
            ),
            EvidenceReference(
                "Guided A05 conversation collection",
                path="tests/test_acceptance_runner.py::test_conversation_acceptance_collection_runs_without_command_trace",
            ),
            EvidenceReference(
                "Operational task requires observation",
                path="tests/test_react_runner.py::test_operational_task_cannot_complete_without_observation",
            ),
            EvidenceReference(
                "Tool success then finish",
                path="tests/test_react_runner.py::test_tool_success_then_finish_completes_operational_task",
            ),
        ),
    ),
    EvidenceItem(
        item_id="VG03",
        group="Verification Gate",
        requirement=VERIFICATION_GATES[2][1],
        status="covered",
        evidence=(
            "Golden scenario matrix covers static mutation families including service, cron, package, firewall, user, SSH key, sysctl, and container.",
            "DynTool mutation paths require confirmation and static verification before completion.",
            "The release acceptance runner can collect A07 from a constrained operator-approved service_restart or safe_config_patch workflow.",
        ),
        references=(
            EvidenceReference(
                "Static mutation matrix",
                path="tests/test_golden_scenarios.py::test_golden_static_mutation_matrix_requires_approval_and_post_verification",
                command="python -m pytest tests/test_golden_scenarios.py -q",
            ),
            EvidenceReference(
                "DynTool mutation approval and verification",
                path="tests/test_golden_scenarios.py::test_golden_dyntool_mutation_requires_approval_and_static_verification",
            ),
            EvidenceReference(
                "Failed mutation cannot satisfy gate",
                path="tests/test_react_runner.py::test_failed_mutation_does_not_satisfy_completion_gate",
            ),
            EvidenceReference(
                "Post-mutation verification judge",
                path="tests/test_react_runner.py::test_verification_judge_only_requires_post_mutation_tool_evidence",
            ),
            EvidenceReference(
                "A07 constrained mutation drill",
                path="tests/test_acceptance_mutation_drill.py::test_operator_approved_mutation_drill_runs_constrained_workflow",
            ),
        ),
    ),
    EvidenceItem(
        item_id="VG04",
        group="Verification Gate",
        requirement=VERIFICATION_GATES[3][1],
        status="covered",
        evidence=(
            "Interrupted tasks can be resumed through /resume and explained with /next.",
            "Abandon releases stale task locks and user-cancelled approvals are recorded as non-execution.",
        ),
        references=(
            EvidenceReference(
                "Resume golden path",
                path="tests/test_golden_scenarios.py::test_golden_resume_interrupted_task_keeps_resume_turn_and_completes",
            ),
            EvidenceReference(
                "/next blocked advice",
                path="tests/test_agent_upgrade_features.py::test_slash_next_summarizes_blocked_task_advice",
            ),
            EvidenceReference(
                "User-cancelled confirmation",
                path="tests/test_golden_scenarios.py::test_golden_user_cancelled_confirmation_does_not_execute_mutation",
            ),
        ),
    ),
    EvidenceItem(
        item_id="VG05",
        group="Verification Gate",
        requirement=VERIFICATION_GATES[4][1],
        status="covered",
        evidence=(
            "Trace spans, audit exports, replay packages, tool result data, and final summaries are sanitized.",
            "Hard-blocked tools and failed workflows produce durable audit evidence.",
            "Web release-readiness output is generated from submitted artifacts and redacts secrets before returning JSON.",
            "Acceptance evidence bundles sanitize submitted text and structured check records before ZIP export.",
            "Acceptance bundle JSONL records are not misclassified as replay audit logs.",
            "A09 replay-export collection validates the generated ZIP structure before treating replay evidence as collected.",
        ),
        references=(
            EvidenceReference(
                "Trace store redaction",
                path="tests/test_agent_upgrade_features.py::test_trace_store_redacts_secret_values_in_generic_fields",
            ),
            EvidenceReference(
                "Tool result audit and replay sanitization",
                path="tests/test_agent_upgrade_features.py::test_tool_result_audit_and_replay_exports_are_sanitized",
            ),
            EvidenceReference(
                "Hard-blocked tool evidence",
                path="tests/test_golden_scenarios.py::test_golden_hard_blocked_tool_is_not_confirmed_or_executed",
            ),
            EvidenceReference(
                "Release readiness artifact ingestion",
                path="docs/RELEASE_READINESS.md",
            ),
            EvidenceReference(
                "Web readiness sanitization",
                path="tests/test_web_api.py::test_web_release_readiness_route_summarizes_submitted_text_and_redacts",
            ),
            EvidenceReference(
                "Bundle sanitization",
                path="tests/test_acceptance_bundle.py::test_cli_acceptance_bundle_writes_zip_without_api_config",
            ),
            EvidenceReference(
                "Bundle JSONL is not replay evidence",
                path="tests/test_release_readiness.py::test_release_readiness_does_not_treat_acceptance_bundle_jsonl_as_replay",
            ),
            EvidenceReference(
                "Replay-export acceptance collection",
                path="tests/test_acceptance_runner.py::test_replay_acceptance_collection_exports_real_replay_zip",
            ),
        ),
    ),
    EvidenceItem(
        item_id="VG06",
        group="Verification Gate",
        requirement=VERIFICATION_GATES[5][1],
        status="covered",
        evidence=(
            "Slash commands expose status, plan, audit, examples, playbooks, doctor, memory, target, skills, hooks, and why surfaces.",
            "TUI welcome and status formatting keep recovery and diagnostic controls visible.",
            "The Web release panel and API expose acceptance and readiness controls for browser users.",
            "CLI and Web can export a single acceptance evidence bundle for release-note attachment.",
            "CLI, slash commands, Web API, and Web UI expose the guided acceptance runner entrypoint.",
            "The CLI acceptance suite writes a local evidence kit for release handoff without API, SSH, or mutation workflows.",
            "The model-check mode is opt-in and requires explicit CLI mode or a connected Web session with model configuration.",
            "The ui-review mode is opt-in and exposed through CLI and Web Release controls.",
            "The conversation-check mode is opt-in and exposed through CLI and Web Release controls.",
            "The read-only collect mode is opt-in and requires an explicit CLI flag or connected Web session.",
            "The recovery-drill mode is opt-in and exposed through CLI and Web Release controls.",
            "The replay-export mode is opt-in on CLI and requires an existing audit session id before writing A09 evidence.",
            "The operator-approved A07 mutation drill is exposed through CLI plan files, Web API, and the Web Release Drill control.",
            "The Web Release panel shows strict release-gate state and top blocking reasons from the readiness payload.",
            "The Web Release panel also displays release-gate next actions from the same readiness payload.",
        ),
        references=(
            EvidenceReference(
                "Slash command summaries",
                path="tests/test_agent_upgrade_features.py::test_tui_facing_slash_commands_render_readable_summaries",
            ),
            EvidenceReference(
                "Examples are context-aware",
                path="tests/test_agent_upgrade_features.py::test_slash_examples_are_context_aware",
            ),
            EvidenceReference(
                "Playbooks list production workflows",
                path="tests/test_agent_upgrade_features.py::test_slash_playbooks_lists_copy_ready_workflows",
            ),
            EvidenceReference(
                "TUI welcome controls",
                path="tests/test_tui_formatting.py::test_tui_welcome_and_bindings_expose_recovery_and_diagnostic_commands",
            ),
            EvidenceReference(
                "Release acceptance controls",
                path="tests/test_acceptance_checklist.py::test_slash_acceptance_command_and_alias_use_current_target",
            ),
            EvidenceReference(
                "Release readiness controls",
                path="tests/test_release_readiness.py::test_slash_release_readiness_command_and_alias",
            ),
            EvidenceReference(
                "Web acceptance route",
                path="tests/test_web_api.py::test_web_acceptance_route_returns_template_without_connected_session",
            ),
            EvidenceReference(
                "Web connected target checklist",
                path="tests/test_web_api.py::test_web_acceptance_manager_uses_connected_session_target",
            ),
            EvidenceReference(
                "Acceptance bundle documentation",
                path="docs/ACCEPTANCE_BUNDLE.md",
            ),
            EvidenceReference(
                "Guided acceptance runner CLI and slash",
                path="tests/test_acceptance_runner.py::test_slash_acceptance_runner_command_uses_current_target",
            ),
            EvidenceReference(
                "CLI acceptance suite",
                path="tests/test_acceptance_runner.py::test_cli_acceptance_suite_writes_local_evidence_kit",
            ),
            EvidenceReference(
                "Guided acceptance runner Web route",
                path="tests/test_web_api.py::test_web_acceptance_runner_route_returns_guided_artifact",
            ),
            EvidenceReference(
                "CLI model-check collection",
                path="tests/test_acceptance_runner.py::test_cli_acceptance_runner_model_check_collects_a02",
            ),
            EvidenceReference(
                "Web model-check collection",
                path="tests/test_web_api.py::test_web_acceptance_runner_model_check_uses_session_llm",
            ),
            EvidenceReference(
                "CLI UI-review collection",
                path="tests/test_acceptance_runner.py::test_cli_acceptance_runner_ui_review_collects_a04",
            ),
            EvidenceReference(
                "Web UI-review collection",
                path="tests/test_web_api.py::test_web_acceptance_runner_ui_review_uses_collector_without_session",
            ),
            EvidenceReference(
                "CLI conversation-check collection",
                path="tests/test_acceptance_runner.py::test_cli_acceptance_runner_conversation_check_collects_a05",
            ),
            EvidenceReference(
                "Web conversation-check collection",
                path="tests/test_web_api.py::test_web_acceptance_runner_conversation_check_uses_collector",
            ),
            EvidenceReference(
                "CLI recovery-drill collection",
                path="tests/test_acceptance_runner.py::test_cli_acceptance_runner_recovery_drill_collects_a08",
            ),
            EvidenceReference(
                "Web recovery-drill collection",
                path="tests/test_web_api.py::test_web_acceptance_runner_recovery_drill_uses_collector",
            ),
            EvidenceReference(
                "CLI replay-export collection",
                path="tests/test_acceptance_runner.py::test_cli_acceptance_runner_replay_export_collects_a09",
            ),
            EvidenceReference(
                "Read-only collect requires connected Web session",
                path="tests/test_web_api.py::test_web_acceptance_runner_read_only_collect_requires_connected_session",
            ),
            EvidenceReference(
                "Read-only collect Web route",
                path="tests/test_web_api.py::test_web_acceptance_runner_read_only_collect_uses_collector",
            ),
            EvidenceReference(
                "Guided acceptance runner documentation",
                path="docs/ACCEPTANCE_RUNNER.md",
            ),
            EvidenceReference(
                "CLI operator-approved drill",
                path="tests/test_acceptance_runner.py::test_cli_acceptance_runner_operator_drill_uses_collector",
            ),
            EvidenceReference(
                "Web operator-approved drill",
                path="tests/test_web_api.py::test_web_acceptance_mutation_drill_uses_operator_collector",
            ),
            EvidenceReference(
                "Web release gate next actions",
                path="tests/test_web_api.py::test_web_release_readiness_route_summarizes_submitted_text_and_redacts",
            ),
        ),
    ),
    EvidenceItem(
        item_id="VG07",
        group="Verification Gate",
        requirement=VERIFICATION_GATES[6][1],
        status="covered",
        evidence=(
            "Unified sanitizer redacts nested values, commands, memory, target facts, hook payloads, skill args, and exported artifacts.",
            "Remote environment profile and startup errors preserve target identity while hiding passwords and secrets.",
        ),
        references=(
            EvidenceReference(
                "Unified sanitizer",
                path="tests/test_agent_upgrade_features.py::test_unified_output_sanitizer_redacts_nested_values_and_commands",
            ),
            EvidenceReference(
                "Conversation history sanitization",
                path="tests/test_conversation_store.py::test_conversation_store_saves_sanitized_history_and_restores_context",
            ),
            EvidenceReference(
                "Remote profile redaction",
                path="tests/test_agent_upgrade_features.py::test_env_profile_sanitizer_keeps_remote_target_identity_without_credentials",
            ),
            EvidenceReference(
                "Remote failure redacts secret",
                path="tests/test_cli_entrypoints.py::test_create_runtime_remote_connection_failure_redacts_exception_secret",
            ),
        ),
    ),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def render_evidence_matrix() -> str:
    total = len(EVIDENCE_MATRIX)
    covered = sum(1 for item in EVIDENCE_MATRIX if item.status == "covered")
    lines = [
        "Completion evidence matrix:",
        f"Status: {covered}/{total} requirements covered by direct evidence.",
    ]
    for group in ("Product Bar", "Verification Gate"):
        lines.append("")
        lines.append(f"{group}:")
        for item in (entry for entry in EVIDENCE_MATRIX if entry.group == group):
            lines.append(f"- {item.item_id} [{item.status}] {item.requirement}")
            for evidence in item.evidence:
                lines.append(f"  Evidence: {evidence}")
            for reference in item.references:
                ref_bits = []
                if reference.path:
                    ref_bits.append(reference.path)
                if reference.command:
                    ref_bits.append(f"cmd: {reference.command}")
                suffix = f" ({'; '.join(ref_bits)})" if ref_bits else ""
                lines.append(f"  Reference: {reference.label}{suffix}")
    lines.append("")
    lines.append("Suggested smoke commands:")
    lines.append("- python -m pytest tests/test_cli_entrypoints.py tests/test_tui_formatting.py -q")
    lines.append("- python -m pytest tests/test_acceptance_checklist.py tests/test_acceptance_runner.py tests/test_acceptance_mutation_drill.py tests/test_acceptance_bundle.py tests/test_evidence_matrix.py tests/test_release_readiness.py tests/test_web_api.py -q")
    lines.append("- python -m pytest tests/test_react_runner.py tests/test_golden_scenarios.py -q")
    lines.append("- python -m pytest tests/test_agent_upgrade_features.py -q")
    lines.append("- npm run typecheck --prefix web")
    lines.append("- python -m compileall -q sysdialogue tests")
    return "\n".join(lines)


def check_evidence_references(root: Path | str | None = None) -> list[str]:
    base = Path(root) if root is not None else repo_root()
    missing: list[str] = []
    for item in EVIDENCE_MATRIX:
        for reference in item.references:
            if not reference.path:
                continue
            rel_path = reference.path.split("::", 1)[0]
            if not (base / rel_path).exists():
                missing.append(f"{item.item_id}: missing {rel_path} ({reference.label})")
    return missing


def coverage_gaps() -> list[str]:
    required = {item_id: text for item_id, text in PRODUCT_BAR_REQUIREMENTS + VERIFICATION_GATES}
    by_id = {item.item_id: item for item in EVIDENCE_MATRIX}
    gaps: list[str] = []
    for item_id, requirement in required.items():
        item = by_id.get(item_id)
        if item is None:
            gaps.append(f"{item_id}: missing matrix item")
        elif item.requirement != requirement:
            gaps.append(f"{item_id}: requirement text drift")
        elif item.status != "covered":
            gaps.append(f"{item_id}: status is {item.status}")
        elif not item.references:
            gaps.append(f"{item_id}: no evidence references")
    return gaps
