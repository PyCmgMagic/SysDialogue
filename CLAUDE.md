# SysDialogue — 开发进度记录

## 项目简介

SysDialogue v6：面向 Linux 服务器运维场景的操作系统智能代理。
设计文档：`framework/claudeplan6.md`

## 技术栈

- Python 3.11+
- anthropic SDK（Claude API，agentic loop）
- Textual（TUI 界面）
- Paramiko（SSH 远程执行）
- Pydantic（数据校验）
- PyYAML（工作流定义）
- Jinja2（工作流参数插值）

## 项目结构

```
sysdialogue/
├── agent/          # 对话引擎（AgentController, PlanningEngine, WorkflowEngine）
├── runtime/        # 执行适配层（SafeExecutor, LocalExecutor, RemoteExecutor, CapabilityProbe）
├── tools/          # 37 个静态工具 + 动态工具注册表
├── security/       # 安全门（RiskClassifier, RemoteLockoutChecker, CommandSafetyChecker）
├── workflows/      # 10 个内置工作流 YAML
├── audit/          # 审计日志（JSONL）
├── ui/             # TUI 界面（Textual）
└── app/            # 入口（CLI, config, verify）
```

## 已完成模块

### ✅ 项目脚手架
- `pyproject.toml` — 项目配置与依赖
- `requirements.txt` — 依赖清单
- `sysdialogue/` 全部子包 `__init__.py`

---

## 开发优先级参考（来自 claudeplan6.md §12）

### P0（最优先）
- [ ] EnvProfile + CapabilityProbe
- [ ] RiskClassifier (B001-B031 / WH001-WH025 / WL001-WL017)
- [ ] RemoteLockoutChecker
- [ ] LocalExecutor / RemoteExecutor / SafeExecutor
- [ ] AuditLog
- [ ] v4.1 工具（10个）
- [ ] v5.4 核心工具：list_directory / stat_path / search_file_content / backup_path / replace_in_file / validate_config
- [ ] ToolRegistry + AgentController + ClaudeClient

### P1
- [ ] v5.3 工具（9个）
- [ ] v5.4 网络诊断工具
- [ ] CommandSafetyChecker
- [ ] safe_config_patch.yaml / rollback_config.yaml
- [ ] UI（ConfirmModal / 审计面板 / 环境画像面板）

### P2
- [ ] manage_archive / manage_mount / manage_authorized_keys / manage_power / manage_container
- [ ] DynamicToolRegistry（竞赛关闭）
- [ ] OutputSanitizer
