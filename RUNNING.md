# SysDialogue Running Guide

## Baseline

- Active design baseline: `framework/claudeplan9.md`
- Historical reference only: `framework/claudeplan6.md`, `framework/claudeplan7.md`, and `framework/claudeplan8.md`

This guide explains how to install, configure, run, and verify the current SysDialogue runtime.

## 1. Requirements

- Python `>= 3.11`
- Recommended host: Linux
- Windows is supported as a control plane for TUI / `--verify`
- Remote target host should be Linux and reachable over SSH

## 2. Install

From the repository root:

```powershell
cd D:\项目\Nexus
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Linux / macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

## 3. API Configuration

Interactive entrypoints require:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`

Optional:

- `OPENAI_BASE_URL`

### Environment variables

```powershell
$env:OPENAI_API_KEY="your_api_key"
$env:OPENAI_BASE_URL="https://your-openai-compatible-endpoint/v1"
$env:OPENAI_MODEL="your-model-name"
```

### `.env`

Create `.env` in the repo root:

```dotenv
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_MODEL=your-model-name
SYSDIALOGUE_MAX_ITER=160
```

`SYSDIALOGUE_MAX_ITER` is the hard upper bound for ReAct iteration budgets.
Per-task budgets are dynamic:

- casual / explanatory tasks: about `20`
- normal operational tasks: about `80`
- complex mutation / workflow / DynTool tasks: about `140`

All of them are clamped to `20..300`.

## 3.1 Skills, Hooks, And Profiles

Optional local extensions:

- Project skills: `.sysdialogue/skills/<name>/SKILL.md`
- User skills: `~/.sysdialogue/skills/<name>/SKILL.md`
- Project hooks: `.sysdialogue/hooks.json`
- User hooks: `~/.sysdialogue/hooks.json`
- Target profiles: `~/.sysdialogue/targets/`

Skills inject playbook instructions only. Hooks can notify, inject read-only context, or run a bounded command through the DynTool safety chain.

## 4. Git Preflight

Before a development round:

```powershell
python scripts\git_preflight.py
```

This checks:

- current branch and worktree state
- `git fetch --all --prune`
- safe `git pull --rebase` only when allowed

## 5. Main Entrypoints

### Verify

No LLM call. Safe to run without API credentials.

```powershell
python -m sysdialogue.app.cli --verify
```

### Demo

Runs the built-in `security_audit` workflow without using the model API.

```powershell
python -m sysdialogue.app.cli --demo
```

Notes:

- local demo is intended for Linux
- on Windows, `--demo` should return an unsupported-host message instead of a crash

### TUI

```powershell
python -m sysdialogue.app.cli
```

### Scheduled Job Callback

```powershell
python -m sysdialogue.app.cli --run-scheduled-job <job_id>
```

## 6. Remote SSH Mode

Run the control plane locally, but execute tools against a remote Linux host:

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519
```

Password authentication is also supported. Prefer the environment variable so the
password is not stored in shell history:

```powershell
$env:SYSDIALOGUE_SSH_PASSWORD="your_ssh_password"
python -m sysdialogue.app.cli --remote user@example.com:22
```

For quick local testing you can also pass `--ssh-password your_ssh_password`.

Important:

- `--remote` changes the execution target
- first-time SSH hosts are automatically trusted and appended to `known_hosts`;
  changed host keys are still rejected

## 7. TUI Shortcuts

- `F2`: open conversation history
- `F3`: audit panel
- `F4`: environment panel
- `Ctrl+C`: cancel the current task / workflow
- `Ctrl+D`: exit

TUI history restores reusable context only. It does not replay historical tool execution.

## 8. Shared Durable State

SysDialogue now persists shared state under `~/.sysdialogue/`:

- `sessions/`
- `tasks/`
- `locks/`
- `policy.json`
- `memory/`
- `traces/`
- `commands/`
- `targets/`

What this means:

- sessions survive restart at the persisted state layer
- stale active tasks become `interrupted`
- cross-process resource locks are durable leases
- pending confirmations / input are not replayed after restart
- permission policy, memory, and trace spans are shared across TUI / scheduled jobs

## 9. Slash Commands

The interactive entrypoints support shared control-plane commands:

```text
/status
/resume
/locks
/plan
/audit
/memory
/tools
/permissions
/compact
/skills
/skill <name> [json args]
/skill-reload
/hooks
/forget <memory_id>
/target
/why [tool]
```

Examples:

```text
/status
/memory Prefer nginx changes during maintenance windows
/compact nginx service is the current troubleshooting target
/skills
/skill service-triage {"service":"nginx"}
/target set maintenance_window=Sunday 02:00
```

## 10. ReAct Runtime Rules

Every task now runs through task-level ReAct.

Key rules:

- plain natural-language completion is invalid
- final closure must use `finish_task`
- operational tasks must observe environment state before `completed`
- mutation tasks must verify after change before `completed`
- failed mutations do not count as successful changes

## 11. Permission / Memory / Trace

- `PermissionPolicy` supports `allow / ask / deny` rules in `~/.sysdialogue/policy.json`.
- Static tools keep the existing RiskClassifier behavior unless a stricter policy rule matches.
- DynTool commands still ask by default and cannot bypass `BLOCK`.
- `MemoryManager` stores layered reusable facts under `~/.sysdialogue/memory/` and redacts obvious secrets.
- `TraceStore` writes JSONL spans under `~/.sysdialogue/traces/` for observability and replay support.

## 12. DynTool

DynTool is always enabled, but still last-resort.

Use order:

1. static tools
2. built-in workflows
3. inline `execute_dynamic_tool` for one-off commands
4. `propose_dynamic_tool` only for reusable command families

DynTool execution still passes through:

- command safety
- semantic risk mapping
- user confirmation
- audit
- ReAct completion gates

## 13. Validation Commands

Recommended regression commands:

```powershell
python -m pytest -q
python -m compileall -q sysdialogue tests
python -m sysdialogue.app.cli --verify
```

## 14. Known Real-Host Validation Gaps

Still worth validating on a real Linux machine:

- `safe_config_patch`
- rollback chains on real services
- remote mutation + verify flows
- system cron execution
- lock contention between TUI / scheduled jobs

## 16. Troubleshooting

### Missing API config

If TUI refuses to start:

- check `OPENAI_API_KEY`
- check `OPENAI_MODEL`
- if using a compatible proxy, check `OPENAI_BASE_URL`

### Model does not use tools

If the task fails with a ReAct protocol error:

- confirm the model supports Chat Completions `tool_calls`
- try a smaller operational request

### Local demo unsupported

If `--demo` says unsupported on Windows:

- that is expected
- run the demo on Linux or against a remote Linux host

### Remote SSH cannot connect

Check:

- host / port / user
- SSH private key path
- host trust in `known_hosts`

## 15. Quick Start

### Local verify

```powershell
python scripts\git_preflight.py
python -m sysdialogue.app.cli --verify
```

### Local TUI

```powershell
python -m sysdialogue.app.cli
```

