"""CapabilityProbe — 探测目标 Linux 系统能力，构建 EnvProfile。"""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from sysdialogue.runtime.secure_runner import SafeExecutor


class EnvProfile(TypedDict):
    # v4.1 原有字段
    os_release: str
    distro_id: str
    distro_version: str
    distro_family: str
    kernel_version: str
    architecture: str
    current_user: str
    uid: int
    is_root: bool
    has_sudo: bool
    sudo_passwordless: bool
    is_container: bool
    remote_mode: bool
    init_system: str           # "systemd" | "sysvinit" | "unknown"
    package_manager: str       # "apt" | "dnf" | "yum" | "zypper" | "unknown"
    service_manager: str       # "systemd" | "service" | "unknown"
    available_cmds: dict       # 命令可用性映射

    # v5.3 新增
    firewall_backend: str      # "ufw" | "firewalld" | "iptables" | "none"
    ssh_port: int              # SSH 连接端口（用于远程锁门检测）

    # v5.4 新增
    container_backend: str     # "docker" | "podman" | "none"
    config_validators: list    # 可用配置校验器列表
    supports_journalctl: bool
    supports_system_cron: bool
    cron_writable: bool
    mount_capable: bool
    dns_tools: list            # ["dig", "nslookup", "getent"]
    selinux_mode: str          # "enforcing" | "permissive" | "disabled" | "unknown"
    apparmor_mode: str         # "enabled" | "disabled" | "unknown"


def _unknown_profile(remote_mode: bool = False, ssh_port: int = 22) -> EnvProfile:
    """返回全 unknown 的保守 EnvProfile，探测失败时使用。"""
    return EnvProfile(
        os_release="unknown",
        distro_id="unknown",
        distro_version="unknown",
        distro_family="unknown",
        kernel_version="unknown",
        architecture="unknown",
        current_user="unknown",
        uid=-1,
        is_root=False,
        has_sudo=False,
        sudo_passwordless=False,
        is_container=False,
        remote_mode=remote_mode,
        init_system="unknown",
        package_manager="unknown",
        service_manager="unknown",
        available_cmds={},
        firewall_backend="unknown",
        ssh_port=ssh_port,
        container_backend="unknown",
        config_validators=[],
        supports_journalctl=False,
        supports_system_cron=False,
        cron_writable=False,
        mount_capable=False,
        dns_tools=[],
        selinux_mode="unknown",
        apparmor_mode="unknown",
    )


class CapabilityProbe:
    """探测目标 Linux 系统能力，构建 EnvProfile。"""

    CMD_PROBES = [
        "systemctl", "service", "journalctl",
        "ss", "netstat", "ip", "ifconfig",
        "apt", "apt-get", "yum", "dnf", "zypper",
        "ufw", "firewall-cmd", "iptables",
        "docker", "podman",
        "dig", "nslookup", "getent",
        "mount", "umount",
        "crontab",
        "nginx", "apachectl", "sshd", "visudo",
        "sestatus", "aa-status",
    ]

    def __init__(self, executor: "SafeExecutor", remote_mode: bool = False, ssh_port: int = 22):
        self._exec = executor
        self._remote_mode = remote_mode
        self._ssh_port = ssh_port

    def probe(self) -> EnvProfile:
        profile = _unknown_profile(self._remote_mode, self._ssh_port)
        try:
            self._probe_os(profile)
            self._probe_user(profile)
            self._probe_cmds(profile)
            self._probe_init(profile)
            self._probe_package_manager(profile)
            self._probe_firewall(profile)
            self._probe_container(profile)
            self._probe_validators(profile)
            self._probe_dns_tools(profile)
            self._probe_mac(profile)
            self._probe_ssh_port(profile)
        except Exception:
            pass
        return profile

    # ------------------------------------------------------------------
    def _run(self, cmd: list[str], timeout: int = 5) -> tuple[str, int]:
        try:
            out, code = self._exec.run(cmd, timeout=timeout)
            return out.strip(), code
        except Exception:
            return "", 1

    def _cmd_exists(self, name: str) -> bool:
        out, code = self._run(["which", name])
        return code == 0 and bool(out)

    # ------------------------------------------------------------------
    def _probe_os(self, p: EnvProfile) -> None:
        out, code = self._run(["cat", "/etc/os-release"])
        if code == 0:
            kv: dict[str, str] = {}
            for line in out.splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    kv[k.strip()] = v.strip().strip('"')
            p["distro_id"] = kv.get("ID", "unknown").lower()
            p["distro_version"] = kv.get("VERSION_ID", "unknown")
            p["os_release"] = kv.get("PRETTY_NAME", "unknown")

            dist_id = p["distro_id"]
            if dist_id in ("ubuntu", "debian", "linuxmint", "pop"):
                p["distro_family"] = "debian"
            elif dist_id in ("centos", "rhel", "fedora", "openeuler", "anolis", "rocky", "almalinux"):
                p["distro_family"] = "rhel"
            elif dist_id in ("opensuse", "sles"):
                p["distro_family"] = "suse"
            else:
                p["distro_family"] = "unknown"

        out, _ = self._run(["uname", "-r"])
        if out:
            p["kernel_version"] = out

        out, _ = self._run(["uname", "-m"])
        if out:
            p["architecture"] = out

        out, code = self._run(["cat", "/.dockerenv"])
        if code == 0:
            p["is_container"] = True
        else:
            out, _ = self._run(["cat", "/proc/1/cgroup"])
            if "docker" in out or "kubepods" in out or "lxc" in out:
                p["is_container"] = True

    def _probe_user(self, p: EnvProfile) -> None:
        out, code = self._run(["whoami"])
        if code == 0 and out:
            p["current_user"] = out

        out, code = self._run(["id", "-u"])
        if code == 0 and out.isdigit():
            p["uid"] = int(out)
            p["is_root"] = p["uid"] == 0
        else:
            p["is_root"] = p["current_user"] == "root"

        _, code = self._run(["sudo", "-n", "true"])
        p["sudo_passwordless"] = code == 0
        p["has_sudo"] = p["sudo_passwordless"] or p["is_root"]
        run_privileged = getattr(self._exec, "run_privileged", None)
        has_sudo_password = bool(getattr(self._exec, "has_sudo_password", False))
        if not p["has_sudo"] and has_sudo_password and callable(run_privileged):
            _, priv_code = run_privileged(["true"], timeout=5)
            p["has_sudo"] = priv_code == 0

    def _probe_cmds(self, p: EnvProfile) -> None:
        avail: dict[str, bool] = {}
        for cmd in self.CMD_PROBES:
            avail[cmd] = self._cmd_exists(cmd)
        p["available_cmds"] = avail

    def _probe_init(self, p: EnvProfile) -> None:
        avail = p.get("available_cmds", {})
        if avail.get("systemctl"):
            out, code = self._run(["systemctl", "is-system-running"])
            if code in (0, 1) and out:
                p["init_system"] = "systemd"
                p["service_manager"] = "systemd"
                p["supports_journalctl"] = bool(avail.get("journalctl"))
                return
        if avail.get("service"):
            p["init_system"] = "sysvinit"
            p["service_manager"] = "service"
        else:
            p["init_system"] = "unknown"
            p["service_manager"] = "unknown"

    def _probe_package_manager(self, p: EnvProfile) -> None:
        avail = p.get("available_cmds", {})
        if avail.get("apt") or avail.get("apt-get"):
            p["package_manager"] = "apt"
        elif avail.get("dnf"):
            p["package_manager"] = "dnf"
        elif avail.get("yum"):
            p["package_manager"] = "yum"
        elif avail.get("zypper"):
            p["package_manager"] = "zypper"
        else:
            p["package_manager"] = "unknown"

    def _probe_firewall(self, p: EnvProfile) -> None:
        avail = p.get("available_cmds", {})
        if avail.get("ufw"):
            out, code = self._run(["ufw", "status"])
            if code == 0:
                p["firewall_backend"] = "ufw"
                return
        if avail.get("firewall-cmd"):
            _, code = self._run(["firewall-cmd", "--state"])
            if code == 0:
                p["firewall_backend"] = "firewalld"
                return
        if avail.get("iptables"):
            p["firewall_backend"] = "iptables"
        else:
            p["firewall_backend"] = "none"

    def _probe_container(self, p: EnvProfile) -> None:
        avail = p.get("available_cmds", {})
        if avail.get("docker"):
            out, code = self._run(["docker", "info"], timeout=3)
            if code == 0:
                p["container_backend"] = "docker"
                return
            if "permission denied" in out.lower() and ("docker.sock" in out.lower() or "docker api" in out.lower()):
                p["container_backend"] = "none"
                p["container_backend_error"] = "docker_permission_denied"
                return
            p["container_backend_error"] = "docker_unavailable"
        if avail.get("podman"):
            out, code = self._run(["podman", "info"], timeout=3)
            if code == 0:
                p["container_backend"] = "podman"
                return
            p["container_backend_error"] = "podman_unavailable"
        p["container_backend"] = "none"

    def _probe_validators(self, p: EnvProfile) -> None:
        validators: list[str] = []
        checks = [
            ("nginx", ["nginx", "-t"]),
            ("apache", ["apachectl", "-t"]),
            ("sshd", ["sshd", "-t"]),
            ("sudoers", ["visudo", "-c"]),
        ]
        for name, cmd in checks:
            avail = p.get("available_cmds", {})
            if avail.get(cmd[0]):
                validators.append(name)

        if p.get("init_system") == "systemd":
            validators.append("systemd-unit")

        p["config_validators"] = validators

        avail = p.get("available_cmds", {})
        p["supports_system_cron"] = bool(avail.get("crontab"))
        if p["is_root"]:
            p["cron_writable"] = True
        elif p["has_sudo"]:
            p["cron_writable"] = True
        else:
            _, code = self._run(["test", "-w", "/etc/cron.d"])
            p["cron_writable"] = code == 0
        p["mount_capable"] = bool(avail.get("mount"))

    def _probe_dns_tools(self, p: EnvProfile) -> None:
        avail = p.get("available_cmds", {})
        tools = []
        for t in ("dig", "nslookup", "getent"):
            if avail.get(t):
                tools.append(t)
        p["dns_tools"] = tools

    def _probe_mac(self, p: EnvProfile) -> None:
        avail = p.get("available_cmds", {})
        if avail.get("sestatus"):
            out, code = self._run(["sestatus"])
            if code == 0:
                m = re.search(r"SELinux status:\s+(\w+)", out)
                if m and m.group(1) == "enabled":
                    m2 = re.search(r"Current mode:\s+(\w+)", out)
                    p["selinux_mode"] = m2.group(1) if m2 else "unknown"
                else:
                    p["selinux_mode"] = "disabled"
            else:
                p["selinux_mode"] = "unknown"
        else:
            p["selinux_mode"] = "unknown"

        if avail.get("aa-status"):
            out, code = self._run(["aa-status", "--enabled"])
            p["apparmor_mode"] = "enabled" if code == 0 else "disabled"
        else:
            p["apparmor_mode"] = "unknown"

    def _probe_ssh_port(self, p: EnvProfile) -> None:
        if self._remote_mode:
            # 远程模式优先从连接参数取，已在构造时注入
            p["ssh_port"] = self._ssh_port
        else:
            avail = p.get("available_cmds", {})
            if avail.get("ss"):
                out, code = self._run(["ss", "-tlnp"])
                if code == 0:
                    for line in out.splitlines():
                        if "sshd" in line:
                            m = re.search(r":(\d+)\s", line)
                            if m:
                                p["ssh_port"] = int(m.group(1))
                                return
            p["ssh_port"] = self._ssh_port


class EnvProfileSanitizer:
    """注入 SystemPrompt 前脱敏 EnvProfile，移除凭证相关内容。"""

    _CREDENTIAL_PATTERNS = [
        re.compile(r"password[=:]\S+", re.IGNORECASE),
        re.compile(r"token[=:]\S+", re.IGNORECASE),
        re.compile(r"secret[=:]\S+", re.IGNORECASE),
        re.compile(r"https?://[^:@\s]+:[^:@\s]+@", re.IGNORECASE),
    ]

    @classmethod
    def sanitize(cls, profile: EnvProfile) -> dict:
        """返回适合注入 SystemPrompt 的脱敏字典（只含能力特征，无敏感凭证）。"""
        safe: dict = {
            "os": profile.get("os_release", "unknown"),
            "distro": profile.get("distro_id", "unknown"),
            "distro_family": profile.get("distro_family", "unknown"),
            "kernel": profile.get("kernel_version", "unknown"),
            "arch": profile.get("architecture", "unknown"),
            "user": profile.get("current_user", "unknown"),
            "uid": profile.get("uid", -1),
            "is_root": profile.get("is_root", False),
            "has_sudo": profile.get("has_sudo", False),
            "sudo_passwordless": profile.get("sudo_passwordless", False),
            "is_container": profile.get("is_container", False),
            "remote_mode": profile.get("remote_mode", False),
            "init_system": profile.get("init_system", "unknown"),
            "package_manager": profile.get("package_manager", "unknown"),
            "firewall_backend": profile.get("firewall_backend", "unknown"),
            "container_backend": profile.get("container_backend", "unknown"),
            "container_backend_error": profile.get("container_backend_error", ""),
            "config_validators": profile.get("config_validators", []),
            "supports_journalctl": profile.get("supports_journalctl", False),
            "cron_writable": profile.get("cron_writable", False),
            "dns_tools": profile.get("dns_tools", []),
            "selinux_mode": profile.get("selinux_mode", "unknown"),
            "apparmor_mode": profile.get("apparmor_mode", "unknown"),
        }
        return safe
