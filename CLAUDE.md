# SysDialogue v6 — 开发交接文档

> 设计文档：`framework/claudeplan6.md`（1684 行，19 章，完整独立设计，无需参考历史版本）
> 当前状态：v6 主骨架、任务级 ReAct runtime、OpenAI-compatible Chat Completions 适配、远程目标机文件访问、`--run-scheduled-job`、`--simple`、Web 控制台、ConversationManager、TUI 历史恢复/取消/输入链路均已落地；Linux 真机链路仍需最终验收。
> 当前入口：`python -m sysdialogue.app.cli --verify/--demo/--simple/--web/(无参启动 TUI)`，Windows 本地 `--demo` 会明确返回“不支持本地 Linux 巡检演示”而非模糊失败。

---

## 项目简介

SysDialogue v6：面向 Linux 服务器运维场景的操作系统智能代理。用户用自然语言输入运维需求，代理在受控工具体系内规划执行，所有操作经过安全门拦截和审计记录。

**核心原则**：Static-first / Workflow-first / Preview-Backup-Validate / EnvProfile-driven / DynTool-last（一次性命令优先 inline 执行，可复用能力才注册）

---

## 当前开发基线与执行约束

**唯一设计基线**：后续开发默认以 `framework/claudeplan6.md` 为准。`framework/plan_archive/` 中的历史 plan 只作为背景参考；如果历史 plan 与 v6 设计冲突，一律以 v6 为准。

**Git-first 开发约束**（每次开始一轮新开发时默认执行）：

1. **先检查仓库状态**：先看 `git status --short --branch`，确认当前分支、是否存在未提交改动、是否有未跟踪文件。
2. **先同步再改动**：随后执行 `git fetch --all --prune`；若当前分支存在 upstream 且 worktree 干净，再执行 `git pull --rebase`，确保基于最新代码开发。
3. **脏工作区禁止盲拉取**：若存在未提交改动、merge/rebase 未完成或冲突，禁止直接 `pull`、`stash`、`reset --hard`。应先暂停并说明现场状态，再由用户决定是先提交 checkpoint、先清理，还是在当前基础上继续。
4. **改动前先建立版本边界**：较大任务优先放到独立分支开发，建议分支名使用 `codex/<topic>`；小任务至少要记录当前分支与 `HEAD` 提交，保证回溯清晰。
5. **改动后先验证再提交**：完成修改后先跑最小必要验证，再检查 `git diff` / `git status`；提交时只暂存与本任务直接相关的文件，commit message 保持单一目的。
6. **危险 Git 操作必须显式授权**：未经用户明确许可，不执行 `git reset --hard`、`git clean -fd`、`git checkout -- <file>`、强制推送或改写共享历史。
7. **每轮结束汇报版本信息**：说明本次工作基于哪个分支、同步是否成功、是否存在未提交改动，以及本轮变更涉及哪些文件。

**推荐入口**：开发前先运行 `python scripts/git_preflight.py`。该脚本会自动完成状态检查、`fetch`，并且只会在工作区干净时执行 `git pull --rebase`。

---

## 近期对齐更新（2026-04-23）

- **远程目标机文件访问统一化**：新增 `sysdialogue/runtime/target_fs.py`，`file_ops.py`、`backup_restore.py`、`config_validate.py`、`hosts_entries.py`、`cron_jobs.py` 统一通过目标机文件访问层读写，本地/远程语义保持一致。
- **计划任务闭环**：`manage_cron` 现在除了维护 JSON 索引外，还会同步安装 user/system cron；CLI 新增 `--run-scheduled-job <job_id>` 非交互入口。
- **会话上下文与多入口复用**：新增 `ConversationManager`、`ConversationStore`、`runtime_factory.py`、`jobs.py`、`simple_cli.py`，TUI / Simple CLI / Web 共用同一套 controller/runtime 组装方式；TUI 可用 `F2` 从 `~/.sysdialogue/conversations/` 恢复最近对话上下文。
- **Web 控制台最小可用版**：新增 `sysdialogue/web/`，提供 `GET /`、`GET /api/session/{id}/state`、`POST /turn`、`POST /confirm`、`POST /cancel`。
- **TUI 交互对齐**：`Ctrl+C` 改为取消当前执行并触发 workflow rollback 链路，`Ctrl+D` 退出应用，`F2` 打开历史对话；新增 `InputModal`，支持单行/多行输入请求；ReAct 纠偏记录默认折叠到技术详情，不再刷屏打断主流程。
- **安全与规则补齐**：补上 WH025 批量私网探测升级和 HTTP/TLS 重定向入私网拦截，完成 authorized_keys fingerprint 删除、iptables `-D` 删除路径与 `supports_system_cron` 判定修正。
- **测试与开发体验**：新增 `tests/` 与 `requirements-dev.txt`；`--verify` 改为编码安全输出，Windows/GBK 终端不会因 emoji 崩溃。

---

## 技术栈

| 依赖 | 用途 |
|---|---|
| `openai>=1.59.0` | OpenAI-compatible Chat Completions，agentic loop |
| `paramiko>=3.4.0` | SSH 远程执行 |
| `textual>=0.59.0,<1.0` | TUI 界面（1.0+ Static/Visual API 不兼容） |
| `pydantic>=2.6.0` | 数据校验 |
| `pyyaml>=6.0.1` | 工作流 YAML 定义 |
| `jinja2>=3.1.4` | 工作流参数插值 |
| `filelock>=3.13.0` | 动态工具注册表并发安全 |
| `click>=8.1.7` | CLI 入口 |
| `python-dotenv>=1.0.1` | 环境变量 / API Key 管理 |

---

## 项目结构（实际文件树，全部✅完成）

```
sysdialogue/
├── __init__.py
├── agent/                       ✅ 完成
│   ├── __init__.py              # 导出 AgentController / OpenAIChatClient / build_system_prompt
│   ├── controller.py            # AgentController + OpenAIChatClient（agentic loop + 安全门 + 元工具路由）
│   ├── prompt.py                # build_system_prompt()（注入 EnvProfile 脱敏 + 执行模式 + 安全摘要 + 工具清单）
│   ├── planner.py               # PlanningEngine（plan_steps 冻结 + 风险预判定 + FrozenPlan.display_text）
│   └── workflow_engine.py       # WorkflowEngine + ResourceLockManager（YAML 加载 + 5 step type + Jinja2 插值）
├── runtime/                     ✅ 完成
│   ├── capability_probe.py      # EnvProfile TypedDict + CapabilityProbe + EnvProfileSanitizer
│   ├── secure_runner.py         # SafeExecutor 抽象基类 + LocalExecutor（subprocess shell=False）
│   ├── local_adapter.py         # LocalExecutor 重导出
│   └── ssh_adapter.py           # RemoteExecutor（paramiko, known_hosts RejectPolicy, 单命令 exec）
├── tools/                       ✅ 完成（37 个静态工具 + 元工具 + 注册表 + DynTool）
│   ├── base.py                  # ToolResult
│   ├── registry.py              # ToolDef + ToolRegistry + default_registry()（37 工具 JSON Schema 注册）
│   ├── meta_tools.py            # set_execution_mode / propose_dynamic_tool / execute_dynamic_tool / finish_task
│   ├── dynamic_registry.py      # DynamicToolRegistry + StaticRuleMapper（默认启用，三层安全链）
│   ├── system_info.py           # get_system_info, get_disk_usage
│   ├── process_ports.py         # list_processes, kill_process, get_port_status, get_network_info, find_files
│   ├── file_reading.py          # read_log
│   ├── users_groups.py          # create_user, delete_user, modify_user_groups
│   ├── services.py              # manage_service（含 reload/daemon-reload）
│   ├── file_ops.py              # read_file, write_file, delete_path, create_directory, copy_move_path
│   ├── packages.py              # manage_package, get_resource_stats
│   ├── firewall.py              # manage_firewall（ufw/firewalld/iptables 三后端）
│   ├── system_config.py         # get_set_system_config
│   ├── fs_browse.py             # list_directory, stat_path, search_file_content
│   ├── backup_restore.py        # backup_path, replace_in_file（dry_run + diff 预览 + 原子写入）
│   ├── config_validate.py       # validate_config（nginx/sshd/sudoers/json/yaml 等）
│   ├── net_diag.py              # resolve_dns, check_endpoint（ping/tcp/http/tls，WL017 超频）
│   ├── cron_jobs.py             # manage_cron（只允许 tool/workflow 调度）
│   ├── sysctl_ops.py            # manage_sysctl
│   ├── archive_ops.py           # manage_archive（含 B027 条目安全校验）
│   ├── mount_ops.py             # manage_mount
│   ├── containers.py            # manage_container（docker/podman）
│   ├── auth_keys.py             # manage_authorized_keys
│   ├── power_ops.py             # manage_power
│   └── hosts_entries.py         # manage_hosts_entries
├── security/                    ✅ 完成
│   ├── path_policies.py         # 路径保护具名集合 + is_private_host / matches_hosts_protected
│   ├── risk_classifier.py       # classify(tool, args, env_profile) → RiskDecision
│   ├── remote_lockout.py        # assess_tool / assess_cmd → LockoutRisk
│   ├── command_safety.py        # check_command(cmd, env_profile) → SafetyDecision（CS001-CS009）
│   └── approval_rules.py        # ConfirmationRequest 标准化结构
├── audit/                       ✅ 完成
│   ├── trace_store.py           # AuditLog（JSONL，线程安全）
│   └── serializers.py           # export_replay_package + format_audit_table
├── workflows/                   ✅ 完成（10 个 YAML）
│   ├── new_user.yaml                 # v4.1 创建开发者账号
│   ├── disk_cleanup.yaml             # v4.1 磁盘空间分析
│   ├── service_restart.yaml          # v4.1 安全重启服务
│   ├── security_audit.yaml           # v4.1 安全巡查
│   ├── port_scan.yaml                # v4.1 端口扫描
│   ├── file_edit.yaml                # v5.3 安全编辑配置文件
│   ├── safe_config_patch.yaml        # v5.4 + v6 修复（推荐主路径）
│   ├── rollback_config.yaml          # v5.4 配置回滚
│   ├── container_rollout.yaml        # v5.4 + v6 修复（整数插值去引号）
│   └── scheduled_health_check.yaml   # v5.4 + v6 修复
├── ui/                          ✅ 完成（Textual TUI）
│   ├── tui_app.py               # SysDialogueTUI 主界面（五区布局 + worker 线程 + 交互回调）
│   ├── confirm_modal.py         # WARN-HIGH 确认 ModalScreen
│   ├── audit_panel.py           # F3 审计面板（DataTable，最近 50 条）
│   └── env_panel.py             # F4 环境画像面板
└── app/                         ✅ 完成
    ├── config.py                # AppConfig + load_config（.env / 环境变量）
    ├── verify.py                # --verify 自检 / --demo 演示（不调 API）
    └── cli.py                   # Click CLI 入口：python -m sysdialogue.app.cli
```

---

## 运行方式

```bash
# 系统自检（不调 API）— 列工具/工作流/规则 + 检查 API Key
python -m sysdialogue.app.cli --verify

# 演示模式（不调 API）— 本地跑 security_audit 工作流展示引擎
python -m sysdialogue.app.cli --demo

# 轻量命令行模式（需 API Key）
python -m sysdialogue.app.cli --simple

# Web 控制台（需 API Key）
python -m sysdialogue.app.cli --web --host 127.0.0.1 --port 8000

# cron/调度器回调入口
python -m sysdialogue.app.cli --run-scheduled-job <job_id>

# 启动 TUI（需 OPENAI_API_KEY + 模型）
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=...
export OPENAI_MODEL=...
python -m sysdialogue.app.cli

# 远程 SSH 模式
python -m sysdialogue.app.cli --remote user@host:22 --ssh-key ~/.ssh/id_ed25519

# 覆盖 OpenAI-compatible 模型
python -m sysdialogue.app.cli --model your-model-name
```

---

## 六层架构总览

```
UI 层（Textual TUI）
  ↓ 用户输入（自然语言）
对话引擎层（AgentController）
  ↓ messages + tool_definitions
AI 层（OpenAIChatClient / OpenAI-compatible Chat Completions）
  ↓ tool_use 块
安全门层（RiskClassifier + RemoteLockout + CommandSafety + 用户确认 + AuditLog）
  ↓ 已批准 ToolCall
执行适配层（SafeExecutor: Local / Remote）
  ↓ argv
工具层（37 静态工具 + DynTool）
```

**闭环**：自然语言 → 意图解析（由 LLM）→ 环境感知（EnvProfile）→ 风险判定 → 计划生成/工作流匹配 → 预览/确认 → 执行 → 验证 → 回滚或完成 → 自然语言反馈 → 审计记录。

---

## 关键接口约定

### ToolResult（`tools/base.py`）
```python
@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""
    exit_code: int = 0
    cmd_trace: list[str]
```

### RiskDecision（`security/risk_classifier.py`）
```python
@dataclass
class RiskDecision:
    level: str                     # SAFE | WARN-LOW | WARN-HIGH | BLOCK
    rule_ids: list[str]
    reason: str
    requires_confirmation: bool
    rollback_hint: str
```

### SafetyDecision（`security/command_safety.py`）
```python
@dataclass
class SafetyDecision:
    level: str                     # SAFE | WARN-HIGH | BLOCK
    rule_ids: list[str]            # CS001-CS009 / B010 / B015-B017 / WH023
    reason: str
```

### AgentController.run_turn 调用流程（ReAct 闭环）
```
用户输入 → ReActRunner.run
  → task_started
  → 计算动态迭代预算（普通聊天约 20，一般运维约 80，复杂变更/workflow/DynTool 约 140；受 SYSDIALOGUE_MAX_ITER 硬上限约束）
  → messages_create
  → 收到 tool_use / finish_task
  → 元工具拦截（set_execution_mode=workflow → WorkflowEngine.run；mode=plan → PlanningEngine.freeze）
  → 非元工具：RiskClassifier.classify（叠加 remote_lockout）
    → audit.log_decision(level)
    → BLOCK → 返回 is_error=True
    → WARN-HIGH → confirm_callback → 拒绝 → audit.log_decision("user_cancelled")
    → 执行：registry.call(name, args, executor, session_counters, env_profile)
    → audit.log_command(cmd_trace, exit_code, output_preview)
    → 返回 tool_result
  → 模型基于 tool_result 继续 observe/act/verify
  → finish_task 通过完成门校验
  → task_finished / task_failed
  → 返回最终用户摘要
```

完成门要点：
- 运维/诊断/远程目标机任务若没有环境观察，不能 `completed`。
- 失败或被安全门拦截的变更工具不会计入成功变更；必须后续有成功变更和后置验证，才能 `completed`。
- `safe_config_patch`、`service_restart`、`container_rollout`、`rollback_config`、`file_edit` 这类内建校验 workflow 成功后，可作为变更后的验证证据。
- 取消发生在多工具响应中时，未执行的 tool call 也会写入取消型 `tool_result`，避免下一轮 OpenAI tool message 配对损坏。
- 模型没有按 `tool_calls` / `finish_task` 收口时会触发 ReAct 纠偏；纠偏事件默认只进技术详情，只有最终协议失败时才向用户显示友好错误。

### WorkflowEngine 执行约定
- YAML 加载两阶段：占位符 skeleton 解析 `parameters` → regex 参数预渲染（integer 去引号场景）→ YAML 最终解析 → 每步 Jinja2 注入 step 引用
- 步骤类型：`tool_call | confirm | approval | display | input`
- `condition=false` → 跳过，**视为已成功完成**（后续 `depends_on` 正常执行）
- `depends_on` 中有 step status=`failed` → 当前步骤级联 `failed`（区别 skipped）
- `on_fail: rollback` → 触发 rollback 段；rollback 内再失败 → `rollback_failed` 终态
- `lock_scope: "file:…/service:…/user:…/cron:…"` → ResourceLockManager 申请锁，超时 30s 失败

---

## 已知限制与设计取舍（真机验证时注意）

> 2026-04-23 更正：下面这组清单是早期交接时记录的“待补缺口”，其中远程 backup/hosts、真实 cron 安装、authorized_keys fingerprint 删除、WH025、iptables `-D` 路径都已经完成。后续判断请以本节新增说明和仓库当前实现为准，不要再把下面旧条目当成现状。
>
> 当前真实限制主要是：
> 1. Linux 真机端到端验收仍未完成，尤其是 `safe_config_patch` / `rollback_config` / `container_rollout` / `scheduled_health_check`。
> 2. 远程 `cron.d` 权限、不同发行版行为、SSH 靶机锁门规则仍需真机复核。
> 3. Web 会话目前是进程内存态，没有单独数据库持久化。
> 4. Windows 本地 `--demo` 按设计返回 unsupported，需要在 Linux 本机或 `--remote` 到 Linux 主机上跑完整演示。
> 5. `kill_process` 依然采用保守 WARN-HIGH 判定。

以下项为**当前实现的已知缺口**，不影响核心通路但需要在真机环境或演示时留意：

1. **`backup_restore.py` 仅支持本地模式**：`backup_path` 直接操作本机 `~/.sysdialogue/backups/`，远程 SSH 模式下备份的是**控制端**而非目标机文件。若需远程备份，应改为通过 executor 执行 `tar czf` 并落在目标机。
2. **`hosts_entries.py` 直接写本机 `/etc/hosts`**：同上，远程模式下无效。需通过 executor `write_file`/`sed` 间接操作。
3. **`manage_cron` 只写 JSON 索引**：当前 `~/.sysdialogue/cron_index.json` 是内部调度表，并未翻译为系统 crontab 条目。完整实现需要在 WorkflowEngine 层把 `job_target` 翻译为 `/etc/cron.d/` 条目（调用 `sysdialogue --run-scheduled-job <id>`）。
4. **`auth_keys.py` fingerprint 删除简化**：`remove` 目前只按 public_key 内容匹配；fingerprint 匹配路径未实现 `ssh-keygen -l` 比对。
5. **`risk_classifier._classify_kill_process`**：安全门层无法获取进程属主，因此所有 `kill_process`（非 PID 1）统一 WARN-HIGH。设计意图的 WH001 "非当前用户进程" 精化未实现。
6. **`firewall.py` iptables delete**：当前 delete 与 deny 共用逻辑。iptables 真正删除（`-D`）需要行号或完整规则匹配，未实现。
7. **session_counters 依赖调用方**：`resolve_dns` / `check_endpoint` 的 WL017 超频计数靠 `AgentController._session_counters` 注入；脱离 controller 直接调用 `registry.call` 若不传 session_counters 计数失效。
8. **WH025 私网批量/重定向升级未实现**：WL016 单次私网探测已覆盖；批量扫描（同一 /24 超过 10 次）与 HTTP 重定向到私网的 WH025 升级尚未接入。

---

## 验证状态（本轮 mock 测试总计 39+ 项全部通过）

> 2026-04-23 最新验证结论：
> - 新增/修改的 controller/runtime/tools/ui/web/tests 已全部 `py_compile` 通过。
> - `python -m sysdialogue.app.cli --verify` 在当前 Windows/GBK 终端可稳定输出，不再因 emoji 崩溃；未配置 OpenAI key/model 时会清晰返回配置 warning。
> - `python -m sysdialogue.app.cli --demo` 在当前 Windows 本地会明确返回 unsupported host，而不是 workflow 失败。
> - `python -c "from sysdialogue.web.app import create_web_app ..."` 已确认 Web app 可构建并注册路由。
> - `pytest` 当前 7 项测试全部通过，覆盖 WH025 升级、私网重定向拦截、authorized_keys fingerprint 删除、cron 安装、iptables delete、CapabilityProbe cron 能力判定。
>
> 下方表格是历史 mock 验证记录，可保留参考，但不应替代上面的最新结论。

| 层 | 验证内容 |
|---|---|
| Task 10 AgentController | SAFE/BLOCK/WARN-HIGH 三类 decision、元工具拦截、未注册工具、SystemPrompt 内容（8 项） |
| Task 11 WorkflowEngine | 10 YAML 加载、整数插值、confirm 状态机、rollback 触发、plan 冻结、controller 路由（10 项） |
| Task 12 CommandSafety + DynTool | CS001-CS009 各规则、远程锁门叠加、DynTool 注册/执行/拒绝/上限（16 项） |
| Task 13 TUI | 主界面挂载、F3/F4 切换面板、Input submit 触发 agentic loop、Ctrl+L 清屏（5 项） |
| CLI `--verify` / `--demo` | 手动运行通过：列 37 工具 / 10 YAML / 5 段自检；demo 能跑 `security_audit` |

模块 import 全绿（48 个模块 0 失败）。

---

## Git 提交历史

```
59afdbc feat(app): CLI + --verify/--demo + 远程 SSH 模式 + 配置加载
581db34 feat(ui): Textual TUI — 主界面 + 确认弹窗 + 审计/环境面板
6441e1b feat(security): CommandSafetyChecker CS001-CS009 + DynamicToolRegistry
bb60ad9 feat(workflows): 10 个内置工作流 YAML（v4.1 + v5.3 + v5.4 + v6 修复）
7a7ed12 feat(agent): PlanningEngine + WorkflowEngine + set_execution_mode 路由
c27741e feat(agent): SystemPromptBuilder + initial LLM client + AgentController
91038d0 feat(tools): ToolRegistry + 37 工具 Schema 注册表 + 元工具 Schema
42bd75c fix(security): 修补 WL016 / B024 / firewall 规则三项缺陷
a0ea42d docs: 更新 CLAUDE.md 为完整交接文档
535ea33 feat(P1/P2): v5.4 剩余工具 9 个 — 系统维护 + 现代运维
9527ab1 feat(P1): v5.3 工具 9 个 — file_ops + packages + firewall + system_config
c21e15a feat(P0): v5.4 核心工具 — fs_browse + backup_restore + config_validate
054773f feat(P0): v4.1 核心工具 10 个
0d08c70 feat(P0): AuditLog — JSONL 审计日志 + 复现包导出
2c73882 feat(P0): 执行适配层 — SafeExecutor + LocalExecutor + RemoteExecutor
332051e feat(P0): 安全规则核心 — path_policies + RiskClassifier + RemoteLockoutChecker
8856ba4 feat(P0): EnvProfile + CapabilityProbe
d3c29a5 feat: 项目脚手架 — pyproject.toml, requirements, package structure
```

---

## 下一步建议

> 2026-04-23 更正：当前不再是“设计内已无空缺”的纯演示阶段，代码层面对齐工作已完成主线，但仍需要 Linux 真机验收和少量展示层补强。以下列表继续作为后续推进建议。

已无**设计内**的功能空缺。剩余工作都是真机验证与演示层面：

1. **真机 Linux 端到端验证**：
   - Ubuntu 22.04 本地 + 真 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
   - 最重要：跑通 `safe_config_patch` nginx 端口修改场景（claudeplan6.md §13 场景 4）
   - 跑 `rollback_config`（场景 5）、`container_rollout`（场景 7）
   - 远程 SSH 模式在一台靶机上验证 B010/B015-B017 真的会 BLOCK
2. **演示材料**：录屏或截图 10 个场景（claudeplan6.md §13 列表），覆盖 BLOCK 拒绝、WARN-HIGH 确认、回滚链、复现包导出。
3. **可选修补**（优先级降序）：
   - 修已知限制 #1 #2（远程模式下 backup/hosts 通过 executor 实现）
   - 修 #3（manage_cron 真实 crontab 写入）
   - 实现 WH025（批量私网探测升级）
   - 加 OutputSanitizer 统一脱敏（§P2）

**真机验证建议脚本**：
```bash
# 1. 自检
python -m sysdialogue.app.cli --verify

# 2. 演示工作流（无需 API Key）
python -m sysdialogue.app.cli --demo

# 3. 完整 TUI（需 API Key）
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=...
export OPENAI_MODEL=...
python -m sysdialogue.app.cli
# 输入示例：
#   - "查看一下系统信息"                                    → SAFE 工具，自动执行
#   - "重启 nginx 服务"                                      → WARN-HIGH，弹窗确认
#   - "把 /etc/nginx/nginx.conf 里的 listen 80 改成 8080"     → 匹配 safe_config_patch
#   - "删除 root 用户"                                        → BLOCK 直接拒绝
```

---

## 演示场景清单（验收对照）

参见 `framework/claudeplan6.md §13`，10 个场景均覆盖：

| 场景 | 目标 | 主要工具/工作流 |
|---|---|---|
| 1 | 查看系统信息 | `get_system_info` |
| 2 | 端口监听排查 | `get_port_status` |
| 3 | **危险请求拒绝**（BLOCK） | `delete_user(root)`、`kill_process(pid=1)` |
| 4 | **nginx 端口修改完整闭环** | `safe_config_patch.yaml` |
| 5 | **配置损坏自动回滚** | `rollback_config.yaml` |
| 6 | 安全巡查 | `security_audit.yaml` |
| 7 | **容器发布 + 连通性验证** | `container_rollout.yaml` |
| 8 | 定时健康巡检 | `scheduled_health_check.yaml` |
| 9 | 用户创建 | `new_user.yaml` |
| 10 | 磁盘分析 | `disk_cleanup.yaml` |
