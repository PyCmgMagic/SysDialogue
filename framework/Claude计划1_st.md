# AI Hackathon 2026 — OS Intelligent Agent 详细实现方案

> 超聚变数字技术 AI Hackathon 2026 初赛：操作系统智能代理  
> 文档：`D:\match\AI_Hackathon_2026.pdf` | 评分：客观 70% + 主观 30%  
> 设计参考：**Warp Terminal**（warpdotdev/Warp）— 当前最成熟的 AI Agent 终端产品

---

## 一、技术栈选型

| 层级 | 选型 | 版本 | 理由 |
|---|---|---|---|
| 语言 | Python | 3.11+ | 最快迭代，AI SDK 生态最好，subprocess/psutil 完备 |
| AI 模型 | claude-sonnet-4-6 | 最新 | 工具调用最可靠，64K 输出 token，1M 上下文 |
| SDK | anthropic | ≥0.40.0 | 官方 Python SDK，支持流式+工具调用 |
| 主界面 | Textual | ≥1.0.0 | SSH 终端可用，支持 CSS 样式，响应式更新 |
| 备用界面 | Rich + prompt-toolkit | 最新 | `--simple` 降级模式，哑终端兼容 |
| OS 监控 | psutil | ≥6.0 | 跨发行版，无 shell 依赖，进程/端口/磁盘一体 |
| 数据校验 | pydantic | ≥2.0 | 工具参数类型安全，配置文件校验 |
| 配置文件 | TOML | stdlib (3.11+) | 人类友好，安全门 allowlist 用 |
| Workflow | PyYAML | ≥6.0 | Workflow 模板文件解析 |
| 语音（可选）| SpeechRecognition | ≥3.10 | `--voice` 模式，演示加分项 |

**依赖文件 `pyproject.toml`：**
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "sysdialogue"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=0.40.0",
    "textual>=1.0.0",
    "rich>=13.0.0",
    "psutil>=6.0.0",
    "pydantic>=2.0.0",
    "PyYAML>=6.0",
    "SpeechRecognition>=3.10",  # 可选
]

[project.scripts]
sysdialogue = "sysdialogue.__main__:main"
```

---

## 二、项目目录结构

```
sysdialogue/
├── __main__.py              # 入口：参数解析，模式选择
├── config.py                # pydantic Settings，加载 ~/.sysdialogue/config.toml
├── conversation.py          # ConversationManager：历史窗口 + OS 快照注入
├── claude_client.py         # ClaudeClient：agentic loop + 流式输出
│
├── security/
│   ├── __init__.py
│   ├── risk_classifier.py   # RiskClassifier：SAFE/WARN/BLOCK 规则引擎
│   ├── risk_rules.py        # 声明式规则表（策略文件，最高价值模块）
│   └── audit_log.py         # AuditLog：追加式 JSON Lines 日志
│
├── tools/
│   ├── __init__.py          # ToolRegistry：工具注册 + Claude tool_definitions 生成
│   ├── base.py              # ToolCall, ToolResult dataclasses
│   ├── disk.py              # 磁盘工具：psutil + df/du/lsblk
│   ├── files.py             # 文件工具：find（有界限）
│   ├── processes.py         # 进程工具：psutil（跨发行版）
│   ├── ports.py             # 端口工具：psutil + ss fallback
│   └── users.py             # 用户工具：useradd/userdel/usermod（sudo 受限）
│
├── executor.py              # SafeExecutor：subprocess 封装，超时+截断+审计
│
├── planning.py              # PlanningEngine：多步骤计划生成与执行
│
├── workflows/
│   ├── engine.py            # WorkflowEngine：YAML 模板加载与执行
│   ├── new_user.yaml        # 内置：创建用户
│   ├── disk_cleanup.yaml    # 内置：磁盘清理
│   ├── security_audit.yaml  # 内置：安全审计
│   └── port_scan.yaml       # 内置：端口扫描
│
├── ui/
│   ├── tui_app.py           # SysDialogueApp：Textual App 主体
│   ├── widgets.py           # BlockWidget, StatusPanel, ConfirmModal
│   ├── simple_cli.py        # 纯文本 fallback 模式
│   └── voice.py             # 语音输入（可选）
│
└── prompts/
    └── system_prompt.py     # 构建带实时 OS 快照的 system prompt
```

---

## 三、核心架构：Agentic Loop 实现

### 3.1 完整 Agentic Loop 代码（`claude_client.py`）

```python
import anthropic
from anthropic.types import MessageParam, ToolResultBlockParam
from typing import AsyncGenerator, Callable
import asyncio

class ClaudeClient:
    MAX_ITERATIONS = 10  # 防止无限循环
    
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
    
    async def run_turn(
        self,
        messages: list[MessageParam],
        tools: list[dict],
        system_prompt: str,
        on_tool_call: Callable,      # 安全门回调：返回 ToolResult
        on_text_chunk: Callable,     # 流式文本回调
        on_tool_event: Callable,     # 工具执行状态回调（用于 TUI Block）
    ) -> str:
        """执行一轮完整的 agentic 对话，包含多次工具调用"""
        
        current_messages = messages.copy()
        final_text = ""
        iterations = 0
        
        while iterations < self.MAX_ITERATIONS:
            iterations += 1
            
            # 流式调用 Claude API
            accumulated_content = []
            accumulated_text = ""
            
            with self.client.messages.stream(
                model=self.model,
                max_tokens=8192,
                system=system_prompt,
                tools=tools,
                messages=current_messages,
            ) as stream:
                # 流式输出文本（边生成边显示）
                for text_chunk in stream.text_stream:
                    accumulated_text += text_chunk
                    on_text_chunk(text_chunk)  # 回调给 TUI 更新 Block
                
                # 获取完整响应（含工具调用块）
                response = stream.get_final_message()
            
            # 将 assistant 消息加入历史
            current_messages.append({
                "role": "assistant",
                "content": response.content
            })
            
            # 如果不需要工具调用，结束循环
            if response.stop_reason == "end_turn":
                final_text = accumulated_text
                break
            
            if response.stop_reason != "tool_use":
                break
            
            # 处理所有工具调用（可能多个）
            tool_results: list[ToolResultBlockParam] = []
            
            for block in response.content:
                if block.type != "tool_use":
                    continue
                
                on_tool_event("running", block.name, block.input)
                
                # 调用安全门（同步，因为需要用户确认时阻塞）
                result = await on_tool_call(block)
                
                on_tool_event(
                    "blocked" if result.is_error else "done",
                    block.name, result.content
                )
                
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result.content,
                    "is_error": result.is_error,
                })
            
            # 将工具结果加入历史，继续循环
            current_messages.append({
                "role": "user",
                "content": tool_results
            })
        
        return final_text
```

**关键设计点：**
- `stream.text_stream` 逐 chunk 推送给 TUI，实现"打字机效果"
- `stream.get_final_message()` 保证获取完整工具调用（不被截断）
- BLOCK 时返回 `is_error=True` 的 tool_result，让 Claude 优雅解释原因（不抛异常）
- 最多 10 次工具调用迭代，防止 runaway agent

---

## 四、工具定义与 Tool Registry

### 4.1 工具注册表（`tools/__init__.py`）

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]

@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    duration_ms: int = 0

# 传给 Claude API 的 tool_definitions 列表
TOOL_DEFINITIONS = [
    {
        "name": "get_disk_usage",
        "description": "获取磁盘使用情况。不指定路径则返回所有挂载点。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要检查的路径，如 /var/log 或 / 表示所有挂载点"
                },
                "recursive": {
                    "type": "boolean",
                    "description": "是否递归统计子目录（仅对目录有效，可能较慢）",
                    "default": False
                }
            }
        }
    },
    {
        "name": "find_files",
        "description": "在指定目录下搜索文件。",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_path": {"type": "string", "description": "搜索起始路径"},
                "pattern": {"type": "string", "description": "文件名匹配模式，如 *.log"},
                "min_size_mb": {"type": "number", "description": "最小文件大小（MB）"},
                "max_depth": {"type": "integer", "description": "最大递归深度", "default": 3, "maximum": 10}
            },
            "required": ["search_path"]
        }
    },
    {
        "name": "list_processes",
        "description": "列出当前运行的进程，按 CPU 或内存排序。",
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "description": "返回前 N 个进程", "default": 10},
                "sort_by": {"type": "string", "enum": ["cpu", "memory"], "default": "memory"},
                "filter_user": {"type": "string", "description": "只显示指定用户的进程"}
            }
        }
    },
    {
        "name": "kill_process",
        "description": "终止指定 PID 的进程。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "进程 ID"},
                "signal": {"type": "string", "enum": ["SIGTERM", "SIGKILL"], "default": "SIGTERM"}
            },
            "required": ["pid"]
        }
    },
    {
        "name": "get_port_status",
        "description": "查询监听端口信息，可查询特定端口或所有端口。",
        "input_schema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "特定端口号，不指定则返回所有监听端口"},
                "protocol": {"type": "string", "enum": ["tcp", "udp", "all"], "default": "all"}
            }
        }
    },
    {
        "name": "create_user",
        "description": "在系统上创建新用户账号。需要 sudo 权限。",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "用户名"},
                "groups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "加入的附加用户组列表"
                },
                "shell": {"type": "string", "description": "登录 shell", "default": "/bin/bash"},
                "create_home": {"type": "boolean", "default": True}
            },
            "required": ["username"]
        }
    },
    {
        "name": "delete_user",
        "description": "删除系统用户账号。需要 sudo 权限。",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "要删除的用户名"},
                "remove_home": {"type": "boolean", "description": "是否同时删除主目录", "default": False}
            },
            "required": ["username"]
        }
    },
    {
        "name": "get_system_info",
        "description": "获取系统基本信息：主机名、OS 版本、CPU、内存、负载等。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
]
```

### 4.2 磁盘工具实现（`tools/disk.py`）

```python
import psutil
import subprocess
import shlex
from .base import ToolResult

def get_disk_usage(path: str = "/", recursive: bool = False) -> ToolResult:
    """使用 psutil 获取磁盘使用，fallback 到 df"""
    import time
    start = time.time()
    
    try:
        if path == "/" or path == "all":
            # 所有挂载点
            lines = []
            for part in psutil.disk_partitions():
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    lines.append(
                        f"{part.device:<20} {part.mountpoint:<15} "
                        f"{_human(usage.total):>8} {_human(usage.used):>8} "
                        f"{_human(usage.free):>8} {usage.percent:>5.1f}%"
                    )
                except PermissionError:
                    pass
            result = "挂载点磁盘使用情况：\n设备                 挂载点          总计     已用     可用   使用率\n"
            result += "\n".join(lines)
        else:
            usage = psutil.disk_usage(path)
            result = (
                f"路径: {path}\n"
                f"总计: {_human(usage.total)}\n"
                f"已用: {_human(usage.used)} ({usage.percent:.1f}%)\n"
                f"可用: {_human(usage.free)}"
            )
            
            if recursive:
                # du 递归统计（需要 subprocess）
                du_result = subprocess.run(
                    ["du", "-sh", "--max-depth=2", path],
                    capture_output=True, text=True, timeout=15
                )
                if du_result.returncode == 0:
                    result += f"\n\n子目录详情：\n{du_result.stdout[:5000]}"
        
        return ToolResult(content=result, duration_ms=int((time.time()-start)*1000))
    
    except FileNotFoundError:
        return ToolResult(content=f"路径不存在: {path}", is_error=True)
    except Exception as e:
        return ToolResult(content=f"磁盘查询失败: {e}", is_error=True)

def _human(n: int) -> str:
    """字节转人类可读"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"
```

### 4.3 进程工具实现（`tools/processes.py`）

```python
import psutil
from .base import ToolResult
import time

def list_processes(top_n: int = 10, sort_by: str = "memory",
                   filter_user: str = None) -> ToolResult:
    """使用 psutil 列出进程，纯 Python 无 shell 依赖"""
    start = time.time()
    
    procs = []
    for proc in psutil.process_iter(
        ['pid', 'name', 'username', 'cpu_percent', 'memory_percent',
         'status', 'create_time']
    ):
        try:
            info = proc.info
            if filter_user and info['username'] != filter_user:
                continue
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    # 排序
    key = 'cpu_percent' if sort_by == 'cpu' else 'memory_percent'
    procs.sort(key=lambda x: x.get(key, 0) or 0, reverse=True)
    procs = procs[:top_n]
    
    # 格式化输出
    header = f"{'PID':>7} {'名称':<20} {'用户':<12} {'CPU%':>6} {'内存%':>6} {'状态'}"
    lines = [header, "-" * 60]
    for p in procs:
        lines.append(
            f"{p['pid']:>7} {(p['name'] or 'N/A')[:20]:<20} "
            f"{(p['username'] or 'N/A')[:12]:<12} "
            f"{(p['cpu_percent'] or 0):>6.1f} "
            f"{(p['memory_percent'] or 0):>6.2f} "
            f"{p['status']}"
        )
    
    return ToolResult(
        content="\n".join(lines),
        duration_ms=int((time.time()-start)*1000)
    )

def kill_process(pid: int, signal: str = "SIGTERM") -> ToolResult:
    """终止进程"""
    import signal as sig_module
    start = time.time()
    try:
        proc = psutil.Process(pid)
        proc_name = proc.name()
        if signal == "SIGKILL":
            proc.kill()
        else:
            proc.terminate()
        return ToolResult(
            content=f"已向进程 {proc_name}（PID {pid}）发送 {signal} 信号",
            duration_ms=int((time.time()-start)*1000)
        )
    except psutil.NoSuchProcess:
        return ToolResult(content=f"进程 {pid} 不存在", is_error=True)
    except psutil.AccessDenied:
        return ToolResult(content=f"无权限终止进程 {pid}（需要 sudo）", is_error=True)
```

### 4.4 端口工具实现（`tools/ports.py`）

```python
import psutil
import socket
import subprocess
from .base import ToolResult
import time

def get_port_status(port: int = None, protocol: str = "all") -> ToolResult:
    """端口查询，psutil 优先，ss 备用"""
    start = time.time()
    
    # 方案1：psutil（无需 root，更安全）
    try:
        kind_map = {"tcp": "tcp", "udp": "udp", "all": "inet"}
        conns = psutil.net_connections(kind=kind_map.get(protocol, "inet"))
        
        results = []
        for conn in conns:
            if conn.status not in (psutil.CONN_LISTEN, 'LISTEN', None):
                if protocol != "udp":  # UDP 没有 LISTEN 状态
                    continue
            
            if port and conn.laddr.port != port:
                continue
            
            # 查找进程名
            proc_name = "N/A"
            if conn.pid:
                try:
                    proc_name = psutil.Process(conn.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            proto = "TCP" if conn.type == socket.SOCK_STREAM else "UDP"
            results.append(
                f"{proto:<4} {conn.laddr.ip}:{conn.laddr.port:<6} "
                f"PID:{conn.pid or 'N/A':<8} {proc_name}"
            )
        
        if not results:
            msg = f"端口 {port} 未在监听" if port else "没有找到监听端口"
            return ToolResult(content=msg)
        
        header = f"协议 本地地址:端口      PID      进程名"
        return ToolResult(
            content=header + "\n" + "\n".join(sorted(results)),
            duration_ms=int((time.time()-start)*1000)
        )
    
    except PermissionError:
        # 方案2：降级到 ss 命令
        return _get_ports_via_ss(port, protocol, start)

def _get_ports_via_ss(port, protocol, start):
    """fallback: 用 ss 命令"""
    try:
        result = subprocess.run(
            ["ss", "-tlnp"] if protocol in ("tcp", "all") else ["ss", "-ulnp"],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout
        if port:
            lines = [l for l in output.split('\n') if f":{port}" in l]
            output = "\n".join(lines)
        return ToolResult(content=output, duration_ms=int((time.time()-start)*1000))
    except FileNotFoundError:
        # 方案3：最终降级到 netstat
        result = subprocess.run(
            ["netstat", "-tlnp"], capture_output=True, text=True, timeout=5
        )
        return ToolResult(content=result.stdout[:5000])
```

---

## 五、安全门（RiskClassifier）完整实现

### 5.1 规则表（`security/risk_rules.py`）

```python
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Any

class RiskLevel(Enum):
    SAFE  = "safe"   # 自动执行
    WARN  = "warn"   # 弹窗确认
    BLOCK = "block"  # 无条件拒绝

@dataclass
class RiskRule:
    rule_id: str
    level: RiskLevel
    description: str           # 给运维人员看的规则说明
    explanation: str           # 给用户看的拒绝/警告原因
    condition: Callable[[str, dict], bool]  # (tool_name, args) -> bool

# === BLOCK 规则（优先级最高）===
BLOCK_RULES: list[RiskRule] = [
    RiskRule("B001", RiskLevel.BLOCK,
        "禁止删除系统核心文件",
        "此操作会损坏系统完整性，可能导致服务器无法启动。已拒绝。",
        lambda name, args: (
            name == "delete_file" and
            any(args.get("path", "").startswith(p)
                for p in ["/etc/passwd", "/etc/shadow", "/etc/sudoers",
                          "/boot/", "/lib/systemd/", "/usr/lib/", "/bin/", "/sbin/"])
        )
    ),
    RiskRule("B002", RiskLevel.BLOCK,
        "禁止 kill PID 1（init/systemd）",
        "PID 1 是系统的根进程，终止它会导致系统崩溃。已拒绝。",
        lambda name, args: name == "kill_process" and args.get("pid") == 1
    ),
    RiskRule("B003", RiskLevel.BLOCK,
        "禁止创建 UID=0 的用户",
        "UID 0 等同于 root 权限，创建此类用户是安全漏洞。已拒绝。",
        lambda name, args: name == "create_user" and args.get("uid") == 0
    ),
    RiskRule("B004", RiskLevel.BLOCK,
        "禁止修改 root 账户",
        "修改 root 账户会影响系统安全性。已拒绝。",
        lambda name, args: name in ("delete_user", "modify_user") and
                           args.get("username") == "root"
    ),
    RiskRule("B005", RiskLevel.BLOCK,
        "禁止路径穿越攻击",
        "检测到路径遍历攻击模式（含 ..）。已拒绝。",
        lambda name, args: any(
            ".." in str(v) for v in args.values() if isinstance(v, str)
        )
    ),
    RiskRule("B006", RiskLevel.BLOCK,
        "禁止 find /（全盘无限制搜索）",
        "在 / 路径进行无限深度搜索会消耗大量资源。请指定具体路径或限制深度。",
        lambda name, args: (
            name == "find_files" and
            args.get("search_path", "") in ("/", "") and
            (args.get("max_depth", 99) or 99) > 5
        )
    ),
    RiskRule("B007", RiskLevel.BLOCK,
        "禁止访问内核内存和密钥文件",
        "此路径包含敏感系统数据，访问被禁止。",
        lambda name, args: any(
            str(v).startswith(p)
            for v in args.values() if isinstance(v, str)
            for p in ["/proc/kcore", "/dev/mem", "/proc/sys/kernel/"]
        )
    ),
]

# === WARN 规则 ===
WARN_RULES: list[RiskRule] = [
    RiskRule("W001", RiskLevel.WARN,
        "修改 /etc/ 下的配置文件",
        "此操作将修改系统配置文件，可能影响正在运行的服务。请确认操作。",
        lambda name, args: (
            name in ("delete_file", "write_file") and
            args.get("path", "").startswith("/etc/")
        )
    ),
    RiskRule("W002", RiskLevel.WARN,
        "终止系统进程",
        "您将终止一个系统级进程，可能影响系统稳定性。请确认操作。",
        lambda name, args: (
            name == "kill_process" and
            _is_system_process(args.get("pid"))
        )
    ),
    RiskRule("W003", RiskLevel.WARN,
        "删除用户账号",
        "此操作将永久删除用户账号。如果用户有运行中的进程或定时任务，可能造成问题。",
        lambda name, args: name == "delete_user"
    ),
    RiskRule("W004", RiskLevel.WARN,
        "递归统计大目录",
        "递归统计磁盘使用可能耗时较长，并产生大量 I/O。请确认操作。",
        lambda name, args: name == "get_disk_usage" and args.get("recursive")
    ),
    RiskRule("W005", RiskLevel.WARN,
        "修改用户所属组",
        "修改用户权限组将影响该用户的访问权限。请确认操作。",
        lambda name, args: name == "modify_user_groups"
    ),
    RiskRule("W006", RiskLevel.WARN,
        "创建新用户账号",
        "将在系统上创建新用户账号，该用户将能够登录系统。请确认操作。",
        lambda name, args: name == "create_user"
    ),
]

def _is_system_process(pid: int) -> bool:
    """判断是否为系统进程（非当前用户）"""
    if not pid:
        return False
    try:
        import psutil, os
        proc = psutil.Process(pid)
        return proc.uids().real != os.getuid()
    except Exception:
        return True  # 无法判断时保守处理
```

### 5.2 规则引擎（`security/risk_classifier.py`）

```python
from .risk_rules import BLOCK_RULES, WARN_RULES, RiskLevel, RiskRule
from ..tools.base import ToolCall

class ClassificationResult:
    def __init__(self, level: RiskLevel, rule: RiskRule | None, explanation: str):
        self.level = level
        self.rule = rule
        self.explanation = explanation

class RiskClassifier:
    """确定性规则引擎，不调用 LLM，毫秒级响应"""
    
    def classify(self, tool_call: ToolCall) -> ClassificationResult:
        # BLOCK 规则优先
        for rule in BLOCK_RULES:
            if rule.condition(tool_call.name, tool_call.args):
                return ClassificationResult(RiskLevel.BLOCK, rule, rule.explanation)
        
        # WARN 规则
        for rule in WARN_RULES:
            if rule.condition(tool_call.name, tool_call.args):
                return ClassificationResult(RiskLevel.WARN, rule, rule.explanation)
        
        # 默认 SAFE
        return ClassificationResult(RiskLevel.SAFE, None, "")
```

### 5.3 审计日志（`security/audit_log.py`）

```python
import json
import time
from pathlib import Path
from datetime import datetime, timezone

class AuditLog:
    def __init__(self, path: str = "~/.sysdialogue/audit.log"):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
    
    def record(self, tool_name: str, args: dict, risk_level: str,
               decision: str, rule_id: str = None,
               exit_code: int = None, duration_ms: int = None,
               user: str = None):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "args": self._sanitize(args),  # 脱敏
            "risk": risk_level,
            "decision": decision,  # "auto_executed" / "user_confirmed" / "user_cancelled" / "blocked_by_rule"
        }
        if rule_id:    entry["rule_id"] = rule_id
        if exit_code is not None: entry["exit_code"] = exit_code
        if duration_ms is not None: entry["duration_ms"] = duration_ms
        if user:       entry["user"] = user
        
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    def _sanitize(self, args: dict) -> dict:
        """脱敏：移除密码等敏感字段"""
        import re
        result = {}
        for k, v in args.items():
            if k.lower() in ("password", "passwd", "token", "secret", "key"):
                result[k] = "***REDACTED***"
            else:
                result[k] = v
        return result
```

---

## 六、System Prompt 设计（`prompts/system_prompt.py`）

每轮对话都注入实时系统快照，让 Claude 始终知晓当前环境：

```python
import psutil, socket, time, platform

def build_system_prompt(security_mode: str = "balanced") -> str:
    # 实时系统信息
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    load = psutil.getloadavg()
    
    try:
        distro = platform.freedesktop_os_release().get("NAME", "Linux")
    except Exception:
        distro = "Linux"
    
    return f"""你是一个 Linux 服务器系统管理 AI 助手，名为 SysDialogue。
你通过调用工具（而非生成原始 shell 命令）来完成系统管理任务。

## 当前系统状态（每轮自动刷新）
- 主机名：{socket.gethostname()}
- 系统：{distro}
- 当前用户：{_get_current_user()}
- 内存：{_human(mem.used)} / {_human(mem.total)} ({mem.percent:.1f}% 已用)
- 磁盘 /：{_human(disk.used)} / {_human(disk.total)} ({disk.percent:.1f}% 已用)
- 系统负载：{load[0]:.2f} {load[1]:.2f} {load[2]:.2f}（1/5/15分钟）

## 安全模式：{security_mode}
- balanced：只读操作自动执行，危险操作需确认，高危操作直接拒绝
- strict：所有操作均需确认
- permissive：仅高危操作拒绝

## 工作原则
1. 始终使用工具执行操作，**绝不**生成原始 shell 命令让用户自己执行
2. 执行操作前简要说明你的意图（一句话）
3. 操作完成后用自然语言总结结果
4. 如果工具返回错误，分析原因并提出替代方案
5. 对于多步骤任务，先展示执行计划再逐步执行
6. 不确定时宁可询问用户，不要擅自假设

## 语言
与用户用中文交流，技术术语可保留英文。"""
```

---

## 七、对话历史管理（`conversation.py`）

```python
from anthropic.types import MessageParam
import json
from pathlib import Path

class ConversationManager:
    MAX_TURNS = 20  # 滑动窗口，防止 token 爆炸
    
    def __init__(self, session_id: str = None):
        self.messages: list[MessageParam] = []
        self.session_id = session_id or _generate_id()
        self.session_path = Path(f"~/.sysdialogue/sessions/{self.session_id}.json").expanduser()
    
    def add_user_message(self, text: str):
        self.messages.append({"role": "user", "content": text})
        self._trim()
    
    def add_assistant_message(self, content):
        """content 可以是字符串或 Claude 返回的 content block 列表"""
        self.messages.append({"role": "assistant", "content": content})
        self._trim()
    
    def _trim(self):
        """保留最近 MAX_TURNS 轮，确保成对"""
        if len(self.messages) > self.MAX_TURNS * 2:
            # 保留偶数个（user/assistant 成对），从头删
            excess = len(self.messages) - self.MAX_TURNS * 2
            self.messages = self.messages[excess:]
    
    def save_session(self):
        """持久化会话到磁盘"""
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.session_path, "w", encoding="utf-8") as f:
            json.dump({
                "session_id": self.session_id,
                "messages": self.messages
            }, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def resume(cls, session_id: str) -> "ConversationManager":
        """恢复上次会话"""
        mgr = cls(session_id)
        if mgr.session_path.exists():
            data = json.loads(mgr.session_path.read_text())
            mgr.messages = data["messages"]
        return mgr
```

---

## 八、Planning 模式（`planning.py`）

借鉴 Warp 的 `/plan` 命令，复杂操作先展示计划：

```python
from dataclasses import dataclass
from security.risk_classifier import RiskClassifier, RiskLevel

@dataclass
class PlanStep:
    step_num: int
    description: str
    tool_name: str
    tool_args: dict
    risk_level: RiskLevel
    risk_explanation: str = ""

class PlanningEngine:
    """检测多步骤意图，生成执行计划供用户确认"""
    
    # 触发 Planning 模式的关键词
    PLANNING_KEYWORDS = [
        "配置", "设置", "部署", "批量", "帮我", "为我",
        "创建账号", "新建用户", "清理", "迁移", "备份"
    ]
    
    def should_plan(self, user_input: str) -> bool:
        return any(kw in user_input for kw in self.PLANNING_KEYWORDS)
    
    def format_plan(self, steps: list[PlanStep]) -> str:
        """格式化计划展示给用户"""
        lines = ["我将执行以下操作计划，请确认后输入 'go' 开始执行：\n"]
        for step in steps:
            risk_tag = {
                RiskLevel.SAFE: "[安全]",
                RiskLevel.WARN: "[⚠需确认]",
                RiskLevel.BLOCK: "[✗已禁止]",
            }[step.risk_level]
            lines.append(f"  步骤 {step.step_num} {risk_tag} {step.description}")
            if step.risk_level == RiskLevel.WARN:
                lines.append(f"           └─ {step.risk_explanation}")
        
        lines.append("\n输入 'go' 开始执行，或描述修改意见 >")
        return "\n".join(lines)
```

---

## 九、Warp 设计借鉴详解

### 9.1 Block UI 模型（`ui/widgets.py`）

```python
from textual.widget import Widget
from textual.reactive import reactive
from textual.widgets import Static
from rich.text import Text
from rich.panel import Panel

class BlockWidget(Widget):
    """
    借鉴 Warp Block 设计：每次对话为独立 Block，
    带状态标识、时间戳、工具调用摘要
    """
    
    DEFAULT_CSS = """
    BlockWidget {
        border: tall $secondary;
        margin: 0 0 1 0;
        padding: 0 1;
        height: auto;
    }
    BlockWidget.success { border: tall green; }
    BlockWidget.warn    { border: tall yellow; }
    BlockWidget.blocked { border: tall red; }
    BlockWidget.running { border: tall $accent; }
    """
    
    status = reactive("running")   # running / success / warn / blocked
    content_text = reactive("")
    tool_events: list = []
    
    def __init__(self, user_input: str, block_num: int, timestamp: str):
        super().__init__()
        self.user_input = user_input
        self.block_num = block_num
        self.timestamp = timestamp
        self.tool_events = []
    
    def watch_status(self, new_status: str):
        self.remove_class("running", "success", "warn", "blocked")
        self.add_class(new_status)
    
    def watch_content_text(self, new_text: str):
        self.refresh()
    
    def add_tool_event(self, status: str, tool_name: str, detail: str):
        icon = {"running": "⟳", "done": "✓", "blocked": "✗"}.get(status, "·")
        self.tool_events.append(f"  {icon} {tool_name}: {detail[:80]}")
        self.refresh()
    
    def render(self):
        status_icons = {
            "running": "⟳ 执行中",
            "success": "✓ 完成",
            "warn":    "⚠ 已确认",
            "blocked": "✗ 已拒绝",
        }
        header = f"Block #{self.block_num}  {status_icons.get(self.status, '')}  {self.timestamp}"
        
        content = f"[bold]> {self.user_input}[/bold]\n"
        if self.content_text:
            content += f"\n{self.content_text}\n"
        if self.tool_events:
            content += "\n" + "\n".join(self.tool_events)
        
        return Panel(content, title=header, border_style=self._border_color())
    
    def _border_color(self):
        return {"success": "green", "warn": "yellow",
                "blocked": "red", "running": "blue"}.get(self.status, "white")
```

### 9.2 三级权限模式

```toml
# ~/.sysdialogue/config.toml
[security]
mode = "balanced"         # strict | balanced | permissive

# 自定义 allowlist（这些工具跳过确认，即使是 WARN 级）
allowlist = [
    "get_disk_usage",
    "list_processes",
    "get_port_status",
    "get_system_info",
    "find_files",
]

# 自定义 denylist（这些工具永远 BLOCK）
denylist = []
```

```python
# config.py
from pydantic import BaseModel
from enum import Enum
import tomllib, pathlib

class SecurityMode(str, Enum):
    STRICT     = "strict"
    BALANCED   = "balanced"
    PERMISSIVE = "permissive"

class SecurityConfig(BaseModel):
    mode: SecurityMode = SecurityMode.BALANCED
    allowlist: list[str] = []
    denylist: list[str] = []

class AppConfig(BaseModel):
    security: SecurityConfig = SecurityConfig()
    api_key: str = ""
    model: str = "claude-sonnet-4-6"

def load_config() -> AppConfig:
    config_path = pathlib.Path("~/.sysdialogue/config.toml").expanduser()
    if config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        return AppConfig.model_validate(data)
    return AppConfig()
```

### 9.3 Workflow YAML 模板系统

```yaml
# workflows/new_user.yaml
name: "创建开发者账号"
description: "创建新用户，设置主目录，加入开发者组"
version: "1.0"
triggers:
  - "新建用户"
  - "创建账号"
  - "添加用户"

parameters:
  - name: username
    type: text
    description: "用户名"
    required: true
  - name: group
    type: enum
    description: "所属组"
    default: "developers"
    enum_values: ["developers", "ops", "admin"]

steps:
  - id: create
    tool: create_user
    args:
      username: "{{username}}"
      groups: ["{{group}}"]
      create_home: true
    risk: WARN
    description: "创建用户 {{username}}"
  
  - id: verify
    tool: get_system_info
    args: {}
    risk: SAFE
    description: "验证用户创建结果"
    depends_on: [create]
```

```python
# workflows/engine.py
import yaml
from pathlib import Path
import re

class WorkflowEngine:
    """YAML Workflow 模板加载与参数替换"""
    
    def __init__(self, workflow_dir: str = "sysdialogue/workflows"):
        self.workflow_dir = Path(workflow_dir)
        self.workflows = self._load_all()
    
    def _load_all(self) -> dict:
        workflows = {}
        for f in self.workflow_dir.glob("*.yaml"):
            with open(f, encoding="utf-8") as fp:
                wf = yaml.safe_load(fp)
                workflows[wf["name"]] = wf
        return workflows
    
    def match_trigger(self, user_input: str) -> dict | None:
        """检查用户输入是否匹配某个 Workflow 触发器"""
        for wf in self.workflows.values():
            for trigger in wf.get("triggers", []):
                if trigger in user_input:
                    return wf
        return None
    
    def render_step(self, step: dict, params: dict) -> dict:
        """将 {{username}} 等占位符替换为实际参数"""
        rendered = {}
        for k, v in step.get("args", {}).items():
            if isinstance(v, str):
                for param_name, param_val in params.items():
                    v = v.replace(f"{{{{{param_name}}}}}", str(param_val))
            rendered[k] = v
        return {**step, "args": rendered}
```

### 9.4 自我纠错机制

每个工具定义 fallback 链，命令不存在时自动降级：

```python
# tools/ports.py
TOOL_FALLBACK_CHAIN = {
    "get_port_status": [
        _get_via_psutil,    # 首选：psutil（无需 root）
        _get_via_ss,        # 次选：ss 命令
        _get_via_netstat,   # 三选：netstat
        _get_via_proc_net,  # 最后：/proc/net/tcp（原始解析）
    ]
}

def get_port_status(port=None, protocol="all") -> ToolResult:
    """尝试 fallback 链，直到成功"""
    last_error = None
    for fn in TOOL_FALLBACK_CHAIN["get_port_status"]:
        try:
            result = fn(port, protocol)
            if not result.is_error:
                return result
            last_error = result
        except (FileNotFoundError, PermissionError) as e:
            last_error = ToolResult(content=str(e), is_error=True)
            continue
    return ToolResult(
        content=f"所有方案均失败: {last_error.content if last_error else '未知错误'}",
        is_error=True
    )
```

---

## 十、TUI 主界面（`ui/tui_app.py`）

```python
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Static, RichLog, Header, Footer
from textual.screen import ModalScreen
from textual.reactive import reactive
import psutil, datetime, asyncio

class ConfirmModal(ModalScreen[bool]):
    """风险确认弹窗，需键入 'confirm' 而非简单 y/n"""
    
    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: auto;
        border: double yellow;
        background: $panel;
        padding: 1 2;
    }
    """
    
    def __init__(self, warning: str):
        super().__init__()
        self.warning = warning
    
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"[bold yellow]⚠ 风险操作警告[/bold yellow]")
            yield Static(f"\n{self.warning}\n")
            yield Static("[dim]输入 'confirm' 确认执行，或直接按 Enter 取消[/dim]")
            yield Input(placeholder="confirm", id="confirm_input")
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip().lower() == "confirm")

class StatusPanel(Static):
    """右侧系统状态面板，每 5 秒刷新"""
    
    def on_mount(self):
        self.set_interval(5, self.refresh_status)
        self.refresh_status()
    
    def refresh_status(self):
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)
        disk = psutil.disk_usage("/")
        load = psutil.getloadavg()
        
        self.update(
            f"[bold]系统状态[/bold]\n"
            f"CPU:  {cpu:.1f}%\n"
            f"内存: {mem.percent:.1f}% ({_human(mem.used)}/{_human(mem.total)})\n"
            f"磁盘: {disk.percent:.1f}%\n"
            f"负载: {load[0]:.2f} {load[1]:.2f} {load[2]:.2f}\n"
            f"\n[dim]{datetime.datetime.now().strftime('%H:%M:%S')}[/dim]"
        )

class SysDialogueApp(App):
    CSS = """
    Screen { layout: horizontal; }
    #chat_area { width: 3fr; border: solid $primary; }
    #status_area { width: 1fr; border: solid $secondary; padding: 1; }
    #blocks_scroll { height: 1fr; }
    #input_bar { height: 3; dock: bottom; }
    """
    
    BINDINGS = [
        ("ctrl+c", "quit", "退出"),
        ("f1", "show_help", "帮助"),
        ("f3", "show_audit", "审计日志"),
    ]
    
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="chat_area"):
                with VerticalScroll(id="blocks_scroll"):
                    pass  # BlockWidget 动态添加
                yield Input(placeholder="输入指令（如：查看磁盘使用情况）...",
                           id="cmd_input")
        with Vertical(id="status_area"):
            yield StatusPanel()
        yield Footer()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        event.input.value = ""
        asyncio.create_task(self._handle_input(event.value))
    
    async def _handle_input(self, user_input: str):
        # 创建新 Block
        block = BlockWidget(
            user_input,
            block_num=self._next_block_num(),
            timestamp=datetime.datetime.now().strftime("%H:%M:%S")
        )
        await self.query_one("#blocks_scroll").mount(block)
        self.query_one("#blocks_scroll").scroll_end(animate=False)
        
        # 调用 ClaudeClient，回调更新 Block
        await self.claude_client.run_turn(
            messages=self.conversation.messages,
            tools=TOOL_DEFINITIONS,
            system_prompt=build_system_prompt(self.config.security.mode.value),
            on_tool_call=self._handle_tool_call,
            on_text_chunk=lambda chunk: self._append_to_block(block, chunk),
            on_tool_event=lambda s, n, d: block.add_tool_event(s, n, d),
        )
        block.status = "success"
    
    async def _handle_tool_call(self, tool_call_block) -> ToolResult:
        """安全门：分级处理工具调用"""
        from tools.base import ToolCall
        tc = ToolCall(
            id=tool_call_block.id,
            name=tool_call_block.name,
            args=tool_call_block.input
        )
        
        result_cls = self.risk_classifier.classify(tc)
        
        if result_cls.level == RiskLevel.BLOCK:
            self.audit_log.record(tc.name, tc.args, "block",
                                  "blocked_by_rule", result_cls.rule.rule_id)
            return ToolResult(content=result_cls.explanation, is_error=True)
        
        if result_cls.level == RiskLevel.WARN:
            confirmed = await self.push_screen_wait(
                ConfirmModal(result_cls.explanation)
            )
            if not confirmed:
                self.audit_log.record(tc.name, tc.args, "warn", "user_cancelled")
                return ToolResult(content="用户已取消此操作。", is_error=True)
            self.audit_log.record(tc.name, tc.args, "warn", "user_confirmed")
        else:
            self.audit_log.record(tc.name, tc.args, "safe", "auto_executed")
        
        # 执行工具
        return self.executor.run(tc)
```

---

## 十一、SafeExecutor（`executor.py`）

```python
from tools.base import ToolCall, ToolResult
from tools import disk, processes, ports, files, users
import time

TOOL_MAP = {
    "get_disk_usage":  disk.get_disk_usage,
    "find_files":      files.find_files,
    "list_processes":  processes.list_processes,
    "kill_process":    processes.kill_process,
    "get_port_status": ports.get_port_status,
    "create_user":     users.create_user,
    "delete_user":     users.delete_user,
    "get_system_info": processes.get_system_info,
}

MAX_OUTPUT_BYTES = 50 * 1024  # 50KB 截断上限

class SafeExecutor:
    def run(self, tool_call: ToolCall) -> ToolResult:
        fn = TOOL_MAP.get(tool_call.name)
        if not fn:
            return ToolResult(
                content=f"未知工具: {tool_call.name}", is_error=True
            )
        
        start = time.time()
        try:
            result = fn(**tool_call.args)
            
            # 输出截断
            if len(result.content.encode()) > MAX_OUTPUT_BYTES:
                result.content = (
                    result.content[:MAX_OUTPUT_BYTES].rsplit('\n', 1)[0]
                    + f"\n... [输出已截断，共 {len(result.content)} 字节]"
                )
            
            result.duration_ms = int((time.time() - start) * 1000)
            return result
        
        except Exception as e:
            return ToolResult(
                content=f"工具执行异常: {type(e).__name__}: {e}",
                is_error=True,
                duration_ms=int((time.time() - start) * 1000)
            )
```

---

## 十二、入口与启动（`__main__.py`）

```python
import argparse
import sys

def main():
    parser = argparse.ArgumentParser(
        description="SysDialogue — Linux 系统管理 AI Agent"
    )
    parser.add_argument("--simple", action="store_true",
                        help="纯文本模式（无 TUI，适合哑终端）")
    parser.add_argument("--voice", action="store_true",
                        help="启用语音输入")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：所有操作需确认")
    parser.add_argument("--permissive", action="store_true",
                        help="宽松模式：仅高危操作拒绝")
    parser.add_argument("--resume", metavar="SESSION_ID",
                        help="恢复上次会话")
    parser.add_argument("--test-tools", action="store_true",
                        help="测试所有工具是否正常工作")
    
    args = parser.parse_args()
    
    if args.test_tools:
        _run_tool_tests()
        return
    
    config = load_config()
    if args.strict:
        config.security.mode = SecurityMode.STRICT
    elif args.permissive:
        config.security.mode = SecurityMode.PERMISSIVE
    
    if args.simple:
        from ui.simple_cli import run_simple_cli
        run_simple_cli(config, resume=args.resume)
    else:
        from ui.tui_app import SysDialogueApp
        app = SysDialogueApp(config=config, resume_session=args.resume)
        app.run()

def _run_tool_tests():
    """快速验证所有工具是否正常工作"""
    from tools import disk, processes, ports
    tests = [
        ("磁盘使用", lambda: disk.get_disk_usage("/")),
        ("进程列表", lambda: processes.list_processes(top_n=3)),
        ("端口状态", lambda: ports.get_port_status()),
        ("系统信息", lambda: processes.get_system_info()),
    ]
    for name, fn in tests:
        result = fn()
        status = "✓" if not result.is_error else "✗"
        print(f"{status} {name}: {result.content[:80]}...")

if __name__ == "__main__":
    main()
```

---

## 十三、部署与运行

### 13.1 一键安装脚本（`install.sh`）

```bash
#!/bin/bash
set -e

echo "=== SysDialogue 安装 ==="

# 检查 Python 版本
python3 --version | grep -E "3\.(11|12|13)" || {
    echo "需要 Python 3.11+"
    exit 1
}

# 安装（推荐 pipx）
if command -v pipx &>/dev/null; then
    pipx install .
else
    pip3 install --user .
fi

# 创建配置目录
mkdir -p ~/.sysdialogue/sessions

# 写入默认配置
cat > ~/.sysdialogue/config.toml <<'EOF'
[security]
mode = "balanced"
allowlist = ["get_disk_usage", "list_processes", "get_port_status", "get_system_info"]

[api]
# api_key 优先读取环境变量 ANTHROPIC_API_KEY
model = "claude-sonnet-4-6"
EOF

echo "✓ 安装完成"
echo "使用方法: export ANTHROPIC_API_KEY=sk-... && sysdialogue"
```

### 13.2 Sudoers 配置（用户管理功能）

```bash
# 仅在需要用户管理功能时配置
sudo visudo -f /etc/sudoers.d/sysdialogue

# 写入以下内容（替换 YOUR_USERNAME）：
# YOUR_USERNAME ALL=(ALL) NOPASSWD: /usr/sbin/useradd, /usr/sbin/userdel, /usr/sbin/usermod, /usr/bin/passwd
```

### 13.3 Docker 演示环境（`Dockerfile`）

```dockerfile
FROM openeuler/openeuler:22.03-lts-sp3

RUN yum install -y python3.11 python3.11-pip sudo passwd

WORKDIR /app
COPY . .
RUN pip3.11 install -e .

# 创建演示用 sudoers
RUN echo "root ALL=(ALL) NOPASSWD: /usr/sbin/useradd, /usr/sbin/userdel" >> /etc/sudoers

CMD ["sysdialogue", "--simple"]
```

---

## 十四、演示视频脚本（5-8 分钟）

| 时间 | 内容 | 对应评分 |
|---|---|---|
| 0-1min | 新 SSH 会话 → `bash install.sh` → 3秒内 TUI 启动 | Hook：冲击力 |
| 1-2min | "查看磁盘使用" → Block显示 / "找所有>100MB日志" → 文件列表 / "8080端口是什么进程" | 客观：基础功能 |
| 2-3min | "哪个进程占内存最多" → 列表；"它监听哪些端口" → 代词解析；"kill掉它" → WARN弹窗 | 客观：多轮上下文 |
| 3-4min | "删除sudoers文件" → 红色BLOCK Block；"删用户test" → 黄色WARN弹窗；键入confirm执行 | 主观：安全设计 |
| 4-5min | "为新同事alice创建账号，加入developers组" → Planning展示4步计划 → 输入go → 逐步执行 | 客观：操作闭合 |
| 5-6min | 展示审计日志（F3）：BLOCK/WARN/SAFE 均有记录 | 主观：工程质量 |
| 6-7min | 展示 `risk_rules.py`（声明式策略）、工具窄接口、Workflow YAML | 主观：创新性 |
| 7-8min | （可选）`--voice` 语音输入演示 | 主观：创新性 |

---

## 十五、验证方案

```bash
# 1. 工具自测
sysdialogue --test-tools

# 2. 安全规则回归测试
python -m pytest tests/test_security.py -v
# 测试用例：
# - B001: delete_file /etc/passwd → BLOCK
# - B002: kill_process pid=1 → BLOCK
# - W003: delete_user alice → WARN
# - SAFE: get_disk_usage / → SAFE（无弹窗）

# 3. 端到端场景测试（5个标准场景）
python scripts/demo_scenarios.py

# 4. 跨发行版兼容性
docker run -it openeuler/openeuler:22.03 sysdialogue --test-tools
docker run -it ubuntu:22.04 sysdialogue --test-tools
docker run -it centos:8 sysdialogue --test-tools
```

---

## 十六、评分对照表

| 评分项 | 权重 | 我们的方案 |
|---|---|---|
| 功能实现（基础+风险识别+复杂任务）| 50% | 8工具覆盖4个基础域；声明式安全门14条规则；Planning模式多步骤编排 |
| 环境感知 | 10% | 每轮注入实时系统快照；自动检测发行版；工具降级适配 busybox |
| 结果反馈 | 10% | 自然语言摘要；Block UI 状态标识；流式输出打字机效果 |
| 操作闭合 | 10% | Planning模式展示完整计划；多步骤统一反馈；会话持久化 |
| 性能 | 10% | psutil 纯 Python 无 shell 延迟；输出截断 50KB；流式显示 |
| 稳定性 | 10% | 工具 fallback 链；异常捕获不崩溃；`--simple` 降级模式 |
| 用户体验 | 10% | Block UI；流式输出；命令历史；会话恢复 |
| 工程质量 | 10% | 声明式规则引擎；窄接口工具；审计日志；pydantic 类型安全 |
| 创新价值 | 10% | Warp-style Block UI；Workflow YAML 模板；三级权限；MCP 兼容接口 |
