"""Simple stdin/stdout CLI for lightweight interaction."""

from __future__ import annotations

from sysdialogue.app.runtime_factory import create_runtime


def run_simple_cli(config) -> int:
    runtime = create_runtime(
        config,
        require_api=True,
        confirm_callback=_confirm_callback,
        input_callback=_input_callback,
    )
    runtime.controller.event_callback = _event_callback
    try:
        print("SysDialogue Simple CLI")
        print("输入运维需求开始对话，输入 quit / exit 退出。")
        while True:
            try:
                text = input("you> ").strip()
            except EOFError:
                print()
                break
            if not text:
                continue
            if text.lower() in {"quit", "exit"}:
                break
            if text.lower() == "cancel":
                runtime.controller.request_cancel()
                print("system> 已请求取消当前执行。")
                continue
            reply = runtime.controller.run_turn(text)
            print(f"sysdialogue> {reply}")
        return 0
    finally:
        runtime.close()


def _confirm_callback(req) -> bool:
    print("\n[需要确认]")
    print(f"工具: {req.tool}")
    print(f"风险: {req.risk.level}")
    print(f"原因: {req.risk.reason}")
    if req.rollback_hint:
        print(f"回滚: {req.rollback_hint}")
    answer = input("批准执行？[y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def _input_callback(prompt: str, multiline: bool) -> str:
    print(f"\n[需要输入] {prompt}")
    if not multiline:
        return input("> ")
    print("输入多行内容，单独输入 '.' 结束。")
    lines: list[str] = []
    while True:
        line = input()
        if line == ".":
            break
        lines.append(line)
    return "\n".join(lines)


def _event_callback(event) -> None:
    stage = getattr(event, "stage", "event")
    message = getattr(event, "message", "")
    print(f"system> [{stage}] {message}")
