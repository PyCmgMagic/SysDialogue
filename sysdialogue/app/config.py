"""应用配置加载 — API Key / 模型 / 部署模式。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    remote_mode: bool = False
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_key_file: str = ""
    ssh_password: str = ""
    ssh_sudo_password: str = ""
    workflows_dir: str = ""  # 空则默认 sysdialogue/workflows/
    max_iterations: int = 160


def load_config(
    *,
    env_file: str | None = None,
    model: str | None = None,
    remote: bool = False,
    ssh: dict | None = None,
) -> AppConfig:
    """从环境变量 + 可选 .env 文件加载配置。"""
    # 优先加载 .env
    if env_file and Path(env_file).exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except ImportError:
            pass
    elif Path(".env").exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(".env")
        except ImportError:
            pass

    cfg = AppConfig(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL", ""),
        model=model or os.environ.get("OPENAI_MODEL", "") or os.environ.get("SYSDIALOGUE_MODEL", ""),
        remote_mode=remote,
        max_iterations=_env_int_clamped("SYSDIALOGUE_MAX_ITER", default=160, minimum=20, maximum=300),
        workflows_dir=os.environ.get("SYSDIALOGUE_WORKFLOWS_DIR", ""),
    )
    if ssh:
        cfg.ssh_host = ssh.get("host", "")
        cfg.ssh_port = int(ssh.get("port", 22))
        cfg.ssh_user = ssh.get("user", "")
        cfg.ssh_key_file = ssh.get("key_file", "")
        cfg.ssh_password = ssh.get("password", "") or os.environ.get("SYSDIALOGUE_SSH_PASSWORD", "")
        cfg.ssh_sudo_password = (
            ssh.get("sudo_password", "")
            or os.environ.get("SYSDIALOGUE_SUDO_PASSWORD", "")
        )
    return cfg


def _env_int_clamped(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))
