# AI Hackathon 2026 — OS Intelligent Agent 实现方案

## Context

超聚变数字技术 AI Hackathon 2026 初赛题目：开发一个可真实部署的 AI Agent，作为 Linux 服务器系统管理的智能界面，用户通过自然语言替代命令行进行操作。评分：客观 70% + 主观 30%。

文档路径：`D:\match\AI_Hackathon_2026.pdf`

本方案借鉴 **Warp 终端**（warpdotdev/Warp）的架构设计——目前最成熟的 AI Agent 终端产品，其 Block UI 模型、三级权限系统、Planning 模式和 Workflow 系统直接对应比赛的各项评分维度。

---

## 技术栈

| 层级 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 最快迭代，AI SDK 生态最好 |
| AI 模型 | Claude claude-sonnet-4-6 via Anthropic SDK | 工具调用最可靠，结构化输出稳定 |
| 主界面 | Textual (TUI) | SSH 终端可用，视觉专业，无需浏览器 |
| 备用界面 | Rich + prompt-toolkit | `--simple` 降级模式 |
| OS 执行 | subprocess + psutil | 标准库，无第三方守护进程 |
| 配置/状态 | pydantic + TOML | 类型安全，易序列化 |
| 语音（加分） | SpeechRecognition + Whisper | 可选，演示用 |

---

## 架构设计

```
用户界面层 (TUI / CLI / Voice)
        │ UserMessage
对话引擎层 (ConversationManager)
  - 维护滑动窗口历史（最多20轮）
  - 每轮注入实时系统快照（主机名、OS版本、磁盘概览）
        │ messages[] + tool_definitions[]
Claude API层 (agentic loop, 最多10次迭代, 流式输出)
        │ tool_use blocks
安全门 (RiskClassifier) — 规则引擎，不调用LLM
  SAFE → 自动执行   WARN → 弹窗确认   BLOCK → 拒绝+解释
        │ approved ToolCall
执行层 (SafeExecutor)
  - 超时限制（默认15s/命令）
  - 输出截断（50KB上限）
  - 追加到审计日志
        │ ToolResult → 回到Claude API层
```

**核心架构决策：**
1. Claude **不生成原始 shell 字符串**，所有 OS 操作建模为带明确参数的工具定义
2. 安全门操作结构化 ToolCall 对象，不解析 shell 语法
3. 应用程序自控 agentic loop（不托管给 SDK），可在任意工具调用前插入安全确认步骤
4. BLOCK 时返回合成 tool_result（不抛异常），让 Claude 优雅地向用户解释原因

---

## 项目结构

```
sysdialogue/
├── __main__.py              # 入口，参数解析，启动模式
├── config.py                # pydantic 配置，TOML 加载
├── conversation.py          # 对话历史管理，Session 持久化
├── claude_client.py         # Anthropic SDK 封装，流式，agentic loop
│
├── security/
│   ├── risk_classifier.py   # 规则引擎：SAFE/WARN/BLOCK 分类
│   ├── risk_rules.py        # 声明式规则定义（策略文件）
│   └── audit_log.py         # 追加式操作日志（JSON Lines）
│
├── tools/
│   ├── __init__.py          # 工具注册表，传给 Claude 的 tool_definitions
│   ├── base.py              # ToolCall, ToolResult dataclasses
│   ├── disk.py              # df, du, lsblk
│   ├── files.py             # find, stat
│   ├── processes.py         # psutil（跨发行版，不依赖 shell 工具）
│   ├── ports.py             # ss / /proc/net
│   └── users.py             # useradd, userdel（sudo 受限）
│
├── executor.py              # SafeExecutor（subprocess 封装）
│
├── ui/
│   ├── tui_app.py           # Textual App
│   ├── widgets.py           # ChatHistory, StatusPanel, ConfirmModal
│   ├── simple_cli.py        # 降级纯文本模式
│   └── voice.py             # 可选语音输入
│
└── prompts/
    └── system_prompt.py     # 构建带实时 OS 上下文的系统提示词
```

---

## 工具参数设计（窄接口原则）

**禁止：** `run_shell(command: str)` — 无法审计，无法分类风险  
**正确：** `find_files(search_path: str, pattern: str, max_depth: int)`

每个工具参数都有明确类型约束，路径参数在执行前校验白名单/黑名单。

---

## 安全风险分类规则

### BLOCK（无条件拒绝）
- 删除 `/etc/passwd`, `/etc/shadow`, `/boot/*`, `/lib/systemd/*`
- kill PID 1（init/systemd）
- 修改 root 账户
- 创建 uid=0 用户
- 路径包含 `..` 穿越
- 路径为 `/` 或空字符串

### WARN（需要用户确认，弹窗显示风险说明）
- 删除 `/etc/` 下任意文件
- kill 系统进程（非当前用户所有）
- `delete_user` 任何用户
- `find /` 全盘搜索
- 修改用户权限
- 批量操作超过10个用户

### SAFE（自动执行，仅记录审计日志）
- 所有只读操作（disk usage, list processes, port status, file stat, system info）
- 有限范围的文件搜索（深度 ≤ 3, 非根目录）

---

## 实施阶段

### Phase 0（0-2h）：脚手架
- `pyproject.toml` + 依赖
- `simple_cli.py` 基础输入循环
- `claude_client.py` agentic loop 骨架（无工具）
- 验证：Claude API 连通，能对话

### Phase 1（2-10h）：核心 OS 工具
优先级：磁盘 → 进程 → 端口 → 文件搜索 → 用户管理  
每个工具写完即可直接测试，不依赖 Claude

### Phase 2（8-16h，与 Phase 1 并行）：安全门
- `risk_rules.py` 声明式规则表
- `UserConfirmationModal`（需键入 "confirm"，不是 y/n）
- `audit_log.py` JSON Lines 日志
- 测试："删除根文件系统" → BLOCK；"查看磁盘" → 无提示执行

### Phase 3（14-24h）：Textual TUI
```
┌─ SysDialogue ──────── hostname ── user@host ── uptime ─┐
│ 对话历史区域          │ 系统状态面板（每5s刷新）       │
│                       │ CPU/MEM/DISK/Load              │
│                       │ 最近操作记录（含审计状态）     │
├───────────────────────────────────────────────────────┤
│ > 输入框                                              │
└─ F1:帮助  F2:历史  F3:审计日志  Ctrl+C:退出 ──────────┘
```

### Phase 4（22-36h）：多轮上下文 + 润色
- 代词解析（"kill it" → 解析到上一轮讨论的 PID）
- Session 持久化 `--resume`
- 流式输出（边生成边显示）
- 优雅错误恢复

### Phase 5（34-44h，如时间允许）：语音输入
- `--voice` 模式，长按空格录音，转录后显示在输入框可编辑

### Phase 6（40-48h）：演示准备
- `install.sh` 一键安装脚本
- Docker 容器（openEuler base）可复现环境
- 录制演示视频
- `DESIGN.md`, `SECURITY.md`, `TESTING.md`

---

## 演示视频脚本（5-8分钟）

1. **0-1min Hook**：新 SSH 会话 → 一行安装命令 → TUI 在3秒内启动
2. **1-2min 基础能力**（客观分）：磁盘/文件搜索/端口/用户 4 个场景
3. **2-3min 多轮上下文**：5 轮连续对话，代词解析，kill 命令
4. **3-4min 安全演示**（主观分）：删 sudoers → BLOCK；删用户 → WARN 确认；创建 uid=0 → BLOCK；审计日志实时更新
5. **4-5min 多步骤编排**："为 Alice 创建开发者账号并配置 sudo 权限" → 自动拆解为多个工具调用，每步确认
6. **5-6min（可选）语音**：语音输入演示
7. **6-7min 工程展示**：`risk_rules.py`（可读策略）、窄接口工具定义、审计日志格式

---

## 依赖包

```
anthropic>=0.40.0
textual>=1.0.0
rich>=13.0.0
psutil>=6.0.0
pydantic>=2.0.0
SpeechRecognition>=3.10   # 可选
pyaudio>=0.2.14            # 可选
```

---

## 验证方案

1. **工具单元测试**：`python -m sysdialogue --test-tools` 逐个调用工具，打印结果
2. **安全规则测试**：脚本自动验证所有 BLOCK/WARN 规则触发条件
3. **端到端演示序列**：5个预设场景 + `demo_reset.sh` 重置环境
4. **真实 Linux 服务器**：在 openEuler 或 Ubuntu Server 上完整运行（不用虚拟机演示）
5. **审计日志验证**：确认每次操作（含 BLOCK）都有记录

---

---

## Warp 设计借鉴

### 1. Block-Based UI 模型（借鉴 Warp 核心交互）

Warp 将每次命令和输出封装为独立的"Block"，而非线性文本流。我们同样在 TUI 中实现：

```
┌─────────────────────────────────────────────────────────┐
│ Block #3  ✓ SUCCESS  0.3s  2026-04-22 14:32            │  ← Block 头
│ > 查看 /var 目录的磁盘使用情况                           │  ← 用户输入
│ ─────────────────────────────────────────────────────── │
│ /var 目录占用 12.4 GB，主要来源：                        │  ← Agent 输出
│   /var/log    8.1 GB (65%)                               │
│   /var/cache  3.2 GB (26%)                               │
│ [工具调用: get_disk_usage("/var") → 成功]                │  ← 工具记录
└─────────────────────────────────────────────────────────┘
```

每个 Block 包含：
- 状态标识（✓ SUCCESS / ⚠ WARNED / ✗ BLOCKED / ⟳ RUNNING）
- 耗时和时间戳
- 工具调用摘要（可折叠展开）
- 颜色编码：成功=绿色侧边栏，警告=黄色，阻止=红色

### 2. 三级权限模式（借鉴 Warp Strict/Balanced/Permissive）

Warp 实现了 Strict / Balanced / Permissive 三档权限，我们直接对应：

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `--strict` | 每个操作都需确认（包括只读） | 生产服务器，演示用 |
| `--balanced`（默认） | SAFE自动执行，WARN需确认，BLOCK拒绝 | 日常管理 |
| `--permissive` | 仅BLOCK需确认 | 受信任管理员 |

同时支持配置文件中的**个人 Allowlist**（用户自定义哪些操作跳过确认）：
```toml
# ~/.sysdialogue/config.toml
[security]
mode = "balanced"
allowlist = ["get_disk_usage", "list_processes", "get_port_status"]
denylist_patterns = ["rm -rf", "chmod 777"]
```

### 3. Planning 模式（借鉴 Warp /plan 命令）

对于复杂多步骤操作，Agent 先展示执行计划，用户确认后再逐步执行：

```
用户: 为新员工 bob 创建账号，配置好开发环境权限

Agent: 我将执行以下计划，请确认：
  Step 1 [WARN] 创建用户账号: useradd -m bob
  Step 2 [WARN] 设置初始密码: passwd bob
  Step 3 [WARN] 加入开发组: usermod -aG developers bob
  Step 4 [SAFE] 验证账号: id bob

输入 'go' 开始执行，或修改后再执行 >
```

Planning 模式触发条件：
- 用户意图涉及 3 步以上操作
- 用户输入包含"帮我"、"配置"、"批量"等关键词
- 任一步骤包含 WARN 级操作

### 4. Workflow 模板系统（借鉴 Warp Workflows YAML）

内置常用运维任务模板，可用自然语言触发：

```yaml
# workflows/new_user.yaml
name: "创建开发者账号"
trigger: ["新建用户", "创建账号", "添加用户 {{username}}"]
steps:
  - tool: create_user
    args: {username: "{{username}}", create_home: true}
    risk: WARN
  - tool: add_to_group
    args: {username: "{{username}}", group: "{{group|developers}}"}
    risk: WARN
  - tool: verify_user
    args: {username: "{{username}}"}
    risk: SAFE
```

内置模板：`new_user.yaml`, `disk_cleanup.yaml`, `security_audit.yaml`, `port_scan.yaml`

### 5. 自我纠错（借鉴 Warp Self-Correction）

工具调用失败时，Agent 自动分析原因并尝试替代方案：

```
执行: ss -tlnp （失败，命令不存在）
→ 自动重试: netstat -tlnp
→ 自动重试: cat /proc/net/tcp（最终回退）
→ 向用户解释: "ss 命令不可用，已使用 /proc/net/tcp 替代"
```

每个工具定义包含 `fallback_tools` 列表，处理 busybox 环境或缺失工具的情况。

### 6. MCP 风格工具接口（借鉴 Warp MCP 集成）

工具定义遵循 MCP 标准格式，便于未来扩展：

```python
# 所有工具均可通过 stdio 协议独立运行，支持 MCP 客户端调用
# 未来可接入 Warp 等支持 MCP 的终端
```

---

## 竞争优势

- **Block UI** — 比线性聊天更清晰，每步操作有明确的状态和时间记录（对应主观分：UX）
- **三级权限模式** — 可配置的安全粒度，而非一刀切（对应主观分：工程质量）
- **Planning 模式** — 复杂任务先规划后执行，减少意外（对应客观分：操作闭合性）
- **Workflow 模板** — 常见运维场景可复用，展示创新价值（对应主观分：创新性）
- **自我纠错** — 跨发行版兼容，降级处理优雅（对应客观分：稳定性）
- **声明式安全规则引擎** — 不依赖 LLM 判断安全性，可审计（对应主观分：工程质量）
- **窄接口工具参数** — 防止提示注入，安全门精确分类
- **审计日志** — 生产就绪，体现专业性
