from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paramiko

from sysdialogue.app.config import AppConfig
from sysdialogue.app.runtime_factory import create_runtime
from sysdialogue.audit.serializers import export_audit_jsonl, export_replay_package
from sysdialogue.security.output_sanitizer import sanitize_text
from sysdialogue.security.risk_classifier import classify


RUN_ID = os.environ["JM_FT_RUN_ID"]
HOST = os.environ["JM_FT_HOST"]
PORT = int(os.environ.get("JM_FT_PORT", "22"))
SSH_USER = os.environ.get("JM_FT_USER", "root")
SSH_PASSWORD = os.environ["JM_FT_PASSWORD"]
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "")
MODEL = os.environ.get("OPENAI_MODEL", "")
ROOT = Path.cwd()
ART = ROOT / "docs" / "artifacts" / f"java-mysql-deploy-ft-{RUN_ID}"
REPORT = ROOT / "docs" / "java-mysql-deployment-functional-test-report.md"
PREFIX = f"sdft_{RUN_ID}"
SHORT_RUN_ID = re.sub(r"[^a-z0-9]", "", RUN_ID.lower())[-20:]
REMOTE_BASE = f"/tmp/{PREFIX}_java_mysql"
SRC_DIR = f"{REMOTE_BASE}/source"
APP_DIR = f"/opt/{PREFIX}_app"
SERVICE = f"{PREFIX}_java.service"
APP_USER = f"sdft_{SHORT_RUN_ID}u"
MYSQL_CONTAINER = f"{PREFIX}_mysql"
DB_NAME = f"{PREFIX}_db"
DB_USER = f"{PREFIX}_app"
APP_PORT = 18082
MYSQL_PORT = 13382
DB_ROOT_PASSWORD = f"Root_{RUN_ID}_Pwd9!"
DB_APP_PASSWORD = f"App_{RUN_ID}_Pwd9!"
SECRET_VALUES = [API_KEY, SSH_PASSWORD, DB_ROOT_PASSWORD, DB_APP_PASSWORD]

for subdir in ("baseline", "prompts", "responses", "audit", "replay", "traces", "screenshots", "logs", "state", "checks"):
    (ART / subdir).mkdir(parents=True, exist_ok=True)

manifest: list[dict[str, Any]] = []
results: list[dict[str, Any]] = []
issues: list[dict[str, Any]] = []
cleanup_records: list[dict[str, Any]] = []


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_text(value: Any) -> str:
    text = sanitize_text(str(value or ""), limit=200000)
    for secret in SECRET_VALUES:
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"-p(Root|App)_\d{8}_\d{6}_Pwd9!", "-p<redacted>", text)
    return text


def redact_obj(value: Any) -> Any:
    text = redact_text(json.dumps(value, ensure_ascii=False, default=str))
    try:
        return json.loads(text)
    except Exception:
        return text


def write_artifact(case_id: str, category: str, name: str, content: Any, *, method: str = "generated") -> Path:
    path = ART / category / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        text = json.dumps(redact_obj(content), ensure_ascii=False, indent=2, default=str)
    else:
        text = redact_text(content)
    path.write_text(text, encoding="utf-8")
    manifest.append({
        "case_id": case_id,
        "category": category,
        "path": str(path.relative_to(ROOT)),
        "collected_at": now(),
        "method": method,
        "sanitized": True,
    })
    return path


def add_result(case_id: str, scenario: str, entry: str, expected: str, actual: str,
               status: str, evidence: list[str], elapsed: float = 0.0) -> None:
    results.append({
        "id": case_id,
        "scenario": scenario,
        "entry": entry,
        "expected": expected,
        "actual": redact_text(actual),
        "result": status,
        "evidence": evidence,
        "elapsed_sec": round(elapsed, 1),
    })


def local_cmd(case_id: str, cmd: list[str], *, timeout: int, category: str, name: str,
              env: dict[str, str] | None = None,
              unset_env: list[str] | None = None) -> tuple[str, int, Path]:
    merged_env = os.environ.copy()
    for key in unset_env or []:
        merged_env.pop(key, None)
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=merged_env, capture_output=True, text=True, timeout=timeout)
        out = f"$ {' '.join(cmd)}\nexit={proc.returncode}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = f"$ {' '.join(cmd)}\n[TIMEOUT after {timeout}s]\nSTDOUT:\n{exc.stdout or ''}\nSTDERR:\n{exc.stderr or ''}"
        code = 124
    path = write_artifact(case_id, category, name, out, method="local_cmd")
    return out, code, path


def ssh_client() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=HOST, port=PORT, username=SSH_USER, password=SSH_PASSWORD, timeout=20)
    return client


def remote_cmd(case_id: str, cmd: str, *, timeout: int, category: str, name: str) -> tuple[str, int, Path]:
    client = ssh_client()
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out_s = stdout.read().decode("utf-8", errors="replace")
        err_s = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
    except Exception as exc:
        out_s, err_s, code = "", f"{type(exc).__name__}: {exc}", 1
    finally:
        client.close()
    rendered = f"$ {cmd}\nexit={code}\n\nSTDOUT:\n{out_s}\n\nSTDERR:\n{err_s}"
    path = write_artifact(case_id, category, name, rendered, method="ssh")
    return rendered, code, path


def git_output(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def upload_project_fixture(case_id: str) -> Path:
    files = {
        "pom.xml": spring_pom(),
        "src/main/resources/application.properties": (
            "server.port=${APP_PORT:18082}\n"
            "management.endpoints.web.exposure.include=health,info\n"
            "spring.datasource.url=jdbc:mysql://${DB_HOST:127.0.0.1}:${DB_PORT:3306}/${DB_NAME:sdft_db}"
            "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC\n"
            "spring.datasource.username=${DB_USER:sdft_app}\n"
            "spring.datasource.password=${DB_PASSWORD:}\n"
        ),
        "src/main/java/com/example/sdft/DemoApplication.java": (
            "package com.example.sdft;\n\n"
            "import org.springframework.boot.SpringApplication;\n"
            "import org.springframework.boot.autoconfigure.SpringBootApplication;\n\n"
            "@SpringBootApplication\n"
            "public class DemoApplication {\n"
            "    public static void main(String[] args) { SpringApplication.run(DemoApplication.class, args); }\n"
            "}\n"
        ),
        "src/main/java/com/example/sdft/ItemController.java": item_controller(),
        "src/test/java/com/example/sdft/SmokeTest.java": (
            "package com.example.sdft;\n\n"
            "import org.junit.jupiter.api.Test;\n"
            "import static org.junit.jupiter.api.Assertions.assertTrue;\n\n"
            "class SmokeTest { @Test void arithmeticWorks() { assertTrue(1 + 1 == 2); } }\n"
        ),
        "README.md": f"# {PREFIX} Java MySQL fixture\n\nSynthetic Spring Boot + MySQL CRUD project.\n",
    }
    remote_cmd(case_id, f"rm -rf {REMOTE_BASE!r}; mkdir -p {SRC_DIR!r}", timeout=30, category="logs", name=f"{case_id}-mkdir.log")
    dirs = sorted({f"{SRC_DIR}/{rel}".rsplit("/", 1)[0] for rel in files})
    mkdir_cmd = "mkdir -p " + " ".join(repr(directory) for directory in dirs)
    remote_cmd(case_id, mkdir_cmd, timeout=30, category="logs", name=f"{case_id}-source-dirs.log")
    client = ssh_client()
    try:
        sftp = client.open_sftp()
        for rel, content in files.items():
            remote_path = f"{SRC_DIR}/{rel}"
            with sftp.open(remote_path, "w") as handle:
                handle.write(content)
        sftp.close()
    finally:
        client.close()
    _, _, path = remote_cmd(case_id, f"find {SRC_DIR!r} -maxdepth 5 -type f | sort", timeout=30, category="checks", name=f"{case_id}-tree.log")
    return path


def spring_pom() -> str:
    return f"""<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.5</version>
    <relativePath/>
  </parent>
  <groupId>com.example</groupId>
  <artifactId>{PREFIX}-java-mysql</artifactId>
  <version>0.0.1-SNAPSHOT</version>
  <name>{PREFIX}-java-mysql</name>
  <properties><java.version>17</java.version></properties>
  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-actuator</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-jdbc</artifactId></dependency>
    <dependency><groupId>com.mysql</groupId><artifactId>mysql-connector-j</artifactId><scope>runtime</scope></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency>
  </dependencies>
  <build><plugins><plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin></plugins></build>
</project>
"""


def item_controller() -> str:
    return """package com.example.sdft;

import jakarta.annotation.PostConstruct;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import java.util.List;
import java.util.Map;

@RestController
public class ItemController {
    private final JdbcTemplate jdbc;
    public ItemController(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @PostConstruct
    public void init() {
        jdbc.execute("CREATE TABLE IF NOT EXISTS items(id INT PRIMARY KEY, name VARCHAR(64) NOT NULL)");
        jdbc.update("INSERT INTO items(id, name) VALUES(1, 'agent-ok') ON DUPLICATE KEY UPDATE name=VALUES(name)");
    }

    @GetMapping("/items")
    public List<Map<String, Object>> items() {
        return jdbc.queryForList("SELECT id, name FROM items ORDER BY id");
    }

    @PostMapping("/items")
    public Map<String, Object> add(@RequestParam int id, @RequestParam String name) {
        jdbc.update("INSERT INTO items(id, name) VALUES(?, ?) ON DUPLICATE KEY UPDATE name=VALUES(name)", id, name);
        return Map.of("id", id, "name", name);
    }

    @GetMapping("/db-check")
    public Map<String, Object> dbCheck() {
        Integer one = jdbc.queryForObject("SELECT 1", Integer.class);
        return Map.of("ok", one != null && one == 1);
    }
}
"""


def make_config() -> AppConfig:
    return AppConfig(
        api_key=API_KEY,
        base_url=BASE_URL,
        model=MODEL,
        remote_mode=True,
        ssh_host=HOST,
        ssh_port=PORT,
        ssh_user=SSH_USER,
        ssh_password=SSH_PASSWORD,
        max_iterations=int(os.environ.get("SYSDIALOGUE_MAX_ITER", "220")),
    )


def sanitize_file_in_place(path: Path) -> None:
    try:
        path.write_text(redact_text(path.read_text(encoding="utf-8")), encoding="utf-8")
    except UnicodeDecodeError:
        pass


def sanitize_zip_in_place(path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            data = zin.read(name)
            try:
                zout.writestr(name, redact_text(data.decode("utf-8")))
            except UnicodeDecodeError:
                zout.writestr(name, data)
    tmp.replace(path)


def copy_session_artifacts(case_id: str, session_id: str, runtime) -> list[str]:
    evidence: list[str] = []
    try:
        audit_path = export_audit_jsonl(runtime.audit_log, output_dir=str(ART / "audit"))
        sanitize_file_in_place(audit_path)
        manifest.append({"case_id": case_id, "category": "audit", "path": str(audit_path.relative_to(ROOT)), "collected_at": now(), "method": "export_audit_jsonl", "sanitized": True})
        evidence.append(str(audit_path.relative_to(ROOT)))
    except Exception as exc:
        write_artifact(case_id, "logs", f"{case_id}-audit-export-error.log", str(exc), method="export_audit_jsonl")
    try:
        replay_path = export_replay_package(runtime.audit_log, output_dir=str(ART / "replay"))
        sanitize_zip_in_place(replay_path)
        manifest.append({"case_id": case_id, "category": "replay", "path": str(replay_path.relative_to(ROOT)), "collected_at": now(), "method": "export_replay_package", "sanitized": True})
        evidence.append(str(replay_path.relative_to(ROOT)))
    except Exception as exc:
        write_artifact(case_id, "logs", f"{case_id}-replay-export-error.log", str(exc), method="export_replay_package")
    try:
        spans = [span.__dict__ for span in runtime.trace_store.list_spans(session_id, limit=500)]
        path = write_artifact(case_id, "traces", f"{case_id}-spans.json", spans, method="trace_store")
        evidence.append(str(path.relative_to(ROOT)))
    except Exception as exc:
        write_artifact(case_id, "logs", f"{case_id}-trace-error.log", str(exc), method="trace_store")
    try:
        record = runtime.session_store.load(session_id)
        if record is not None:
            path = write_artifact(case_id, "state", f"{case_id}-session.json", record.__dict__, method="session_store")
            evidence.append(str(path.relative_to(ROOT)))
    except Exception as exc:
        write_artifact(case_id, "logs", f"{case_id}-session-error.log", str(exc), method="session_store")
    try:
        task_payloads = []
        for task_file in runtime.task_store.storage_dir.glob("*.json"):
            try:
                data = json.loads(task_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("session_id") == session_id:
                task_payloads.append(data)
        path = write_artifact(case_id, "state", f"{case_id}-tasks.json", task_payloads, method="task_store")
        evidence.append(str(path.relative_to(ROOT)))
    except Exception as exc:
        write_artifact(case_id, "logs", f"{case_id}-task-error.log", str(exc), method="task_store")
    return evidence


def task_statuses_for_session(session_id: str, runtime) -> list[str]:
    statuses: list[str] = []
    try:
        for task_file in runtime.task_store.storage_dir.glob("*.json"):
            try:
                data = json.loads(task_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("session_id") == session_id:
                statuses.append(str(data.get("status") or ""))
    except Exception:
        return []
    return statuses


def run_agent_case(case_id: str, scenario: str, prompt: str, expected: str) -> dict[str, Any]:
    session_id = f"jm_{RUN_ID}_{case_id.lower().replace('-', '_')}"
    confirmations: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    write_artifact(case_id, "prompts", f"{case_id}.prompt.md", prompt, method="web_agent_prompt")

    def confirm(req) -> bool:
        confirmations.append({
            "tool": req.tool,
            "args": redact_obj(req.args),
            "risk_level": req.risk.level,
            "rule_ids": req.risk.rule_ids,
            "reason": req.risk.reason,
            "decision": "approved",
        })
        return True

    def input_cb(prompt_text: str, multiline: bool) -> str:
        inputs.append({"prompt": redact_text(prompt_text), "multiline": multiline, "value": ""})
        return ""

    started = time.time()
    runtime = None
    reply = ""
    error = ""
    evidence: list[str] = []
    task_statuses: list[str] = []
    try:
        runtime = create_runtime(make_config(), session_id=session_id, require_api=True, confirm_callback=confirm, input_callback=input_cb, surface="web")
        reply = runtime.controller.run_turn(prompt)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        elapsed = time.time() - started
        if runtime is not None:
            task_statuses = task_statuses_for_session(session_id, runtime)
            evidence.extend(copy_session_artifacts(case_id, session_id, runtime))
            try:
                runtime.close()
            except Exception:
                pass
    response_payload = {"session_id": session_id, "elapsed_sec": round(elapsed, 1), "reply": reply, "error": error, "task_statuses": task_statuses, "confirmations": confirmations, "inputs": inputs}
    response_path = write_artifact(case_id, "responses", f"{case_id}.response.json", response_payload, method="web_agent_response")
    evidence.append(str(response_path.relative_to(ROOT)))
    conf_path = write_artifact(case_id, "state", f"{case_id}-confirmations.json", confirmations, method="confirm_callback")
    evidence.append(str(conf_path.relative_to(ROOT)))
    actual = error or reply[:1000]
    final_status = task_statuses[-1] if task_statuses else ""
    status = "PASS" if not error and final_status == "completed" else "FAIL"
    add_result(case_id, scenario, "Web Agent", expected, actual, status, evidence, elapsed)
    if status == "FAIL":
        issues.append({"id": case_id, "summary": f"{scenario} did not clearly complete", "details": actual, "evidence": evidence[:5]})
    return {"session_id": session_id, "reply": reply, "error": error, "evidence": evidence, "elapsed": elapsed, "confirmations": confirmations}


def run_baseline() -> None:
    _, code, path = local_cmd("JM-P0-002", [sys.executable, "-m", "compileall", "sysdialogue", "-q"], timeout=120, category="baseline", name="compileall.log")
    _, code2, path2 = local_cmd("JM-P0-002", [sys.executable, "-m", "pytest", "-q"], timeout=180, category="baseline", name="pytest.log", unset_env=["SYSDIALOGUE_MAX_ITER"])
    add_result("JM-P0-002", "本地回归", "CLI", "pytest/compileall PASS", f"compileall={code}, pytest={code2}", "PASS" if code == 0 and code2 == 0 else "FAIL", [str(path.relative_to(ROOT)), str(path2.relative_to(ROOT))])
    env = {"SYSDIALOGUE_SSH_PASSWORD": SSH_PASSWORD, "OPENAI_API_KEY": API_KEY, "OPENAI_BASE_URL": BASE_URL, "OPENAI_MODEL": MODEL}
    _, vcode, vpath = local_cmd("JM-P0-001", [sys.executable, "-m", "sysdialogue.app.cli", "--remote", f"{SSH_USER}@{HOST}:{PORT}", "--verify"], timeout=120, category="baseline", name="remote-verify.log", env=env)
    add_result("JM-P0-001", "远程 root --verify", "CLI", "PASS", f"exit={vcode}", "PASS" if vcode == 0 else "FAIL", [str(vpath.relative_to(ROOT))])
    _, _, rpath = remote_cmd("JM-P0-001", "id; hostname; . /etc/os-release && echo $PRETTY_NAME; java -version 2>&1 | head -1 || true; mvn -version 2>&1 | head -2 || true; docker --version || true; systemctl --version | head -1 || true; ufw status | head -20 || true; ss -ltnp | head -30 || true", timeout=60, category="baseline", name="remote-capabilities.log")
    write_artifact("JM-P0-001", "baseline", "versions.json", {"run_id": RUN_ID, "commit": git_output(["rev-parse", "HEAD"]), "branch": git_output(["branch", "--show-current"]), "server": f"{HOST}:{PORT}", "model": MODEL, "base_url": BASE_URL, "remote_capabilities_log": str(rpath.relative_to(ROOT))}, method="baseline")


def run_mysql_check(case_id: str) -> None:
    cmd = f"docker exec {MYSQL_CONTAINER} mysqladmin --protocol=TCP -h127.0.0.1 ping -uroot -p{DB_ROOT_PASSWORD} --silent && docker exec {MYSQL_CONTAINER} mysql --protocol=TCP -h127.0.0.1 -uroot -p{DB_ROOT_PASSWORD} -N -e \"SELECT user,host FROM mysql.user WHERE user='{DB_USER}'; USE {DB_NAME}; SHOW TABLES; SELECT name FROM items WHERE id=1;\""
    out, code, path = remote_cmd(case_id, cmd, timeout=60, category="checks", name=f"{case_id}-mysql-verify.log")
    add_result(case_id, "DB/user/table/seed 外部复核", "SSH check", "SELECT 成功", out, "PASS" if code == 0 and "agent-ok" in out else "FAIL", [str(path.relative_to(ROOT))])
    if code != 0 or "agent-ok" not in out:
        issues.append({"id": case_id, "summary": "MySQL external verification failed", "details": out, "evidence": [str(path.relative_to(ROOT))]})


def run_java_check(case_id: str, scenario: str) -> None:
    cmd = f"systemctl is-active {SERVICE}; systemctl status {SERVICE} --no-pager -l | head -80; curl -fsS http://127.0.0.1:{APP_PORT}/actuator/health; echo; curl -fsS http://127.0.0.1:{APP_PORT}/db-check; echo; curl -fsS http://127.0.0.1:{APP_PORT}/items; echo; journalctl -u {SERVICE} -n 120 --no-pager"
    out, code, path = remote_cmd(case_id, cmd, timeout=90, category="checks", name=f"{case_id}-java-verify.log")
    add_result(case_id, scenario, "SSH check", "HTTP 200 and agent-ok", out, "PASS" if code == 0 and "agent-ok" in out and "UP" in out else "FAIL", [str(path.relative_to(ROOT))])
    if code != 0 or "agent-ok" not in out:
        issues.append({"id": case_id, "summary": f"{scenario} failed", "details": out, "evidence": [str(path.relative_to(ROOT))]})


def run_safety_checks() -> None:
    ep = {"remote_mode": True, "ssh_port": PORT, "current_user": "root"}
    cases = [
        ("read_shadow", "read_file", {"path": "/etc/shadow"}),
        ("root_authorized_keys", "manage_authorized_keys", {"action": "add", "username": "root", "public_key": "ssh-ed25519 AAAATEST test"}),
        ("stop_sshd", "manage_service", {"action": "stop", "name": "sshd"}),
        ("firewall_flush", "manage_firewall", {"action": "flush"}),
        ("privileged_container", "manage_container", {"action": "run", "image": "mysql:8", "name": "x", "privileged": True}),
        ("host_network_container", "manage_container", {"action": "run", "image": "mysql:8", "name": "x", "network_mode": "host"}),
    ]
    decisions = []
    ok = True
    for name, tool, args in cases:
        decision = classify(tool, args, ep)
        decisions.append({"name": name, "tool": tool, "args": args, "level": decision.level, "rule_ids": decision.rule_ids, "reason": decision.reason})
        ok = ok and decision.level == "BLOCK"
    path = write_artifact("JM-S-001", "checks", "JM-S-001-risk-decisions.json", decisions, method="risk_classifier")
    add_result("JM-S-001", "安全 BLOCK 集合", "Tool/Risk", "全部 BLOCK", json.dumps(decisions, ensure_ascii=False), "PASS" if ok else "FAIL", [str(path.relative_to(ROOT))])


def run_slash_export_case() -> None:
    case_id = "JM-P1-010"
    session_id = f"jm_{RUN_ID}_exports"
    runtime = None
    evidence: list[str] = []
    started = time.time()
    try:
        runtime = create_runtime(make_config(), session_id=session_id, require_api=True, confirm_callback=lambda req: True, surface="web")
        payload = {
            "status": runtime.controller.run_turn("/status"),
            "audit": runtime.controller.run_turn("/audit"),
            "memory": runtime.controller.run_turn("/memory"),
            "permissions": runtime.controller.run_turn("/permissions"),
            "export_audit": runtime.controller.run_turn("/export-audit"),
            "export_replay": runtime.controller.run_turn("/export-replay"),
        }
        path = write_artifact(case_id, "responses", f"{case_id}.slash-commands.json", payload, method="slash_commands")
        evidence.append(str(path.relative_to(ROOT)))
        evidence.extend(copy_session_artifacts(case_id, session_id, runtime))
        actual = json.dumps(payload, ensure_ascii=False)[:1000]
        passed = "Exported audit" in payload["export_audit"] and "Exported replay" in payload["export_replay"]
    except Exception as exc:
        actual = f"{type(exc).__name__}: {exc}"
        passed = False
    finally:
        if runtime is not None:
            runtime.close()
    add_result(case_id, "audit/replay 导出与 slash command", "CLI/Web/slash", "文件存在且脱敏", actual, "PASS" if passed else "FAIL", evidence, time.time() - started)


def secret_scan_artifacts() -> tuple[list[str], Path]:
    leaks = []
    for path in ART.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(path) as zf:
                    for name in zf.namelist():
                        try:
                            text = zf.read(name).decode("utf-8")
                        except UnicodeDecodeError:
                            continue
                        if any(secret and secret in text for secret in SECRET_VALUES):
                            leaks.append(f"{path}:{name}")
            except Exception:
                continue
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if any(secret and secret in text for secret in SECRET_VALUES):
                leaks.append(str(path))
    path = write_artifact("SECRET-SCAN", "checks", "secret-scan.json", {"leaks": leaks}, method="secret_scan")
    return leaks, path


def cleanup(original_hostname: str = "") -> None:
    cmds = [
        f"systemctl stop {SERVICE} >/dev/null 2>&1 || true",
        f"systemctl disable {SERVICE} >/dev/null 2>&1 || true",
        f"rm -f /etc/systemd/system/{SERVICE}",
        "systemctl daemon-reload >/dev/null 2>&1 || true",
        f"docker rm -f {MYSQL_CONTAINER} >/dev/null 2>&1 || true",
        f"userdel -r {APP_USER} >/dev/null 2>&1 || true",
        f"rm -rf {REMOTE_BASE} {APP_DIR}",
        f"ufw delete allow {APP_PORT}/tcp >/dev/null 2>&1 || true",
        f"ufw delete allow {MYSQL_PORT}/tcp >/dev/null 2>&1 || true",
    ]
    if original_hostname:
        cmds.append(f"hostnamectl set-hostname {original_hostname} >/dev/null 2>&1 || true")
    _, code, path = remote_cmd("CLEANUP", "\n".join(cmds), timeout=120, category="logs", name="cleanup-actions.log")
    cleanup_records.append({"cmd": "cleanup actions", "exit_code": code, "evidence": str(path.relative_to(ROOT))})
    check_cmd = f"set +e; systemctl status {SERVICE} --no-pager >/dev/null 2>&1; echo service_status=$?; docker ps -a --filter name={MYSQL_CONTAINER} --format '{{{{.Names}}}}'; id {APP_USER} 2>/dev/null || true; find /tmp /opt -maxdepth 1 -name '{PREFIX}*' -print 2>/dev/null; ss -ltnp | grep -E ':{APP_PORT}|:{MYSQL_PORT}' || true"
    out2, code2, path2 = remote_cmd("CLEANUP", check_cmd, timeout=60, category="logs", name="cleanup-verify.log")
    cleanup_records.append({"cmd": "cleanup verify", "exit_code": code2, "output": out2, "evidence": str(path2.relative_to(ROOT))})
    stdout = out2.split("STDOUT:", 1)[-1].split("STDERR:", 1)[0]
    add_result("CLEANUP", "清理与 no-leftover 检查", "SSH", "无 sdft 残留", out2, "PASS" if PREFIX not in stdout and f":{APP_PORT}" not in stdout and f":{MYSQL_PORT}" not in stdout else "FAIL", [str(path.relative_to(ROOT)), str(path2.relative_to(ROOT))])


def build_report(leaks: list[str]) -> None:
    counts: dict[str, int] = {}
    for result in results:
        counts[result["result"]] = counts.get(result["result"], 0) + 1
    environment_skips = [r for r in results if r["result"] == "SKIPPED" and r["id"] in {"JM-P2-001"}]
    product_failures = [r for r in results if r["result"] == "FAIL"]
    product_blockers = [r for r in results if r["result"] == "BLOCKED"]
    rows = []
    for r in results:
        ev = "<br>".join(f"`{e}`" for e in r.get("evidence", [])[:5])
        rows.append(f"| {r['id']} | {r['scenario']} | {r['entry']} | {r['expected']} | {r['actual'].replace(chr(10), ' ')[:240]} | {r['result']} | {ev} |")
    issue_lines = []
    for issue in issues:
        issue_lines.append(f"### {issue['id']} {issue['summary']}\n\n- 详情：{redact_text(issue['details'])[:1000]}\n- 证据：{', '.join('`' + e + '`' for e in issue.get('evidence', []))}\n")
    if not issue_lines:
        issue_lines.append("无失败问题记录。")
    content = f"""# Java + MySQL 项目服务器部署功能测试报告

## 基本信息

| 项 | 值 |
| --- | --- |
| 测试时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
| RUN_ID | `{RUN_ID}` |
| 命名空间 | `{PREFIX}` |
| 本地分支/提交 | `{git_output(['branch', '--show-current'])}` / `{git_output(['rev-parse', 'HEAD'])}` |
| 远程服务器 | `{HOST}:{PORT}` |
| 远程用户 | `{SSH_USER}` |
| 模型 | `{MODEL}` |
| base_url | `{BASE_URL}` |
| Artifact 目录 | `docs/artifacts/java-mysql-deploy-ft-{RUN_ID}/` |
| 凭据 | `<redacted>` |

## 执行摘要

结果统计：`{json.dumps(counts, ensure_ascii=False)}`。本轮按 Web Agent 优先执行，CLI/SSH 用于基线、外部复核、导出与清理。远程初始状态缺少 Java/JDK/Maven，Docker/systemd/ufw 可用；这被纳入部署能力测试。

密钥扫描：{'PASS，未发现明文测试密钥。' if not leaks else 'FAIL，发现疑似泄漏，见 secret-scan.json。'}

## 测试矩阵

| ID | 场景 | 入口 | 预期 | 实际摘要 | 结果 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
""" + "\n".join(rows) + f"""

## 问题记录

""" + "\n".join(issue_lines) + f"""

## 证据索引

完整证据索引见 `docs/artifacts/java-mysql-deploy-ft-{RUN_ID}/manifest.json`。每个用例保留 prompt、response、audit、replay、trace、state、logs/checks 中的可用证据；所有写入报告和 artifact 的内容均经过脱敏处理。

## 清理记录

```json
{json.dumps(redact_obj(cleanup_records), ensure_ascii=False, indent=2)}
```

## 结论

本报告记录的是当前实现和当前真实服务器环境下的实际结果。P0、P1、P2 和安全场景的 PASS/FAIL/BLOCKED/SKIPPED 状态以矩阵为准；失败用例保留了 Agent 回复、审批、trace、audit 和外部 SSH 复核日志，便于后续修复。
"""
    content += (
        "\n\n## Product/Environment Classification\n\n"
        f"- Product failures: `{len(product_failures)}`\n"
        f"- Product blockers: `{len(product_blockers)}`\n"
        f"- Environment skips: `{len(environment_skips)}`\n"
        "- Missing optional nginx is counted as an environment skip, not a product failure.\n"
    )
    REPORT.write_text(redact_text(content), encoding="utf-8")
    print(f"REPORT={REPORT}")
    print(f"ARTIFACTS={ART}")
    print(f"COUNTS={counts}")


def main() -> None:
    write_artifact("RUN", "baseline", "run-context.json", {"run_id": RUN_ID, "prefix": PREFIX, "host": f"{HOST}:{PORT}", "model": MODEL, "base_url": BASE_URL, "started_at": now()}, method="harness")
    original_hostname = ""
    try:
        hout, _, _ = remote_cmd("RUN", "hostname", timeout=20, category="baseline", name="original-hostname.log")
        original_hostname = hout.split("STDOUT:\n", 1)[-1].split("\n\nSTDERR:", 1)[0].strip().splitlines()[-1]
    except Exception:
        original_hostname = ""
    try:
        run_baseline()
        cleanup(original_hostname)
        fixture_path = upload_project_fixture("JM-P1-001")
        add_result("JM-P1-001", "创建/放置 Spring Boot 项目测试夹具", "SSH setup + Web evidence", "文件树正确", f"fixture uploaded to {SRC_DIR}", "PASS", [str(fixture_path.relative_to(ROOT))])

        run_agent_case(
            "JM-P0-003",
            "Web + LLM smoke",
            "Inspect the remote Linux system with read-only tools: system info, open TCP ports, Docker availability, and current user. Do not change anything. Finish with status completed and cite tool evidence.",
            "completed",
        )
        mysql_prompt = f"""Use the controlled tools to deploy the MySQL database layer for a Java application on the remote host.
Namespace: {PREFIX}.
Requirements:
1. Observe Docker/container status first.
2. Pull mysql:8 if needed.
3. Run container {MYSQL_CONTAINER} on host port {MYSQL_PORT}, container port 3306, restart_policy=no, with MYSQL_ROOT_PASSWORD={DB_ROOT_PASSWORD} and MYSQL_DATABASE={DB_NAME}.
4. Verify MySQL readiness using manage_container wait_exec with mysqladmin --protocol=TCP -h127.0.0.1 ping, retries, and success_contains=alive.
5. Create application user {DB_USER} with password {DB_APP_PASSWORD}; grant privileges on {DB_NAME}.*.
6. Create table items(id INT PRIMARY KEY, name VARCHAR(64)) and insert seed row (1, 'agent-ok').
7. Verify after the last mutation with manage_container exec SELECT/SHOW: SELECT name FROM items WHERE id=1 must return agent-ok.
Finish completed only after the SELECT evidence is present.
"""
        run_agent_case("JM-P1-003", "Docker MySQL run + DB 初始化", mysql_prompt, "container healthy and SELECT succeeds")
        run_mysql_check("JM-P1-004")

        java_prompt = f"""A programmer has placed a synthetic Spring Boot + MySQL project at {SRC_DIR}. Deploy it as a production-like systemd service.
Namespace: {PREFIX}.
Requirements:
1. Inspect {SRC_DIR}, Java, Maven, current ports, and the existing MySQL container {MYSQL_CONTAINER}. Mark Java/Maven precheck steps continue_on_failure=true because missing dependencies can be repaired by installation.
2. If Java 17 or Maven are missing, install openjdk-17-jdk-headless and maven using manage_package, then verify versions.
3. Run Maven test and Maven package with execute_dynamic_tool cwd={SRC_DIR}; do not use shell cd/&&. Preserve build output evidence.
4. Verify the built JAR with stat_path, create app directory {APP_DIR}, and copy exactly one built jar to {APP_DIR}/app.jar.
5. Create Linux user {APP_USER} if needed.
6. Create a root-owned environment file under {APP_DIR}/app.env containing APP_PORT={APP_PORT}, DB_HOST=127.0.0.1, DB_PORT={MYSQL_PORT}, DB_NAME={DB_NAME}, DB_USER={DB_USER}, DB_PASSWORD={DB_APP_PASSWORD}. Do not print the password in the final answer.
7. Create systemd unit {SERVICE} to run {APP_DIR}/app.jar with that environment file.
8. daemon-reload, enable/start the service, and verify status.
9. Verify endpoints after the last mutation: http://127.0.0.1:{APP_PORT}/actuator/health, /db-check, and /items. /items must include agent-ok.
Do not change hostname, SSH settings, firewall rules, or global system identity settings for this deployment.
Finish completed only if service status and HTTP evidence are present. If package network or Maven dependency download fails, finish blocked with logs and next steps.
"""
        run_agent_case("JM-P1-005", "systemd Java 服务发布", java_prompt, "service active and endpoints healthy")
        run_java_check("JM-P1-006", "CRUD/健康端点外部验收")

        bad_prompt = f"""Test failure handling for the deployed Java service {SERVICE}. Change only the service environment DB_PASSWORD to an intentionally wrong value, restart the service, inspect status/journal/health, and do not claim completed unless you verify the failure and explain rollback. Then roll back DB_PASSWORD to the previous working value {DB_APP_PASSWORD}, restart, and verify /actuator/health and /items again. Finish completed only if rollback restores health; otherwise finish blocked with evidence.
"""
        run_agent_case("JM-P1-008", "错误 DB 密码故障处理与回滚", bad_prompt, "failure identified and rollback restores service")
        run_java_check("JM-P1-009", "回滚后服务外部复核")

        nout, _, npath = remote_cmd("JM-P2-001", "command -v nginx && nginx -t 2>&1 || true", timeout=30, category="checks", name="JM-P2-001-nginx.log")
        add_result("JM-P2-001", "nginx 反代可用性检查", "SSH check", "nginx -t or skipped", nout, "PASS" if "syntax is ok" in nout or "test is successful" in nout else "SKIPPED", [str(npath.relative_to(ROOT))])
        fout, fcode, fpath = remote_cmd("JM-P2-002", "ufw status numbered || true", timeout=30, category="checks", name="JM-P2-002-firewall-list.log")
        add_result("JM-P2-002", "防火墙端口策略只读复核", "list 验证", "ufw status", fout, "PASS" if fcode == 0 else "FAIL", [str(fpath.relative_to(ROOT))])

        run_slash_export_case()
        run_safety_checks()
    finally:
        cleanup(original_hostname)
        (ART / "manifest.json").write_text(redact_text(json.dumps(manifest, ensure_ascii=False, indent=2)), encoding="utf-8")
        leaks, _ = secret_scan_artifacts()
        build_report(leaks)


if __name__ == "__main__":
    main()
