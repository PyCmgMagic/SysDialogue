"""Markdown skill/playbook support for SysDialogue v9."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sysdialogue.security.output_sanitizer import sanitize_value


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    when_to_use: str = ""
    user_invocable: bool = True
    model_invocable: bool = True
    allowed_tools: tuple[str, ...] = ()
    permission: dict[str, Any] = field(default_factory=dict)
    arguments: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    path: str = ""
    scope: str = "user"


@dataclass(frozen=True)
class SkillInvocation:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    source: str = "user"  # user | model
    context: str = ""
    record_path: str = ""


class SkillManager:
    """Load project/user SKILL.md playbooks.

    Project skills override user skills with the same name. Activating a skill
    injects instructions only; it never performs OS work by itself.
    """

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        user_root: str | Path | None = None,
    ):
        self.project_root = Path(project_root or Path.cwd())
        self.user_root = Path(user_root or os.path.expanduser("~/.sysdialogue/skills"))
        self.project_skills_dir = self.project_root / ".sysdialogue" / "skills"
        self._skills: dict[str, SkillRecord] | None = None

    def reload(self) -> list[SkillRecord]:
        loaded: dict[str, SkillRecord] = {}
        for root, scope in ((self.user_root, "user"), (self.project_skills_dir, "project")):
            for skill_file in _iter_skill_files(root):
                record = _load_skill(skill_file, scope=scope)
                if record is not None:
                    loaded[record.name] = record
        self._skills = dict(sorted(loaded.items()))
        return list(self._skills.values())

    def list_skills(self) -> list[SkillRecord]:
        if self._skills is None:
            self.reload()
        return list((self._skills or {}).values())

    def get(self, name: str) -> SkillRecord | None:
        key = _normalize_name(name)
        if not key:
            return None
        if self._skills is None:
            self.reload()
        return (self._skills or {}).get(key)

    def activate(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        source: str = "user",
    ) -> SkillInvocation:
        record = self.get(name)
        if record is None:
            raise KeyError(f"Skill not found: {name}")
        if source == "user" and not record.user_invocable:
            raise PermissionError(f"Skill is not user-invocable: {record.name}")
        if source == "model" and not record.model_invocable:
            raise PermissionError(f"Skill is not model-invocable: {record.name}")
        safe_args = _json_object(sanitize_value(args or {}))
        context = _render_skill_context(record, safe_args)
        return SkillInvocation(
            name=record.name,
            args=safe_args,
            source=source,
            context=context,
            record_path=record.path,
        )

    def render_prompt_summary(self, *, limit: int = 12) -> str:
        skills = [skill for skill in self.list_skills() if skill.model_invocable]
        if not skills:
            return "[Skills]\nNo model-invocable skills are currently installed."
        lines = ["[Skills]"]
        for skill in skills[:limit]:
            when = f" Use when: {skill.when_to_use}" if skill.when_to_use else ""
            tools = f" Allowed tools hint: {', '.join(skill.allowed_tools)}." if skill.allowed_tools else ""
            lines.append(f"- {skill.name}: {skill.description}{when}{tools}")
        if len(skills) > limit:
            lines.append(f"- ... {len(skills) - limit} more skills available via activate_skill.")
        return "\n".join(lines)


def _iter_skill_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.glob("*/SKILL.md") if path.is_file())


def _load_skill(path: Path, *, scope: str) -> SkillRecord | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _split_frontmatter(text)
    name = _normalize_name(meta.get("name") or path.parent.name)
    if not name:
        return None
    return SkillRecord(
        name=name,
        description=str(meta.get("description") or _first_sentence(body) or name),
        when_to_use=str(meta.get("when_to_use") or ""),
        user_invocable=bool(meta.get("user_invocable", True)),
        model_invocable=bool(meta.get("model_invocable", True)),
        allowed_tools=tuple(str(item) for item in (meta.get("allowed_tools") or []) if str(item).strip()),
        permission=dict(meta.get("permission") or {}),
        arguments=dict(meta.get("arguments") or {}),
        body=body.strip(),
        path=str(path),
        scope=scope,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), parts[2]


def _render_skill_context(record: SkillRecord, args: dict[str, Any]) -> str:
    payload = {
        "name": record.name,
        "description": record.description,
        "when_to_use": record.when_to_use,
        "allowed_tools": list(record.allowed_tools),
        "arguments": args,
        "source_path": record.path,
    }
    return (
        "[Activated Skill]\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n[Skill Instructions]\n"
        + record.body
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "-")
    text = re.sub(r"[^a-z0-9_.-]+", "-", text)
    return text.strip("-")


def _first_sentence(text: str) -> str:
    clean = " ".join(str(text or "").strip().split())
    if not clean:
        return ""
    return clean.split(". ", 1)[0][:160]
