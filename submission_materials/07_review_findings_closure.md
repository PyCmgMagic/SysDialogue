# 07. Review Findings 闭环说明

本文件用于解释用户列出的 review findings 在当前提交材料中的处理口径。代码证据以当前 `main` 为准。

| # | Finding 摘要 | 当前处理 | 代码/测试证据 |
| --- | --- | --- | --- |
| 1 | workflow resource lock 只保护单进程 | `lock_scope` 使用 `LockStore` durable lease；无 task owner 时 fail-closed。 | `sysdialogue/agent/workflow_engine.py`；`tests/test_workflow_engine.py` |
| 2 | Web sessions 只在内存 | Web state 读取 `SessionStore`，entries/events/task/pending 持久化。 | `sysdialogue/web/service.py`；`tests/test_web_service.py` |
| 3 | Web 直接展示 traceback | Web turn 异常走 `present_error()`，技术详情进入 `technical_details`。 | `sysdialogue/web/service.py`；`sysdialogue/agent/error_presentation.py` |
| 4 | plan mode advisory | plan freeze 后由 `TaskStore` 持久化 steps，`ReActRunner` 拒绝偏离 next executable step。 | `sysdialogue/agent/react_runner.py`；`tests/test_react_runner.py` |
| 5 | claudeplan6 仍有旧 competition mode | `claudeplan9.md` 为唯一当前基线；v6/v7/v8 为历史参考。 | `framework/claudeplan9.md`；`CLAUDE.md`；`RUNNING.md` |
| 6 | direct mutation 绕过 durable lock | direct mutating tools 通过 `_direct_lock_scopes()` 获取 `LockStore` lease。 | `sysdialogue/agent/controller.py`；`tests/test_react_runner.py` |
| 7 | session transcript 丢 user turns | `SessionStore.append_user_turn()` 和 `save_turn()` 记录 user/assistant entries。 | `sysdialogue/agent/state_store.py`；`tests/test_react_runner.py` |
| 8 | fresh runtime 不 hydrate history | `runtime_factory.create_runtime()` 从 `SessionStore` 恢复 ConversationManager。 | `sysdialogue/app/runtime_factory.py`；`tests/test_cli_entrypoints.py` |
| 9 | Web pending 不可恢复 | pending descriptor 持久化；同进程/跨请求可 resolve，丢失 owner 时标记 interrupted。 | `sysdialogue/web/service.py`；`tests/test_web_service.py` |
| 10 | TUI history 与 shared sessions 分裂 | `ConversationStore` 作为 `SessionStore` 兼容包装。 | `sysdialogue/agent/conversation_store.py`；`tests/test_conversation_store.py` |
| 11 | Web 状态枚举旧协议 | 模板识别 running/waiting_confirm/waiting_input/interrupted。 | `sysdialogue/web/templates/index.html`；`tests/test_web_template_contract.py` |
| 12 | Web pending 仍依赖 Event | Event 用于活线程等待，持久化 pending 用于跨请求 resolve/recover。 | `sysdialogue/web/service.py`；`tests/test_web_service.py` |
| 13 | Web 中文 mojibake | 模板测试验证中文标签且拒绝 mojibake 样本。 | `tests/test_web_template_contract.py` |
| 14 | resume 依赖用户话术 | 显式 `/resume`、Web `/resume` API、controller forced resume；普通“继续”不猜测。 | `sysdialogue/agent/react_runner.py`；`tests/test_react_runner.py` |
| 15 | direct 锁只读判断 truthiness | `value=""` 视为 set/mutation。 | `sysdialogue/agent/controller.py`；`tests/test_react_runner.py` |
| 16 | TraceStore 写入非跨进程安全 | Trace JSONL append 使用 `FileLock`，读取容忍损坏行。 | `sysdialogue/agent/trace_store.py`；`tests/test_agent_upgrade_features.py` |
| 17 | Memory 写入会丢并发更新 | Memory read-modify-write 使用 `FileLock`。 | `sysdialogue/agent/memory.py`；`tests/test_agent_upgrade_features.py` |
| 18 | Permission precedence 脆弱 | most specific wins，同 specificity later wins，explain 输出候选规则。 | `sysdialogue/agent/permission_policy.py`；`tests/test_agent_upgrade_features.py` |
| 19 | Trace 脱敏漏 generic string | TraceStore 复用敏感字符串脱敏，对 output/error/summary 生效。 | `sysdialogue/agent/trace_store.py`；`tests/test_agent_upgrade_features.py` |
| 20 | `/resume` 记录合成用户输入 | transcript 保留用户显式 `/resume`，恢复目标作为 internal metadata。 | `sysdialogue/agent/controller.py`；`tests/test_react_runner.py` |

## 提交材料中的说明建议

- 不把 `framework/claudeplan6.md` 作为当前实现依据；只说明它是历史文档。
- 若评委问到 review findings，应展示本文件和对应测试。
- 若评委要求现场验证，可运行：

```powershell
python -m pytest -q
python -m sysdialogue.app.cli --verify
```

