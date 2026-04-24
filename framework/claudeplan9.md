# SysDialogue v9 Design Baseline

## 1. Positioning

SysDialogue v9 extends the v8 durable ReAct operations agent with reusable skills, controlled hooks, explicit role handoff, target profiles, and richer permission explanations.

This document supersedes `claudeplan8.md`.

- Active baseline: `claudeplan9.md`
- Historical reference only: `claudeplan6.md`, `claudeplan7.md`, and `claudeplan8.md`

## 2. Core Invariants

1. Static tools and built-in workflows remain the preferred execution path.
2. DynTool is always available, but remains last resort and always passes safety checks, permission policy, confirmation, audit, trace, locks, and ReAct completion gates.
3. One-off commands should use inline `execute_dynamic_tool`; reusable command families may use `propose_dynamic_tool`.
4. `BLOCK` has no override path.
5. Mutations require observe -> act -> verify -> finish.
6. Sessions, tasks, locks, memory, permissions, traces, skills, hooks, and target profiles are local durable state.
7. User-facing surfaces show concise task cards and friendly errors; raw technical details remain folded or audit-only.

## 3. Persistent Layout

Shared state lives under `~/.sysdialogue/`:

- `sessions/`: visible transcripts, conversation history/context, pending descriptors
- `tasks/`: durable task records, phases, steps, events, heartbeat
- `locks/`: cross-process lock leases
- `policy.json`: allow / ask / deny permission rules
- `memory/`: layered memory records and `MEMORY.md`
- `traces/`: JSONL trace spans with redaction and file locks
- `skills/`: user Markdown skills
- `hooks.json`: user hook rules
- `targets/`: target profiles

Project-local extensions live under `.sysdialogue/`:

- `.sysdialogue/skills/<name>/SKILL.md`
- `.sysdialogue/hooks.json`

Project skills override user skills with the same name.

## 4. Skills

Skills are Markdown playbooks. A `SKILL.md` may include YAML frontmatter:

```yaml
---
name: service-triage
description: Triage Linux service failures
when_to_use: The user asks to diagnose a service outage
user_invocable: true
model_invocable: true
allowed_tools:
  - manage_service
  - read_log
permission:
  risk: read-only-first
arguments:
  service:
    type: string
---
```

Activating a skill only injects instructions and context. It never executes OS operations by itself and cannot bypass safety gates.

Available controls:

- `/skills`
- `/skill <name> [json args]`
- `/skill-reload`
- meta tool `activate_skill`

## 5. Hooks

Hooks are controlled automation rules. Supported events:

- `task_started`
- `pre_tool`
- `post_tool`
- `approval_requested`
- `lock_conflict`
- `task_finished`
- `task_failed`

Supported actions:

- `notify`: add a task event
- `inject_context`: add read-only context
- `execute_command`: run through DynTool inline safety chain

Hook commands are bounded by timeout, recursion guards, permission policy, confirmation, audit, trace, and `BLOCK` refusal.

## 6. Role Handoff

Built-in role profiles:

- `planner`
- `executor`
- `verifier`
- `risk_reviewer`
- `toolsmith`

`handoff_to_role(role, objective, constraints)` returns structured guidance and records a trace/task event. It is serial and advisory in v9; it does not create parallel agents and does not transfer execution ownership away from `AgentController`.

## 7. Permissions, Memory, And Targets

`PermissionPolicy` uses OpenCode-style `allow / ask / deny` rules. The most specific match wins, and equal specificity uses the later rule. Decisions expose:

- matched rule
- candidate rules
- reason
- suggested session-only always grant

Memory remains Markdown + JSON without embeddings in v9. Long-term memory redacts secrets and supports `/forget` and `/compact --preview`.

Target profiles record reusable facts about the current local or SSH target under `~/.sysdialogue/targets/` and are exposed with `/target`.

## 8. Slash Commands

Shared commands across TUI, Web, and Simple CLI:

- `/status`
- `/resume`
- `/locks`
- `/plan`
- `/audit`
- `/memory`
- `/tools`
- `/permissions`
- `/compact`
- `/skills`
- `/skill`
- `/skill-reload`
- `/hooks`
- `/forget`
- `/target`
- `/why`

## 9. Web API Additions

- `GET /api/session/{id}/skills`
- `POST /api/session/{id}/skill`
- `GET /api/session/{id}/hooks`
- `GET /api/session/{id}/permissions/explain`

These endpoints expose control-plane state only. Execution still goes through the same ReAct/runtime safety path.
