"""Target profile persistence for SysDialogue v9."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from sysdialogue.security.output_sanitizer import sanitize_text, sanitize_value


@dataclass
class TargetProfile:
    target_id: str
    label: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    common_services: list[str] = field(default_factory=list)
    risk_preferences: dict[str, Any] = field(default_factory=dict)
    last_verification: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TargetProfileStore:
    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.sysdialogue/targets"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def target_id_from_env(self, env_profile: dict[str, Any] | None) -> str:
        env = env_profile or {}
        if env.get("remote_mode"):
            host = str(env.get("host") or env.get("hostname") or "remote")
            port = str(env.get("ssh_port") or env.get("port") or "22")
            return _safe_id(f"ssh-{host}-{port}")
        return _safe_id(str(env.get("hostname") or "local"))

    def load(self, target_id: str) -> TargetProfile | None:
        path = self._path(target_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return TargetProfile(
            target_id=str(data.get("target_id") or target_id),
            label=str(data.get("label") or ""),
            facts=dict(data.get("facts") or {}),
            common_services=[str(item) for item in data.get("common_services") or []],
            risk_preferences=dict(data.get("risk_preferences") or {}),
            last_verification=str(data.get("last_verification") or ""),
            updated_at=str(data.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )

    def save(self, profile: TargetProfile) -> TargetProfile:
        profile.updated_at = datetime.now(timezone.utc).isoformat()
        path = self._path(profile.target_id)
        lock = FileLock(str(path) + ".lock", timeout=10)
        with lock:
            tmp = path.with_suffix(path.suffix + ".tmp")
            payload = sanitize_value(asdict(profile))
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return profile

    def delete(self, target_id: str) -> bool:
        path = self._path(target_id)
        lock = FileLock(str(path) + ".lock", timeout=10)
        with lock:
            if not path.exists():
                return False
            path.unlink()
            return True

    def remember_fact(self, target_id: str, key: str, value: Any) -> TargetProfile:
        profile = self.load(target_id) or TargetProfile(target_id=target_id)
        safe_key = sanitize_text(str(key), limit=120).strip() or "fact"
        profile.facts[safe_key] = sanitize_value(value, limit=2000)
        return self.save(profile)

    def list_profiles(self, limit: int = 30) -> list[TargetProfile]:
        profiles = [profile for profile in (self.load(path.stem) for path in self.storage_dir.glob("*.json")) if profile]
        profiles.sort(key=lambda profile: profile.updated_at, reverse=True)
        return profiles[:limit]

    def render_prompt_summary(self, target_id: str) -> str:
        profile = self.load(target_id)
        if profile is None:
            return f"[Target Profile]\nCurrent target: {target_id}. No persisted profile yet."
        lines = [f"[Target Profile]\nCurrent target: {profile.target_id}"]
        if profile.label:
            lines.append(f"Label: {profile.label}")
        for key, value in sorted(profile.facts.items()):
            lines.append(f"- {sanitize_text(key, limit=120)}: {sanitize_text(value, limit=1000)}")
        if profile.common_services:
            lines.append("Common services: " + ", ".join(profile.common_services[:10]))
        if profile.last_verification:
            lines.append("Last verification: " + profile.last_verification)
        return "\n".join(lines)

    def _path(self, target_id: str) -> Path:
        return self.storage_dir / f"{_safe_id(target_id)}.json"


def _safe_id(value: str) -> str:
    text = str(value or "default").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "-", text)
    return text.strip("-") or "default"
