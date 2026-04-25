# SysDialogue v7 Design Baseline

## 1. Positioning

SysDialogue is a Linux operations agent that accepts natural-language goals and executes only through controlled tools, workflows, safety gates, and audit logs.

This document is now a historical baseline. The current design baseline is `claudeplan8.md`.

- Active baseline: `claudeplan8.md`
- Historical reference: `claudeplan7.md`
- Historical reference only: `claudeplan6.md`

## 2. Design Principles

1. Static-tool-first
2. Workflow-first
3. Preview / Backup / Validate for configuration changes
4. EnvProfile-driven execution
5. DynTool-last, but always available
6. BLOCK rules have no override path
7. Every operation must be auditable
8. Mutations require verification before completion

## 3. Runtime Surfaces

Supported entrypoints:

- TUI
- Web console
- scheduled jobs / cron callback
- verify / demo helper entrypoints

All interactive surfaces should share the same session, task, and lock semantics.

## 4. Shared Persistent State

Persistent state is stored under `~/.sysdialogue/` using `JSON + filelock`.

### 4.1 SessionStore

Location:

- `~/.sysdialogue/sessions/<session_id>.json`

Purpose:

- user-visible transcript summary
- reusable `ConversationManager.history/context`
- task event summaries
- active task binding
- pending confirmation / pending input descriptors
- friendly technical details for the latest failure

### 4.2 TaskStore

Location:

- `~/.sysdialogue/tasks/<task_id>.json`

Purpose:

- durable task ownership
- mode: `direct | plan | workflow`
- status lifecycle
- current phase
- dynamic iteration budget
- plan/workflow step progress
- heartbeat timestamp
- task events

### 4.3 LockStore

Location:

- `~/.sysdialogue/locks/<scope_hash>.json`

Purpose:

- cross-process resource leases
- ownership by `task_id`
- heartbeat-based stale reclaim

## 5. Task Model

### 5.1 Task Statuses

- `ready`
- `running`
- `waiting_confirm`
- `waiting_input`
- `blocked`
- `interrupted`
- `failed`
- `completed`
- `rolled_back`
- `cancelled`

### 5.2 Task Phases

Direct-mode tasks track phases:

- `analysis`
- `observe`
- `act`
- `verify`
- `finish`

### 5.3 Task Steps

Plan and workflow tasks persist `TaskStepRecord` entries.

Each step includes:

- `step_id`
- `status`
- `kind`
- `tool`
- `purpose`
- `args`
- `expected_risk`
- `actual_risk`
- `rule_ids`
- `error`
- `lock_scope`
- `audit_refs`

## 6. ReAct Runtime

`sysdialogue/agent/react_runner.py` is the task-level orchestration layer.

It is responsible for:

- creating or resuming `TaskRecord`
- dynamic iteration budgets
- explicit ReAct loop enforcement
- finish-task completion gates
- event emission
- syncing task/session state

### 6.1 ReAct Closure Rules

- plain natural-language completion is invalid
- final closure must be `finish_task`
- operational tasks require observation before `completed`
- mutating tasks require post-mutation verification
- failed mutations do not count as successful change

### 6.2 Iteration Budgets

`SYSDIALOGUE_MAX_ITER` is a hard limit, not a per-task fixed loop count.

Default hard limit: `160`

Dynamic budgets:

- casual/non-operational: about `20`
- normal operational tasks: about `80`
- complex mutation / workflow / DynTool tasks: about `140`

All budgets are clamped by the hard limit.

## 7. Plan Mode

`set_execution_mode(mode="plan")` creates a durable frozen plan.

Plan mode is no longer advisory-only.

Rules:

- a frozen plan creates persistent plan steps
- the next tool call must match the next pending frozen step
- deviations are rejected
- `completed` is invalid until all plan steps finish or are explicitly resolved

## 8. Workflow Mode

Built-in workflows still execute through `WorkflowEngine`, but workflow progress is also persisted into `TaskStore`.

Requirements:

- workflow steps and rollback steps update durable task steps
- workflow lock scopes use cross-process leases
- workflow cancellation and rollback states stay visible after restart

## 9. Cross-Process Resource Locking

`lock_scope` now means a durable resource lease, not a process-local lock.

Rules:

- owner is always `task_id`
- heartbeat refresh interval: 5 seconds
- stale threshold: 30 seconds
- stale reclaim marks the old task `interrupted`
- final task states release leases

## 10. Session Recovery

After restart:

- transcript and reusable context are restored
- stale active tasks become `interrupted`
- pending confirmation / input are cleared
- the UI should tell the user the previous run was interrupted
- the system does not pretend to resume a half-finished OS command

Recovery granularity is step/phase boundary, not mid-command replay.

## 11. DynTool Policy

DynTool is always enabled.

Rules:

- static tools first
- built-in workflows next
- inline `execute_dynamic_tool` for one-off commands
- `propose_dynamic_tool` only for reusable command families
- all DynTool execution still goes through:
  - command safety
  - semantic risk mapping
  - user confirmation
  - audit
  - ReAct completion gates

Unknown or unproven commands are treated conservatively as state-changing.

## 12. Error Presentation

User-facing errors must use a shared presentation model:

- `user_summary`
- `impact`
- `suggested_next_action`
- `technical_details`

Requirements:

- TUI / Web show friendly summaries by default
- tracebacks and raw API errors stay in technical details
- ReAct protocol failures must not expose internal correction chatter directly

## 13. Surface-Specific Notes

### TUI

- main interaction surface
- compact task cards for ReAct timeline
- `F2` conversation history
- `Ctrl+C` cancels current task
- `Ctrl+D` exits

### Web

- session state must survive process restarts at the persisted layer
- transcript must not dump raw tracebacks into the visible chat stream


### Scheduled Jobs

- create durable task records
- no interactive confirmation allowed
- `WARN-HIGH` and `BLOCK` are rejected before execution

## 14. Verification Requirements

Minimum regression suite:

```powershell
python -m pytest -q
python -m compileall -q sysdialogue tests
python -m sysdialogue.app.cli --verify
```

Important live-host validation:

- remote SSH config mutation + verify
- rollback chains
- concurrent resource lock contention across surfaces
- system cron execution on Linux

## 15. Out Of Scope For This Baseline

Not required in v7 baseline:

- database-backed state store
- distributed worker queue
- token streaming UX overhaul
- mid-command replay after process crash

## 16. Migration Note From v6

Compared with `claudeplan6.md`, this baseline explicitly changes:

- DynTool is always on
- DynTool is always available through the normal safety gates
- sessions/tasks/locks are durable
- plan mode is a task graph, not advisory text
- cross-surface consistency is a first-class runtime requirement
