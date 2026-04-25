# SysDialogue v8 Design Baseline

## 1. Positioning

SysDialogue is a Linux operations agent that executes natural-language operational goals through controlled tools, workflows, ReAct task loops, durable state, safety gates, permission policy, memory, traces, and audit logs.

This document supersedes `claudeplan7.md`.

- Active baseline: `claudeplan8.md`
- Historical reference only: `claudeplan6.md` and `claudeplan7.md`

## 2. Design Principles

1. Static tools and workflows first.
2. DynTool is always available, but remains last resort.
3. One-off commands use inline `execute_dynamic_tool`; reusable command families may use `propose_dynamic_tool`.
4. BLOCK rules have no override path.
5. Permission policy may deny or require extra confirmation, but must not weaken risk classification.
6. Mutations require observe -> act -> verify -> finish.
7. Sessions, tasks, locks, memory, permissions, and traces are durable.
8. User-facing surfaces show friendly summaries; raw technical detail stays foldable or audit-only.

## 3. Shared Persistent State

SysDialogue stores shared state under `~/.sysdialogue/` using JSON files and file locks:

- `sessions/`: visible transcript summaries, reusable conversation context, pending descriptors
- `tasks/`: durable task records, phases, steps, events, heartbeat
- `locks/`: cross-process resource leases
- `policy.json`: allow / ask / deny permission rules
- `memory/`: layered memory records and `MEMORY.md`
- `traces/`: local JSONL trace spans
- `commands/`: reserved for future user-defined slash commands
- `targets/`: reserved for persistent target profiles

## 4. ReAct And Task Graph

Every user turn enters the ReAct runtime unless it is a slash command.

Rules:

- Plain natural-language completion is invalid; tasks close through `finish_task`.
- Operational tasks must observe the environment before `completed`.
- Mutating tasks must verify after a successful mutation.
- Failed or blocked mutation attempts do not count as completed changes.
- `set_execution_mode(mode="plan")` creates durable task steps, not advisory text.
- Frozen plan execution must match the next dependency-ready step.
- Explicit `/resume` or Web resume should be preferred over guessing from user wording.

## 5. Permission Policy

`PermissionPolicy` implements OpenCode-style `allow / ask / deny` semantics.

Supported rule kinds:

- `tool`
- `command`
- `path`
- `target`
- `risk`

Default behavior preserves the existing `RiskClassifier` for static tools. Dynamic commands still require explicit confirmation by default. `BLOCK` is always denied, regardless of policy.

## 6. Memory

`MemoryManager` stores reusable context without a vector database in v8.

Scopes:

- `global`: user/runtime preferences
- `target`: remembered host or SSH target facts
- `project`: reusable project facts
- `session`: compacted conversation summaries

Secrets, tokens, passwords, and private keys are redacted before long-term storage. Raw stderr and full tool output should not be written to memory by default.

## 7. Trace Store

`TraceStore` records local spans that link agent behavior to task and audit state.

Span types include:

- `llm_call`
- `tool_call`
- `guardrail`
- `handoff`
- `lock`
- `approval`
- `verification`

Traces are for observability and replay support. They do not replace `AuditLog`, and they must redact obvious secrets.

## 8. Slash Commands

All interactive surfaces support a shared command layer:

- `/status`
- `/resume`
- `/locks`
- `/plan`
- `/audit`
- `/memory`
- `/tools`
- `/permissions`
- `/compact`

Slash commands are control-plane operations. Normal operational requests still go through ReAct.

## 9. Role Handoff Profiles

SysDialogue uses lightweight internal role profiles instead of external multi-agent frameworks:

- `planner`
- `executor`
- `verifier`
- `risk_reviewer`
- `toolsmith`

These profiles constrain prompt intent and tool choice. They are not permission bypasses and do not run concurrently in v8.

## 10. Surfaces

TUI, Web, and scheduled jobs share the same controller services:

- same session/task/lock semantics
- same permission policy
- same memory and traces
- same friendly error presentation
- same ReAct completion gates

Web exposes:

- `POST /api/session/{id}/resume`
- `POST /api/session/{id}/command`
- `GET /api/session/{id}/traces`
- `GET /api/session/{id}/memory`

## 11. Verification

Minimum regression commands:

```powershell
python -m pytest -q
python -m compileall -q sysdialogue tests
python -m sysdialogue.app.cli --verify
```

Live-host validation remains required for:

- remote SSH mutation and verification
- safe config patch and rollback chains
- system cron execution
- cross-surface lock contention
