"""Model adapter diagnostics for OpenAI-compatible tool calling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sysdialogue.security.output_sanitizer import sanitize_text


DIAGNOSTIC_TOOL_NAME = "diagnostic_ping"


@dataclass
class ModelDiagnosticResult:
    ok: bool
    status: str
    summary: str
    model: str = ""
    base_url: str = ""
    tool_name: str = DIAGNOSTIC_TOOL_NAME
    stop_reason: str = ""
    error_type: str = ""
    technical_details: str = ""
    next_steps: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [
            "Model tool-call diagnostic:",
            f"- Status: {self.status}",
            f"- Summary: {self.summary}",
        ]
        if self.model:
            lines.append(f"- Model: {self.model}")
        if self.base_url:
            lines.append(f"- Base URL: {self.base_url}")
        if self.stop_reason:
            lines.append(f"- Stop reason: {self.stop_reason}")
        if self.error_type:
            lines.append(f"- Error type: {self.error_type}")
        if self.technical_details:
            lines.append(f"- Detail: {self.technical_details}")
        if self.next_steps:
            lines.append("")
            lines.append("Next steps:")
            lines.extend(f"- {item}" for item in self.next_steps)
        return "\n".join(lines)


def diagnose_tool_call_support(llm_client: Any) -> ModelDiagnosticResult:
    """Call the configured model once and verify it can emit a function/tool call.

    This uses a synthetic diagnostic tool only. It never dispatches OS-facing
    SysDialogue tools and does not mutate local or remote systems.
    """
    model = sanitize_text(getattr(llm_client, "model", "") or "", limit=200)
    base_url = sanitize_text(getattr(llm_client, "base_url", "") or "", limit=300)
    try:
        response = llm_client.messages_create(
            system=(
                "You are running a SysDialogue model adapter diagnostic. "
                "Call the diagnostic_ping tool exactly once with ok=true. "
                "Do not answer in plain text."
            ),
            messages=[
                {
                    "role": "user",
                    "content": "Run the tool-call diagnostic now.",
                }
            ],
            tools=[_diagnostic_tool_schema()],
        )
    except Exception as exc:
        return ModelDiagnosticResult(
            ok=False,
            status="failed",
            summary="The configured model endpoint could not complete a Chat Completions tool-call request.",
            model=model,
            base_url=base_url,
            error_type=type(exc).__name__,
            technical_details=sanitize_text(str(exc), limit=1000),
            next_steps=[
                "Check OPENAI_API_KEY, OPENAI_BASE_URL, and OPENAI_MODEL.",
                "Confirm the endpoint is a Chat Completions-compatible /v1 API.",
                "Confirm the selected model supports function/tool calls.",
            ],
        )

    tool_blocks = [
        block for block in _content_as_list(getattr(response, "content", []))
        if _block_type(block) == "tool_use"
    ]
    matching = [
        block for block in tool_blocks
        if _block_attr(block, "name") == DIAGNOSTIC_TOOL_NAME
    ]
    stop_reason = sanitize_text(getattr(response, "stop_reason", "") or "", limit=120)
    if matching:
        return ModelDiagnosticResult(
            ok=True,
            status="ok",
            summary="The model returned a valid tool call for the diagnostic tool.",
            model=model,
            base_url=base_url,
            stop_reason=stop_reason,
        )

    visible_text = _visible_text(getattr(response, "content", []))
    detail = (
        f"Model returned {len(tool_blocks)} tool call(s), but none matched {DIAGNOSTIC_TOOL_NAME}."
        if tool_blocks
        else f"Model returned plain text instead of a tool call: {visible_text or '(empty response)'}"
    )
    return ModelDiagnosticResult(
        ok=False,
        status="failed",
        summary="The endpoint responded, but the model did not follow the tool-call protocol.",
        model=model,
        base_url=base_url,
        stop_reason=stop_reason,
        technical_details=sanitize_text(detail, limit=1000),
        next_steps=[
            "Use a model/endpoint that supports Chat Completions tool_calls.",
            "If this is an OpenAI-compatible proxy, verify it forwards the tools and tool_choice fields.",
            "Try a smaller diagnostic model call before running an operational task.",
        ],
    )


def _diagnostic_tool_schema() -> dict[str, Any]:
    return {
        "name": DIAGNOSTIC_TOOL_NAME,
        "description": "Return a diagnostic acknowledgement for SysDialogue model adapter checks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["ok"],
        },
    }


def _content_as_list(content) -> list:
    if isinstance(content, list):
        return content
    return [content]


def _block_type(block) -> str:
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _block_attr(block, key: str):
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _visible_text(content) -> str:
    parts: list[str] = []
    for block in _content_as_list(content):
        if _block_type(block) == "text":
            text = _block_attr(block, "text") or ""
            if text:
                parts.append(str(text))
    return sanitize_text("\n".join(parts), limit=500)
