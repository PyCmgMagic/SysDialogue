# 07. 问题闭环说明

本文件汇总开发和审查过程中发现的主要问题、处理结果与验证证据。代码证据以当前 `main` 为准。

| # | 问题摘要 | 处理结果 | 代码/测试证据 |
| --- | --- | --- | --- |
| 1 | workflow 资源锁只保护单进程 | `lock_scope` 使用 `LockStore` 持久租约；无任务归属时按失败处理。 | `sysdialogue/agent/workflow_engine.py`；`tests/test_workflow_engine.py` |
| 2 | sessions 只在内存 | state 读取 `SessionStore`，entries/events/task/pending 持久化。 | `sysdialogue/agent/state_store.py`；`tests/test_react_runner.py` |
| 3 | TUI 直接展示 traceback | turn 异常走 `present_error()`，技术详情进入 `technical_details`。 | `sysdialogue/agent/error_presentation.py` |
| 4 | 计划模式约束不足 | 计划冻结后由 `TaskStore` 持久化步骤，`ReActRunner` 拒绝偏离下一可执行步骤。 | `sysdialogue/agent/react_runner.py`；`tests/test_react_runner.py` |
| 5 | 历史设计文档存在旧表述 | `claudeplan9.md` 为唯一当前基线；v6/v7/v8 为历史参考。 | `framework/claudeplan9.md`；`RUNNING.md` |
| 6 | direct mutation 绕过 durable lock | direct mutating tools 通过 `_direct_lock_scopes()` 获取 `LockStore` lease。 | `sysdialogue/agent/controller.py`；`tests/test_react_runner.py` |
| 7 | session transcript 丢 user turns | `SessionStore.append_user_turn()` 和 `save_turn()` 记录 user/assistant entries。 | `sysdialogue/agent/state_store.py`；`tests/test_react_runner.py` |
| 8 | 新 runtime 未恢复历史上下文 | `runtime_factory.create_runtime()` 从 `SessionStore` 恢复 ConversationManager。 | `sysdialogue/app/runtime_factory.py`；`tests/test_cli_entrypoints.py` |
| 9 | pending 不可恢复 | pending descriptor 持久化；同进程可 resolve，丢失 owner 时标记 interrupted。 | `sysdialogue/agent/state_store.py`；`tests/test_react_runner.py` |
| 10 | TUI history 与 shared sessions 分裂 | `ConversationStore` 作为 `SessionStore` 兼容包装。 | `sysdialogue/agent/conversation_store.py`；`tests/test_conversation_store.py` |
| 11 | 状态枚举旧协议 | TUI 识别 running/waiting_confirm/waiting_input/interrupted。 | `sysdialogue/ui/tui_app.py`；`tests/test_react_runner.py` |
| 12 | pending 仍依赖 Event | Event 用于活线程等待，持久化 pending 用于 recover。 | `sysdialogue/agent/state_store.py`；`tests/test_react_runner.py` |
| 13 | 中文显示异常 | TUI 标签测试验证中文正常渲染。 | `tests/test_agent_upgrade_features.py` |
| 14 | resume 依赖用户话术 | 显式 `/resume`、controller forced resume；普通”继续”不猜测。 | `sysdialogue/agent/react_runner.py`；`tests/test_react_runner.py` |
| 15 | direct 锁的只读判断不严谨 | `value=""` 视为设置类变更。 | `sysdialogue/agent/controller.py`；`tests/test_react_runner.py` |
| 16 | TraceStore 写入非跨进程安全 | Trace JSONL append 使用 `FileLock`，读取容忍损坏行。 | `sysdialogue/agent/trace_store.py`；`tests/test_agent_upgrade_features.py` |
| 17 | Memory 写入会丢并发更新 | Memory read-modify-write 使用 `FileLock`。 | `sysdialogue/agent/memory.py`；`tests/test_agent_upgrade_features.py` |
| 18 | Permission precedence 脆弱 | most specific wins，同 specificity later wins，explain 输出候选规则。 | `sysdialogue/agent/permission_policy.py`；`tests/test_agent_upgrade_features.py` |
| 19 | Trace 脱敏漏 generic string | TraceStore 复用敏感字符串脱敏，对 output/error/summary 生效。 | `sysdialogue/agent/trace_store.py`；`tests/test_agent_upgrade_features.py` |
| 20 | `/resume` 记录合成用户输入 | transcript 保留用户显式 `/resume`，恢复目标作为 internal metadata。 | `sysdialogue/agent/controller.py`；`tests/test_react_runner.py` |

## 提交材料说明口径

- 不把 `framework/claudeplan6.md` 作为当前实现依据；只说明它是历史文档。
- 若评审关注问题闭环，可展示本文件和对应测试。
- 若评委要求现场验证，可运行：

```powershell
python -m pytest -q
python -m sysdialogue.app.cli --verify
```

