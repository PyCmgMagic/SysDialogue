# SysDialogue Current Handoff

> Active design baseline: `framework/claudeplan8.md`
> Historical archive only: `framework/claudeplan6.md` and `framework/claudeplan7.md`

## What This Project Is

SysDialogue is a Linux operations agent with:

- OpenAI-compatible Chat Completions tool-calling
- task-level ReAct runtime
- 37 static tools + 4 meta tools
- built-in workflows
- TUI / Simple CLI / Web / scheduled-job entrypoints
- risk gates, confirmation, rollback hints, and audit logs
- OpenCode-style permission policy
- layered memory and local trace spans
- shared slash commands
- local and SSH remote execution

The current runtime always enables DynTool; there is no gated development-only mode.
DynTool is always enabled, but still gated by safety checks, confirmation, audit, and ReAct completion rules.

## Current Source Of Truth

Use `framework/claudeplan8.md` for current architecture and behavior.

Do not treat `framework/claudeplan6.md` or `framework/claudeplan7.md` as normative anymore. They are kept only so we can trace earlier design decisions and audit the migration path.

## Git-First Rules

Before each substantial change:

1. Run `python scripts/git_preflight.py`
2. If the worktree is dirty, do not blindly `pull`, `stash`, or `reset --hard`
3. Work from the latest code when the preflight allows it
4. Validate before committing
5. Dangerous Git operations require explicit user approval

## Important Runtime Facts

### ReAct

- Every turn runs through `sysdialogue/agent/react_runner.py`
- Plain natural-language termination is invalid; final closure must be `finish_task`
- Operational tasks must observe the environment before `completed`
- Mutating tasks must satisfy `observe -> act -> verify -> finish`
- Failed or blocked mutations do not count as completed changes

### Sessions / Tasks / Locks

Persistent state now lives under:

- `~/.sysdialogue/sessions/`
- `~/.sysdialogue/tasks/`
- `~/.sysdialogue/locks/`
- `~/.sysdialogue/policy.json`
- `~/.sysdialogue/memory/`
- `~/.sysdialogue/traces/`

Shared stores are implemented in `sysdialogue/agent/state_store.py`:

- `SessionStore`
- `TaskStore`
- `LockStore`

Locking is cross-process lease-based, not in-memory-only anymore.

### Policy / Memory / Trace

- `PermissionPolicy` is additive: it can deny or ask for extra confirmation, but cannot downgrade `BLOCK`.
- `MemoryManager` stores layered reusable context and redacts obvious secrets before long-term storage.
- `TraceStore` writes local JSONL spans for LLM calls, tools, guardrails, confirmations, and verification.

### Slash Commands

Shared commands are available in TUI, Web, and Simple CLI:

- `/status`
- `/resume`
- `/locks`
- `/plan`
- `/audit`
- `/memory`
- `/tools`
- `/permissions`
- `/compact`

### Entry Surfaces

- TUI: `python -m sysdialogue.app.cli`
- Simple CLI: `python -m sysdialogue.app.cli --simple`
- Web: `python -m sysdialogue.app.cli --web`
- Verify: `python -m sysdialogue.app.cli --verify`
- Demo: `python -m sysdialogue.app.cli --demo`
- Scheduled jobs: `python -m sysdialogue.app.cli --run-scheduled-job <job_id>`

### Provider

The runtime is OpenAI-compatible only.

Required for TUI / Simple CLI / Web:

- `OPENAI_API_KEY`
- `OPENAI_MODEL` or `--model`

Optional:

- `OPENAI_BASE_URL`

### DynTool

DynTool is always available, but still last-resort:

- prefer static tools
- prefer built-in workflows
- use inline `execute_dynamic_tool` for one-off ad-hoc commands
- use `propose_dynamic_tool` only for reusable command families

## Key Files

- `sysdialogue/agent/controller.py`
- `sysdialogue/agent/react_runner.py`
- `sysdialogue/agent/state_store.py`
- `sysdialogue/agent/error_presentation.py`
- `sysdialogue/agent/planner.py`
- `sysdialogue/agent/workflow_engine.py`
- `sysdialogue/agent/permission_policy.py`
- `sysdialogue/agent/memory.py`
- `sysdialogue/agent/trace_store.py`
- `sysdialogue/agent/command_registry.py`
- `sysdialogue/app/runtime_factory.py`
- `sysdialogue/app/jobs.py`
- `sysdialogue/app/simple_cli.py`
- `sysdialogue/web/service.py`
- `sysdialogue/ui/tui_app.py`
- `sysdialogue/ui/task_timeline.py`
- `framework/claudeplan8.md`

## Current Behavior Guarantees

- Web sessions survive restart at the persisted state layer
- stale active tasks are marked `interrupted`
- pending confirmations / pending input are not replayed after restart
- workflow resource locks use cross-process leases
- plan mode now creates durable task steps instead of advisory text only
- friendly error presentation is shared across TUI / Web / Simple CLI
- slash commands are shared across TUI / Web / Simple CLI
- PermissionPolicy, MemoryManager, and TraceStore are injected into every runtime

## Remaining Real-World Validation Gaps

Still worth validating on a real Linux target:

- end-to-end `safe_config_patch`
- rollback chains on real services
- remote SSH mutation + verification flow
- cron/system cron scheduling in a live Linux host
- concurrent lock contention across surfaces

## Verification Commands

Use these after meaningful changes:

```powershell
python -m pytest -q
python -m compileall -q sysdialogue tests
python -m sysdialogue.app.cli --verify
```

## Notes For Future Work

The next likely improvement areas are:

- richer durable plan resume UX across surfaces
- better Web session list / session recovery UI
- Linux real-host acceptance coverage
- tighter task-store visibility in the Web frontend
