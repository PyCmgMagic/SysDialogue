---                                      
                                 
  # Nexus — 技术实现方案 (Tech Implementation Plan)
                                                                                                             
  > AI Hackathon 2026 · 操作系统智能代理                                                                   
  > 本文档的 feature 基准：`ClaudeFeaturePlan.md`（唯一功能事实来源）                                        
  > 技术参考：`ClaudePlan4.1.md`、`ClaudePlan5.1.md`、`ClaudePlan5.2.md`                                     
  > 文档目的：把 FeaturePlan 中所有 feature 翻译为可实现、可验证的模块契约、数据结构与代码边界               
                                                                                                             
  ---                                                                                                      
                                                                                                           
  ## 零、文档定位                                                                                            
                                                                                                             
  FeaturePlan 描述"做什么、为什么"，本文档描述"怎么实现、契约是什么"。当二者出现矛盾时以 FeaturePlan 为准；本文档被视为落地细节，可修订但不可扩张功能面。                                                       
                                                                                                             
  **本方案的阅读顺序**：                                                                                     
                                                                                                             
  1. 第一、二章给出技术栈、目录、整体架构——建立脑图。                                                        
  2. **第三章集中、完整地定义所有跨模块契约**；这是整份方案的"事实基础"，后续每个模块都只引用、不重复。      
  3. 第四章描述各模块实现逻辑（代码片段仅作关键路径示意）。                                                
  4. 第五至八章是面向评审可直接对照的清单：工具面、规则表、verifier 细节、审计 schema。                      
  5. 第九章起为数据流、测试矩阵、里程碑、附录。                                                            
                                                                                                             
  **三条硬不变式**（贯穿全文，违反即阻断 PR）：                                                      
                                         
  1. **安全门不可绕过**：`SafeExecutor.execute()` 入口强制断言 `risk_result is not None`；除元工具外，任一`ToolCall` 到达执行层前必须完成 `RiskClassifier` 或 `CommandVerifier` 定级。                             
  2. **命令不回流对话**：`command_trace`/`argv` 从 `ToolResult` 到 `AuditLog` 是单向路径，不进 `messages`、不进 UI 主区。                                                                                 
  3. **审计先于告知**：`AuditLog.append()` 必须 `fsync` 完成才允许 UI 展示"已执行"；写审计失败整轮报错终止。 
                                                                                                             
  ---                                                                                                        
                                                                                                             
  ## 一、技术栈与项目布局                                                                                    
                                                                                                           
  ### 1.1 语言与核心依赖                                                                                     
                                                                                                             
  | 依赖 | 版本 | 用途 |                                                                                   
  |---|---|---|                                                                                              
  | Python | 3.11+ | 主语言；依赖 `TypedDict`、`match/case`、结构化异常组 |                                
  | anthropic | ≥ 0.34 | Claude API SDK；主模型 + verifier 模型 |                                            
  | textual | ≥ 0.60 | TUI 框架（对话主区 / 审计面板 / 环境面板 / 确认弹窗） |                               
  | paramiko | ≥ 3.4 | SSH exec + known_hosts + SHA256 指纹 |                                              
  | filelock | ≥ 3.15 | `dynamic_tools.json` 并发写保护 |                                                    
  | PyYAML | ≥ 6.0 | 演示脚本解析 |                                                                        
  | tomllib | stdlib | `config.toml` 解析 |                                                                  
  | tiktoken | ≥ 0.7 | Token 估算（英文基线），中文字符用修正系数 |                                        
  | rich | ≥ 13.7 | 日志与进度条 |                                                                           
  | pytest / pytest-asyncio | ≥ 8 | 测试框架 |                                                               
  | SpeechRecognition / faster-whisper | 可选 | 语音输入两套实现，运行时二选一 |                             
                                                                                                             
  默认部署：单一 Python 包，`pipx install nexus-agent` 或源码 `pip install -e .`，不依赖外部服务。           
                                                                                                             
  ### 1.2 与 v5.2 的不可协商对齐修订                                                                         
                                                                                                             
  | 修订项 | v5.2 状态 | 本方案（对齐 FeaturePlan） |                                                        
  |---|---|---|                                                                                              
  | Workflow 模板 | 保留 5 个 YAML 模板 + WorkflowEngine | **整体删除**，连续任务统一走计划模式 |            
  | 计划形态 | `PlanStep[]` 冻结，修改需重生成 | **动态状态机**，AI 可通过元工具 `add_step / remove_step / modify_step` 在执行过程中演进 |                                                                            
  | 执行模式元工具 | `mode ∈ {direct, plan, workflow}` | `mode ∈ {direct, plan}`，并扩展 `action` 字段承担计划修改 |                                                                                         
  | Shell 通道 | 不存在 | 新增 `execute_shell` 工具（默认不可见），受两阶段 verifier 强制把关 |            
  | Verifier | CommandSafetyChecker 纯规则 | **规则 + LLM 两阶段**，下限 WARN-LOW，不确定升级 |              
  | 自检入口 | `--test-tools` + `--verify-demo` | 收敛为 `--verify` + 新增 `--demo` |                      
  | 环境画像刷新 | 会话启动一次 | 启动全量采集 + **事件驱动字段级增量刷新** |                                
                                                                                                             
  其它均继承 v5.2：21 OS 工具 + 元工具、BLOCK/WARN 规则表、CapabilityProbe、LocalExecutor/RemoteExecutor 合约、OutputSanitizer、RemoteLockoutChecker 共享判定器、`dynamic_tools.json` 原子写。                      
                                                                                                             
  ### 1.3 代码目录结构                                                                               
                                                                                                             
  ```text                                                                                                    
  nexus/                                                                                                     
  ├── __init__.py                                                                                            
  ├── __main__.py                 # python -m nexus 入口                                                     
  ├── cli/                                                                                                   
  │   ├── main.py                 # 主 TUI 入口                                                              
  │   ├── simple.py               # --simple 无 TUI 入口                                                   
  │   ├── verify.py               # --verify 自检                                                          
  │   └── demo.py                 # --demo 演示                                                            
  ├── core/                                                                                                
  │   ├── models.py               # 共享 TypedDict / dataclass（第三章 3.1）
  │   ├── session.py              # SessionState / TurnBundle 持久化                                         
  │   ├── ids.py                  # session_id / plan_id / request_id / env_profile_id
  │   └── errors.py               # 统一异常类型                                                             
  ├── ai/                                                                                            
  │   ├── client.py               # ClaudeClient (agentic loop)                                              
  │   ├── prompt_builder.py       # SystemPromptBuilder                                                    
  │   └── prompt_registry.py      # Prompt 版本化                                                            
  ├── conversation/                                                                                        
  │   ├── manager.py              # ConversationManager                                                      
  │   ├── turn_bundle.py          # TurnBundle 持久化                                                        
  │   ├── compactor.py            # Compact 摘要                                                             
  │   └── budget.py               # Token 预算 + 中文修正                                                    
  ├── tools/                                                                                                 
  │   ├── base.py                 # Tool Protocol + ExecutionContext                                         
  │   ├── registry.py             # ToolRegistry（静态 + 动态 + 元）                                         
  │   ├── meta.py                 # set_execution_mode / propose_dynamic_tool                                
  │   ├── shell.py                # execute_shell 通道                                                       
  │   ├── dyntool.py              # DynamicToolRegistry / StaticRuleMapper / validate_proposal               
  │   └── os/                                                                                                
  │       ├── disk.py file_ops.py process.py port.py user.py system.py                                     
  │       ├── service.py network.py log.py packages.py firewall.py                                           
  │       ├── monitoring.py system_config.py                                                               
  │       └── __init__.py         # TOOLS = [...]                                                            
  ├── security/                                                                                              
  │   ├── classifier.py           # RiskClassifier                                                           
  │   ├── rules.py                # BLOCK / WARN-HIGH / WARN-LOW 规则注册表                                  
  │   ├── path_sets.py            # PATH_PARAMETERS / SENSITIVE_*_PATHS / CRITICAL_SERVICES                  
  │   ├── command_safety.py       # CommandSafetyChecker (CS001-CS015)                                       
  │   ├── remote_lockout.py       # RemoteLockoutChecker（B010/B015-B017 共享）                              
  │   ├── verifier.py             # CommandVerifier（规则 + LLM 两阶段）                                     
  │   └── sanitizer.py            # OutputSanitizer                                                          
  ├── execution/                                                                                             
  │   ├── probe.py                # CapabilityProbe                                                          
  │   ├── adapter.py              # ExecutorAdapter Protocol + CommandPlanner                                
  │   ├── local.py                # LocalExecutor                                                            
  │   ├── remote.py               # RemoteExecutor（known_hosts + RejectPolicy）                             
  │   ├── safe_executor.py        # SafeExecutor（timeout / 截断 / 归一化 / 重试）                           
  │   └── refresh.py              # EnvProfile 事件驱动刷新                                                  
  ├── planning/                                                                                              
  │   ├── engine.py               # PlanningEngine                                                           
  │   ├── plan.py                 # Plan / PlanStep / PlanModification                                       
  │   └── policy.py               # ModificationPolicy（default / strict）                                   
  ├── audit/                                                                                               
  │   ├── log.py                  # AuditLog 落盘（JSONL + fsync）                                           
  │   ├── record.py               # AuditRecord / DecisionTrace / CommandTraceItem / ResultBlock             
  │   ├── index.py                # SQLite 索引                                                              
  │   └── export.py               # --export-audit / --export-repro-pack                                     
  ├── ui/                                                                                                    
  │   ├── app.py bridge.py        # Textual App + UIBridge 实现                                              
  │   ├── dialog.py input.py      # 对话主区 / 命令输入                                                    
  │   ├── status_panel.py         # 顶部状态栏                                                               
  │   ├── confirm.py              # 风险确认弹窗                                                           
  │   ├── fingerprint.py          # 远程首次连接指纹确认                                                     
  │   ├── audit_panel.py          # F3 审计面板                                                              
  │   ├── env_panel.py            # F4 环境画像面板                                                          
  │   └── dyntool_proposal.py     # DynTool 提案弹窗                                                         
  ├── voice/                                                                                                 
  │   ├── input.py                # VoiceInput 主入口                                                        
  │   ├── local_whisper.py        # 本地识别                                                                 
  │   └── cloud.py                # 联网识别                                                                 
  ├── config/                                                                                                
  │   ├── loader.py schema.py paths.py                                                                       
  └── tests/                                                                                                 
      ├── unit/ integration/ chaos/ fixtures/                                                                
  ```                                                                                                        
                                                                                                           
  ### 1.4 用户侧落盘目录                                                                                     
                                                                                                             
  ```text                                                                                                    
  ~/.nexus/                                                                                                  
  ├── config.toml              # 主配置（权限 ≤ 0644，警告）                                                 
  ├── .env                     # API Key 等敏感值（权限 0600，启动硬校验）                           
  ├── known_hosts              # Nexus 专用 SSH known_hosts，不与系统 ~/.ssh 共用                            
  ├── dynamic_tools.json       # 动态工具注册表（权限 0600）                                               
  ├── prompts/                 # 已发布 prompt 版本，hash 命名                                               
  │   └── nexus-v1-a3f2.txt                                                                          
  └── sessions/                                                                                              
      └── sess_01H.../                                                                                     
          ├── meta.json        # session 元信息                                                            
          ├── env_profile.json # 本会话采集的环境画像                                                        
          ├── audit.jsonl      # 本会话审计日志（append-only）                                               
          ├── audit.db         # 审计 SQLite 索引（由 audit.jsonl 推导）                                     
          ├── plans/plan_xxx.json                                                                            
          └── turns/           # TurnBundle 快照 / compact 摘要                                              
  ```                                                                                                        
                                                                                                             
  ---                                                                                                        
                                                                                                             
  ## 二、整体架构                                                                                            
                                                                                                             
  ### 2.1 分层与依赖方向                                                                                     
                                                                                                             
  层次严格单向，下层不得反向调用上层：                                                                       
                                                                                                             
  ```text                                                                                                    
  [UI / Voice]                                                                                             
      │ 输入文本 + input_source 标签                                                                       
  [ConversationManager]──┐                                                                                 
      │                  │ 维护 TurnBundle                                                                   
  [ClaudeClient]─────────┤ agentic loop  
      │                  │ 发出 ToolCall                                                                     
  [Security Gate]        │ RiskClassifier / CommandVerifier / UserConfirmation                       
      │                  │ 发出 Approved ToolCall                                                            
  [PlanningEngine]───────┤ 仅在 plan 模式嵌入，驱动步骤                                                    
      │                                                                                                    
  [ExecutorAdapter]      │ CapabilityProbe + CommandPlanner                                                  
      │                                                                                                    
  [Tool Layer / Shell / DynTool]                                                                             
      │ 经 SafeExecutor + OutputSanitizer                                                                    
  [AuditLog]  ← 横切：所有模块写入，单写多读                                                                 
  ```                                                                                                        
                                                                                                             
  ### 2.2 硬边界的技术保障                                                                                   
                                                                                                             
  - **SafeExecutor 是执行层唯一入口**：`Executor.run()` 的唯一调用方是 `SafeExecutor.execute()`；后者入参要求
   `risk_result: RiskResult` 非空断言。                                                                    
  - **display_payload ↔ command_trace 分离**：`ClaudeClient` 回填 `messages` 时只取                          
  `ToolResult.display_payload`；`command_trace` 字段只流向 `AuditLog`。                                      
  - **审计 fsync 阻塞告知**：`AuditLog.append()` 内部 `f.write(); f.flush(); os.fsync(f.fileno())` 完成后才  
  return；上层拿到返回值才允许把"已执行"消息推给 UI。                                                        
                                                                                                             
  ---                                                                                                      
                                                                                                             
  ## 三、核心契约                                                                                          
                                                                                                             
  本章集中定义所有跨模块共享的数据结构。**所有字段都是显式 TypedDict / dataclass /                           
  Protocol**，不使用字典字面量串场。后续模块章节只引用、不重复。                                           
                                                                                                             
  契约划分：                                                                                               
  - 3.1 共享基础类型（`core/models.py` + `core/session.py`）                                                 
  - 3.2 Planning 契约                                                                                      
  - 3.3 Security 契约                                                                                      
  - 3.4 Executor 契约                                                                                      
  - 3.5 Tool 与执行上下文契约                                                                                
  - 3.6 DynamicTool 契约                 
  - 3.7 Audit 契约                                                                                           
  - 3.8 UI 与 UserConfirmation 契约                                                                  
  - 3.9 异常树                           
                                                                                                             
  ### 3.1 共享基础类型                                                                                     
                                                                                                             
  ```python                                                                                          
  # core/models.py                                                                                           
                                                                                                           
  from __future__ import annotations                                                                         
  from dataclasses import dataclass, field                                                                   
  from typing import TypedDict, Literal, Protocol, Callable, Awaitable, Any
                                                                                                             
  # ---------- 环境画像 ----------                                                                         
                                                                                                             
  DistroFamily = Literal["rhel", "debian", "openeuler", "suse", "alpine", "unknown"]                       
  InitSystem   = Literal["systemd", "sysvinit", "openrc", "unknown"]                                       
  PackageMgr   = Literal["apt", "yum", "dnf", "zypper", "apk", "unknown"]                                  
  FirewallBE   = Literal["ufw", "firewalld", "iptables", "none"]                                           
                                         
  class EnvProfile(TypedDict):                                                                               
      profile_id: str                # "env_" + uuid4()[:8]
      collected_at: str              # ISO8601                                                               
      refresh_count: int             # 被事件驱动刷新的次数                                          
                                                                                                             
      os_release: str                # 如 "openEuler 22.03 LTS SP3"                                          
      distro_family: DistroFamily                                                                            
      kernel: str                    # uname -r                                                              
                                                                                                             
      init_system: InitSystem                                                                                
      package_manager: PackageMgr                                                                            
      firewall_backend: FirewallBE                                                                           
                                                                                                             
      current_user: str                                                                                      
      is_root: bool                                                                                          
      sudo_available: bool                                                                                 
                                                                                                             
      is_container: bool             # /.dockerenv 或 /proc/1/cgroup                                       
      remote_mode: bool                                                                                      
      remote_host: str | None                                                                              
      ssh_port: int                  # 本地默认 22；远程为连接端口                                           
                                                                                                           
      # 命令可用性矩阵：至少包含 systemctl service journalctl ss netstat ip ifconfig sudo                    
      # apt apt-get yum dnf zypper ufw firewall-cmd iptables ps top free df lsof                     
      available_cmds: dict[str, bool]    
                                                                                                           
                                                                                                             
  # ---------- 输入源 / 执行模式 ----------                                                                  
                                                                                                             
  InputSource  = Literal["text", "voice"]                                                                    
  ModeDecision = Literal["direct", "plan", "none"]                                                   
  IssuedBy     = Literal["ai", "plan_engine", "user_override"]                                               
  ToolStatus   = Literal["ok", "blocked", "user_cancelled", "timeout", "error"]                              
                                                                                                             
                                                                                                             
  # ---------- ToolCall / ToolResult ----------                                                            
                                                                                                             
  @dataclass                                                                                               
  class ToolCall:                                                                                            
      request_id: str                # 供审计关联                                                    
      tool_name: str                                                                                         
      arguments: dict[str, Any]                                                                              
      issued_by: IssuedBy                                                                                    
      plan_step_id: str | None = None                                                                        
                                                                                                           
  @dataclass                                                                                               
  class ToolResult:                                                                                          
      request_id: str                                                                                      
      tool_name: str                                                                                         
      exit_code: int | None          # 工具未执行时为 None（BLOCK / 用户取消等）                             
      duration_ms: int                   
      display_payload: str           # 给 UI 与 AI messages 的脱敏结果，≤ 8 KB                               
      raw_output_truncated: str      # 审计 preview（已脱敏），≤ 2 KB                                        
      command_trace: list["CommandTraceItem"]  # 只进审计，不进 messages
      output_redacted: bool          # OutputSanitizer 是否命中                                              
      status: ToolStatus                                                                                   
      error_type: str | None = None  # 仅 status=error 时填充
  ```                                                                                                      
                                                                                                             
  **AI messages 反馈契约**：只回填 `display_payload`；`command_trace` 一律不得进入 `messages` 数组。
                                                                                                             
  ```python                                                                                                  
  # core/session.py                                                                                          
                                                                                                             
  ConfirmationPolicy = Literal["default", "strict"]                                                          
  VoiceBackend       = Literal["local", "cloud", "disabled"]                                                 
                                                                                                           
  @dataclass                                                                                                 
  class SessionState:                                                                                      
      session_id: str                                                                                        
      started_at: str                                                                                      
      env_profile_id: str                                                                                  
      prompt_version: str                                                                                  
      confirmation_policy: ConfirmationPolicy   # 计划修改确认策略                                           
      shell_channel_enabled: bool               # 默认 False                                                 
      voice_backend: VoiceBackend                                                                            
                                                                                                             
  @dataclass                                                                                         
  class TurnBundle:                                                                                          
      turn_id: str                   # "turn_" + 8-hex                                                     
      sequence: int                  # 从 1 递增                                                             
      user_input: str                                                                                
      input_source: InputSource                                                                              
      messages: list[dict]           # Anthropic messages (user/assistant/tool_result)                       
      tool_calls: list[ToolCall]                                                                             
      tool_results: list[ToolResult]                                                                         
      plan_id: str | None                                                                                    
      mode_decision: ModeDecision    # none 表示模型跳过了元工具                                           
      mode_skipped_meta_tool: bool   # 若 AI 跳过元工具直接调工具，则为 True                                 
      started_at: str                                                                                        
      ended_at: str | None                                                                                   
      compacted: bool                # 是否已被 compact 替换为摘要                                           
      summary: str | None            # compacted=True 时的结构化摘要                                         
      assistant_final_text: str | None = None                                                              
                                                                                                             
  @dataclass                                                                                               
  class TurnSummary:                                                                                         
      """Compactor 输出的结构化摘要。token 目标 ≤ 400。"""                                                   
      user_intent: str                                                                                       
      actions_taken: list[str]                                                                               
      key_findings: list[str]                                                                                
      remaining_risks: list[str]                                                                             
  ```                                                                                                        
                                                                                                             
  ### 3.2 Planning 契约                                                                                    
                                                                                                             
  ```python                                                                                                  
  # planning/plan.py                                                                                         
                                                                                                             
  from dataclasses import dataclass, field                                                           
  from typing import Literal                                                                                 
                                                                                                           
  StepStatus   = Literal["pending", "running", "completed", "failed", "skipped", "cancelled"]                
  ExpectedRisk = Literal["SAFE", "WARN-LOW", "WARN-HIGH", "BLOCK", "UNKNOWN"]                              
  PlanStatus   = Literal["draft", "confirmed", "executing", "completed", "aborted"]                          
  StepOrigin   = Literal["ai_initial", "ai_dynamic", "user_override"]                                
  ModKind      = Literal["add", "remove", "modify", "abort"]
  ModActor     = Literal["ai", "user"]                                                                     
                                                                                                           
  @dataclass                            
  class PlanStep:                       
      step_id: str                   # "s1" / "s2" ...；AI 也可自选                                          
      tool: str                                                                                              
      args: dict                                                                                             
      purpose: str                   # 自然语言用途，展示给用户                                              
      expected_risk: ExpectedRisk                                                                            
      confirm_required: bool | None  # None 代表按策略自动推断                                               
      status: StepStatus             # 初始 "pending"                                                        
      depends_on: list[str]          # 仅展示依赖；引擎本身按序执行                                          
      created_at: str                                                                                      
      created_by: StepOrigin                                                                                 
      last_result_request_id: str | None = None  # 关联到审计记录                                            
                                                                                                             
  @dataclass                                                                                                 
  class PlanModification:                                                                            
      kind: ModKind                  # add / remove / modify / abort                                         
      step_id: str | None            # kind=abort 时为 None                                                  
      reason: str                    # 必填，中文解释，进审计                                                
      at: str                        # ISO8601                                                               
      by: ModActor                                                                                           
      before_snapshot: dict | None   # kind ∈ {remove, modify} 时保存被改前的步骤快照                        
      after_snapshot: dict | None    # kind ∈ {add, modify} 时保存新步骤快照                                 
                                                                                                             
  @dataclass                                                                                               
  class Plan:                                                                                                
      plan_id: str                   # "plan_" + 8-hex                                                     
      session_id: str                                                                                        
      created_at: str                                                                                        
      version: int                   # 每次动态修改 +1                                                       
      steps: list[PlanStep]                                                                                  
      status: PlanStatus                                                                                     
      modification_history: list[PlanModification] = field(default_factory=list)                             
                                                                                                             
  # planning/context.py                                                                                    
                                                                                                           
  @dataclass                                                                                               
  class PlanningContext:                                                                                   
      """PlanningEngine.drive() 所需的外部依赖集合，便于测试替换。"""                                        
      ui: "UIBridge"                     
      ai: "ClaudeClient"                                                                                     
      classifier: "RiskClassifier"                                                                           
      safe_exec: "SafeExecutor"          
      audit: "AuditLog"                                                                                      
      policy: "ModificationPolicy"                                                                         
      session: SessionState                                                                                  
      env_profile: EnvProfile                                                                              
  ```                                                                                                        
                                                                                                             
  **不可变性**：任何 `status ∈ {running, completed}` 的步骤不可被修改或删除；引擎尝试变更时抛                
  `PlanImmutableError`。                                                                                     
                                                                                                           
  ### 3.3 Security 契约                                                                                      
                                                                                                             
  ```python                                                                                                
  # security/types.py                                                                                        
                                                                                                           
  from dataclasses import dataclass                                                                          
  from typing import Literal                                                                               
                                                                                                             
  RiskLevel       = Literal["SAFE", "WARN-LOW", "WARN-HIGH", "BLOCK"]                                
  VerifierLevel   = Literal["WARN-LOW", "WARN-HIGH", "BLOCK"]  # 下限 WARN-LOW
  VerifierStage   = Literal["rule", "llm"]                                                                 
  VerifierOrigin  = Literal["shell", "dyntool_create", "dyntool_execute"]                                  
                                                                                                             
  @dataclass                         
  class RuleHit:                                                                                             
      rule_id: str                   # "B002" / "WH005" / "WL001"                                    
      level: RiskLevel                   
      reason: str                    # 中文结构化原因                                                        
      self_lockout_warning: bool = False                                                                     
                                                                                                             
      def as_risk_result(self) -> "RiskResult":                                                              
          return RiskResult(level=self.level, rule_id=self.rule_id,                                          
                            reason=self.reason,                                                              
                            self_lockout_warning=self.self_lockout_warning,                                  
                            verifier_result=None)                                                            
                                                                                                             
  @dataclass                                                                                                 
  class RiskResult:                                                                                          
      level: RiskLevel                                                                                       
      rule_id: str | None            # SAFE 时可为 None                                                      
      reason: str                                                                                            
      self_lockout_warning: bool     # 远程模式可能自锁时 True                                             
      verifier_result: "VerifierResult | None"  # 仅 shell / DynTool 有值                                    
                                                                                                           
  @dataclass                             
  class VerifierContext:                                                                                   
      env_profile: EnvProfile                                                                              
      user_input: str                # 原始用户请求（LLM 意图上下文）
      origin: VerifierOrigin             
                                         
  @dataclass                                                                                                 
  class VerifierResult:                                                                                      
      stage: VerifierStage           # 最终定级发生在哪一阶段                                                
      level: VerifierLevel           # 下限 WARN-LOW，不出现 SAFE                                            
      flags: list[str]               # 触发的规则 ID / LLM 标签                                      
      explanation: str               # 展示给用户的中文解释                                                
      llm_raw: dict | None           # LLM 返回的原始结构化 JSON（进审计）                                   
      elapsed_ms: int                                                                                        
      self_lockout_warning: bool = False                                                                     
      confidence: float | None = None  # LLM 自评置信度；rule 阶段为 None                                    
  ```                                                                                                
                                                                                                             
  ### 3.4 Executor 契约                                                                                      
                                                                                                             
  ```python                                                                                                  
  # execution/adapter.py                                                                                     
                                                                                                             
  from typing import Protocol, Literal                                                                       
                                                                                                             
  ExecutorMode = Literal["local", "remote"]                                                                  
                                                                                                             
  class ExecutorAdapter(Protocol):                                                                         
      mode: ExecutorMode                                                                                   
                                                                                                           
      def run(self, cmd: list[str], timeout: int,                                                            
              stdin: str | None = None, 
              env: dict[str, str] | None = None) -> tuple[str, int]:                                         
          """执行单条命令，返回 (合并后的 stdout+stderr, exit_code)。                                
          cmd 必须是 argv 列表，shell=False；禁止出现 && | ; 等 shell 元字符。"""                            
          ...                                                                                              
                                                                                                             
      def close(self) -> None: ...                                                                           
  ```                                    
                                                                                                             
  `LocalExecutor` 与 `RemoteExecutor` 均实现此协议；工具层拿到的是 `ExecutorAdapter`，对本地/远程无感知。    
                                         
  ### 3.5 Tool 与执行上下文契约                                                                              
                                                                                                             
  ```python                                                                                                  
  # tools/base.py                                                                                            
                                                                                                           
  from typing import Protocol, Callable, Awaitable                                                         
  from dataclasses import dataclass                                                                          
                                                                                                           
  @dataclass                                                                                                 
  class ExecutionContext:                                                                            
      """传给每个 Tool.handler 的运行时上下文。"""                                                           
      session: SessionState                                                                                
      env: EnvProfile                                                                                      
      turn: TurnBundle                                                                                     
      executor: "ExecutorAdapter"                                                                          
      safe_exec: "SafeExecutor"                                                                              
      verifier: "CommandVerifier"       
      classifier: "RiskClassifier"                                                                           
      ui: "UIBridge"                                                                                 
      audit: "AuditLog"                                                                                      
      request_id: str                # 本次 ToolCall 的 request_id                                         
                                                                                                             
  ToolHandler = Callable[[dict, ExecutionContext], Awaitable[ToolResult]]                                    
                                         
  class Tool(Protocol):                                                                                      
      name: str                                                                                              
      description: str                   
      input_schema: dict              # JSON Schema Draft-7                                                  
      classifier_hints: dict          # 供 RiskClassifier 使用（如路径参数名、关键字段）                     
      handler: ToolHandler               
  ```                                                                                                        
                                                                                                             
  每个 OS 工具以实现 `Tool` 协议的类形式注册；`tools/os/__init__.py` 通过显式 `TOOLS = [DiskUsageTool,
  FindFilesTool, ...]` 注册，避免反射。                                                                      
                                                                                                           
  ### 3.6 DynamicTool 契约               
                                                                                                             
  ```python                                                                                                  
  # tools/dyntool.py                                                                                         
                                                                                                             
  from dataclasses import dataclass                                                                          
  from typing import Literal, Protocol                                                                     
                                                                                                             
  DynToolStatus = Literal["active", "disabled", "revoked"]                                                   
                                         
  @dataclass                                                                                                 
  class DynamicToolProposal:                                                                               
      """AI 通过 propose_dynamic_tool 提交的注册请求。"""
      name: str                      # 需符合 ^[a-z][a-z0-9_]{2,31}$，不与静态工具重名                     
      description: str                                                                                     
      input_schema: dict              # JSON Schema Draft-7                                                  
      cmd_template: str              # 使用 {{param}} 占位；占位必须出现在 input_schema 中
      estimated_risk: VerifierLevel  # 提案侧预估风险，不允许 SAFE                                           
      reversible: bool                # 是否可逆（供用户确认时展示）                                 
      consequences: str              # 中文影响说明
      rationale: str                  # 提出原因（为什么静态工具不够）                                       
                                                                                                             
  @dataclass                                                                                                 
  class DynamicTool:                                                                                         
      tool_id: str                   # "dyn_" + uuid4()[:8]                                                  
      name: str                                                                                              
      description: str                                                                                       
      input_schema: dict                                                                                     
      cmd_template: str                                                                                      
      estimated_risk: VerifierLevel                                                                          
      reversible: bool                                                                                       
      consequences: str                                                                                      
      created_at: str                                                                                        
      created_by: Literal["user_approved"]   # 当前仅支持用户审批创建                                      
      session_id_created: str        # 创建时的 session id，便于追溯                                         
      version: int                    # proposal 升级时 +1                                                 
      status: DynToolStatus          # active / disabled / revoked
      call_count: int                                                                                        
      last_called_at: str | None                                                                           
                                                                                                             
  @dataclass                                                                                         
  class ProposalValidation:              
      ok: bool                                                                                               
      reason: str | None             # ok=False 时填写                                                       
      warnings: list[str]            # ok=True 时仍可携带 warning                                            
                                                                                                             
  class DynamicToolRegistry(Protocol):                                                               
      """落盘于 ~/.nexus/dynamic_tools.json，权限 0600，filelock + os.replace 原子写。"""                    
                                                                                                           
      def list_active(self) -> list[DynamicTool]: ...                                                        
      def get(self, tool_id: str) -> DynamicTool | None: ...                                               
      def get_by_name(self, name: str) -> DynamicTool | None: ...                                          
      def validate_proposal(self, p: DynamicToolProposal,                                                    
                            reserved_names: set[str]) -> ProposalValidation: ...                             
      def add(self, p: DynamicToolProposal) -> DynamicTool: ...                                              
      def disable(self, tool_id: str) -> None: ...                                                           
      def revoke(self, tool_id: str) -> None: ...                                                            
      def record_call(self, tool_id: str, at: str) -> None: ...                                            
                                                                                                             
  class StaticRuleMapper(Protocol):                                                                          
      """DynTool 执行链第二层：把动态工具的命令映射到已有 OS 工具风险规则。
      若能映射则复用 RiskClassifier 结论；否则走 CommandVerifier 两阶段。"""                                 
      def map(self, dyn: DynamicTool, rendered_cmd: list[str],                                             
              env: EnvProfile) -> RiskResult | None: ...                                                     
  ```                                                                                                        
                                                                                                             
  注册表上限 20；超限 `validate_proposal` 返回 `ok=False`。                                                  
                                                                                                           
  ### 3.7 Audit 契约                                                                                         
                                                                                                             
  ```python                                                                                                  
  # audit/record.py                                                                                          
                                                                                                             
  from dataclasses import dataclass, field                                                                   
  from typing import Literal, Any                                                                            
                                                                                                           
  AuditMode     = Literal["direct", "plan", "none"]                                                          
  MetaAction    = Literal["declare_direct", "declare_plan",                                                
                          "add_step", "remove_step", "modify_step", "abort_plan"]                          
  DecisionFinal = Literal["executed", "blocked", "user_cancelled",                                         
                          "timeout", "error", "meta_applied", "meta_rejected"]                               
                                         
  @dataclass                                                                                                 
  class UserConfirmationTrace:                                                                       
      required: bool                     
      granted: bool                                                                                          
      actor: Literal["user", "timeout", "voice_rejected", "not_required"]                                    
      at: str | None                 # ISO8601；not_required 时为 None                                       
      comment: str | None = None                                                                             
                                                                                                             
  @dataclass                                                                                                 
  class VerifierTrace:                                                                                       
      stage: VerifierStage                                                                                 
      level: VerifierLevel                                                                                   
      flags: list[str]                                                                                     
      confidence: float | None                                                                               
      llm_model: str | None                                                                                  
      elapsed_ms: int                                                                                        
      explanation: str                                                                                       
                                                                                                           
  @dataclass                                                                                                 
  class DecisionTrace:                                                                                     
      risk_level: RiskLevel | Literal["META"]    # 元工具记录为 "META"                                       
      rule_id: str | None                                                                            
      reason: str                        
      self_lockout_warning: bool                                                                             
      verifier: VerifierTrace | None             # shell / DynTool 有值                                    
      user_confirmation: UserConfirmationTrace                                                               
      trace: list[str]                           # 决策链字符串化步骤                                
      final: DecisionFinal               
                                                                                                           
  @dataclass                                                                                                 
  class CommandTraceItem:               
      executor: ExecutorMode                     # local / remote                                            
      argv: list[str]                                                                                
      rendered: str                              # shlex.join(argv)                                          
      planner_route: str | None                  # 如 "service_restart/systemd"                              
      retry_attempt: int = 0                     # 0=首次，1+=重试                                           
                                                                                                             
  @dataclass                                                                                                 
  class ResultBlock:                                                                                       
      status: ToolStatus | Literal["meta_applied", "meta_rejected"]                                        
      exit_code: int | None                                                                                  
      duration_ms: int                                                                                     
      preview: str                               # raw_output_truncated                                      
      output_redacted: bool                                                                                  
      refreshed_env_fields: list[str]            # 本次执行后刷新了哪些 EnvProfile 字段                      
      error_type: str | None = None                                                                          
                                                                                                             
  @dataclass                                                                                               
  class PlanDelta:                                                                                           
      """元工具记录专用：描述本次 plan 变更摘要。"""                                                         
      before_version: int                                                                                    
      after_version: int                                                                                     
      modification: PlanModification                                                                         
                                                                                                             
  @dataclass                                                                                                 
  class DynamicMeta:                                                                                         
      """dynamic=True 时附带的 DynTool 执行信息。"""                                                         
      dynamic_tool_id: str                                                                                 
      semantic_unmapped: bool                    # StaticRuleMapper 是否未命中                               
      safety_check: dict                         # CommandSafetyChecker 的完整结果                         
      risk_classifier_result: dict | None        # 映射命中时的结果                                          
                                                                                                     
  @dataclass                                                                                                 
  class AuditRecord:                                                                                       
      record_id: str                             # "rec_" + ULID                                             
      timestamp: str                                                                                         
      session_id: str                                                                                        
      request_id: str                                                                                        
      turn_id: str                                                                                           
      prompt_version: str                                                                                    
      env_profile_id: str                                                                                    
                                                                                                             
      mode: AuditMode                                                                                      
      plan_id: str | None                                                                                    
      plan_step_id: str | None                                                                             
                                                                                                             
      tool_name: str                             # 元工具时为 "set_execution_mode" / "propose_dynamic_tool"
      tool_args: dict                    
      issued_by: IssuedBy                                                                                  
                                                                                                             
      decision: DecisionTrace           
      command_trace: list[CommandTraceItem]      # 元工具 / BLOCK / user_cancelled 时为空                    
      result: ResultBlock                                                                            
                                                                                                             
      dynamic: bool = False                                                                                
      dynamic_meta: DynamicMeta | None = None                                                                
                                                                                                             
      meta_action: MetaAction | None = None      # 仅元工具记录                                              
      plan_delta: PlanDelta | None = None        # 仅 set_execution_mode 记录                                
  ```                                                                                                      
                                                                                                             
  `AuditRecord` 序列化为单行 JSON 追加到 `audit.jsonl`；SQLite 索引从 JSONL 派生。详细字段见第八章。       
                                                                                                             
  ### 3.8 UI 与 UserConfirmation 契约                                                                
                                                                                                             
  ```python                                                                                                
  # ui/bridge.py                                                                                             
                                                                                                     
  from typing import Protocol                                                                                
  from dataclasses import dataclass                                                                          
                                                                                                             
  @dataclass                                                                                                 
  class ConfirmResult:                                                                                     
      approved: bool                                                                                         
      actor: Literal["user", "timeout", "voice_rejected"]                                                  
      comment: str | None = None     # 用户可选备注                                                        
                                                                                                           
  class UserConfirmation(Protocol):                                                                          
      async def request(self, risk: RiskResult, call: ToolCall) -> ConfirmResult: ...
      async def request_fingerprint(self, host: str, port: int,                                              
                                    fingerprint: str) -> bool: ...                                   
      async def request_dyntool_proposal(
              self, p: DynamicToolProposal,                                                                
              validation: ProposalValidation,                                                                
              verifier: VerifierResult | None) -> ConfirmResult: ...                                         
                                                                                                             
  class UIBridge(UserConfirmation, Protocol):                                                                
      """UI 暴露给下层模块的纯函数式接口。TUI 和无头 CLI 各自实现。"""                                       
                                                                                                           
      async def show_assistant_text(self, text: str) -> None: ...                                            
      async def show_tool_result(self, call: ToolCall, result: ToolResult) -> None: ...                    
      async def show_plan(self, plan: Plan) -> None: ...                                                     
      async def request_plan_confirmation(self, plan: Plan) -> bool: ...                                   
      async def notify_plan_modified(self, plan: Plan, mod: PlanModification) -> None: ...                   
      async def report_error(self, err: Exception, context: str) -> None: ...                              
  ```                                                                                                        
                                                                                                             
  **语音排除规则**：`UIBridge` 实现的任何 `request()` 不得接受来自 `input_source="voice"`                    
  的字符串作为确认输入——详见 4.11 `ConfirmModal` 与 4.12 语音约束。                                          
                                                                                                           
  ### 3.9 异常树                                                                                             
                                                                                                             
  ```python                                                                                                  
  # core/errors.py                                                                                           
                                                                                                             
  class NexusError(Exception): ...                                                                           
                                                                                                           
  class ConfigError(NexusError): ...                                                                         
  class InsecureKeyFileError(ConfigError): ...                                                             
  class MissingApiKeyError(ConfigError): ...
                                                                                                           
  class SecurityError(NexusError): ...                                                                     
  class BlockedByRuleError(SecurityError): ...                                                               
  class UserCancelledError(SecurityError): ...
  class VerifierBlockError(SecurityError): ...                                                               
                                                                                                     
  class ToolError(NexusError): ...                                                                           
  class ToolArgumentError(ToolError): ...                                                                  
  class ToolTimeoutError(ToolError): ...                                                                     
  class ShellChannelDisabledError(ToolError): ...                                                    
                                                                                                             
  class PlanError(NexusError): ...                                                                           
  class PlanMutationError(PlanError): ...                                                                  
  class PlanImmutableError(PlanError): ...                                                                   
  class UnexpectedStopReason(NexusError): ...                                                              
                                                                                                             
  class RemoteError(NexusError): ...                                                                         
  class UnknownHostError(RemoteError): ...                                                                 
  class UserRejectedHostError(RemoteError): ...                                                              
  ```                                                                                                      
                                                                                                             
  所有对外抛出的异常必须是 `NexusError` 子类；第三方库异常在模块边界被转义（wrap）或立即处理。             
                                                                                                             
  ---                                                                                                        
                                                                                                             
  ## 四、模块实现                                                                                            
                                                                                                             
  ### 4.1 AI 调用层（ClaudeClient）                                                                        
                                                                                                             
  **职责**：与 Claude 之间的 agentic loop；把对话上下文 + 工具定义提交给模型，接收                           
  tool_call，调度对应执行通路，把结果回写给模型，直到 `stop_reason=end_turn`。
                                                                                                             
  ```python                                                                                                
  # ai/client.py                         
                                                                                                           
  class ClaudeClient:                                                                                        
      def __init__(self, api_key: str, model: str,
                   conversation: ConversationManager,                                                        
                   tool_registry: ToolRegistry,                                                      
                   classifier: RiskClassifier,                                                               
                   verifier: CommandVerifier,                                                              
                   planning: PlanningEngine,                                                               
                   executor: ExecutorAdapter,                                                              
                   safe_exec: SafeExecutor,                                                                
                   audit: AuditLog,     
                   prompt_builder: SystemPromptBuilder,                                                      
                   ui: UIBridge) -> None: ...
                                                                                                             
      async def run_turn(self, user_input: str,                                                      
                         input_source: InputSource) -> TurnBundle: ...                                       
                                                                                                             
      async def consult_next(self, plan: Plan, just_run: PlanStep) -> "AIDecision":                        
          """被 PlanningEngine 调用，让 AI 基于最近一步结果决定下一步。"""                                   
  ```                                                                                                      
                                                                                                             
  **主循环伪代码**：                                                                                       
                                                                                                             
  ```python                                                                                                  
  async def run_turn(user_input, input_source):
      turn = conversation.open_turn(user_input, input_source)                                                
      system = prompt_builder.build()                    # 含 env_profile 快照                               
      tools  = tool_registry.definitions_for_ai()        # 按 session 可见性裁剪                             
      first_call = True                                                                                      
                                                                                                             
      while True:                                                                                    
          resp = await anthropic.messages.create(                                                          
              model=self.model, system=system,                                                             
              messages=conversation.build_messages_for_model(),                                              
              tools=tools, max_tokens=4096, stream=True)
          assistant_msg = collect_streaming_response(resp)                                                   
          conversation.append_assistant(assistant_msg)                                                       
                                                                                                             
          if resp.stop_reason == "end_turn":                                                                 
              turn.assistant_final_text = extract_text(assistant_msg)                                      
              break                                                                                          
                                                                                                           
          if resp.stop_reason == "tool_use":                                                                 
              tool_results = []                                                                              
              for call in extract_tool_calls(assistant_msg):                                                 
                  result = await self._dispatch_tool_call(call, first_call, turn)                            
                  tool_results.append(result)                                                              
              conversation.append_tool_results(tool_results)                                                 
              first_call = False                                                                             
              continue                                                                                     
                                                                                                             
          raise UnexpectedStopReason(resp.stop_reason)                                                     
                                         
      conversation.close_turn(turn)                                                                          
      return turn                                                                                            
  ```                                                                                                        
                                                                                                             
  **首次 tool_call 模式判定**：                                                                      
                                                                                                             
  ```python                                                                                                
  async def _dispatch_tool_call(self, call, first_call, turn):                                               
      if call.name == "set_execution_mode":                                                          
          audit.record_meta_tool(call, turn)                                                                 
          if first_call:                                                                                   
              turn.mode_decision = call.input.get("action_to_mode", "plan")                                  
          return await planning.handle_meta_tool(call, turn)                                         
                                         
      if call.name == "propose_dynamic_tool":                                                              
          audit.record_meta_tool(call, turn)                                                               
          return await ui.request_dyntool_proposal(...)                                                      
                                         
      if first_call:                                                                                         
          turn.mode_decision = "direct"                                                              
          turn.mode_skipped_meta_tool = True                                                                 
                                                                                                             
      return await self._execute_os_tool(call, turn)                                                         
  ```                                                                                                        
                                                                                                           
  **容错**：AI 跳过元工具直接调用 OS 工具不报错，仅丢失计划展示与动态调整能力；审计记录                      
  `mode_skipped_meta_tool=True`。                                                                          
                                                                                                             
  **API Key 加载优先级**：`ANTHROPIC_API_KEY` 环境变量 > `~/.nexus/.env` (0600 强制) > `config.toml  
  [api].key`（启动警告）。任一层缺失或权限不对抛 `MissingApiKeyError` / `InsecureKeyFileError`，退出码 10。  
                                                                                                           
  ### 4.2 对话管理（ConversationManager / Compactor / Budget）                                               
                                                                                                     
  **职责**：维护 `list[TurnBundle]`；构造给模型的 `messages`；控制 Token 预算；触发 Compact；落盘/加载会话。 
                                                                                                             
  **Token 预算**：                                                                                         
                                                                                                             
  ```python                                                                                                  
  # conversation/budget.py               
                                                                                                             
  TOTAL_BUDGET     = 150_000   # claude-opus-4-7 上下文窗口预留                                              
  SYSTEM_RESERVE   = 16_000    # system prompt + 工具定义
  RESPONSE_RESERVE = 8_000     # 模型输出空间                                                                
  ACTIVE_BUDGET    = TOTAL_BUDGET - SYSTEM_RESERVE - RESPONSE_RESERVE  # 126_000                           
                                                                                                             
  def estimate_tokens(text: str) -> int:                                                                   
      """英文部分 tiktoken + 中文字符 * 1.6 修正。"""                                                      
      cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')                                           
      en_part  = ''.join(c for c in text if not ('\u4e00' <= c <= '\u9fff'))                               
      return tiktoken_count(en_part, encoding="cl100k_base") + int(cn_chars * 1.6)                           
  ```                                                                                                        
                                                                                                             
  单次 `ToolResult.display_payload` ≤ 8 KB（由 SafeExecutor 硬截断），对应 token ≤ ~3 K。                    
                                                                                                             
  **Compact 流程**：`current_tokens > ACTIVE_BUDGET * 0.8` 触发，压缩到 ≤ `ACTIVE_BUDGET * 0.6`。压缩单位是已
   `closed` 的 TurnBundle；调用 Haiku (`claude-haiku-4-5-20251001`) 生成 `TurnSummary`（contract 见          
  3.1），token 目标 ≤ 400。Compactor 输入仅含 `user_input` + `tool_calls` +                                  
  `display_payload[:512]`，不含原始输出。若压缩后仍超预算，按 `sequence` 从小到大整轮丢弃并打                
  `discarded=True`，`audit.jsonl` 原始记录保留。                                                             
                                                                                                             
  **落盘**：每个 Turn 结束写 `sessions/<sid>/turns/<turn_id>.json`；compact 后原文件加 `.compacted`          
  后缀，摘要单独落 `<turn_id>.summary.md`。`audit.db` 按 `turn_id` 建 B-Tree 索引供回放。                    
                                                                                                           
  ### 4.3 系统提示词（SystemPromptBuilder + PromptRegistry）                                                 
                                                                                                           
  SystemPrompt 结构："固定骨架 + 动态切片"：                                                                 
                                                                                                           
  ```text                                                                                                  
  ## 身份与行为约束                 <固定文本>                                                             
  ## 工具使用规则                   <固定文本>                                                               
  ## 当前环境快照                   <动态：EnvProfile>
  ## 已注册动态工具                 <动态：DynamicToolRegistry.summary()>                                    
  ## 执行模式上下文                 <动态：当前 plan_id / 未完成步骤>                                        
  ```                                    
                                                                                                             
  **版本化**：                                                                                             
                                                                                                             
  ```python                                                                                                  
  class PromptRegistry:                                                                                    
      def publish(self, skeleton_text: str) -> str:                                                          
          digest = hashlib.sha256(skeleton_text.encode()).hexdigest()[:8]                                    
          path = self.base_dir / f"nexus-v1-{digest}.txt"                                                    
          if not path.exists():                                                                              
              path.write_text(skeleton_text)                                                                 
          return f"nexus-v1-{digest}"                                                                      
  ```                                                                                                      
                                                                                                           
  每条 `AuditRecord.prompt_version` 写入的是这个返回值；动态切片不进版本。                                   
                                         
  **事件驱动画像刷新**：                                                                                     
                                                                                                             
  ```python                                                                                                  
  # execution/refresh.py                                                                                     
                                                                                                             
  REFRESH_TRIGGERS: dict[str, list[str]] = {                                                               
      "manage_service":  ["available_cmds"],                                                                 
      "create_user":     ["current_user"],                                                                   
      "delete_user":     ["current_user"],
      "manage_package":  ["available_cmds", "package_manager"],                                              
      "manage_firewall": ["firewall_backend"],                                                             
  }                                      
                                                                                                             
  def refresh_if_needed(profile, tool_name, probe):                                                        
      fields = REFRESH_TRIGGERS.get(tool_name, [])                                                           
      for field in fields:                                                                                   
          probe.refresh_field(profile, field)
      profile["refresh_count"] += len(fields) > 0 and 1 or 0                                                 
  ```                                                                                                        
                                                                                                             
  字段级刷新（`which ufw` 而不是整份画像），目标 < 200ms。`ResultBlock.refreshed_env_fields` 记录本次刷新。  
                                                                                                             
  ### 4.4 ToolRegistry                                                                                       
                                                                                                           
  ```python                                                                                                  
  class ToolRegistry:                                                                                      
      def __init__(self, env_profile: EnvProfile, session_state: SessionState,                               
                   dyntool: DynamicToolRegistry) -> None: ...                                              
                                                                                                             
      def definitions_for_ai(self) -> list[dict]:                                                            
          """按当前 session 可见性裁剪后的 Anthropic tool_definition 列表。"""                             
                                                                                                             
      def get_handler(self, name: str) -> ToolHandler: ...                                                 
                                                                                                             
      def all_names(self) -> set[str]:                                                                     
          """静态 + 动态 + 元工具，供 validate_proposal 去重。"""                                            
                                                                                                             
      def verify_schemas(self) -> None:                                                                      
          """启动调用；对每个 Tool 做 JSON Schema Draft-7 合法性校验。"""                                    
  ```                                                                                                        
                                                                                                           
  **可见性分层**：                                                                                           
                                                                                                           
  | 工具集合 | 默认可见 | 控制方式 |                                                                         
  |---|---|---|                                                                                              
  | 元工具 `set_execution_mode` / `propose_dynamic_tool` | 总是 | — |                                        
  | 21 个 OS 工具 | 总是 | — |                                                                               
  | `execute_shell` | **否** | `session.shell_channel_enabled=True` 才加入；`--enable-shell` / `/shell on` | 
  | 已注册 DynamicTool | 是 | `--list-dynamic-tools` / `--delete-dynamic-tool` |                             
  | 高危工具（`delete_path` / `write_file`） | 是 | `config.toml [tools.hidden].names` 显式隐藏 |            
                                                                                                             
  ### 4.5 安全门（RiskClassifier + CommandVerifier + UserConfirmation）                                    
                                                                                                             
  **RiskClassifier**：对静态 OS 工具调用定级，不处理 shell / DynTool（它们走 verifier）。                  
                                                                                                             
  ```python                                                                                                
  class RiskClassifier:                                                                                      
      def classify(self, tool: str, args: dict, env: EnvProfile) -> RiskResult: ...                          
  ```                                                                                                        
                                                                                                             
  规则以装饰器注册于 `security/rules.py`，取最高级（`BLOCK > WARN-HIGH > WARN-LOW > SAFE`）。                
                                                                                                           
  ```python                                                                                                  
  @register_rule("B002", applies_to={"kill_process"})                                                      
  def block_pid_1(args, env) -> RuleHit | None:                                                              
      if args.get("pid") == 1:                                                                             
          return RuleHit("B002", "BLOCK", "防止终止 init 进程")                                              
      return None                                                                                          
                                                                                                             
  @register_rule("B010", applies_to={"manage_service"})                                                      
  def block_remote_sshd_stop(args, env) -> RuleHit | None:
      return RemoteLockoutChecker.assess_tool("manage_service", args, env)                                   
  ```                                                                                                        
                                                                                                             
  **路径检测** (`security/path_sets.py`)：                                                                   
                                                                                                             
  ```python                                                                                          
  def has_path_traversal(path: str) -> bool:                                                                 
      return ".." in Path(os.path.normpath(path)).parts                                                    
                                                                                                             
  def is_under(path: str, roots: Iterable[str]) -> bool:                                                     
      ap = Path(os.path.normpath(path)).resolve(strict=False)                                                
      return any(str(ap).startswith(str(Path(r).resolve())) for r in roots)                                  
  ```                                                                                                        
                                                                                                           
  **CommandVerifier（两阶段）**：专用于 `execute_shell` 与 DynTool 命令级语义评估——详见第七章。              
                                                                                                           
  **UserConfirmation**：契约见 3.8。UI 侧由 `ConfirmModal` 实现；若 `session.voice_backend !=                
  "disabled"`，弹窗仅接受键盘 `enter/esc`，不接受语音转写文字（见 4.12）。                                 
                                                                                                             
  ### 4.6 环境探测与执行适配                                                                                 
                                                                                                             
  **CapabilityProbe**：                                                                                      
                                                                                                             
  ```python                                                                                                  
  class CapabilityProbe:                                                                                   
      def __init__(self, executor: ExecutorAdapter) -> None: ...                                             
      def collect_full(self) -> EnvProfile:                                                                
          """会话启动全量探测，目标 < 2s。"""                                                                
      def refresh_field(self, profile: EnvProfile, field: str) -> None:                                      
          """事件驱动单字段刷新，目标 < 200ms。"""                                                         
  ```                                                                                                        
                                                                                                           
  采集项映射：                           
                                                                                                           
  | 字段 | 来源 |                                                                                            
  |---|---|                                                                                                  
  | `os_release` | 读 `/etc/os-release` |                                                                    
  | `distro_family` | 读 `/etc/os-release` 的 ID / ID_LIKE |                                                 
  | `kernel` | `uname -r` |                                                                                  
  | `init_system` | `cat /proc/1/comm` + `systemctl` 存在性 |                                              
  | `package_manager` | `which apt/yum/dnf/zypper/apk` |                                                     
  | `firewall_backend` | `which ufw/firewall-cmd/iptables` 且对应服务 active |                             
  | `current_user` / `is_root` | `getpass.getuser()` + `os.geteuid()` |                                    
  | `sudo_available` | `sudo -n true` 退出码 |                                                               
  | `is_container` | `/.dockerenv` 或 `/proc/1/cgroup` 含 docker/kubepods |                                
  | `ssh_port` | 远程为连接端口；本地 `ss -tlnp \| grep sshd` |                                              
  | `available_cmds` | 对固定列表逐个 `command -v` |                                                         
                                                                                                             
  **CommandPlanner**：                                                                                       
                                                                                                             
  ```python                                                                                                  
  class CommandPlanner:                                                                                      
      def plan(self, capability: str, **kwargs) -> list[str]:                                                
          """capability ∈ {service_status, service_start, service_stop, service_restart,                     
             read_log_systemd, read_log_file, list_ports, get_network_info,                                  
             list_packages, install_package, ...}"""                                                         
                                                                                                             
  PLAN_TABLE = {                                                                                             
      ("service_status", "systemd"): lambda name, **_: ["systemctl", "status", name],                      
      ("service_status", "sysvinit"): lambda name, **_: ["service", name, "status"],                         
      ("read_log_systemd", True): lambda unit, n, **_: ["journalctl", "-u", unit, "-n", str(n)],           
      ("read_log_file",    True): lambda path, n, **_: ["tail", "-n", str(n), path],                         
      ("list_ports", "ss"):      lambda proto, **_: ["ss", "-tnlp" if proto=="tcp" else "-unlp"],            
      ("list_ports", "netstat"): lambda proto, **_: ["netstat", f"-{proto[0]}nlp"],                          
      # ... 其它能力域                                                                                       
  }                                                                                                          
  ```                                                                                                        
                                                                                                             
  选路只依赖 `EnvProfile.available_cmds`，不每条命令重新探测。                                             
                                                                                                             
  **LocalExecutor**：                                                                                      
                                                                                                             
  ```python                                                                                                  
  class LocalExecutor:                                                                                       
      mode = "local"                                                                                         
      def run(self, cmd, timeout, stdin=None, env=None):                                                     
          proc = subprocess.run(                                                                           
              cmd, capture_output=True, text=True,                                                           
              timeout=timeout, shell=False,                                                                
              stdin=subprocess.PIPE if stdin is not None else None,                                          
              input=stdin,                                                                           
              env={**os.environ, **(env or {})})                                                             
          return (proc.stdout + proc.stderr), proc.returncode                                              
  ```                                                                                                      
                                                                                                             
  **RemoteExecutor**（paramiko + RejectPolicy）：                                                            
                                                                                                             
  ```python                                                                                                  
  class RemoteExecutor:                                                                                      
      mode = "remote"                                                                                      
                                                                                                             
      def __init__(self, host, user, port=22, key_path=None,                                               
                   known_hosts_path: Path = Path.home()/".nexus"/"known_hosts"):                             
          self.ssh = paramiko.SSHClient()                                                                  
          self.ssh.load_host_keys(str(known_hosts_path))                                                     
          self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())                                    
          try:                                                                                               
              self.ssh.connect(host, port=port, username=user, key_filename=key_path,                
                               timeout=10, banner_timeout=10)                                                
          except paramiko.SSHException as e:                                                               
              if "not found in known_hosts" in str(e):                                                       
                  raise UnknownHostError(host, port) from e                                                  
              raise                      
                                                                                                             
      def run(self, cmd, timeout, stdin=None, env=None):                                                     
          cmd_str = " ".join(shlex.quote(a) for a in cmd)
          if env:                                                                                            
              prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())                           
              cmd_str = f"{prefix} {cmd_str}"                                                                
          chan = self.ssh.get_transport().open_session()                                                     
          chan.settimeout(timeout)                                                                           
          chan.exec_command(cmd_str)                                                                         
          if stdin:                                                                                          
              chan.sendall(stdin.encode()); chan.shutdown_write()                                    
          out = b""                                                                                          
          while True:                                                                                      
              if chan.recv_ready():        out += chan.recv(65536)                                           
              if chan.recv_stderr_ready(): out += chan.recv_stderr(65536)                            
              if chan.exit_status_ready(): break                                                             
              time.sleep(0.02)                                                                             
          return out.decode("utf-8", errors="replace"), chan.recv_exit_status()                              
  ```                                                                                                      
                                                                                                             
  **首次连接指纹流程**：                                                                                     
                                                                                                             
  ```python                                                                                                  
  async def connect_with_fingerprint_check(host, port, user, key_path, ui):                          
      try:                                                                                                   
          return RemoteExecutor(host, user, port, key_path)                                                  
      except UnknownHostError:                                                                             
          fp = _probe_fingerprint_readonly(host, port)   # 只读取 host key，不建立交互                       
          approved = await ui.request_fingerprint(host, port, fp)                                          
          if not approved:                                                                                   
              raise UserRejectedHostError(host)                                                            
          _append_known_hosts(host, port, fp)             # 写入 ~/.nexus/known_hosts                        
          return RemoteExecutor(host, user, port, key_path)                                                  
  ```                                    
                                                                                                             
  **SafeExecutor**：所有执行的唯一入口。                                                                   
                                                                                                             
  ```python                                                                                                  
  class SafeExecutor:                                                                                        
      MAX_OUTPUT_BYTES = 8 * 1024                                                                            
                                                                                                             
      def __init__(self, executor, audit, sanitizer): ...                                                    
                                                                                                             
      async def execute(self, cmd: list[str],                                                                
                        risk_result: RiskResult,   # 非空断言                                        
                        request_id: str,                                                                   
                        tool_name: str,                                                                      
                        timeout: int = 30) -> ToolResult:                                                  
          assert risk_result is not None                                                                     
          t0 = time.perf_counter()                                                                   
          try:                                                                                               
              raw, code = self.executor.run(cmd, timeout)                                                  
          except subprocess.TimeoutExpired:                                                                  
              return self._timeout_result(...)                                                               
          except PermissionError as e:                                                                       
              return self._error_result("permission_denied", str(e), ...)                                    
          except paramiko.SSHException as e:                                                                 
              return self._retry_or_fail(cmd, e, ...)     # 指数退避 1s, 2s                                
          truncated = raw[: self.MAX_OUTPUT_BYTES]                                                           
          sanitized = self.sanitizer.sanitize(truncated)                                                   
          return ToolResult(                                                                                 
              request_id=request_id, tool_name=tool_name,                                                  
              exit_code=code,                                                                                
              duration_ms=int((time.perf_counter() - t0) * 1000),                                    
              display_payload=sanitized,                                                                     
              raw_output_truncated=sanitized[:2048],                                                       
              command_trace=[CommandTraceItem(                                                               
                  executor=self.executor.mode, argv=cmd,                                                     
                  rendered=shlex.join(cmd),                                                                  
                  planner_route=None, retry_attempt=0)],                                                     
              output_redacted=(sanitized != truncated),                                                    
              status="ok")                                                                                   
  ```                                                                                                        
                                                                                                           
  重试策略：SSH 断连触发一次重连（指数退避 1s, 2s），仍失败 `status="error"`；超时不重试。                   
                                                                                                             
  ### 4.7 OS 工具与 OutputSanitizer                                                                          
                                                                                                             
  **Handler 二次校验**：Schema 合法不等于语义合法。                                                          
                                                                                                           
  ```python                                                                                                  
  class ReadLogTool:                                                                                       
      name = "read_log"                                                                                      
      input_schema = {                                                                                       
          "type": "object",                                                                                
          "properties": {                                                                                    
              "unit":  {"type": "string"},                                                                 
              "lines": {"type": "integer", "minimum": 10, "maximum": 500, "default": 50},                    
              "since": {"type": "string"},                                                                 
          },                                                                                                 
      }                                                                                                      
      classifier_hints = {"path_params": []}                                                               
                                                                                                             
      async def handle(self, args, ctx: ExecutionContext) -> ToolResult:                                     
          lines = min(max(args.get("lines", 50), 10), 500)
          unit  = args.get("unit")                                                                           
          if unit and not _SAFE_UNIT_NAME.match(unit):                                                       
              raise ToolArgumentError("invalid unit name")                                                   
          cmd = ctx.planner.plan("read_log_systemd" if unit else "read_log_file",                            
                                 unit=unit, n=lines, path="/var/log/messages")                               
          risk = ctx.classifier.classify("read_log", args, ctx.env)                                          
          return await ctx.safe_exec.execute(cmd, risk, ctx.request_id, "read_log")                        
  ```                                                                                                        
                                                                                                           
  `find_files` 等同理：`max_depth ≤ 7`、结果条数 ≤ 200。                                                     
                                                                                                             
  **OutputSanitizer**（所有工具输出的唯一出口）：                                                            
                                                                                                             
  ```python                                                                                                  
  SENSITIVE_PATTERNS: list[tuple[re.Pattern, str | Callable]] = [                                            
      (re.compile(r"Bearer\s+[A-Za-z0-9\-_.=]+", re.I),        "Bearer <REDACTED_TOKEN>"),                   
      (re.compile(r"Authorization:\s*\S+", re.I),              "Authorization: <REDACTED>"),               
      (re.compile(r"(password\s*=\s*)\S+", re.I),              r"\1<REDACTED_PASSWORD>"),                    
      (re.compile(r"--password[= ]\S+", re.I),                 "--password <REDACTED>"),                     
      (re.compile(r"sk-[A-Za-z0-9]{20,}"),                     "<REDACTED_OPENAI_KEY>"),                   
      (re.compile(r"AKIA[0-9A-Z]{16}"),                        "<REDACTED_AWS_AK>"),                         
      (re.compile(r"aws_secret_access_key\s*=\s*\S+", re.I),   "aws_secret_access_key=<REDACTED>"),        
      (re.compile(r"[a-z]+://[^:@\s]+:([^@\s]+)@"),            lambda m: m.group(0).replace(m.group(1), 
  "<REDACTED>")),                                                                                            
      (re.compile(r"ANTHROPIC_API_KEY\s*=\s*\S+"),             "ANTHROPIC_API_KEY=<REDACTED>"),              
  ]                                                                                                          
                                                                                                             
  class OutputSanitizer:                                                                                     
      def sanitize(self, text: str) -> str:                                                                  
          out = text                                                                                         
          for pattern, repl in SENSITIVE_PATTERNS:                                                           
              out = pattern.sub(repl, out)                                                                   
          return out                                                                                         
  ```                                                                                                        
                                                                                                             
  ### 4.8 PlanningEngine                                                                                     
                                                                                                             
  **状态机**：                                                                                             
                                                                                                           
  ```text                                                                                                    
        user_request                                                                                       
             │                                                                                               
        [draft]──── declare_plan ──→ [draft with steps]                                              
                                           │
                                    user_confirm                                                             
                                           ▼                                                               
                                     [confirmed]                                                             
                                           │ engine_start                                            
                                           ▼
                                    [executing]─── step run ──→ [executing]                                
                                    │    │                                                                   
                             step_failed  all_done                                                           
                                    ▼          ▼                                                             
                                [aborted]  [completed]                                                       
  ```                                                                                                        
                                                                                                           
  **主循环**：                                                                                               
                                                                                                           
  ```python                                                                                                  
  class PlanningEngine:                                                                                    
      async def drive(self, plan: Plan, ctx: PlanningContext):                                               
          await ctx.ui.show_plan(plan)                                                               
          confirmed = await ctx.ui.request_plan_confirmation(plan)                                           
          if not confirmed:                                                                                
              plan.status = "aborted"                                                                        
              self.audit.record_plan_event(plan, "user_aborted"); return                                   
                                                                                                           
          plan.status = "executing"                                                                        
          while (nxt := self._next_pending_step(plan)) is not None:                                        
              await self._execute_step(plan, nxt, ctx)                                                       
              # 每步后回到 ClaudeClient，让 AI 看结果、决定下一步                                            
              decision = await ctx.ai.consult_next(plan, nxt)                                                
              self._apply_modifications(plan, decision.modifications, ctx)                                   
          plan.status = "completed"                                                                          
  ```                                                                                                        
                                                                                                           
  每完成一步，结果回传 AI，AI 的下一个 tool_call 可能是：                                                    
  1. 元工具调用（`add_step` / `modify_step` / `remove_step` / `abort_plan`） → 引擎应用修改                  
  2. 直接执行下一个工具 → 引擎匹配到下一个 pending 步骤（匹配失败即"计划外动作"，单独作为额外 ToolCall       
  并记审计）                                                                                                 
  3. 结束对话（`end_turn`）                                                                                  
                                                                                                             
  **修改 API**：                                                                                             
                                                                                                             
  ```python                                                                                                
  def apply_add_step(plan, new_step_data, insert_after, reason) -> PlanModification: ...                     
  def apply_remove_step(plan, target_step_id, reason) -> PlanModification: ...                               
  def apply_modify_step(plan, target_step_id, new_step_data, reason) -> PlanModification: ...                
  def apply_abort_plan(plan, reason) -> PlanModification: ...                                                
  ```                                                                                                        
                                                                                                           
  所有修改仅对 `status="pending"` 的步骤生效；否则抛 `PlanImmutableError` 并写审计。每次修改 `plan.version +=
   1`，追加 `PlanModification` 到 `modification_history`，写一条元工具 `AuditRecord`（含 `PlanDelta`）。   
                                                                                                             
  **修改确认策略**：                                                                                         
                                                                                                           
  ```python                                                                                                  
  class ModificationPolicy(Protocol):                                                                      
      def needs_confirm(self, step: PlanStep, risk: RiskResult) -> bool: ...                                 
                                                                                                           
  class DefaultPolicy:                                                                                       
      """SAFE/WARN-LOW 自动执行；WARN-HIGH 及以上要求确认。"""                                             
      def needs_confirm(self, step, risk):                                                                   
          return risk.level in ("WARN-HIGH", "BLOCK")                                                
                                         
  class StrictPolicy:                                                                                      
      """任何计划修改都需要确认。"""                                                                         
      def needs_confirm(self, step, risk):                                                                   
          return True                                                                                        
  ```                                                                                                        
                                                                                                             
  `SessionState.confirmation_policy` 决定启动时选择哪一个；`AuditRecord` 同时写入使用的策略类名。          
                                                                                                             
  **用户介入**：`plan.status="executing"` 时 TUI 仍接受输入。按 Esc 或输入非空文本：引擎暂停，把 "current  
  plan snapshot + executed history + user message" 作为新一轮输入送回 AI；AI                                 
  可选择修改剩余步骤、终止计划、或声明新 direct 调用。                                                       
                                                                                                           
  ### 4.9 AuditLog                                                                                           
                                                                                                           
  ```python                              
  class AuditLog:                                                                                            
      def append(self, record: AuditRecord) -> None:                                                       
          """append-only；f.write()+f.flush()+os.fsync() 完成后返回。写失败整轮报错。"""                     
                                                                                                     
      def record_meta_tool(self, call: ToolCall, turn: TurnBundle) -> None: ...                              
      def record_plan_event(self, plan: Plan, kind: str, **kw) -> None: ...                                
      def index_turn(self, turn_id: str, record_ids: list[str]) -> None: ...                                 
      def export_jsonl(self, session_id: str, out_path: Path) -> None: ...                                 
      def export_repro_pack(self, session_id: str, out_path: Path) -> None: ...                              
  ```                                                                                                
                                         
  JSONL 是权威来源；`audit.db` 每 N 条 flush 从尾部同步，丢失可完整重建。                                    
                                                                                                           
  **repro pack** 内容：                                                                                      
                                                                                                             
  ```text                                                                                                    
  repro_sess_xxx/                                                                                            
  ├── manifest.json            # nexus 版本、打包时间、SHA256                                                
  ├── audit.jsonl                                                                                            
  ├── env_profile.json                                                                                       
  ├── prompts/<prompt_version>.txt                                                                           
  ├── plans/plan_xxx.json                                                                                  
  ├── sessions/meta.json                                                                                     
  ├── demo_script.md                                                                                       
  └── screenshots_index.json   # 本地截图相对路径（观众对照视频）
  ```                                                                                                        
                                                                                                             
  ### 4.10 DynamicToolRegistry                                                                               
                                                                                                             
  契约见 3.6。执行链三层：                                                                                   
                                                                                                           
  ```text                                                                                                    
  AI 发起 propose_dynamic_tool                                                                             
    │                                                                                                        
    ▼ 层 1: validate_proposal                                                                              
    ├─ 名字合法、不与静态工具重名、不超过 20 个                                                              
    ├─ cmd_template 占位与 input_schema 属性一致                                                             
    ├─ estimated_risk ∈ {WARN-LOW, WARN-HIGH, BLOCK}                                                       
    │                                                                                                        
    ▼ 层 2: CommandVerifier.verify(rendered, origin="dyntool_create")                                      
    │  对一个示例渲染结果做两阶段评估，作为提案风险参考                                                      
    │                                                                                                      
    ▼ 层 3: UI.request_dyntool_proposal                                                                    
    ├─ 用户看到 cmd_template + consequences + reversible + verifier 结论                                     
    ├─ 必须完整键入 "approve" 才通过（单键不生效）                                                           
    │                                                                                                        
    ▼ 通过 → DynamicToolRegistry.add(p) → 落盘 dynamic_tools.json(0600, filelock, os.replace)                
  ```                                                                                                        
                                                                                                             
  **调用链（已注册 DynTool 被 AI 调用时）**：                                                                
                                                                                                             
  ```python                                                                                                  
  async def execute_dyntool(dyn: DynamicTool, args: dict, ctx: ExecutionContext):                            
      cmd = render_template(dyn.cmd_template, args)                                                          
      # 第二层：优先尝试静态规则映射                                                                         
      mapped = ctx.rule_mapper.map(dyn, cmd, ctx.env)                                                        
      if mapped is not None:                                                                               
          risk = mapped                                                                                      
      else:                                                                                                  
          v = await ctx.verifier.verify(cmd, VerifierContext(                                              
              env_profile=ctx.env, user_input=ctx.turn.user_input, origin="dyntool_execute"))                
          if v.level == "BLOCK":                                                                             
              return make_blocked_result(v)
          risk = RiskResult(level=v.level, rule_id="VERIFIER",                                               
                            reason=v.explanation,                                                            
                            self_lockout_warning=v.self_lockout_warning,                                     
                            verifier_result=v)                                                               
      if ctx.policy.needs_confirm(..., risk):                                                              
          confirmed = await ctx.ui.request(risk, ToolCall(...))                                              
          if not confirmed.approved:                                                                         
              return make_cancelled_result()                                                               
      return await ctx.safe_exec.execute(cmd, risk_result=risk, ...)                                         
  ```                                                                                                        
                                                                                                             
  ### 4.11 终端界面（TUI）                                                                                   
                                                                                                           
  **布局**：                                                                                                 
                                                                                                             
  ```text                                                                                                    
  ┌─────────────────────────────────────────────────────────────┐                                            
  │ Nexus · openEuler 22.03 · local · direct │ tokens 12k/150k  │  StatusBar                                 
  ├─────────────────────────────────────────────────────────────┤                                          
  │  Turn #3  [22:04]                                           │                                            
  │   ▸ 用户: 帮我查一下哪些进程占 CPU 最多，把超过 80% 的停了   │                                         
  │   ▸ AI: 我先列出 top 进程，然后再决定哪些需要停止...        │
  │   ▸ ① get_disk_usage → 已执行 (SAFE, 240ms)                  │                                           
  │   ▸ ② create_user alice → 待确认 (WARN-HIGH)                 │                                         
  │  [计划 plan_a3f2 · 4 步 · 已完成 2 · 待执行 2]              │                                            
  ├─────────────────────────────────────────────────────────────┤                                    
  │ > _                                                         │                                            
  └─────────────────────────────────────────────────────────────┘                                          
  F1: 帮助   F3: 审计面板   F4: 环境画像   Ctrl+C: 取消本轮                                                  
  ```                                                                                                        
                                                                                                             
  **快捷键**：`F1` 帮助 / `F3` 审计面板 / `F4` 环境画像 / `F5` 手动刷新画像 / `Ctrl+C` 取消本轮 / `Ctrl+D`   
  结束会话 / `/shell on|off` 运行时开关 shell 通道 / `/policy strict|default`                                
  切换修改确认策略（仅影响后续计划）。                                                                       
                                                                                                             
  **确认弹窗**：                                                                                             
                                                                                                           
  ```text                                                                                                    
  ┌─── 风险确认 [WARN-HIGH] ───────────────────┐                                                           
  │ 操作: kill_process(pid=2314)               │                                                             
  │ 风险依据: WH001 非当前用户进程             │                                                             
  │ 自锁风险: 否                               │                                                           
  │ 预计影响: 终止 nginx-worker                │                                                             
  │                                            │                                                           
  │ (y) 确认   (n) 取消   (e) 修改参数重新提交 │                                                             
  └────────────────────────────────────────────┘                                                           
  ```                                                                                                        
                                                                                                             
  远程模式下若 `self_lockout_warning=True`，弹窗顶部追加红色行："⚠ 远程锁门风险：操作可能切断当前 SSH 
  连接，确认前请确保有带外恢复手段。"                                                                        
                                                                                                           
  **指纹确认**：                                                                                             
                                                                                                             
  ```text                                                                                                    
  ┌─── 首次连接新主机 ───────────────────────────┐                                                           
  │ 目标: user@10.0.0.8:22                        │                                                          
  │ 主机指纹: SHA256:xYzAbc123...                 │                                                        
  │ 该主机不在 known_hosts 中。                   │                                                          
  │ 若此指纹与你独立渠道获得的指纹一致，输入 trust │                                                       
  │ 确认后将写入 ~/.nexus/known_hosts。           │                                                          
  │ [trust] 信任并继续    [cancel] 取消连接       │                                                          
  └───────────────────────────────────────────────┘
  ```                                                                                                        
                                                                                                             
  必须完整键入 `trust` 才通过（单键 `y` 不生效），防止误按。DynTool 提案弹窗同理，必须键入 `approve` / 
  `reject`。                                                                                                 
                                                                                                           
  ### 4.12 VoiceInput                                                                                        
                                                                                                             
  ```python                                                                                                  
  class VoiceInput(Protocol):                                                                                
      async def transcribe(self) -> str:                                                                     
          """录音（按空格开始/停止）→ 文本。超时 30s。"""                                                  
      async def close(self) -> None: ...                                                                     
  ```                                                                                                      
                                                                                                             
  两种实现：`LocalWhisper`（`faster-whisper` 的 `base` 模型，CPU 约 2x RT，音频不出本机）与                  
  `CloudBackend`（`config.voice.backend` 指定）。启动时按 `config.voice.backend` 实例化，运行时不切换。
                                                                                                             
  **安全约束**：VoiceInput 输出带 `input_source="voice"` 标签。**唯一例外**：                              
                                                                                                             
  ```python                                                                                                  
  class ConfirmModal:                                                                                        
      def handle_key(self, event):                                                                           
          if self.voice_enabled and event.source == "voice":                                         
              return            # 忽略语音输入                                                               
          ...                                                                                              
  ```                                                                                                        
                                                                                                             
  即 WARN-HIGH 及以上确认绝不接受语音转写文字。
                                                                                                             
  ### 4.13 Config / CLI / Session / --verify / --demo                                                        
                                                                                                             
  **config.toml**：                                                                                          
                                                                                                             
  ```toml                                                                                            
  [api]                                                                                                      
  model = "claude-opus-4-7"                                                                                
  verifier_model = "claude-haiku-4-5-20251001"                                                               
                                                                                                           
  [session]                                                                                                  
  confirmation_policy = "default"      # default | strict                                            
  shell_channel_enabled = false                                                                              
  retention_days = 30                                                                                      
  max_sessions = 20                                                                                        
                                                                                                           
  [voice]                                                                                                    
  backend = "disabled"                 # disabled | local | cloud                                            
                                                                                                             
  [verifier]                                                                                                 
  timeout_ms = 3000                                                                                          
  max_tokens = 512                                                                                         
  cache_ttl_seconds = 300                                                                                    
  confidence_floor = 0.5                                                                                   
                                                                                                             
  [tools.hidden]                                                                                           
  names = []                                                                                                 
  ```                                                                                                      
                                                                                                             
  `ConfigLoader` 优先级：环境变量 > `~/.nexus/config.toml` > 包内默认值。                            
                                                                                                             
  **会话管理 CLI**：                                                                                       
                                                                                                             
  ```text                                                                                            
  nexus --list-sessions                                                                                      
  nexus --delete-session <id>                                                                              
  nexus --export-audit <id> [path]                                                                         
  nexus --export-repro-pack <id> [path]                                                                    
  nexus --resume <id>                                                                                        
  ```                                
                                                                                                             
  启动时扫描 `~/.nexus/sessions/`，删除 `retention_days` 之前的目录；超 `max_sessions` 按 mtime 淘汰最旧。
                                         
  **--verify**：10 项自检串行跑，每项对应 `tests/integration/verify_*.py` 的 scenario；`cli/verify.py`       
  直接调用，不经 pytest。详见第十章。                                                                      
                                                                                                             
  **--demo**：读 `demo/scripts/<name>.yaml`：                                                        
                                                                                                             
  ```yaml                                                                                                  
  name: basics                                                                                             
  steps:                                                                                                     
    - say: "查看根分区使用情况"                                                                            
    - expect_tool: get_disk_usage                                                                            
    - sleep: 1                                                                                       
    - say: "列出 CPU 占用前 5 的进程"                                                                        
    - expect_tool: list_processes                                                                            
  ```                                                                                                      
                                                                                                             
  Demo 用真实 Claude 调用，用户输入自动注入，所有过程同样写审计，不走特殊路径。场景：`basics / risk /        
  planning / restart / multiturn / remote / multi_distro / voice / audit / chaos / shell / all`。
                                                                                                             
  ---                                                                                                      
                                                                                                             
  ## 五、工具面完整规格                                                                                    
                                                                                                             
  ### 5.1 OS 工具清单（21 个）                                                                             
                                                                                                             
  | # | 工具名 | 能力域 | 关键参数（简写） | 最高风险 |                                                      
  |---|---|---|---|---|                                                                                    
  | 1 | `get_disk_usage` | 磁盘 | `path`, `recursive:bool` | WARN-LOW |                                      
  | 2 | `find_files` | 文件 | `search_path`, `pattern`, `min_size_mb`, `max_depth≤7`, `limit≤200` | WARN-LOW 
  |                                      
  | 3 | `list_processes` | 进程 | `top_n≤50`, `sort_by:cpu\|mem\|time`, `filter_user` | SAFE |               
  | 4 | `kill_process` | 进程 | `pid`, `signal:TERM\|KILL\|HUP` | WARN-HIGH/BLOCK |                        
  | 5 | `get_port_status` | 网络 | `port`, `protocol:tcp\|udp\|all` | SAFE |                                 
  | 6 | `create_user` | 用户 | `username`, `groups[]`, `shell`, `create_home` | WARN-HIGH |          
  | 7 | `delete_user` | 用户 | `username`, `remove_home` | WARN-HIGH/BLOCK |                                 
  | 8 | `modify_user_groups` | 用户 | `username`, `groups[]`, `action:add\|remove` | WARN-HIGH/BLOCK |     
  | 9 | `get_system_info` | 系统 | 无参 | SAFE |                                                             
  | 10 | `manage_service` | 服务 | `name`, `action:start\|stop\|restart\|status\|enable\|disable` |          
  SAFE~BLOCK |                                                                                               
  | 11 | `get_network_info` | 网络 | `interface?` | SAFE |                                                   
  | 12 | `read_log` | 日志 | `unit?`, `lines∈[10,500]`, `since?` | SAFE/WARN-LOW |                           
  | 13 | `read_file` | 文件 | `path`, `start_line?`, `end_line?`, `max_bytes≤8192` | WARN-LOW/BLOCK |        
  | 14 | `write_file` | 文件 | `path`, `content`, `mode:overwrite\|append\|create_only` | WARN-HIGH/BLOCK |  
  | 15 | `delete_path` | 文件 | `path`, `recursive:bool` | WARN-HIGH/BLOCK |                                 
  | 16 | `create_directory` | 文件 | `path`, `parents:bool` | WARN-LOW |                                     
  | 17 | `copy_move_path` | 文件 | `src`, `dst`, `action:copy\|move` | WARN-LOW/WARN-HIGH |                  
  | 18 | `manage_package` | 包管理 | `name`/`names[]`, `action:install\|remove\|update\|list\|search`,       
  `manager:auto\|apt\|yum\|dnf` | SAFE/WARN-HIGH |                                                         
  | 19 | `get_resource_stats` | 监控 | `resource:cpu\|memory\|all`, `top_n_procs≤20` | SAFE |                
  | 20 | `manage_firewall` | 防火墙 | `backend`, `action`, `target:{port,service,protocol,source_ip}`,       
  `direction`, `policy` | SAFE/WARN-HIGH/BLOCK |                                                             
  | 21 | `get_set_system_config` | 系统配置 | `key:hostname\|timezone\|locale`, `value?` | SAFE/WARN-HIGH |  
                                                                                                             
  完整 JSON Schema 在 `tools/os/<name>.py`。                                                                 
                                                                                                             
  ### 5.2 元工具 `set_execution_mode`                                                                        
                                                                                                             
  ```json                                                                                                    
  {                                                                                                          
    "name": "set_execution_mode",                                                                            
    "description": "声明本轮执行模式或在计划模式中修改计划。",                                               
    "input_schema": {                                                                                      
      "type": "object",                                                                                      
      "properties": {                                                                                      
        "action": {                                                                                          
          "type": "string",                                                                                
          "enum": ["declare_direct", "declare_plan",                                                         
                   "add_step", "remove_step", "modify_step", "abort_plan"]                                 
        },                                                                                                   
        "plan_steps": {                                                                                      
          "type": "array",                                                                                   
          "description": "仅 action=declare_plan 时使用；初始步骤列表",                                      
          "items": {                                                                                 
            "type": "object",                                                                              
            "properties": {                                                                                  
              "step_id": {"type": "string"},                                                               
              "tool":    {"type": "string"},                                                                 
              "args":    {"type": "object"},                                                         
              "purpose": {"type": "string"},                                                                 
              "expected_risk": {                                                                           
                "type": "string",                                                                            
                "enum": ["SAFE","WARN-LOW","WARN-HIGH","BLOCK","UNKNOWN"]                                  
              }                                                                                              
            },                                                                                       
            "required": ["step_id","tool","args","purpose"]
          }                                                                                                  
        },                                                                                                 
        "new_step": {                                                                                        
          "type": "object",                                                                          
          "description": "仅 action=add_step / modify_step 时使用",                                          
          "properties": {                                                                                    
            "step_id":       {"type": "string"},                                                           
            "insert_after":  {"type": "string"},                                                             
            "tool":          {"type": "string"},                                                             
            "args":          {"type": "object"},
            "purpose":       {"type": "string"},                                                             
            "expected_risk": {"type": "string"}                                                            
          }                              
        },                                                                                                   
        "target_step_id": {                                                                                
          "type": "string",                                                                                  
          "description": "仅 action=remove_step / modify_step 时使用"                                        
        },                               
        "reason": {                                                                                          
          "type": "string",                                                                                  
          "description": "本次动作的中文解释，用于展示与审计"
        }                                                                                                    
      },                                                                                                   
      "required": ["action", "reason"]   
    }                                                                                                      
  }                                                                                                          
  ```                                   
                                                                                                             
  约束：                                                                                             
  - `declare_direct` / `declare_plan` 只在本轮**首个 tool_call** 合法；其它时机调用返回状态错误，AI 需改用   
  `add_step` / `modify_step`。                                                                             
  - `modify_step` / `remove_step` 只能作用于 `status=pending` 的步骤；否则返回                               
  `PlanImmutableError`，审计记录失败。                                                               
  - `reason` 必填，直接进 `DecisionTrace.trace`。
                                                                                                           
  ### 5.3 Shell 通道 `execute_shell`                                                                       
                                     
  ```json                               
  {                                                                                                          
    "name": "execute_shell",            
    "description": "当既有工具和已注册动态工具都无法完成时，执行任意 shell                                   
  命令。该工具默认不可见，需用户显式开启。每次调用由 verifier 两阶段评估，并强制用户确认。",         
    "input_schema": {                                                                                        
      "type": "object",                                                                                    
      "properties": {                                                                                        
        "cmd": {                                                                                           
          "type": "array", "items": {"type": "string"},                                                    
          "description": "argv 列表，不允许包含 && | ; 等 shell 元字符",                                   
          "minItems": 1, "maxItems": 20                                                                      
        },                                                                                                   
        "purpose": {"type": "string"},                                                                       
        "timeout": {"type": "integer", "minimum": 1, "maximum": 120, "default": 30}                          
      },                                                                                             
      "required": ["cmd", "purpose"]                                                                       
    }                                                                                                      
  }                                                                                                          
  ```                                                                                                        
                                                                                                             
  执行路径（受 `session.shell_channel_enabled=True` 门控）：                                                 
                                                                                                             
  ```python                                                                                                  
  async def execute_shell(args, ctx):                                                                      
      if not ctx.session.shell_channel_enabled:                                                              
          raise ShellChannelDisabledError()                                                                
      cmd = args["cmd"]                  
      v = await ctx.verifier.verify(cmd, VerifierContext(                                                  
          env_profile=ctx.env, user_input=ctx.turn.user_input, origin="shell"))                            
      if v.level == "BLOCK":                                                                                 
          return make_blocked_result(v)  
      risk = RiskResult(level=v.level, rule_id="VERIFIER",                                                   
                        reason=v.explanation,                                                        
                        self_lockout_warning=v.self_lockout_warning,
                        verifier_result=v)                                                                   
      approved = await ctx.ui.request(risk, ToolCall(                                                      
          tool_name="execute_shell", arguments=args,                                                         
          request_id=ctx.request_id, issued_by="ai"))                                                
      if not approved.approved:                                                                              
          return make_cancelled_result()                                                                   
      return await ctx.safe_exec.execute(                                                                    
          cmd, risk_result=risk, request_id=ctx.request_id,                                                  
          tool_name="execute_shell", timeout=args.get("timeout", 30))                                      
  ```                                                                                                        
                                                                                                           
  ### 5.4 `propose_dynamic_tool` 元工具                                                                      
                                                                                                             
  ```json                                                                                                    
  {                                                                                                          
    "name": "propose_dynamic_tool",                                                                          
    "description": "当既有工具面无法覆盖需求时提议注册新工具。用户审批后持久化。",                         
    "input_schema": {                                                                                        
      "type": "object",                                                                                    
      "properties": {                                                                                        
        "name":           {"type": "string", "pattern": "^[a-z][a-z0-9_]{2,31}$"},                         
        "description":    {"type": "string"},                                                              
        "input_schema":   {"type": "object"},                                                              
        "cmd_template":   {"type": "string"},                                                                
        "estimated_risk": {"type": "string", "enum": ["WARN-LOW","WARN-HIGH","BLOCK"]},
        "reversible":     {"type": "boolean"},                                                               
        "consequences":   {"type": "string"},                                                        
        "rationale":      {"type": "string"}                                                                 
      },                                                                                                   
      "required": ["name","description","input_schema","cmd_template",                                       
                   "estimated_risk","reversible","consequences","rationale"]                               
    }                                                                                                        
  }                                                                                                  
  ```                                                                                                        
                                                                                                             
  注册上限 20；超限 `validate_proposal` 返回 `ok=False`。                                                    
                                                                                                             
  ---                                                                                                      
                                                                                                             
  ## 六、风险规则完整表                                                                                    
                                                                                                             
  规则定级取最高级：`BLOCK > WARN-HIGH > WARN-LOW > SAFE`。`B010 / B015-B017` 共享                   
  `RemoteLockoutChecker`；`CS*` 由 `CommandSafetyChecker` 在 verifier 规则阶段使用。                         
                                                                                                           
  ### 6.1 BLOCK 规则（B001-B017）                                                                            
                                                                                                     
  | ID | 触发条件 | 实现位置 |                                                                               
  |---|---|---|                                                                                            
  | B001 | 访问 `/etc/passwd` / `/etc/shadow` / `/boot/*` / `/lib/systemd/*` | security/rules.py |           
  | B002 | `kill_process(pid=1)` | security/rules.py |                                                     
  | B004 | `delete_user("root")` 或 `modify_user_groups("root")` | security/rules.py |                       
  | B005 | 任一路径参数含 `..` 组件 | security/path_sets.py |                                        
  | B006 | `find_files(search_path="/", max_depth>5)` | security/rules.py |                                  
  | B007 | 路径匹配 `/proc/kcore` / `/dev/mem` / `/proc/sys/kernel/*` | security/rules.py |                
  | B008 | 路径匹配 `/etc/sudoers` 或 `/etc/sudoers.d/*` | security/rules.py |                             
  | B009 | 路径匹配 `/etc/ssh/sshd_config` | security/rules.py |                                             
  | B010 | 远程模式下 `manage_service(name ∈ ssh\|sshd, action ∈ stop\|disable)` | remote_lockout.py |     
  | B011 | `read_file(path)` 匹配 SENSITIVE_CREDENTIAL_PATHS | security/rules.py |                           
  | B012 | `write_file(path)` 匹配关键系统文件集合 | security/rules.py |                             
  | B013 | `delete_path(recursive=true)` 目标为 `/`/`/etc`/`/usr`/`/boot`/`/lib`/`/bin`/`/sbin` |            
  security/rules.py |                                                                                        
  | B014 | `delete_path(path)` 路径命中 B001 集合 | security/rules.py |                                      
  | B015 | 远程模式下 `manage_firewall(action=flush)` | remote_lockout.py |                                  
  | B016 | 远程模式下 `manage_firewall(action=set-default, policy ∈ drop\|reject)` | remote_lockout.py |     
  | B017 | 远程模式下 `manage_firewall(action=deny, target.port=ssh_port 或 target.service ∈ ssh\|sshd)` |   
  remote_lockout.py |                                                                                        
                                                                                                             
  ### 6.2 WARN-HIGH 规则（WH001-WH012）                                                                      
                                                                                                             
  | ID | 触发条件 |                                                                                          
  |---|---|                                                                                                  
  | WH001 | `kill_process` 终止非当前用户进程 |                                                              
  | WH002 | `delete_user` 任意非 root 用户 |                                                                 
  | WH003 | `create_user` |                                                                                  
  | WH004 | `modify_user_groups` |                                                                           
  | WH005 | `manage_service(action ∈ stop\|disable)` 作用于关键服务 |                                        
  | WH006 | `manage_service(action ∈ start\|restart\|enable)` 作用于关键服务 |                             
  | WH007 | `write_file(mode ∈ overwrite\|append)`；或 `create_only` 命中 PERSISTENCE_ENTRY_PATHS |        
  | WH008 | `delete_path(recursive=false)` |                                                                 
  | WH009 | `manage_package(action ∈ install\|remove\|update)` |                                           
  | WH010 | `manage_firewall(action ∈ allow\|deny\|delete\|set-default)`（远程下 B015-B017 先 BLOCK） |      
  | WH011 | `get_set_system_config(value 不为空)` |                                                          
  | WH012 | `copy_move_path(action=move)` 且目标已存在 |                                                     
                                                                                                             
  ### 6.3 WARN-LOW 规则（WL001-WL009）                                                                       
                                                                                                             
  | ID | 触发条件 |                                                                                          
  |---|---|                                                                                                  
  | WL001 | `get_disk_usage(recursive=true)` |                                                               
  | WL002 | `find_files(search_path="/", max_depth≤5)` |                                                     
  | WL003 | `kill_process` 未命中 WH001/B002 |                                                               
  | WL004 | `manage_service(action ∈ start\|restart)` 非关键服务 |                                           
  | WL005 | `read_log(unit=None)` |                                                                          
  | WL006 | `read_file(path)` 匹配 `/etc/*`（非 BLOCK 路径） |                                               
  | WL007 | `write_file(mode=create_only)` 且不在 PERSISTENCE_ENTRY_PATHS |                                
  | WL008 | `copy_move_path(action=copy)` |                                                                  
  | WL009 | `create_directory(path)` 在 `/` 或 `/etc` 下 |                                                 
                                                                                                             
  ### 6.4 CS 命令级规则（CommandSafetyChecker）                                                            
                                                                                                             
  | ID | 检查项 | 结果 |                                                                                     
  |---|---|---|                                                                                              
  | CS001 | `rm -rf /` / `rm -rf /*` / `rm -rf ~` | BLOCK |                                                  
  | CS002 | `dd of=/dev/...` | BLOCK |                                                                       
  | CS003 | `mkfs` / `fdisk` / `parted` 作用于系统盘 | BLOCK |                                               
  | CS004 | `curl \| sh`、`wget \| bash` 等管道到 shell | BLOCK |                                            
  | CS005 | 含 `&&` / `\|` / `;` / `>` / `<` 等 shell 元字符（argv 列表应避免） | BLOCK |                  
  | CS006 | `chmod 777` 作用于 `/etc` / `/usr` / `/bin` | BLOCK |                                            
  | CS007 | `chown -R` 作用于系统目录 | BLOCK |                                                            
  | CS008 | `sudo -s` / `sudo su` / `sudo bash` | BLOCK |                                                    
  | CS009 | 命令长度 > 4 KB 或 argv 数 > 20 | BLOCK |                                                      
  | CS010 | 远程 `iptables -F` / `nft flush ruleset` | BLOCK（RemoteLockoutChecker） |                       
  | CS011 | 远程 `ufw --force reset` / `ufw disable` | BLOCK |                                               
  | CS012 | 远程 `systemctl stop sshd` | BLOCK（复用 B010） |                                                
  | CS013 | 远程 `reboot` / `shutdown` / `poweroff` / `halt` | BLOCK |                                       
  | CS014 | 远程断网动作 `ip link set down` 作用于主网卡 | BLOCK |                                           
  | CS015 | `execute_shell.cmd` 首元素不在 `EnvProfile.available_cmds` | WARN-HIGH |                         
                                                                                                             
  ### 6.5 参数化集合                                                                                         
                                                                                                             
  ```python                                                                                                  
  # security/path_sets.py                                                                                    
                                                                                                             
  PATH_PARAMETERS = {                                                                                        
      "get_disk_usage":     ["path"],                                                                        
      "find_files":         ["search_path"],                                                                 
      "read_file":          ["path"],                                                                        
      "write_file":         ["path"],                                                                        
      "delete_path":        ["path"],                                                                      
      "create_directory":   ["path"],                                                                      
      "copy_move_path":     ["src", "dst"],                                                                  
  }                                                                                                        
                                                                                                             
  CRITICAL_SERVICES = {                                                                              
      "mysql", "mysqld", "mariadb", "postgresql", "postgres",                                                
      "nginx", "httpd", "apache2",                                                                         
      "redis", "redis-server",                                                                               
      "mongodb", "mongod",                                                                                   
      "elasticsearch",                   
      "rabbitmq", "rabbitmq-server",                                                                         
      "docker", "containerd",                                                                              
  }                                      
                                                                                                             
  SENSITIVE_CREDENTIAL_PATHS = {                                                                             
      "exact": {"/etc/shadow", "/etc/gshadow"},                                                              
      "glob": [                                                                                              
          "~/.ssh/id_*", "~/.ssh/authorized_keys",                                                   
          "~/.aws/credentials", "~/.aws/config",                                                             
          "~/.kube/config",                                                                                  
          "**/.env", "**/.env.*",                                                                          
          "*.pem", "*.key",                                                                                  
          "*_rsa", "*_ed25519", "*_ecdsa",                                                                 
          "*.pfx", "*.p12",                                                                                  
      ],                                                                                                     
  }                                                                                                          
                                                                                                             
  PERSISTENCE_ENTRY_PATHS = [                                                                                
      "/etc/systemd/system/",                                                                              
      "/etc/cron.d/", "/etc/cron.daily/", "/etc/cron.hourly/",                                               
      "/etc/cron.weekly/", "/etc/cron.monthly/",                                                           
      "/etc/init.d/", "/etc/profile.d/", "/etc/rc.d/",                                                       
      "/etc/ld.so.conf.d/",                                                                                
  ]                                                                                                          
                                                                                                           
  SYSTEM_WRITE_BLOCKED = {                                                                                 
      "/etc/passwd", "/etc/shadow", "/etc/sudoers",                                                          
      "/boot/*", "/lib/systemd/*", "/etc/ssh/sshd_config",                                                 
  }                                                                                                          
  SYSTEM_WRITE_BLOCKED_GLOB = ["/etc/sudoers.d/*"]                                                   
  ```                                                                                                        
                                                                                                             
  ---                                                                                                        
                                                                                                             
  ## 七、CommandVerifier 详细实现                                                                          
                                                                                                             
  ### 7.1 两阶段流程                                                                                         
                                                                                                             
  ```python                                                                                                  
  class CommandVerifier:                                                                             
      async def verify(self, cmd: list[str], ctx: VerifierContext) -> VerifierResult:                        
          rule_hit = await self._rule_stage(cmd, ctx.env_profile)                                          
          if rule_hit.level == "BLOCK":                                                                    
              return rule_hit.to_verifier_result(stage="rule")                                             
                                                                                                           
          llm_raw = await self._llm_stage(cmd, ctx)                                                          
          try:                           
              parsed = json.loads(llm_raw)                                                                   
              level  = parsed["level"]                                                               
              conf   = float(parsed.get("confidence", 1.0))                                                  
              if level == "SAFE":                                                                            
                  level = "WARN-LOW"            # 下限保护                                                   
              if level not in ("WARN-LOW", "WARN-HIGH", "BLOCK"):                                            
                  raise ValueError                                                                   
              if conf < CONFIDENCE_FLOOR:                                                                    
                  level = max_level(level, "WARN-HIGH")                                                    
          except Exception:                                                                                  
              return VerifierResult(                                                                       
                  stage="llm", level="BLOCK",                                                                
                  flags=["LLM_PARSE_FAIL"],                                                                
                  explanation="命令 verifier 输出异常，保守拦截",                                            
                  llm_raw=None, elapsed_ms=..., confidence=None)                                           
                                                                                                             
          final_level = max_level(level, rule_hit.level)  # 合并规则阶段 WARN-HIGH                         
          return VerifierResult(stage="llm", level=final_level,                                              
                                flags=rule_hit.flags + parsed.get("flags", []),                            
                                explanation=parsed.get("reasoning", ""),                                     
                                llm_raw=parsed, elapsed_ms=...,                                            
                                self_lockout_warning=rule_hit.self_lockout_warning,                          
                                confidence=conf)                                                             
  ```                                                                                                        
                                                                                                             
  ### 7.2 LLM 阶段 Prompt                                                                                    
                                                                                                             
  **模型**：`claude-haiku-4-5-20251001`（verifier 专用），`max_tokens=512`，`temperature=0`。                
                                                                                                             
  ```text                                                                                                  
  <system>                                                                                                   
  你是一个 Linux 命令安全审查员。请对下面的命令做语义级风险评估。                                          
  输出 **纯 JSON**，严格匹配以下 schema：                                                                    
  {                                                                                                        
    "level": "WARN-LOW" | "WARN-HIGH" | "BLOCK",                                                             
    "flags": ["<tag1>", ...],                                                                              
    "reasoning": "<不超过 200 字的中文解释>",                                                                
    "confidence": 0.0~1.0                                                                            
  }                                                                                                          
  - 下限是 WARN-LOW，不允许输出 SAFE                                                                       
  - 任何破坏性、权限提升、外联下载、长时运行或判不准的情况应输出 WARN-HIGH 或 BLOCK                          
  - 不要产出除 JSON 以外的内容                                                                               
  </system>                                                                                                  
                                                                                                             
  <user>                                                                                             
  环境：{env_summary}                                                                                        
  用户原话：{user_input}                                                                                     
  即将执行的命令：{shlex.join(cmd)}                                                                          
  </user>                                                                                                    
  ```                                                                                                        
                                                                                                             
  ### 7.3 LLM 输出硬约束                                                                                     
                                                                                                             
  1. 无论 LLM 返回什么，不允许输出 `SAFE`（自动提升为 `WARN-LOW`）。                                       
  2. `confidence < CONFIDENCE_FLOOR` 自动升级到至少 `WARN-HIGH`。                                          
  3. 解析失败 / 超时（`timeout_ms`）/ 连接失败 → 升级到 `BLOCK`。                                          
  4. 规则阶段的 `WARN-HIGH` 命中不会被 LLM 降级（`max_level` 合并）。                                        
                                         
  ### 7.4 缓存                                                                                               
                                                                                                     
  内存 LRU：`key = shlex.join(cmd) + ":" + env_profile_id`，`ttl = cache_ttl_seconds (默认 300)`。命中返回旧 
  `VerifierResult` 且 `elapsed_ms=0, flags+="CACHED"`，不落盘。                                            
                                                                                                             
  ### 7.5 运行时配置                                                                                         
                                                                                                             
  ```toml                                                                                                    
  [verifier]                                                                                                 
  model = "claude-haiku-4-5-20251001"                                                                        
  timeout_ms = 3000                                                                                        
  max_tokens = 512                                                                                           
  cache_ttl_seconds = 300                                                                                  
  confidence_floor = 0.5                                                                                     
  ```                                                                                                
                                         
  预算：规则阶段 < 1ms；LLM 阶段 300-800ms；缓存命中 <1ms。                                                  
                                                                                                           
  **审计字段**（追加到 `DecisionTrace.verifier`）：                                                          
                                                                                                     
  ```json                                                                                                    
  {                                                                                                        
    "stage": "llm",                                                                                          
    "level": "WARN-HIGH",                                                                            
    "flags": ["EXTERNAL_DOWNLOAD"],      
    "confidence": 0.83,                                                                                      
    "llm_model": "claude-haiku-4-5-20251001",                                                              
    "elapsed_ms": 412,                                                                                       
    "explanation": "..."                                                                             
  }                                                                                                          
  ```                                                                                                      
                                                                                                           
  ---                                                                                                        
                                                                                                           
  ## 八、AuditLog 落盘 schema                                                                                
                                                                                                             
  ### 8.1 标准记录（对应契约 3.7 的 `AuditRecord`）
                                                                                                             
  ```json                                                                                                  
  {                                      
    "record_id": "rec_01H8XYZ...",                                                                         
    "timestamp": "2026-04-23T14:22:31.842Z",                                                                 
    "session_id": "sess_01H...",        
    "turn_id": "turn_a3f2",                                                                                  
    "request_id": "req_014",                                                                         
    "prompt_version": "nexus-v1-a3f2",                                                                       
    "env_profile_id": "env_b8c1d4",                                                                        
                                                                                                             
    "mode": "plan",                                                                                  
    "plan_id": "plan_004",                                                                                   
    "plan_step_id": "s3",                                                                                  
                                                                                                             
    "tool_name": "manage_service",                                                                           
    "tool_args": {"name": "nginx", "action": "restart"},                                                     
    "issued_by": "plan_engine",                                                                              
                                                                                                             
    "decision": {                                                                                            
      "risk_level": "WARN-HIGH",                                                                             
      "rule_id": "WH006",                                                                                    
      "reason": "关键服务重启可能中断业务",                                                                
      "self_lockout_warning": false,                                                                         
      "verifier": null,                                                                                    
      "user_confirmation": {                                                                                 
        "required": true, "granted": true,                                                                   
        "actor": "user", "at": "2026-04-23T14:22:29.107Z",                                                   
        "comment": null                                                                                      
      },                                                                                             
      "trace": [                                                                                             
        "plan_step(s3)",                                                                                     
        "risk_classifier(WH006)",                                                                            
        "policy(DefaultPolicy)",                                                                             
        "user_confirmed(final)"                                                                              
      ],                                                                                                   
      "final": "executed"                                                                                    
    },                                                                                                     
                                                                                                             
    "command_trace": [                                                                                       
      {"executor": "local",                                                                                
       "argv": ["systemctl", "restart", "nginx"],                                                            
       "rendered": "systemctl restart nginx",                                                              
       "planner_route": "service_restart/systemd",                                                           
       "retry_attempt": 0}                                                                                 
    ],                                                                                                       
                                                                                                           
    "result": {                                                                                            
      "status": "ok",                                                                                        
      "exit_code": 0,                                                                                      
      "duration_ms": 842,                                                                                    
      "preview": "active (running) since ...",                                                       
      "output_redacted": false,                                                                              
      "refreshed_env_fields": ["available_cmds"],                                                            
      "error_type": null                                                                                     
    },                                                                                                       
                                                                                                           
    "dynamic": false,                                                                                      
    "dynamic_meta": null,                                                                                  
    "meta_action": null,                                                                                     
    "plan_delta": null                                                                                       
  }                                                                                                          
  ```                                                                                                        
                                                                                                             
  ### 8.2 特殊记录子类型                                                                                     
                                                                                                             
  - **元工具记录**：`tool_name="set_execution_mode"`，`decision.risk_level="META"`，`command_trace=[]`，`resu
  lt.status="meta_applied"` / `"meta_rejected"`；额外字段 `meta_action`（`declare_plan` / `add_step` /     
  `remove_step` / `modify_step` / `abort_plan`）与 `plan_delta`（契约 3.7 `PlanDelta`）。                    
  - **BLOCK 记录**：`result.status="blocked"`，`command_trace=[]`，`exit_code=null`。                      
  - **用户取消**：`result.status="user_cancelled"`。                                                         
  - **DynTool 记录**：`dynamic=true`，`dynamic_meta`（`DynamicMeta`：`dynamic_tool_id` / `semantic_unmapped` 
  / `safety_check` / `risk_classifier_result`）。
                                                                                                             
  ### 8.3 SQLite 索引                                                                                      
                                                                                                             
  ```sql                                                                                             
  CREATE TABLE records (                                                                                     
    record_id TEXT PRIMARY KEY,                                                                              
    session_id TEXT, turn_id TEXT, plan_id TEXT, plan_step_id TEXT,                                        
    tool_name TEXT, risk_level TEXT, rule_id TEXT,                                                           
    status TEXT, duration_ms INT, timestamp TEXT                                                           
  );                                                                                                         
  CREATE INDEX idx_turn ON records(session_id, turn_id);                                                   
  CREATE INDEX idx_plan ON records(plan_id);                                                               
  CREATE INDEX idx_tool ON records(tool_name, risk_level);                                                 
  ```                                                                                                        
                                         
  索引从 `audit.jsonl` 尾部追加同步（每 N 条 flush），索引丢失可由 JSONL 完整重建。                          
                                                                                                     
  ### 8.4 导出                                                                                               
                                                                                                           
  - `--export-audit <sid> [path]` → 直接 `cp audit.jsonl` 到 `path`。                                        
  - `--export-repro-pack <sid> [path]` → 打成 `repro_<sid>.tar.gz`，内容见 4.9；`manifest.json` 含 nexus   
  版本、打包时间、SHA256，供第三方校验完整性。                                                               
                                                                                                             
  ---                                                                                                      
                                                                                                             
  ## 九、关键数据流                                                                                        
                                                                                                             
  ### 9.1 计划模式 + 动态修改                                                                              
                                                                                                             
  ```text                                                                                                    
  用户输入："查哪些进程占 CPU 最多，超过 80% 的停了"                                                         
    │                                                                                                        
    ├─ ConversationManager.open_turn()                                                                     
    ├─ SystemPromptBuilder.build()（注入当前 EnvProfile）                                                  
    │                                                                                                      
    ▼ ClaudeClient.run_turn()                                                                              
    ├─ 首个 tool_call: set_execution_mode(action=declare_plan,                                               
    │                                      plan_steps=[list_processes, (待定)])
    ├─ PlanningEngine.create_plan(...) → plan_id=plan_a3f2, version=1, status=draft                          
    ├─ UI.show_plan → 用户确认 → status=confirmed → executing                                                
    │                                    
    ▼ 步骤 s1: list_processes(top_n=10, sort_by=cpu)                                                         
    ├─ RiskClassifier.classify → SAFE                                                                        
    ├─ SafeExecutor.execute                                                                                  
    ├─ OutputSanitizer 无命中                                                                                
    ├─ AuditLog.append(rec_001)                                                                              
    │                                                                                                        
    ▼ AI 看到结果，下一轮                                                                                    
    ├─ tool_call: set_execution_mode(action=add_step, insert_after="s1",                                     
    │              new_step={step_id:"s2", tool:"kill_process",                                              
    │                        args:{pid:2314,signal:"TERM"},                                                  
    │                        purpose:"终止超过 80% 的 nginx-worker",                                         
    │                        expected_risk:"WARN-HIGH"},                                                     
    │              reason:"步骤 s1 显示 PID 2314 占 CPU 87%")                                                
    ├─ PlanningEngine.apply_add_step → plan.version=2                                                        
    ├─ UI 同步通知用户                                                                                       
    │                                                                                                        
    ▼ 步骤 s2: kill_process(pid=2314, signal=TERM)                                                           
    ├─ RiskClassifier.classify → WARN-HIGH (WH001)                                                           
    ├─ Policy.needs_confirm → true                                                                           
    ├─ ConfirmModal → 用户 y → approved                                                                      
    ├─ SafeExecutor.execute                                                                                  
    ├─ AuditLog.append(rec_002, plan_step_id=s2)                                                             
    │                                                                                                        
    ▼ AI 下一轮决定 end_turn                                                                                 
    └─ 自然语言回复："已终止 PID 2314 (nginx-worker)，其它进程 CPU 均在 80% 以下。"                          
       （不含任何 shell 命令）                                                                               
  ```                                                                                                        
                                                                                                           
  ### 9.2 Shell 通道                                                                                         
                                                                                                             
  ```text                                                                                                    
  用户输入："把 /var/log/messages 旋转一下"                                                                  
    │                                                                                                        
    ▼ Claude 判断工具面无合适工具、shell 通道已开启                                                          
    ├─ tool_call: execute_shell(cmd=["logrotate","-f","/etc/logrotate.d/messages"],                          
    │                           purpose="强制旋转 messages 日志")                                            
    │                                                                                                        
    ▼ CommandVerifier.verify()                                                                             
    ├─ 规则阶段：CS 全部未命中 BLOCK                                                                       
    ├─ LLM 阶段：Haiku 返回 {level:"WARN-LOW", flags:[], confidence:0.9}                                   
    ├─ 下限保护 → 最终 WARN-LOW                                                                              
    │                                    
    ▼ UI.request (WARN-LOW 仍展示命令详情 + 快速放行)                                                        
    ├─ 用户 y → approved                                                                                     
    │                                                                                                        
    ▼ SafeExecutor.execute                                                                                   
    ├─ LocalExecutor.run(["logrotate","-f",...], timeout=30)                                                 
    ├─ OutputSanitizer.sanitize                                                                              
    ├─ AuditLog.append(含 verifier 字段)                                                                     
    │                                                                                                        
    └─ Claude 回复："日志已旋转，原 messages 被归档为 messages.1。"                                          
  ```                                                                                                        
                                                                                                           
  ### 9.3 远程首次连接 + 指纹                                                                                
                                                                                                             
  ```text                                                                                                    
  nexus --remote user@10.0.0.8                                                                               
    │                                                                                                        
    ▼ RemoteExecutor(host, user, port=22)                                                            
    ├─ ssh.load_host_keys(~/.nexus/known_hosts)                                                              
    ├─ ssh.set_missing_host_key_policy(RejectPolicy())                                                     
    ├─ connect() → paramiko SSHException "not found in known_hosts"                                          
    ├─ 捕获 → UnknownHostError                                                                               
    │                                                                                                      
    ▼ _probe_fingerprint_readonly() 只读 host key                                                            
    ├─ 得到 SHA256:xYz...                                                                                    
    │                                    
    ▼ UI.request_fingerprint                                                                                 
    ├─ Fingerprint Modal，必须键入 "trust"                                                                   
    ├─ 用户输入 trust → approved=True                                                                        
    │                                                                                                        
    ▼ 写入 ~/.nexus/known_hosts (追加)                                                                       
    ├─ 再次 RemoteExecutor(...) 成功                                                                         
    │                                                                                                        
    └─ CapabilityProbe.collect_full() 针对远端                                                               
       ├─ EnvProfile.remote_mode=True, ssh_port=22                                                           
       └─ 进入正常会话                                                                                       
  ```                                                                                                        
                                                                                                             
  ---                                                                                                        
                                                                                                             
  ## 十、测试与故障注入                                                                                      
                                                                                                             
  ### 10.1 --verify 10 项                                                                                    
                                                                                                             
  ```text                                                                                                    
  项               对应检查实现                                                                              
  1  API 连通性    ai.client.health_check()                                                                  
  2  Verifier 模型 security.verifier.health_check()                                                        
  3  OS 工具可用性 tools.registry.verify_all_tools_by_env(env_profile)                                     
  4  规则覆盖      security.rules.list_rules() 覆盖全量 + fuzz_test                                          
  5  PATH_PARAMS   每个带 path 的工具都在 PATH_PARAMETERS 中                                               
  6  环境探测      execution.probe.collect_full() 字段完整性                                                 
  7  输出脱敏      sanitizer fixtures 9 条全部被捕获                                                 
  8  远程指纹      mock paramiko → UnknownHostError → ui.request_fingerprint                                 
  9  故障恢复      chaos 矩阵核心 4 项                                                                     
  10 复现包导出    audit.export.export_repro_pack(sample_session) → 解压校验                                 
  ```                                                                                                        
                                                                                                             
  **示例输出**：                                                                                             
                                                                                                             
  ```text                                                                                                    
  nexus --verify                                                                                             
                                                                                                             
  [ 1/10] API 连通性            ok   (claude-opus-4-7 340ms)                                                 
  [ 2/10] Verifier 模型         ok   (claude-haiku-4-5 180ms)                                                
  [ 3/10] OS 工具可用性矩阵     ok   (21/21)                                                                 
  [ 4/10] 风险规则覆盖          ok   (B001-B017, WH001-WH012, WL001-WL009)                                   
  [ 5/10] PATH_PARAMETERS       ok   (所有工具路径参数已声明)                                              
  [ 6/10] 环境探测              ok   (openEuler 22.03 / systemd / apt)                                     
  [ 7/10] 输出脱敏              ok   (9/9 敏感模式)                                                        
  [ 8/10] 远程主机指纹流程      skip (未配置远程目标)                                                        
  [ 9/10] 故障恢复模拟          ok   (超时/断连/权限不足/取消 4/4)
  [10/10] 复现包导出            ok   (manifest 完整)                                                         
  ```                                                                                                        
                                                                                                             
  ### 10.2 故障注入矩阵                                                                                      
                                                                                                             
  | 用例 | 注入方式 | 期望结果 |                                                                             
  |---|---|---|                                                                                              
  | `timeout` | 工具 handler 显式 `sleep 60`，`timeout=5` | `status="timeout"`，UI 显示超时，不卡死 |        
  | `ssh_disconnect` | 执行期间 iptables DROP 隔离 | SafeExecutor 指数退避重连 1 次，失败后 `status="error"` 
  |                                                                                                          
  | `permission_denied` | 非 root 执行 `read_file("/etc/shadow")` 未被 BLOCK |                               
  `status="error"`，`error_type="permission_denied"` |                                                       
  | `user_cancel` | 模拟 UI 键入 n | `status="user_cancelled"` |                                           
  | `verifier_timeout` | mock Haiku 响应延时 10s | verifier 返回 BLOCK，审计含 LLM_PARSE_FAIL |            
  | `bad_json_from_verifier` | mock Haiku 返回非 JSON | verifier 返回 BLOCK |                              
  | `plan_immutable` | 试图 remove_step 已执行步骤 | 抛 `PlanImmutableError`，审计记录失败 |                 
  | `known_hosts_missing` | 删除 known_hosts 文件 | 指纹流程走一遍 |                                         
  | `config_perm_bad` | `chmod 644 ~/.nexus/.env` | 启动硬拒绝，退出码 10 |                                  
  | `dyntool_overflow` | 注册第 21 个动态工具 | `validate_proposal` 拒绝 |                                   
                                                                                                             
  每个用例在 `tests/integration/chaos/` 下有对应 fixture；`--verify` 选核心子集串行跑。                    
                                                                                                             
  ### 10.3 --demo 场景一览                                                                                   
                                                                                                             
  ```text                                                                                                    
  basics       磁盘/进程/端口/创建用户                                                                       
  risk         BLOCK 拦截                                                                                    
  planning     连续任务 + 动态修改                                                                           
  restart      重启 nginx (WARN-HIGH)                                                                        
  multiturn    多轮追问                                                                                      
  remote       SSH 首次连接 + 指纹确认                                                                     
  multi_distro openEuler + Ubuntu 双环境                                                                     
  voice        语音输入查日志                                                                              
  audit        审计面板回放                                                                                  
  chaos        故障注入                                                                                      
  shell        shell 通道 + verifier                                                                       
  all          全部场景依次播放                                                                              
  ```                                                                                                        
                                                                                                             
  ---                                                                                                        
                                                                                                             
  ## 十一、开发优先级与里程碑                                                                                
                                                                                                             
  ### 11.1 与 FeaturePlan P0-P3 的映射                                                                     
                                                                                                             
  | 优先级 | 本方案模块 |                                                                                  
  |---|---|                                                                                                  
  | P0 主干 | `core/*`、`ai/client.py`（最小 loop）、`tools/registry.py`、`tools/os` 中                    
  `get_disk_usage/list_processes/get_port_status/create_user/delete_user/get_system_info`、`security/classifi
  er.py`（B001-B010+WH001-WH006+WL001-WL005）、`security/sanitizer.py`（基础）、`execution/probe.py`、`execut
  ion/local.py`、`execution/safe_executor.py`、`audit/log.py`（decision+command 两线）、`ui/app.py`（基础对话
   + 确认）、`config/loader.py`、`cli/verify.py` 基础 |
  | P1 完整 | `planning/engine.py`（动态修改）、`tools/meta.py`、`tools/shell.py`、`security/verifier.py`、`s
  ecurity/remote_lockout.py`、`execution/remote.py` + fingerprint、其余 15 个 OS                     
  工具、`tools/dyntool.py`、`ui/audit_panel.py` + `env_panel.py` +                                           
  `dyntool_proposal.py`、`conversation/compactor.py`、`cli/demo.py`、`audit/export.py` |                   
  | P2 加分 | chaos 矩阵 + repro pack manifest、性能面板、RollbackAdvisor（从 audit 反推） |                 
  | P3 可选 | `voice/*`、会话管理 UI 面板 |                                                                
                                                                                                             
  ### 11.2 里程碑                                                                                    
                                         
  ```text                                                                                                    
  M1 (Week 1-2) P0 主干通路                                                                                
    - 本地单步 direct 模式：get_disk_usage / list_processes / create_user                                    
    - BLOCK 规则拦截 PID 1 / /etc/shadow                                                             
    - 审计 JSONL 完整                                                                                        
    - --verify 通过前 5 项                                                                                   
                                                                                                             
  M2 (Week 3) 计划模式 + 元工具                                                                              
    - set_execution_mode(declare_plan / add_step / remove_step / modify_step)                        
    - PlanningEngine 状态机 + 默认/严格策略                                                                
    - compactor + token 预算                                                                                 
                                                                                                             
  M3 (Week 4) 远程 + Verifier + Shell + DynTool                                                              
    - RemoteExecutor + 指纹流程                                                                              
    - RemoteLockoutChecker 共享判定                                                                          
    - CommandVerifier 两阶段 + 缓存                                                                        
    - execute_shell 门控 + 审计                                                                              
    - DynamicToolRegistry 三层执行链                                                                         
                                                                                                             
  M4 (Week 5) 21 工具全开 + TUI 完整                                                                         
    - file_ops / packages / firewall / monitoring / system_config                                            
    - F3 审计面板 / F4 环境面板 / 指纹弹窗 / DynTool 提案弹窗                                                
                                                                                                             
  M5 (Week 6) --demo + repro pack + 故障矩阵                                                                 
    - demo 场景 10+                                                                                          
    - export_repro_pack 打包                                                                                 
    - chaos 全部用例通过                                                                                     
                                                                                                             
  M6 (Week 7) 语音 + 加分项                                                                                  
    - VoiceInput 两后端                                                                                      
    - 性能面板                                                                                               
    - 最终联调 + 文档                                                                                        
  ```                                                                                                        
                                                                                                           
  ---                                                                                                        
                                                                                                           
  ## 十二、附录                                                                                              
                                                                                                             
  ### 12.1 环境变量清单                                                                                    
                                                                                                             
  ```text                                                                                                  
  ANTHROPIC_API_KEY           主模型 + verifier 模型共用                                                     
  NEXUS_CONFIG                覆盖 ~/.nexus/config.toml 路径                                               
  NEXUS_SESSION_DIR           覆盖 ~/.nexus/sessions                                                       
  NEXUS_LOG_LEVEL             debug | info | warning | error                                               
  NEXUS_DISABLE_VOICE         任意值则禁用语音                                                               
  NEXUS_FORCE_LOCAL_EXECUTOR  CI 下强制本地模式
  ```                                                                                                        
                                                                                                     
  ### 12.2 文件权限要求                  
                                                                                                             
  | 路径 | 权限 | 检查策略 |                                                                                 
  |---|---|---|                                                                                              
  | `~/.nexus/.env` | 0600 | 启动硬拒绝 |                                                                    
  | `~/.nexus/config.toml` | ≤ 0644 | 启动警告 |                                                             
  | `~/.nexus/dynamic_tools.json` | 0600 | 创建时 chmod；读时警告 |                                          
  | `~/.nexus/known_hosts` | 0600 | 创建时 chmod |                                                           
  | `~/.nexus/sessions/*/audit.jsonl` | 0600 | 创建时 chmod |                                                
                                                                                                           
  ### 12.3 退出码约定                                                                                      
                                                                                                             
  ```text                                                                                                  
  0   正常结束                                                                                               
  1   通用错误                                                                                       
  2   参数错误                                                                                               
  10  API Key 缺失或不合规                                                                                 
  11  配置文件错误                                                                                           
  20  --verify 子项失败（退出码 = 20 + 失败序号）                                                          
  30  远程连接失败                                                                                           
  40  会话不存在或已过期                                                                                   
  ```                                                                                                        
                                                                                                           
  ### 12.4 命令行速查                                                                                        
                                                                                                     
  ```text                                                                                                    
  nexus                               启动 TUI                                                               
  nexus --simple                      启动 stdin/stdout CLI                                                  
  nexus --remote user@host[:port]     连接远程目标                                                           
  nexus --enable-shell                本轮启用 shell 通道                                                    
  nexus --policy strict               本轮使用严格修改确认策略                                               
  nexus --verify                      自检                                                                 
  nexus --demo [scenario]             演示                                                                   
  nexus --list-sessions               列会话                                                               
  nexus --resume <id>                 恢复                                                                   
  nexus --export-audit <id> [path]    导出 JSONL                                                     
  nexus --export-repro-pack <id> [p]  导出复现包                                                             
  nexus --list-dynamic-tools          列动态工具                                                             
  nexus --delete-dynamic-tool <id>    删除                                                                   
  ```                                                                                                        
                                                                                                             
  ---                                                                                                        
                                                                                                           
  ## 十三、结论                                                                                              
                                                                                                           
  本方案把 FeaturePlan 的 12 个功能模块翻译为 13 个实现层模块、约 50 个 Python 文件、24 个工具（21 OS + 2 元 
  + 1 shell，以及可增长的 DynTool 池）、38 条静态风险规则（B/WH/WL）+ 15 条命令级安全规则（CS）+ 一个双阶段 
  LLM verifier。                                                                                             
                                                                                                           
  **关键契约**——`EnvProfile` / `TurnBundle` / `ToolCall` / `ToolResult` / `Plan` / `PlanStep` /              
  `ExecutionContext` / `Tool` / `DynamicTool` / `DynamicToolProposal` / `DynamicToolRegistry` /              
  `StaticRuleMapper` / `AuditRecord` / `DecisionTrace` / `CommandTraceItem` / `ResultBlock` / `PlanDelta` / 
  `DynamicMeta` / `UIBridge` / `UserConfirmation` / `ConfirmResult`——在第三章一次性锁定；后续模块只能引用、不
  得私自扩张。任何模块替换只要保持契约不变即可独立演进。                                                     
                                                                                                             
  **三条不变式**（4.2 已述，此处重申）：
                                                                                                             
  1. **安全门不可绕过**：`SafeExecutor.execute` 入口断言 `risk_result is not                         
  None`；元工具是唯一例外，但进入审计。                                                                      
  2. **命令不回流**：`command_trace` 从 `ToolResult` 到 `AuditLog` 是单向路径，不进 `messages`、不进 UI    
  主区。                                                                                                     
  3. **审计先于告知**：`AuditLog.append()` fsync 完成才允许 UI 展示"已执行"；写失败整轮报错。        
                                                                                             
  **三条能放大评分的能力**：                                                                               
                                                                                                           
  - **计划动态演进 + 可回放** → 复杂连续任务、交互反馈连贯性、行为一致性、创新性。                           
  - **Verifier 语义判定 + 下限 WARN-LOW** → 高风险识别与处置、风险处置依据、交互友好度。
  - **环境画像 + 事件驱动刷新 + 双发行版** → 环境信息感知、基于环境的安全判断、状态持续更新、多模态无劣变。  
                                                                                                           
  本技术方案与 FeaturePlan 的每一项 feature                                                                  
  一一对应，每一处实现都可追溯到具体文件、函数与数据契约，不留"只在文档里存在"的能力。                     
                                                                                                             
  ---                                                                                                      
  以上就是整份重构后的文档。相对原版的关键改动：                                                             
                                                                                                           
  1. 章节三集中并补齐契约：新增                                                                              
  PlanModification（原只被引用）、PlanningContext、RuleHit、VerifierContext（原散在模块                      
  6）、ExecutionContext（原只在 handler                                                                      
  参数提及）、DecisionTrace/CommandTraceItem/ResultBlock/PlanDelta/DynamicMeta（原 AuditRecord 的字段都是    
  dict）、UserConfirmationTrace、VerifierTrace、DynamicTool/DynamicToolProposal/DynamicToolRegistry/StaticRul
  eMapper/ProposalValidation（原指向 v5.2 未定义）、UIBridge 完整 Protocol、NexusError 异常树。            
  2. 规则全部收敛到第六章：CS001-CS015 补全（原只说"见 v5.2"+补 CS015）；模块 5 不再列规则。                 
  3. 编号规整：去掉"模块 8.5"；14 个模块全部按 4.1-4.13 顺排；DynTool 从 5.4 独立到 4.10。                   
  4. 重复消除：元工具 schema 只在 5.2，模块 4 只引用；verifier 实现细节只在第七章，模块 4.5 只给入口。     
  5. 数据流保留：9.1/9.2/9.3 三条流程全部保留。                                                              
                          