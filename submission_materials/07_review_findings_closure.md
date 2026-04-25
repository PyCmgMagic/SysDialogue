# 07. 问题闭环说明

本文件汇总开发和审查过程中发现的主要问题、处理结果与验证证据。代码证据以 `main` 分支为准。

| # | 问题摘要 | 处理结果 | 代码/测试证据 |
| --- | --- | --- | --- |
| 1 | workflow 资源锁只保护单进程 | `lock_scope` 使用 `LockStore` 持久租约；无任务归属时按失败处理。 | `sysdialogue/agent/workflow_engine.py`；`tests/test_workflow_engine.py` |
| 2 | session 状态只保存在内存 | 状态读取改为 `SessionStore`，对话、事件、任务和 pending 信息持久化保存。 | `sysdialogue/agent/state_store.py`；`tests/test_react_runner.py` |
| 3 | TUI 直接展示异常堆栈 | 异常通过 `present_error()` 转为用户可读摘要，技术细节进入折叠区域。 | `sysdialogue/agent/error_presentation.py` |
| 4 | 计划模式约束不足 | 计划冻结后由 `TaskStore` 持久化步骤，`ReActRunner` 拒绝偏离下一可执行步骤。 | `sysdialogue/agent/react_runner.py`；`tests/test_react_runner.py` |
| 5 | 历史设计文档存在旧表述 | `claudeplan9.md` 为当前设计基线；v6/v7/v8 仅作为历史参考。 | `framework/claudeplan9.md`；`RUNNING.md` |
| 6 | 直接变更工具可能绕过持久锁 | 直接变更类工具通过 `_direct_lock_scopes()` 获取 `LockStore` 租约。 | `sysdialogue/agent/controller.py`；`tests/test_react_runner.py` |
| 7 | 会话记录缺少用户输入 | `SessionStore.append_user_turn()` 和 `save_turn()` 持久化用户与系统回复。 | `sysdialogue/agent/state_store.py`；`tests/test_react_runner.py` |
| 8 | 新 runtime 未恢复历史上下文 | `runtime_factory.create_runtime()` 从 `SessionStore` 恢复 ConversationManager。 | `sysdialogue/app/runtime_factory.py`；`tests/test_cli_entrypoints.py` |
| 9 | pending 状态不可恢复 | pending 描述持久化；同进程可继续处理，丢失归属时标记为 interrupted。 | `sysdialogue/agent/state_store.py`；`tests/test_react_runner.py` |
| 10 | TUI 历史与共享 session 不一致 | `ConversationStore` 作为 `SessionStore` 的兼容包装。 | `sysdialogue/agent/conversation_store.py`；`tests/test_conversation_store.py` |
| 11 | 状态枚举仍使用旧协议 | TUI 识别 running、waiting_confirm、waiting_input、interrupted 等状态。 | `sysdialogue/ui/tui_app.py`；`tests/test_react_runner.py` |
| 12 | pending 等待依赖进程内 Event | Event 仅用于活跃线程等待；持久化 pending 用于恢复和中断判断。 | `sysdialogue/agent/state_store.py`；`tests/test_react_runner.py` |
| 13 | 中文标签显示异常 | TUI 标签测试覆盖中文渲染。 | `tests/test_agent_upgrade_features.py` |
| 14 | resume 行为依赖自然语言猜测 | 恢复操作通过显式 `/resume` 或 controller forced resume 触发。 | `sysdialogue/agent/react_runner.py`；`tests/test_react_runner.py` |
| 15 | direct 锁的只读判断不严谨 | `value=""` 视为设置类变更。 | `sysdialogue/agent/controller.py`；`tests/test_react_runner.py` |
| 16 | TraceStore 写入缺少跨进程保护 | Trace JSONL append 使用 `FileLock`，读取时容忍损坏行。 | `sysdialogue/agent/trace_store.py`；`tests/test_agent_upgrade_features.py` |
| 17 | Memory 并发写入可能丢失更新 | Memory read-modify-write 使用 `FileLock`。 | `sysdialogue/agent/memory.py`；`tests/test_agent_upgrade_features.py` |
| 18 | 权限规则优先级不稳定 | 权限策略采用“更具体规则优先、同等具体度后写规则优先”，并在解释中输出候选规则。 | `sysdialogue/agent/permission_policy.py`；`tests/test_agent_upgrade_features.py` |
| 19 | Trace 脱敏覆盖不完整 | TraceStore 复用敏感字符串脱敏逻辑，对 output、error、summary 生效。 | `sysdialogue/agent/trace_store.py`；`tests/test_agent_upgrade_features.py` |
| 20 | `/resume` 恢复目标混入用户输入 | transcript 保留用户显式 `/resume`，恢复目标作为内部元数据记录。 | `sysdialogue/agent/controller.py`；`tests/test_react_runner.py` |

## 材料说明

- `framework/claudeplan9.md` 是当前实现依据，旧版本设计文档仅作为历史参考。
- 问题闭环以本文件和对应测试为验证材料。
- 验证命令：

```powershell
python -m pytest -q
python -m sysdialogue.app.cli --verify
```
