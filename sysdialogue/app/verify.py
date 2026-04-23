"""自检 (--verify) 与演示 (--demo) 入口。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sysdialogue.app.config import AppConfig


def run_verify(config: "AppConfig") -> int:
    """自检模式 — 不调用 Claude API，只检查：
    1. 探测 EnvProfile
    2. 列出 37 个静态工具 + 元工具 Schema 注册数
    3. 列出 10 个内置 workflow
    4. 打印核心安全规则计数
    5. 检查 API Key 是否配置
    返回 exit code：0=ok / 非 0=问题。
    """
    print("=" * 60)
    print(" SysDialogue v6 — 自检模式 (--verify)")
    print("=" * 60)

    issues: list[str] = []

    # 1. EnvProfile 探测
    try:
        from sysdialogue.runtime.capability_probe import (
            CapabilityProbe, EnvProfileSanitizer,
        )
        from sysdialogue.runtime.secure_runner import LocalExecutor
        probe = CapabilityProbe(LocalExecutor(), remote_mode=config.remote_mode,
                                ssh_port=config.ssh_port)
        profile = probe.probe()
        sanitized = EnvProfileSanitizer.sanitize(profile)
        print("\n[1/5] 环境画像（脱敏）：")
        for k, v in sanitized.items():
            print(f"  {k}: {v}")
    except Exception as e:
        issues.append(f"EnvProfile 探测失败：{e}")
        print(f"  ✗ {e}")

    # 2. ToolRegistry
    try:
        from sysdialogue.tools.registry import default_registry
        from sysdialogue.tools.meta_tools import META_TOOL_SCHEMAS
        reg = default_registry()
        print(f"\n[2/5] 工具注册：{len(reg.all_schemas())} 个静态工具"
              f" + {len(META_TOOL_SCHEMAS)} 个元工具")
        for name, desc in reg.describe()[:5]:
            head = desc.split("。")[0] if desc else ""
            print(f"  - {name}: {head}")
        print(f"  ... 共 {len(reg.names())} 个（完整列表省略）")
    except Exception as e:
        issues.append(f"ToolRegistry 加载失败：{e}")

    # 3. 内置 workflow
    try:
        workflows_dir = Path(config.workflows_dir) if config.workflows_dir else \
            Path(__file__).parent.parent / "workflows"
        yamls = sorted(workflows_dir.glob("*.yaml"))
        print(f"\n[3/5] 内置工作流：{len(yamls)} 个")
        for y in yamls:
            print(f"  - {y.stem}")
        if len(yamls) != 10:
            issues.append(f"工作流数量异常：预期 10，实际 {len(yamls)}")
    except Exception as e:
        issues.append(f"Workflow 目录扫描失败：{e}")

    # 4. 安全规则统计
    try:
        from sysdialogue.security import risk_classifier as rc
        from sysdialogue.security import command_safety as cs
        print("\n[4/5] 安全规则：")
        print(f"  - RiskClassifier 覆盖工具：{len(rc._CLASSIFIERS)} 个")
        print("  - CommandSafetyChecker：CS001-CS009（9 条形态规则 + 远程锁门叠加）")
        print("  - RemoteLockoutChecker：B010 / B015-B017 / WH023")
    except Exception as e:
        issues.append(f"安全规则模块加载失败：{e}")

    # 5. API Key 检查
    print("\n[5/5] 配置：")
    print(f"  - 模型：{config.model}")
    print(f"  - 竞赛模式：{config.competition_mode}")
    print(f"  - 部署模式：{'远程' if config.remote_mode else '本地'}")
    if config.api_key:
        print(f"  - ANTHROPIC_API_KEY：已配置（{config.api_key[:8]}…）")
    else:
        print("  - ANTHROPIC_API_KEY：未配置（启动 TUI 前需要设置）")
        issues.append("ANTHROPIC_API_KEY 未配置")

    print("\n" + "=" * 60)
    if issues:
        print(f"⚠️  自检发现 {len(issues)} 个问题：")
        for i, msg in enumerate(issues, 1):
            print(f"  {i}. {msg}")
        print("=" * 60)
        return 1
    print("✅ 自检通过，系统可启动。")
    print("=" * 60)
    return 0


def run_demo(config: "AppConfig") -> int:
    """演示模式 — 不调用 Claude API，直接跑 security_audit 工作流展示 workflow 引擎。

    适合无 API Key 环境下的功能演示。
    """
    print("=" * 60)
    print(" SysDialogue v6 — 演示模式 (--demo)")
    print(" 场景：security_audit 工作流（只读巡查）")
    print("=" * 60)

    from sysdialogue.agent.controller import AgentController
    from sysdialogue.agent.workflow_engine import WorkflowEngine
    from sysdialogue.audit.trace_store import AuditLog
    from sysdialogue.runtime.capability_probe import CapabilityProbe
    from sysdialogue.runtime.secure_runner import LocalExecutor
    from sysdialogue.tools.registry import default_registry

    # 构造骨架（不需要真实 Claude）
    audit = AuditLog(session_id="demo")
    executor = LocalExecutor()
    probe = CapabilityProbe(executor, remote_mode=config.remote_mode,
                            ssh_port=config.ssh_port)
    profile = probe.probe()

    class _NullClaude:
        def messages_create(self, *, system, messages, tools):
            raise RuntimeError("演示模式不调用 Claude")

    ctrl = AgentController(
        executor=executor,
        env_profile=profile,
        audit_log=audit,
        registry=default_registry(),
        claude_client=_NullClaude(),
        confirm_callback=lambda req: True,  # 演示模式自动批准
        competition_mode=config.competition_mode,
    )
    workflows_dir = config.workflows_dir or \
        str(Path(__file__).parent.parent / "workflows")
    engine = WorkflowEngine(controller=ctrl, workflows_dir=workflows_dir)

    print("\n[运行] security_audit.yaml ...")
    execution = engine.run("security_audit", {})
    print(f"\n[结果] final_status = {execution.final_status}")
    print(f"[结果] message = {execution.final_message}")
    print("\n[步骤状态]")
    for sid, r in execution.steps_state.items():
        err = f" — {r.error}" if r.error else ""
        print(f"  {sid}: {r.status}{err}")

    print(f"\n[审计] 会话 ID：{audit.session_id}")
    print(f"[审计] 日志路径：{audit.path}")
    print("\n" + "=" * 60)
    return 0 if execution.final_status in ("completed", "rolled_back") else 1
