# 验证日志 - 2026-04-25

## 1. Git 预检

命令：

```powershell
python scripts\git_preflight.py
```

结果摘要：

```text
branch: main
fetch: completed
pull: already up to date
```

结论：提交材料整理前，仓库已同步到远端主线。

## 2. 编译检查

命令：

```powershell
python -m compileall -q sysdialogue tests
```

结果：

```text
exit code: 0
```

结论：源码和测试文件均可通过 Python 编译检查。

## 3. 自动化测试

命令：

```powershell
python -m pytest -q
```

结果摘要：

```text
184 passed
```

结论：回归测试通过。

## 4. Runtime 自检

命令：

```powershell
python -m sysdialogue.app.cli --verify
```

结果摘要：

```text
SysDialogue v9 - Self-check (--verify)

[1/5] Sanitized environment profile
[2/5] Registered tools: 37 static + 6 meta
[3/5] Built-in workflows: 10
[4/5] Security rules:
  - RiskClassifier coverage: 37 tools
  - CommandSafetyChecker: CS001-CS010
  - RemoteLockoutChecker: B010 / B015-B017 / WH023
[5/5] Config:
  - model: configured
  - base_url: configured
  - dynamic_tools: enabled
  - deployment_mode: local
  - OPENAI_API_KEY: configured

[OK] Self-check passed.
```

结论：runtime 能识别静态工具、元工具、workflow、安全规则和模型接口配置。

## 5. 现场演示证据路径

TUI 演示完成后，可收集以下路径作为运行证据：

```powershell
Get-ChildItem $env:USERPROFILE\.sysdialogue\sessions
Get-ChildItem $env:USERPROFILE\.sysdialogue\tasks
Get-ChildItem $env:USERPROFILE\.sysdialogue\locks
Get-ChildItem $env:USERPROFILE\.sysdialogue\audit
Get-ChildItem $env:USERPROFILE\.sysdialogue\traces
```

提交前需脱敏：

- API Key
- SSH 私钥和密码
- 真实服务器 IP、主机名和用户名
- 包含业务敏感内容的 stderr/stdout
- `.env`、凭证文件、私钥路径
