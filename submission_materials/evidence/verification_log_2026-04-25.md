# Verification Log - 2026-04-25

## 1. Git preflight

Command:

```powershell
python scripts\git_preflight.py
```

Observed output:

```text
[repo]
root:   D:\项目\Nexus
branch: main
head:   b2a85cc

[status]
## main...origin/main

[fetch]
git fetch --all --prune completed.

[pull]
Already up to date.

[final status]
## main...origin/main
```

Conclusion: repository was synchronized and clean before preparing submission materials.

## 2. Compile check

Command:

```powershell
python -m compileall -q sysdialogue tests
```

Observed result:

```text
exit code: 0
```

Conclusion: Python files compile successfully.

## 3. Test suite

Command:

```powershell
python -m pytest -q
```

Observed output:

```text
........................................................................ [ 71%]
.............................                                            [100%]
101 passed in 18.12s
```

Conclusion: regression suite passes.

## 4. Runtime self-check

Command:

```powershell
python -m sysdialogue.app.cli --verify
```

Observed output summary:

```text
SysDialogue v9 - Self-check (--verify)

[1/5] Sanitized environment profile
[2/5] Registered tools: 37 static + 6 meta
[3/5] Built-in workflows: 10
[4/5] Security rules:
  - RiskClassifier coverage: 37 tools
  - CommandSafetyChecker: CS001-CS009
  - RemoteLockoutChecker: B010 / B015-B017 / WH023
[5/5] Config:
  - model: Ali-dashscope/Qwen3.5-Plus
  - base_url: https://newapi.sduonline.cn/v1
  - dynamic_tools: enabled
  - deployment_mode: local
  - OPENAI_API_KEY: configured

[OK] Self-check passed.
```

Conclusion: runtime sees all tools/workflows/security rules and OpenAI-compatible configuration.

## 5. Evidence paths for live demo

After running TUI demos, collect:

```powershell
Get-ChildItem $env:USERPROFILE\.sysdialogue\sessions
Get-ChildItem $env:USERPROFILE\.sysdialogue\tasks
Get-ChildItem $env:USERPROFILE\.sysdialogue\locks
Get-ChildItem $env:USERPROFILE\.sysdialogue\audit
Get-ChildItem $env:USERPROFILE\.sysdialogue\traces
```

Do not submit secrets. Redact API keys, private hostnames, user names, IPs, and raw stderr if needed.
