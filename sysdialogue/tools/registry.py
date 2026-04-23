"""ToolRegistry — 37 个工具的 JSON Schema 注册表，供 ClaudeClient 拉取 tool_definitions。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult

# 工具函数导入
from sysdialogue.tools.archive_ops import manage_archive
from sysdialogue.tools.auth_keys import manage_authorized_keys
from sysdialogue.tools.backup_restore import backup_path, replace_in_file
from sysdialogue.tools.config_validate import validate_config
from sysdialogue.tools.containers import manage_container
from sysdialogue.tools.cron_jobs import manage_cron
from sysdialogue.tools.file_ops import (
    copy_move_path, create_directory, delete_path, read_file, write_file,
)
from sysdialogue.tools.file_reading import read_log
from sysdialogue.tools.firewall import manage_firewall
from sysdialogue.tools.fs_browse import list_directory, search_file_content, stat_path
from sysdialogue.tools.hosts_entries import manage_hosts_entries
from sysdialogue.tools.mount_ops import manage_mount
from sysdialogue.tools.net_diag import check_endpoint, resolve_dns
from sysdialogue.tools.packages import get_resource_stats, manage_package
from sysdialogue.tools.power_ops import manage_power
from sysdialogue.tools.process_ports import (
    find_files, get_network_info, get_port_status, kill_process, list_processes,
)
from sysdialogue.tools.services import manage_service
from sysdialogue.tools.sysctl_ops import manage_sysctl
from sysdialogue.tools.system_config import get_set_system_config
from sysdialogue.tools.system_info import get_disk_usage, get_system_info
from sysdialogue.tools.users_groups import create_user, delete_user, modify_user_groups


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class ToolDef:
    name: str
    fn: Callable[..., ToolResult]
    schema: dict
    requires_executor: bool = True
    injects_session_counters: bool = False
    injects_env_profile: bool = False


class ToolRegistry:
    """集中管理所有可用工具的 Schema + 调用入口。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, td: ToolDef) -> None:
        self._tools[td.name] = td

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def all_schemas(self) -> list[dict]:
        """导出 Anthropic tool_definitions[] 使用的 Schema 列表。"""
        return [td.schema for td in self._tools.values()]

    def describe(self) -> list[tuple[str, str]]:
        """返回 (name, description) 对，供 SystemPrompt 枚举工具清单。"""
        return [
            (td.name, td.schema.get("description", ""))
            for td in self._tools.values()
        ]

    def call(
        self,
        name: str,
        args: dict,
        *,
        executor: SafeExecutor | None = None,
        session_counters: dict | None = None,
        env_profile: dict | None = None,
    ) -> ToolResult:
        """按 ToolDef 装配参数并调用工具函数。"""
        td = self._tools.get(name)
        if td is None:
            return ToolResult(success=False, error=f"未注册工具：{name}")
        kwargs: dict[str, Any] = dict(args or {})
        if td.requires_executor:
            if executor is None:
                return ToolResult(success=False, error=f"工具 {name} 需要 executor 但未提供")
            kwargs["executor"] = executor
        if td.injects_session_counters:
            kwargs["_session_counters"] = session_counters if session_counters is not None else {}
        if td.injects_env_profile:
            kwargs["env_profile"] = env_profile
        try:
            return td.fn(**kwargs)
        except TypeError as e:
            return ToolResult(success=False, error=f"参数错误：{e}")
        except Exception as e:
            return ToolResult(success=False, error=f"工具执行异常：{e}")


# --------------------------------------------------------------------------
# Schema 定义（37 个工具）
# --------------------------------------------------------------------------

def _schema(name: str, description: str, properties: dict,
            required: list[str] | None = None, **extra) -> dict:
    input_schema: dict = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required
    input_schema.update(extra)
    return {"name": name, "description": description, "input_schema": input_schema}


# ---- 系统观察类 ----

SCHEMA_GET_SYSTEM_INFO = _schema(
    "get_system_info",
    "获取系统基本信息（主机名/内核/架构/运行时间/内存/负载）。无参数。",
    {},
)

SCHEMA_GET_DISK_USAGE = _schema(
    "get_disk_usage",
    "查看磁盘空间使用情况。",
    {
        "path": {"type": "string", "description": "目标路径", "default": "/"},
        "recursive": {"type": "boolean", "description": "是否递归统计子目录大小", "default": False},
    },
)

SCHEMA_FIND_FILES = _schema(
    "find_files",
    "按模式查找文件，可选按最小大小过滤。",
    {
        "search_path": {"type": "string", "default": "."},
        "pattern": {"type": "string", "default": "*"},
        "min_size_mb": {"type": "number", "minimum": 0},
        "max_depth": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
    },
    required=["search_path", "pattern"],
)

SCHEMA_LIST_PROCESSES = _schema(
    "list_processes",
    "列出进程，按 cpu/mem/pid 排序，可按用户过滤。",
    {
        "top_n": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
        "sort_by": {"type": "string", "enum": ["cpu", "mem", "pid"], "default": "cpu"},
        "filter_user": {"type": "string"},
    },
)

SCHEMA_KILL_PROCESS = _schema(
    "kill_process",
    "向进程发送信号终止进程（WARN-HIGH，需确认）。",
    {
        "pid": {"type": "integer", "minimum": 2, "description": "PID；禁止对 1（init）使用"},
        "signal": {"type": "string", "enum": ["SIGTERM", "SIGKILL", "SIGHUP"], "default": "SIGTERM"},
    },
    required=["pid"],
)

SCHEMA_GET_PORT_STATUS = _schema(
    "get_port_status",
    "查看端口/监听状态（ss 或 netstat 回退）。",
    {
        "port": {"type": "integer", "minimum": 1, "maximum": 65535, "description": "可选；留空返回全部监听"},
        "protocol": {"type": "string", "enum": ["tcp", "udp", "all"], "default": "all"},
    },
)

SCHEMA_GET_NETWORK_INFO = _schema(
    "get_network_info",
    "获取网络接口信息与路由表。",
    {"interface": {"type": "string", "description": "可选；指定接口名"}},
)

SCHEMA_READ_LOG = _schema(
    "read_log",
    "读取系统日志或服务日志（journalctl 优先，回退文件日志）。",
    {
        "unit": {"type": "string", "description": "服务名；留空读取全局日志（WL005）"},
        "lines": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 100},
        "since": {"type": "string", "description": "journalctl --since 时间表达式"},
    },
)

# ---- 用户与权限类 ----

SCHEMA_CREATE_USER = _schema(
    "create_user",
    "创建用户（WARN-HIGH）。",
    {
        "username": {"type": "string", "minLength": 1},
        "groups": {"type": "array", "items": {"type": "string"}},
        "shell": {"type": "string", "default": "/bin/bash"},
        "create_home": {"type": "boolean", "default": True},
    },
    required=["username"],
)

SCHEMA_DELETE_USER = _schema(
    "delete_user",
    "删除用户（WARN-HIGH；root 禁止）。",
    {
        "username": {"type": "string", "minLength": 1},
        "remove_home": {"type": "boolean", "default": False},
    },
    required=["username"],
)

SCHEMA_MODIFY_USER_GROUPS = _schema(
    "modify_user_groups",
    "修改用户的附加组（WARN-HIGH；root 禁止）。",
    {
        "username": {"type": "string", "minLength": 1},
        "groups": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "action": {"type": "string", "enum": ["add", "remove"], "default": "add"},
    },
    required=["username", "groups"],
)

# ---- 服务类 ----

SCHEMA_MANAGE_SERVICE = _schema(
    "manage_service",
    "systemd/sysvinit 服务管理。status 为只读；start/restart/enable 视服务关键性分级；stop/disable 一律 WARN-HIGH；远程模式对 SSH/systemd 禁止 stop/disable。",
    {
        "name": {"type": "string", "minLength": 1},
        "action": {"type": "string", "enum": ["start", "stop", "restart", "status", "enable", "disable", "reload", "daemon-reload"]},
        "init_system": {"type": "string", "enum": ["systemd", "sysvinit"], "default": "systemd"},
    },
    required=["name", "action"],
)

# ---- 文件操作类 ----

SCHEMA_READ_FILE = _schema(
    "read_file",
    "读取文件内容，支持 head / tail / range 模式。禁止读取凭证类文件（B011）。",
    {
        "path": {"type": "string"},
        "mode": {"type": "string", "enum": ["head", "tail", "range"], "default": "head"},
        "start_line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
        "head_lines": {"type": "integer", "minimum": 1, "default": 50},
        "tail_lines": {"type": "integer", "minimum": 1, "default": 50},
        "max_bytes": {"type": "integer", "minimum": 512, "default": 8192},
    },
    required=["path"],
)

SCHEMA_WRITE_FILE = _schema(
    "write_file",
    "写入文件（overwrite/append/create_only）。关键系统文件禁止（B012）。推荐先用 backup_path 备份。",
    {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "mode": {"type": "string", "enum": ["overwrite", "append", "create_only"], "default": "overwrite"},
        "atomic": {"type": "boolean", "default": True},
        "create_backup": {"type": "boolean", "default": False},
        "backup_label": {"type": "string"},
    },
    required=["path", "content"],
)

SCHEMA_DELETE_PATH = _schema(
    "delete_path",
    "删除文件或目录。系统关键目录递归删除 BLOCK（B013）。",
    {
        "path": {"type": "string"},
        "recursive": {"type": "boolean", "default": False},
    },
    required=["path"],
)

SCHEMA_CREATE_DIRECTORY = _schema(
    "create_directory",
    "创建目录。",
    {
        "path": {"type": "string"},
        "parents": {"type": "boolean", "default": True},
    },
    required=["path"],
)

SCHEMA_COPY_MOVE_PATH = _schema(
    "copy_move_path",
    "拷贝或移动文件/目录。copy 到系统目录触发 WH024。",
    {
        "src": {"type": "string"},
        "dst": {"type": "string"},
        "action": {"type": "string", "enum": ["copy", "move"], "default": "copy"},
    },
    required=["src", "dst"],
)

# ---- 包管理类 ----

SCHEMA_MANAGE_PACKAGE = _schema(
    "manage_package",
    "包管理（apt/dnf/yum 自动识别）。install/remove/update 为 WARN-HIGH。",
    {
        "action": {"type": "string", "enum": ["install", "remove", "update", "list", "search", "clean-cache", "hold", "unhold"]},
        "name": {"type": "string", "description": "单包名"},
        "names": {"type": "array", "items": {"type": "string"}, "description": "多包名"},
        "manager": {"type": "string", "enum": ["auto", "apt", "yum", "dnf"], "default": "auto"},
    },
    required=["action"],
)

SCHEMA_GET_RESOURCE_STATS = _schema(
    "get_resource_stats",
    "获取 CPU/内存资源使用情况与 top 进程。",
    {
        "resource": {"type": "string", "enum": ["cpu", "memory", "all"], "default": "all"},
        "top_n_procs": {"type": "integer", "minimum": 0, "maximum": 50, "default": 10},
    },
)

# ---- 防火墙与系统配置 ----

SCHEMA_MANAGE_FIREWALL = _schema(
    "manage_firewall",
    "防火墙管理（ufw/firewalld/iptables 自动）。远程模式下 flush/set-default drop/reject/deny SSH 端口均 BLOCK。",
    {
        "backend": {"type": "string", "enum": ["auto", "ufw", "firewalld", "iptables"], "default": "auto"},
        "action": {"type": "string", "enum": ["list", "allow", "deny", "delete", "set-default", "flush", "reload"]},
        "target": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "service": {"type": "string"},
                "protocol": {"type": "string", "enum": ["tcp", "udp", "all"], "default": "tcp"},
                "source_ip": {"type": "string"},
            },
        },
        "direction": {"type": "string", "enum": ["in", "out", "forward"], "default": "in"},
        "policy": {"type": "string", "enum": ["accept", "drop", "reject"]},
    },
    required=["action"],
)

SCHEMA_GET_SET_SYSTEM_CONFIG = _schema(
    "get_set_system_config",
    "获取/设置系统配置（hostname/timezone/locale）。set 为 WARN-HIGH。",
    {
        "key": {"type": "string", "enum": ["hostname", "timezone", "locale"]},
        "value": {"type": "string", "description": "省略则仅查询"},
    },
    required=["key"],
)

# ---- 文件浏览与检索 ----

SCHEMA_LIST_DIRECTORY = _schema(
    "list_directory",
    "列出目录内容。敏感凭证目录 BLOCK（B031）。",
    {
        "path": {"type": "string", "default": "."},
        "recursive": {"type": "boolean", "default": False},
        "max_depth": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1},
        "include_hidden": {"type": "boolean", "default": False},
        "max_entries": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
        "sort_by": {"type": "string", "enum": ["name", "size", "time"], "default": "name"},
    },
    required=["path"],
)

SCHEMA_STAT_PATH = _schema(
    "stat_path",
    "获取文件/目录元数据，可选哈希（WL011）。",
    {
        "path": {"type": "string"},
        "follow_symlink": {"type": "boolean", "default": True},
        "with_hash": {"type": "boolean", "default": False},
        "hash_algo": {"type": "string", "enum": ["md5", "sha1", "sha256", "sha512"], "default": "sha256"},
    },
    required=["path"],
)

SCHEMA_SEARCH_FILE_CONTENT = _schema(
    "search_file_content",
    "在文件内容中搜索文本（grep）。凭证路径/v4.1 敏感核心路径 BLOCK（B025）。",
    {
        "search_path": {"type": "string"},
        "pattern": {"type": "string"},
        "file_glob": {"type": "string", "default": "*"},
        "regex": {"type": "boolean", "default": False},
        "case_sensitive": {"type": "boolean", "default": True},
        "max_matches": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
    },
    required=["search_path", "pattern"],
)

# ---- 配置变更闭环 ----

SCHEMA_BACKUP_PATH = _schema(
    "backup_path",
    "备份/列出/还原/删除备份。restore 命中关键系统文件 BLOCK（B019）。",
    {
        "action": {"type": "string", "enum": ["create", "list", "restore", "delete"]},
        "path": {"type": "string", "description": "create 必填；list 可选过滤"},
        "backup_id": {"type": "string", "description": "restore/delete 必填"},
        "backup_label": {"type": "string"},
    },
    required=["action"],
)

SCHEMA_REPLACE_IN_FILE = _schema(
    "replace_in_file",
    "精准替换文件内容（literal/regex）。关键系统文件 BLOCK（B018）。默认 create_backup=true。dry_run=true 仅返回 diff 预览不写入。",
    {
        "path": {"type": "string"},
        "match_type": {"type": "string", "enum": ["literal", "regex"], "default": "literal"},
        "search": {"type": "string"},
        "replace": {"type": "string"},
        "expected_matches": {"type": "integer", "minimum": 1},
        "max_replacements": {"type": "integer", "minimum": 1, "default": 1},
        "create_backup": {"type": "boolean", "default": True},
        "dry_run": {"type": "boolean", "default": False},
    },
    required=["path", "search", "replace"],
)

SCHEMA_VALIDATE_CONFIG = _schema(
    "validate_config",
    "校验配置文件语法（nginx/apache/sshd/sudoers/systemd-unit/sysctl/json/yaml/toml）。",
    {
        "path": {"type": "string"},
        "target_type": {"type": "string", "enum": ["auto", "nginx", "apache", "sshd", "sysctl", "sudoers", "systemd-unit", "json", "yaml", "toml", "fstab", "cron"], "default": "auto"},
    },
    required=["path"],
)

# ---- 系统维护 ----

SCHEMA_MANAGE_CRON = _schema(
    "manage_cron",
    "计划任务管理。job_target.kind 只允许 'tool' 或 'workflow'，不接受任意 shell。递归风险判定命中 BLOCK 级目标 B026 拒绝。",
    {
        "action": {"type": "string", "enum": ["list", "create", "update", "delete", "enable", "disable"]},
        "scope": {"type": "string", "enum": ["user", "system"], "default": "user"},
        "schedule": {"type": "string", "description": "标准 5 字段 cron 表达式"},
        "job_id": {"type": "string"},
        "job_target": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["tool", "workflow"]},
                "name": {"type": "string"},
                "args": {"type": "object"},
            },
            "required": ["kind", "name"],
        },
    },
    required=["action"],
)

SCHEMA_MANAGE_SYSCTL = _schema(
    "manage_sysctl",
    "内核参数管理（list/get/set/apply-file）。set + persist 会写入 /etc/sysctl.d/。",
    {
        "action": {"type": "string", "enum": ["list", "get", "set", "apply-file"]},
        "key": {"type": "string", "description": "内核参数键；apply-file 时复用为文件路径"},
        "value": {"type": "string"},
        "persist": {"type": "boolean", "default": False},
    },
    required=["action"],
)

SCHEMA_RESOLVE_DNS = _schema(
    "resolve_dns",
    "DNS 解析（dig → nslookup → getent）。私网目标/私网 resolver 触发 WL016。",
    {
        "name": {"type": "string"},
        "record_type": {"type": "string", "enum": ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA"], "default": "A"},
        "resolver": {"type": "string", "description": "指定 DNS 服务器；留空使用系统默认"},
    },
    required=["name"],
)

SCHEMA_CHECK_ENDPOINT = _schema(
    "check_endpoint",
    "连通性诊断（ping/tcp/http/tls）。私网目标触发 WL016；单会话 > 20 次 WL017 拒绝。",
    {
        "kind": {"type": "string", "enum": ["ping", "tcp", "http", "tls"]},
        "host": {"type": "string"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "path": {"type": "string", "default": "/"},
        "method": {"type": "string", "enum": ["GET", "POST", "HEAD"], "default": "GET"},
        "expected_status": {"type": "integer", "minimum": 100, "maximum": 599},
        "timeout": {"type": "integer", "minimum": 1, "maximum": 60, "default": 5},
    },
    required=["kind", "host"],
)

SCHEMA_MANAGE_ARCHIVE = _schema(
    "manage_archive",
    "归档压缩管理（list/create/extract）。extract 到系统目录 BLOCK（B021）；条目越界 BLOCK（B027）。",
    {
        "action": {"type": "string", "enum": ["list", "create", "extract"]},
        "archive_path": {"type": "string"},
        "source_path": {"type": "string", "description": "create 必填"},
        "target_path": {"type": "string", "description": "extract 必填"},
        "format": {"type": "string", "enum": ["auto", "tar", "tar.gz", "zip"], "default": "auto"},
        "strip_components": {"type": "integer", "minimum": 0, "maximum": 10, "default": 0},
    },
    required=["action", "archive_path"],
)

SCHEMA_MANAGE_MOUNT = _schema(
    "manage_mount",
    "挂载管理（即时，不改 /etc/fstab）。系统关键目录 BLOCK（B020）。",
    {
        "action": {"type": "string", "enum": ["list", "mount", "umount", "remount"]},
        "source": {"type": "string"},
        "target": {"type": "string"},
        "fs_type": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
    },
    required=["action"],
)

# ---- 现代运维 ----

SCHEMA_MANAGE_CONTAINER = _schema(
    "manage_container",
    "容器管理（docker/podman）。不提供 privileged/host network/exec。敏感 bind mount BLOCK（B022）。",
    {
        "backend": {"type": "string", "enum": ["auto", "docker", "podman"], "default": "auto"},
        "action": {"type": "string", "enum": ["list", "status", "pull", "start", "stop", "restart", "logs", "inspect", "run", "remove"]},
        "name": {"type": "string"},
        "image": {"type": "string"},
        "ports": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "host_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "container_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "protocol": {"type": "string", "enum": ["tcp", "udp"], "default": "tcp"},
                },
                "required": ["host_port", "container_port"],
            },
        },
        "env_vars": {"type": "object"},
        "volumes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "read_only": {"type": "boolean", "default": False},
                },
                "required": ["source", "target"],
            },
        },
        "restart_policy": {"type": "string", "enum": ["no", "always", "unless-stopped"], "default": "no"},
        "lines": {"type": "integer", "minimum": 10, "maximum": 1000, "default": 50},
    },
    required=["action"],
    additionalProperties=False,
)

SCHEMA_MANAGE_AUTHORIZED_KEYS = _schema(
    "manage_authorized_keys",
    "SSH 授权公钥管理（list/add/remove）。root 账户 BLOCK（B028）；非公钥格式 BLOCK（B023）。",
    {
        "action": {"type": "string", "enum": ["list", "add", "remove"]},
        "username": {"type": "string", "minLength": 1},
        "public_key": {"type": "string"},
        "fingerprint": {"type": "string"},
    },
    required=["action", "username"],
)

SCHEMA_MANAGE_POWER = _schema(
    "manage_power",
    "重启/关机（WH021）。delay_sec=0 立即执行；>0 走 shutdown 定时。",
    {
        "action": {"type": "string", "enum": ["reboot", "shutdown"]},
        "delay_sec": {"type": "integer", "minimum": 0, "maximum": 86400, "default": 0},
        "reason": {"type": "string"},
        "force": {"type": "boolean", "default": False},
    },
    required=["action"],
)

SCHEMA_MANAGE_HOSTS_ENTRIES = _schema(
    "manage_hosts_entries",
    "/etc/hosts 条目管理。localhost/127.0.0.1/::1 受保护条目 BLOCK（B024）。",
    {
        "action": {"type": "string", "enum": ["list", "add", "update", "delete"]},
        "hostname": {"type": "string"},
        "ip_addrs": {"type": "array", "items": {"type": "string"}},
        "comment": {"type": "string"},
    },
    required=["action"],
)


# --------------------------------------------------------------------------
# 默认注册表
# --------------------------------------------------------------------------

def _build_tool_defs() -> list[ToolDef]:
    return [
        # 系统观察
        ToolDef("get_system_info", get_system_info, SCHEMA_GET_SYSTEM_INFO),
        ToolDef("get_disk_usage", get_disk_usage, SCHEMA_GET_DISK_USAGE),
        ToolDef("find_files", find_files, SCHEMA_FIND_FILES),
        ToolDef("list_processes", list_processes, SCHEMA_LIST_PROCESSES),
        ToolDef("kill_process", kill_process, SCHEMA_KILL_PROCESS),
        ToolDef("get_port_status", get_port_status, SCHEMA_GET_PORT_STATUS),
        ToolDef("get_network_info", get_network_info, SCHEMA_GET_NETWORK_INFO),
        ToolDef("read_log", read_log, SCHEMA_READ_LOG),
        # 用户与权限
        ToolDef("create_user", create_user, SCHEMA_CREATE_USER),
        ToolDef("delete_user", delete_user, SCHEMA_DELETE_USER),
        ToolDef("modify_user_groups", modify_user_groups, SCHEMA_MODIFY_USER_GROUPS),
        # 服务
        ToolDef("manage_service", manage_service, SCHEMA_MANAGE_SERVICE),
        # 文件操作
        ToolDef("read_file", read_file, SCHEMA_READ_FILE),
        ToolDef("write_file", write_file, SCHEMA_WRITE_FILE),
        ToolDef("delete_path", delete_path, SCHEMA_DELETE_PATH),
        ToolDef("create_directory", create_directory, SCHEMA_CREATE_DIRECTORY),
        ToolDef("copy_move_path", copy_move_path, SCHEMA_COPY_MOVE_PATH),
        # 包管理
        ToolDef("manage_package", manage_package, SCHEMA_MANAGE_PACKAGE, injects_env_profile=True),
        ToolDef("get_resource_stats", get_resource_stats, SCHEMA_GET_RESOURCE_STATS),
        # 防火墙与系统配置
        ToolDef("manage_firewall", manage_firewall, SCHEMA_MANAGE_FIREWALL, injects_env_profile=True),
        ToolDef("get_set_system_config", get_set_system_config, SCHEMA_GET_SET_SYSTEM_CONFIG),
        # 文件浏览与检索
        ToolDef("list_directory", list_directory, SCHEMA_LIST_DIRECTORY),
        ToolDef("stat_path", stat_path, SCHEMA_STAT_PATH),
        ToolDef("search_file_content", search_file_content, SCHEMA_SEARCH_FILE_CONTENT),
        # 配置变更闭环（无 executor）
        ToolDef("backup_path", backup_path, SCHEMA_BACKUP_PATH, requires_executor=False),
        ToolDef("replace_in_file", replace_in_file, SCHEMA_REPLACE_IN_FILE, requires_executor=False),
        ToolDef("validate_config", validate_config, SCHEMA_VALIDATE_CONFIG),
        # 系统维护
        ToolDef("manage_cron", manage_cron, SCHEMA_MANAGE_CRON),
        ToolDef("manage_sysctl", manage_sysctl, SCHEMA_MANAGE_SYSCTL),
        ToolDef("resolve_dns", resolve_dns, SCHEMA_RESOLVE_DNS, injects_session_counters=True),
        ToolDef("check_endpoint", check_endpoint, SCHEMA_CHECK_ENDPOINT, injects_session_counters=True),
        ToolDef("manage_archive", manage_archive, SCHEMA_MANAGE_ARCHIVE),
        ToolDef("manage_mount", manage_mount, SCHEMA_MANAGE_MOUNT),
        # 现代运维
        ToolDef("manage_container", manage_container, SCHEMA_MANAGE_CONTAINER, injects_env_profile=True),
        ToolDef("manage_authorized_keys", manage_authorized_keys, SCHEMA_MANAGE_AUTHORIZED_KEYS),
        ToolDef("manage_power", manage_power, SCHEMA_MANAGE_POWER),
        ToolDef("manage_hosts_entries", manage_hosts_entries, SCHEMA_MANAGE_HOSTS_ENTRIES),
    ]


def default_registry() -> ToolRegistry:
    """构造包含全部 37 个静态工具的默认注册表。"""
    r = ToolRegistry()
    for td in _build_tool_defs():
        r.register(td)
    return r
