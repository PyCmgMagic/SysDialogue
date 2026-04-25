# 01. Agent 配置说明

## 1. Agent 形态

SysDialogue 是「提示词 + 工具编排」为主的运维 Agent。用户用自然语言提出目标，模型不能直接给用户 shell 命令，而必须通过受控工具、workflow 或 DynTool 安全链路执行。

当前系统由以下层组成：

- LLM 接入层：OpenAI-compatible Chat Completions tool calls。
- Agent 控制层：`AgentController` + `ReActRunner`。
- 工具层：37 个静态工具 + 6 个元工具 + DynTool。
- 工作流层：10 个 YAML workflow。
- 安全层：`RiskClassifier`、`CommandSafetyChecker`、`PermissionPolicy`、审批、审计、Trace。
- 状态层：`SessionStore`、`TaskStore`、`LockStore`、Memory、TargetProfile。
- 交互入口：TUI、scheduled-job callback。

## 2. 模型与 API 配置

配置来源优先级：

1. CLI `--model`
2. `OPENAI_MODEL`
3. 兼容 fallback：`SYSDIALOGUE_MODEL`

推荐 `.env`：

```dotenv
OPENAI_API_KEY=<redacted>
OPENAI_BASE_URL=https://newapi.sduonline.cn/v1
OPENAI_MODEL=Ali-dashscope/Qwen3.5-Plus
SYSDIALOGUE_MAX_ITER=160
```

说明：

- `OPENAI_API_KEY` 不应写入提交材料；所有文档只保留 `<redacted>`。
- `OPENAI_BASE_URL` 可为空；为空时使用 SDK 默认地址。
- `SYSDIALOGUE_MAX_ITER` 是 ReAct 硬上限，任务级预算会动态裁剪。

## 3. 运行入口

```powershell
# 自检，不调用模型 API
python -m sysdialogue.app.cli --verify

# 演示内置 security_audit workflow，不调用模型 API
python -m sysdialogue.app.cli --demo

# TUI 主入口
python -m sysdialogue.app.cli

# 计划任务回调
python -m sysdialogue.app.cli --run-scheduled-job <job_id>
```

## 4. 本地与远程目标机

本地模式：

- 控制平面和执行目标都是当前机器。
- Windows 可作为控制平面运行 TUI/verify。
- Linux 是主要运维目标环境。

远程 SSH 模式：

```powershell
python -m sysdialogue.app.cli --remote user@example.com:22 --ssh-key C:\Users\ASUS\.ssh\id_ed25519
```

设计语义：

- TUI 服务仍运行在控制端本机。
- `--remote` 只改变工具执行目标。
- 目标机文件访问通过目标机文件层和远程 executor 完成。
- 远程锁门规则会阻止停止 SSH、阻断当前 SSH 端口等危险操作。

## 5. 持久化配置

共享状态目录：`~/.sysdialogue/`

| 目录/文件 | 内容 |
| --- | --- |
| `sessions/` | 用户可见对话、上下文、pending 描述、任务事件摘要 |
| `tasks/` | durable task、步骤、阶段、预算、heartbeat |
| `locks/` | 跨进程资源 lease |
| `audit/` | JSONL 审计日志 |
| `traces/` | JSONL trace spans，带脱敏和文件锁 |
| `memory/` | 长期记忆与 `MEMORY.md` |
| `policy.json` | allow / ask / deny 权限规则 |
| `skills/` | 用户级 Markdown Skills |
| `hooks.json` | 用户级 Hooks |
| `targets/` | 目标机画像 |

项目级扩展：

| 路径 | 内容 |
| --- | --- |
| `.sysdialogue/skills/<name>/SKILL.md` | 项目技能，优先级高于用户技能 |
| `.sysdialogue/hooks.json` | 项目 Hooks |

## 6. 安全与审批配置

风险等级：

- `SAFE`：只读或元数据操作，自动执行并审计。
- `WARN-LOW`：低风险，记录风险提示和审计。
- `WARN-HIGH`：高风险，必须用户审批。
- `BLOCK`：不可覆盖，直接拒绝。

审批返回值：

```python
True
False
{"approved": true, "decision": "once"}
{"approved": true, "decision": "always_this_session"}
{"approved": false, "decision": "deny"}
```

`always_this_session` 只在当前 session 有效，不写入长期全局策略。

