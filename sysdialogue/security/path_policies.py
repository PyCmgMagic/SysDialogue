"""路径保护具名集合 — 所有安全规则共用的路径集合定义。"""

from __future__ import annotations

import fnmatch
import os
import re

# --------------------------------------------------------------------------
# 具名集合
# --------------------------------------------------------------------------

SENSITIVE_CREDENTIAL_PATHS: list[str] = [
    "/etc/shadow",
    "/etc/gshadow",
]

SENSITIVE_CREDENTIAL_GLOBS: list[str] = [
    "~/.ssh/id_*",
    "~/.ssh/authorized_keys",
    "~/.aws/credentials",
    "~/.aws/config",
    "~/.kube/config",
    "**/.env",
    ".env.*",
    "*.pem",
    "*.key",
    "*_rsa",
    "*_ed25519",
    "*_ecdsa",
    "*.pfx",
    "*.p12",
]

PERSISTENCE_ENTRY_PATHS: list[str] = [
    "/etc/systemd/system/",
    "/etc/cron.d/",
    "/etc/cron.daily/",
    "/etc/cron.hourly/",
    "/etc/cron.weekly/",
    "/etc/cron.monthly/",
    "/etc/init.d/",
    "/etc/profile.d/",
    "/etc/rc.d/",
    "/etc/ld.so.conf.d/",
]

CRITICAL_EDIT_PATHS: list[str] = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/ssh/sshd_config",
    "/boot/",
    "/lib/systemd/",
    "/etc/sudoers.d/",
]

MOUNT_BLOCK_TARGETS: list[str] = [
    "/",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
    "/run",
]

ARCHIVE_BLOCK_TARGETS: list[str] = [
    "/",
    "/etc",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
]

CONTAINER_SENSITIVE_BIND_SOURCES: list[str] = [
    "/",
    "/etc",
    "/boot",
    "/root",
    "/var/run/docker.sock",
]
# ~/.ssh/ 展开后动态匹配

HOSTS_PROTECTED_ENTRIES: list[tuple[str, str]] = [
    ("127.0.0.1", "localhost"),
    ("::1", "localhost"),
]

SENSITIVE_DIR_PATHS: list[str] = [
    "~/.ssh/",
    "/root/.ssh/",
    "~/.aws/",
    "~/.kube/",
]

# v4.1 原有敏感核心路径（find_files / get_disk_usage 限制）
V41_BLOCK_PATHS: list[str] = [
    "/etc/passwd",
    "/etc/shadow",
    "/boot",
    "/lib/systemd",
    "/proc/kcore",
    "/dev/mem",
    "/proc/sys/kernel",
]

SYSTEM_DIR_PREFIXES: list[str] = [
    "/etc/",
    "/usr/",
    "/lib/",
    "/bin/",
    "/sbin/",
]

# --------------------------------------------------------------------------
# 匹配工具
# --------------------------------------------------------------------------

def normalize(path: str) -> str:
    """规范化路径：展开 ~，realpath，去尾部斜线。"""
    path = os.path.expanduser(path)
    path = os.path.normpath(path)
    return path


def _home() -> str:
    return os.path.expanduser("~")


def matches_sensitive_credential(path: str) -> bool:
    """路径是否命中 SENSITIVE_CREDENTIAL_PATHS 或 SENSITIVE_CREDENTIAL_GLOBS。"""
    n = normalize(path)
    for p in SENSITIVE_CREDENTIAL_PATHS:
        if n == normalize(p):
            return True
    raw = path
    for g in SENSITIVE_CREDENTIAL_GLOBS:
        g_expanded = g.replace("~", _home())
        if fnmatch.fnmatch(raw, g_expanded) or fnmatch.fnmatch(n, g_expanded):
            return True
        if fnmatch.fnmatch(os.path.basename(raw), g):
            return True
    return False


def matches_persistence_entry(path: str) -> bool:
    """路径是否命中 PERSISTENCE_ENTRY_PATHS（前缀匹配）。"""
    n = normalize(path)
    for entry in PERSISTENCE_ENTRY_PATHS:
        prefix = normalize(entry)
        if n == prefix or n.startswith(prefix + os.sep) or n.startswith(prefix):
            return True
    return False


def matches_critical_edit(path: str) -> bool:
    """路径是否命中 CRITICAL_EDIT_PATHS（前缀匹配 + 精确匹配）。"""
    n = normalize(path)
    for entry in CRITICAL_EDIT_PATHS:
        en = normalize(entry)
        if entry.endswith("/"):
            if n == en or n.startswith(en + os.sep) or n.startswith(en):
                return True
        else:
            if n == en:
                return True
    return False


def matches_mount_block(path: str) -> bool:
    n = normalize(path)
    return n in [normalize(p) for p in MOUNT_BLOCK_TARGETS]


def matches_archive_block(path: str) -> bool:
    n = normalize(path)
    return n in [normalize(p) for p in ARCHIVE_BLOCK_TARGETS]


def matches_container_sensitive_bind(path: str) -> bool:
    n = normalize(path)
    sensitive = [normalize(p) for p in CONTAINER_SENSITIVE_BIND_SOURCES]
    sensitive.append(normalize("~/.ssh/"))
    if n in sensitive:
        return True
    for s in sensitive:
        if s.endswith(os.sep) and n.startswith(s):
            return True
    return False


def matches_sensitive_dir(path: str) -> bool:
    n = normalize(path)
    for d in SENSITIVE_DIR_PATHS:
        dn = normalize(d)
        if n == dn or n.startswith(dn + os.sep) or n.startswith(dn):
            return True
    return False


def matches_v41_block(path: str) -> bool:
    n = normalize(path)
    for p in V41_BLOCK_PATHS:
        pn = normalize(p)
        if n == pn or n.startswith(pn + os.sep):
            return True
    return False


def matches_system_dir(path: str) -> bool:
    n = normalize(path) + os.sep
    for prefix in SYSTEM_DIR_PREFIXES:
        if n.startswith(prefix):
            return True
    return False


def has_path_traversal(path: str) -> bool:
    """检测路径中是否包含 .. 组件。"""
    parts = path.replace("\\", "/").split("/")
    return ".." in parts


CRITICAL_SERVICES: set[str] = {
    "mysql", "mysqld", "mariadb", "postgresql", "postgres",
    "nginx", "httpd", "apache2", "redis", "redis-server",
    "mongodb", "mongod", "elasticsearch",
    "rabbitmq", "rabbitmq-server", "docker", "containerd",
}

SSH_SERVICE_ALIASES: set[str] = {
    "ssh", "sshd", "openssh", "openssh-server", "openssh-sshd",
}
