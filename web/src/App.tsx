import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import {
  Archive,
  ArrowUp,
  BellRing,
  Bot,
  Boxes,
  Braces,
  Check,
  ChevronRight,
  ClipboardList,
  Clock3,
  Command,
  Copy,
  Database,
  FileCode2,
  FileText,
  Gauge,
  History,
  KeyRound,
  Layers3,
  Laptop,
  LayoutGrid,
  Loader2,
  LockKeyhole,
  Network,
  PlugZap,
  Plus,
  RefreshCcw,
  Search,
  Send,
  Server,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  UploadCloud,
  Trash2,
  UsersRound,
  Workflow,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import {
  disconnectedServer,
} from "@/data/sysdialogue";
import { createSysDialogueApi, MissingApiConfigurationError, normalizeApiUrl } from "@/lib/api";
import type {
  ApiStatus,
  ApprovalRequest,
  AcceptanceChecklistPayload,
  AcceptanceMutationDrillRequest,
  AuditRecord,
  ChatMessage,
  ConnectionMode,
  InputFieldDefinition,
  Metric,
  NavigationItem,
  RiskLevel,
  RuntimeConfig,
  ReleaseReadinessResponse,
  ReleaseReadinessStatus,
  SafetyProfile,
  ServerConnection,
  Surface,
  TaskEvent,
  TaskExecutionResponse,
  TaskRun,
  TerminalLine,
  ToolCapability,
  WorkflowDefinition,
} from "@/lib/types";
import { cn, compactHost, formatClock, formatRelativeTime, uid } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

gsap.registerPlugin(useGSAP);

const navItems: NavigationItem[] = [
  { id: "workbench", label: "工作台", icon: Bot },
  { id: "servers", label: "服务器", icon: Server },
  { id: "tools", label: "工具", icon: Wrench },
  { id: "workflows", label: "工作流", icon: Workflow },
  { id: "audit", label: "审计", icon: ClipboardList },
  { id: "release", label: "Release", icon: ShieldCheck },
  { id: "settings", label: "设置", icon: Settings },
];

const A07_APPROVAL_PHRASE = "I APPROVE A07 MUTATION DRILL";

type AcceptanceDrillWorkflow = AcceptanceMutationDrillRequest["workflowName"];

interface AcceptanceDrillFormState {
  workflowName: AcceptanceDrillWorkflow;
  argsText: string;
  approvalPhrase: string;
  impact: string;
  rollback: string;
  verification: string;
  disposableTarget: boolean;
}

const categoryIcons: Record<string, LucideIcon> = {
  观测: Gauge,
  文件: FileText,
  权限: UsersRound,
  运行时: Boxes,
  自动化: Clock3,
  网络: Network,
};

const promptPresets = [
  {
    label: "只读巡检",
    text: "检查系统版本、负载、磁盘空间和监听端口，只读执行并总结需要关注的风险。",
  },
  {
    label: "排查服务",
    text: "检查失败的 systemd 服务、最近错误日志和资源占用，先只读观察，不做变更。",
  },
  {
    label: "安全概览",
    text: "做一次安全概览：用户、端口、进程、网络和可疑配置，只读采集证据。",
  },
];

const terminalPresets = [
  { label: "系统", command: "uname -a" },
  { label: "磁盘", command: "df -h" },
  { label: "负载", command: "uptime" },
  { label: "端口", command: "ss -tulpen || netstat -tulpen" },
  { label: "失败服务", command: "systemctl --failed --no-pager" },
];

const auditTypeOptions: Array<{ label: string; value: AuditTypeFilter }> = [
  { label: "全部类型", value: "all" },
  { label: "决策", value: "decision" },
  { label: "命令", value: "command_trace" },
  { label: "工作流", value: "workflow_step" },
  { label: "环境", value: "env_profile" },
  { label: "结果", value: "final" },
];

const auditRiskOptions: Array<{ label: string; value: AuditRiskFilter }> = [
  { label: "全部风险", value: "all" },
  { label: "SAFE", value: "SAFE" },
  { label: "LOW", value: "LOW" },
  { label: "WARN-HIGH", value: "WARN-HIGH" },
  { label: "HARD-BLOCK", value: "HARD-BLOCK" },
];

const RUNTIME_CONFIG_STORAGE_KEY = "sysdialogue.web.runtimeConfig.v1";
const SERVER_DRAFT_STORAGE_KEY = "sysdialogue.web.serverDraft.v1";
const RECENT_CONNECTIONS_STORAGE_KEY = "sysdialogue.web.recentConnections.v1";
const WORKSPACE_PATH = "D:/项目/Nexus";
const SURFACE_HISTORY_LIMIT = 16;

const defaultRuntimeConfig: RuntimeConfig = {
  apiUrl: import.meta.env.VITE_SYSDIALOGUE_API_URL ?? "http://127.0.0.1:8000/api",
  model: "",
  openaiBaseUrl: "",
  maxIterations: 160,
  workflowsDir: "",
  safetyProfile: "standard",
  streamEvents: true,
};

type RunTarget =
  | { kind: "tool"; item: ToolCapability }
  | { kind: "workflow"; item: WorkflowDefinition };

type PaletteItem = {
  detail: string;
  icon: LucideIcon;
  kind: "surface" | "tool" | "workflow";
  label: string;
  risk?: RiskLevel;
  value: string;
};

type RunArgsMode = "form" | "json";
type RecentConnection = Pick<ServerConnection, "mode" | "host" | "port" | "user" | "keyFile" | "safetyProfile"> & {
  id: string;
  lastUsed: string;
};
type AuditTypeFilter = "all" | AuditRecord["type"];
type AuditRiskFilter = "all" | RiskLevel;
type ReadinessState = "done" | "current" | "blocked";

interface ReadinessItem {
  label: string;
  detail: string;
  state: ReadinessState;
}

export default function App() {
  const rootRef = useRef<HTMLDivElement>(null);
  const [surface, setSurfaceState] = useState<Surface>("workbench");
  const [surfaceBackStack, setSurfaceBackStack] = useState<Surface[]>([]);
  const [surfaceForwardStack, setSurfaceForwardStack] = useState<Surface[]>([]);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>(() => loadRuntimeConfig());
  const [draftConfig, setDraftConfig] = useState<RuntimeConfig>(() => loadRuntimeConfig());
  const apiConfigured = Boolean(normalizeApiUrl(runtimeConfig.apiUrl));
  const api = useMemo(() => createSysDialogueApi(runtimeConfig), [runtimeConfig]);
  const [apiStatus, setApiStatus] = useState<ApiStatus>(apiConfigured ? "loading" : "missing_config");
  const [apiError, setApiError] = useState("");
  const [server, setServer] = useState<ServerConnection>(disconnectedServer);
  const [draftServer, setDraftServer] = useState<ServerConnection>(() => loadServerDraft());
  const [recentConnections, setRecentConnections] = useState<RecentConnection[]>(() => loadRecentConnections());
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [tools, setTools] = useState<ToolCapability[]>([]);
  const [workflowList, setWorkflowList] = useState<WorkflowDefinition[]>([]);
  const [audit, setAudit] = useState<AuditRecord[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [tasks, setTasks] = useState<TaskRun[]>([]);
  const [terminal, setTerminal] = useState<TerminalLine[]>([]);
  const [terminalNotice, setTerminalNotice] = useState("");
  const [topbarNotice, setTopbarNotice] = useState("");
  const [prompt, setPrompt] = useState("");
  const [commandText, setCommandText] = useState("");
  const [toolQuery, setToolQuery] = useState("");
  const [workflowQuery, setWorkflowQuery] = useState("");
  const [paletteQuery, setPaletteQuery] = useState("");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequest | null>(null);
  const [connectionError, setConnectionError] = useState("");
  const [operationError, setOperationError] = useState("");
  const [running, setRunning] = useState(false);
  const [runTarget, setRunTarget] = useState<RunTarget | null>(null);
  const [runArgsMode, setRunArgsMode] = useState<RunArgsMode>("form");
  const [runFormValues, setRunFormValues] = useState<Record<string, string>>({});
  const [runArgsText, setRunArgsText] = useState("{}");
  const [runArgsError, setRunArgsError] = useState("");
  const [clock, setClock] = useState(formatClock());
  const [acceptanceChecklist, setAcceptanceChecklist] = useState<AcceptanceChecklistPayload | null>(null);
  const [releaseInput, setReleaseInput] = useState("");
  const [releaseReport, setReleaseReport] = useState<ReleaseReadinessResponse | null>(null);
  const [releaseError, setReleaseError] = useState("");
  const [releaseLoading, setReleaseLoading] = useState(false);
  const [releaseDrillOpen, setReleaseDrillOpen] = useState(false);
  const [releaseDrillForm, setReleaseDrillForm] = useState<AcceptanceDrillFormState>(() => defaultAcceptanceDrillForm("service_restart"));
  const apiReady = apiStatus === "ready";

  useEffect(() => {
    let ignore = false;
    if (!apiConfigured) {
      setApiStatus("missing_config");
      setApiError("未配置 SysDialogue Web API URL。");
      return () => {
        ignore = true;
      };
    }
    setApiStatus("loading");
    api.getOverview().then((overview) => {
      if (ignore) return;
      setTools(overview.tools);
      setWorkflowList(overview.workflows);
      setAudit(overview.audit);
      setMetrics(overview.metrics);
      setApiStatus("ready");
      setApiError("");
    }).catch((error) => {
      if (ignore) return;
      setApiStatus("error");
      setApiError(errorMessage(error));
    });
    return () => {
      ignore = true;
    };
  }, [api, apiConfigured]);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(formatClock()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (surface !== "release" || !apiReady || acceptanceChecklist || releaseLoading) return;
    void loadAcceptanceChecklist();
  }, [surface, apiReady, acceptanceChecklist, releaseLoading]);

  useEffect(() => {
    saveServerDraft(draftServer);
  }, [draftServer]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: 0 });
      document.querySelector<HTMLElement>(".main-scroll [data-radix-scroll-area-viewport]")?.scrollTo({ top: 0 });
    });
  }, [surface]);

  useGSAP(
    () => {
      gsap.from(".gsap-in", {
        opacity: 0,
        y: 12,
        duration: 0.52,
        stagger: 0.035,
        ease: "power2.out",
      });
      gsap.to(".activity-pulse", {
        opacity: 0.42,
        scale: 1.18,
        repeat: -1,
        yoyo: true,
        duration: 0.9,
        ease: "sine.inOut",
      });
    },
    { scope: rootRef },
  );

  const activeTask = tasks.find(isOpenTask) ?? tasks[0];
  const hasOpenTask = tasks.some(isOpenTask);
  const readiness = useMemo(
    () => buildReadinessItems(runtimeConfig.apiUrl, apiConfigured, apiStatus, server, tools.length, workflowList.length),
    [apiConfigured, apiStatus, runtimeConfig.apiUrl, server, tools.length, workflowList.length],
  );
  const activeToolCount = tools.filter((tool) => !tool.readOnly).length;
  const filteredTools = useMemo(() => filterTools(tools, toolQuery), [tools, toolQuery]);
  const groupedTools = useMemo(() => groupTools(filteredTools), [filteredTools]);
  const filteredWorkflows = useMemo(
    () =>
      workflowList.filter((workflow) =>
        `${workflow.name} ${workflow.label} ${workflow.description}`.toLowerCase().includes(workflowQuery.toLowerCase()),
      ),
    [workflowList, workflowQuery],
  );
  const paletteItems = useMemo(
    () => buildPaletteItems(paletteQuery, tools, workflowList),
    [paletteQuery, tools, workflowList],
  );
  const terminalCommands = useMemo(() => recentTerminalCommands(terminal), [terminal]);
  const terminalTranscript = useMemo(() => formatTerminalTranscript(terminal), [terminal]);
  const terminalOutput = useMemo(() => latestTerminalOutput(terminal), [terminal]);
  const canGoBack = surfaceBackStack.length > 0;
  const canGoForward = surfaceForwardStack.length > 0;

  function setSurface(next: Surface) {
    if (next === surface) return;
    setSurfaceBackStack((current) => [...current, surface].slice(-SURFACE_HISTORY_LIMIT));
    setSurfaceForwardStack([]);
    setSurfaceState(next);
  }

  function goBackSurface() {
    const previous = surfaceBackStack[surfaceBackStack.length - 1];
    if (!previous) return;
    setSurfaceBackStack((current) => current.slice(0, -1));
    setSurfaceForwardStack((current) => [surface, ...current].slice(0, SURFACE_HISTORY_LIMIT));
    setSurfaceState(previous);
  }

  function goForwardSurface() {
    const next = surfaceForwardStack[0];
    if (!next) return;
    setSurfaceForwardStack((current) => current.slice(1));
    setSurfaceBackStack((current) => [...current, surface].slice(-SURFACE_HISTORY_LIMIT));
    setSurfaceState(next);
  }

  function openCommandPalette() {
    setPaletteOpen(true);
  }

  function flashTopbarNotice(text: string) {
    setTopbarNotice(text);
    window.setTimeout(() => {
      setTopbarNotice((current) => (current === text ? "" : current));
    }, 4200);
  }

  async function copyWorkspacePath() {
    setTopbarNotice("正在复制");
    try {
      await copyText(WORKSPACE_PATH);
      flashTopbarNotice("已复制路径");
    } catch (error) {
      flashTopbarNotice("复制失败");
      setOperationError(errorMessage(error));
    }
  }

  function applyRuntimeConfig(nextConfig: RuntimeConfig = draftConfig) {
    const normalized: RuntimeConfig = {
      ...nextConfig,
      apiUrl: normalizeApiUrl(nextConfig.apiUrl),
      maxIterations: clampNumber(nextConfig.maxIterations, 20, 300, 160),
    };
    const apiUrlChanged = normalizeApiUrl(normalized.apiUrl) !== normalizeApiUrl(runtimeConfig.apiUrl);
    setRuntimeConfig(normalized);
    setDraftConfig(normalized);
    saveRuntimeConfig(normalized);
    setOperationError("");
    if (apiUrlChanged) {
      setServer(disconnectedServer);
      setDraftServer((current) => ({ ...current, status: "offline", id: "", name: "未连接" }));
      setMetrics([]);
      setTools([]);
      setWorkflowList([]);
      setAudit([]);
      setMessages([]);
      setTasks([]);
      setTerminal([]);
      setPendingApproval(null);
      setAcceptanceChecklist(null);
      setReleaseInput("");
      setReleaseReport(null);
      setReleaseError("");
      setReleaseDrillOpen(false);
      setReleaseDrillForm(defaultAcceptanceDrillForm("service_restart"));
    }
  }

  async function refreshOverview() {
    if (!apiConfigured) {
      setApiStatus("missing_config");
      setApiError("未配置 SysDialogue Web API URL。");
      setSurface("settings");
      return;
    }
    setApiStatus("loading");
    setOperationError("");
    try {
      const overview = await api.getOverview();
      setTools(overview.tools);
      setWorkflowList(overview.workflows);
      setAudit(overview.audit);
      setMetrics(overview.metrics);
      setApiStatus("ready");
      setApiError("");
    } catch (error) {
      setApiStatus("error");
      setApiError(errorMessage(error));
    }
  }

  function appendTaskEvent(taskId: string, event: Omit<TaskEvent, "id" | "at">) {
    setTasks((current) =>
      current.map((task) =>
        task.id === taskId
          ? {
              ...task,
              events: [
                ...task.events,
                {
                  ...event,
                  id: uid("ev"),
                  at: new Date(),
                },
              ],
            }
          : task,
      ),
    );
  }

  function finishTask(taskId: string, status: TaskRun["status"]) {
    setTasks((current) =>
      current.map((task) =>
        task.id === taskId
          ? {
              ...task,
              status,
              finishedAt: new Date(),
            }
          : task,
      ),
    );
  }

  function createTask(title: string, source: TaskRun["source"], status: TaskRun["status"] = "running") {
    const task: TaskRun = {
      id: uid("task"),
      title,
      source,
      status,
      startedAt: new Date(),
      events: [
        {
          id: uid("ev"),
          stage: "task_started",
          message: title,
          tone: status === "waiting_approval" ? "warning" : "info",
          at: new Date(),
        },
      ],
    };
    setTasks((current) => [task, ...current].slice(0, 18));
    return task.id;
  }

  function applyExecutionResponse(taskId: string, response: TaskExecutionResponse, requestTitle: string) {
    const hasBackendEvidence = Boolean(
      response.task ||
      response.events?.length ||
      response.messages?.length ||
      response.reply ||
      response.audit?.length ||
      response.approval,
    );
    if (!hasBackendEvidence) {
      appendTaskEvent(taskId, {
        stage: "api_response",
        message: `真实 API 对「${requestTitle}」返回空结果，前端不会伪造完成状态。`,
        tone: "danger",
      });
      finishTask(taskId, "failed");
      setOperationError("真实 API 返回空结果，无法证明任务已经执行。");
      return;
    }
    if (response.task) {
      setTasks((current) => [response.task as TaskRun, ...current.filter((task) => task.id !== taskId)].slice(0, 18));
    } else {
      for (const event of response.events ?? []) {
        appendTaskEvent(taskId, event);
      }
      finishTask(taskId, response.approval ? "waiting_approval" : "completed");
    }
    if (response.messages?.length) {
      setMessages((current) => [...current, ...response.messages!]);
    } else if (response.reply) {
      setMessages((current) => [
        ...current,
        {
          id: uid("msg"),
          role: "assistant",
          text: response.reply!,
          at: new Date(),
          taskId,
        },
      ]);
    }
    if (response.audit?.length) {
      setAudit((current) => [...response.audit!, ...current].slice(0, 80));
    }
    if (response.approval) {
      setPendingApproval(response.approval);
      return;
    }
  }

  function requireApiAndTarget() {
    if (!apiReady) {
      setOperationError("请先配置并启动真实 SysDialogue Web API。");
      setSurface("settings");
      return false;
    }
    if (server.status !== "online" || !server.id) {
      setOperationError("请先通过服务器页连接真实 SSH 或本地执行目标。");
      setSurface("servers");
      return false;
    }
    setOperationError("");
    return true;
  }

  function handlePromptSubmit(event: FormEvent) {
    event.preventDefault();
    const text = prompt.trim();
    if (!text) return;
    if (!requireApiAndTarget()) return;
    setPrompt("");
    setMessages((current) => [
      ...current,
      { id: uid("msg"), role: "user", text, at: new Date() },
    ]);
    const taskId = createTask(text, "agent");
    setRunning(true);
    api.runTask(server.id, text)
      .then((response) => applyExecutionResponse(taskId, response, text))
      .catch((error) => {
        finishTask(taskId, "failed");
        setOperationError(errorMessage(error));
        setMessages((current) => [
          ...current,
          {
            id: uid("msg"),
            role: "system",
            text: errorMessage(error),
            at: new Date(),
            taskId,
          },
        ]);
      })
      .finally(() => setRunning(false));
  }

  async function resolveApproval(approved: boolean) {
    if (!pendingApproval) return;
    const { id, taskId } = pendingApproval;
    setPendingApproval(null);
    setRunning(true);
    try {
      const response = await api.resolveApproval(id, approved);
      applyExecutionResponse(taskId, response, approved ? "审批通过" : "审批拒绝");
      if (!approved) {
        finishTask(taskId, "cancelled");
      }
    } catch (error) {
      setOperationError(errorMessage(error));
      finishTask(taskId, "failed");
    } finally {
      setRunning(false);
    }
  }

  async function connectServer(event: FormEvent) {
    event.preventDefault();
    setConnectionError("");
    if (!apiReady) {
      setConnectionError("请先配置并启动真实 SysDialogue Web API。");
      return;
    }
    const connection = connectionRequestFromDraft(draftServer, runtimeConfig.safetyProfile);
    setServer((current) => ({ ...current, ...connection, status: "connecting" }));
    try {
      const connected = await api.connectServer(connection);
      setServer(connected);
      setDraftServer(connected);
      setRecentConnections((current) => rememberRecentConnection(current, connected));
      setTerminal([]);
      const overview = await api.getOverview();
      setTools(overview.tools);
      setWorkflowList(overview.workflows);
      setMetrics(overview.metrics);
      setAudit(overview.audit);
    } catch (error) {
      setServer((current) => ({ ...current, status: "offline" }));
      setConnectionError(errorMessage(error));
    }
  }

  async function handleTerminalSubmit(event: FormEvent) {
    event.preventDefault();
    const text = commandText.trim();
    if (!text) return;
    if (!requireApiAndTarget()) return;
    setCommandText("");
    const inputLine: TerminalLine = { id: uid("term"), kind: "input", text, at: new Date() };
    setTerminal((current) => [...current, inputLine]);
    try {
      const result = await api.runCommand(server.id, text);
      setTerminal((current) => [
        ...current,
        ...result.lines.map<TerminalLine>((line) => ({
          id: uid("term"),
          kind: "output",
          text: line,
          at: new Date(),
        })),
      ]);
      if (result.audit?.length) {
        setAudit((current) => [...result.audit!, ...current].slice(0, 80));
      }
    } catch (error) {
      setTerminal((current) => [
        ...current,
        { id: uid("term"), kind: "error", text: errorMessage(error), at: new Date() },
      ]);
    }
  }

  function openToolRunner(tool: ToolCapability) {
    if (!requireApiAndTarget()) return;
    prepareRunTarget({ kind: "tool", item: tool });
    setRunArgsError("");
    setRunTarget({ kind: "tool", item: tool });
  }

  function openWorkflowRunner(workflow: WorkflowDefinition) {
    if (!requireApiAndTarget()) return;
    prepareRunTarget({ kind: "workflow", item: workflow });
    setRunArgsError("");
    setRunTarget({ kind: "workflow", item: workflow });
  }

  function prepareRunTarget(target: RunTarget) {
    const fields = inputFieldsForTarget(target);
    const defaults = defaultArgsForFields(fields);
    setRunFormValues(argsToFormValues(fields, defaults));
    setRunArgsText(formatJson(defaults));
    setRunArgsMode("form");
  }

  async function submitRunTarget() {
    if (!runTarget) return;
    let args: Record<string, unknown>;
    try {
      args = runArgsMode === "form"
        ? argsFromFormValues(inputFieldsForTarget(runTarget), runFormValues, true)
        : parseArgsObject(runArgsText);
    } catch (error) {
      setRunArgsError(errorMessage(error));
      return;
    }
    setRunTarget(null);
    if (runTarget.kind === "tool") {
      await executeTool(runTarget.item, args);
    } else {
      await executeWorkflow(runTarget.item, args);
    }
  }

  function resetRunArgs() {
    if (!runTarget) return;
    prepareRunTarget(runTarget);
    setRunArgsError("");
  }

  function updateRunField(field: InputFieldDefinition, value: string) {
    if (!runTarget) return;
    const nextValues = { ...runFormValues, [field.name]: value };
    const fields = inputFieldsForTarget(runTarget);
    setRunFormValues(nextValues);
    setRunArgsText(formatJson(argsFromFormValues(fields, nextValues, false)));
    setRunArgsError("");
  }

  function updateRunJson(value: string) {
    setRunArgsText(value);
    setRunArgsError("");
    if (!runTarget) return;
    try {
      const parsed = parseArgsObject(value);
      setRunFormValues(argsToFormValues(inputFieldsForTarget(runTarget), parsed));
    } catch {
      // Keep the user's JSON draft intact while they are still editing.
    }
  }

  async function executeTool(tool: ToolCapability, args: Record<string, unknown>) {
    if (!requireApiAndTarget()) return;
    const taskId = createTask(`工具 ${tool.name}`, "tool");
    setSurface("workbench");
    setRunning(true);
    try {
      const response = await api.runTool(server.id, tool, args);
      applyExecutionResponse(taskId, response, `工具 ${tool.name}`);
    } catch (error) {
      setOperationError(errorMessage(error));
      finishTask(taskId, "failed");
    } finally {
      setRunning(false);
    }
  }

  async function executeWorkflow(workflow: WorkflowDefinition, args: Record<string, unknown>) {
    if (!requireApiAndTarget()) return;
    const taskId = createTask(`工作流 ${workflow.label}`, "workflow");
    setSurface("workbench");
    setRunning(true);
    try {
      const response = await api.runWorkflow(server.id, workflow, args);
      applyExecutionResponse(taskId, response, `工作流 ${workflow.label}`);
    } catch (error) {
      setOperationError(errorMessage(error));
      finishTask(taskId, "failed");
    } finally {
      setRunning(false);
    }
  }

  async function exportAudit(format: "jsonl" | "replay") {
    if (!apiReady) {
      setOperationError("请先配置并启动真实 SysDialogue Web API。");
      return;
    }
    const artifact = await api.exportAudit(format);
    const blob = new Blob([artifact.content], {
      type: format === "jsonl" ? "application/jsonl" : "application/zip",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = artifact.fileName;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function loadAcceptanceChecklist() {
    if (!apiReady) {
      setReleaseError("请先配置并启动真实 SysDialogue Web API。");
      setSurface("settings");
      return;
    }
    setReleaseLoading(true);
    setReleaseError("");
    try {
      const payload = await api.getAcceptanceChecklist(server.id || "");
      setAcceptanceChecklist(payload);
      setReleaseInput(payload.text);
      setReleaseReport(null);
    } catch (error) {
      setReleaseError(errorMessage(error));
    } finally {
      setReleaseLoading(false);
    }
  }

  async function buildReleaseReport() {
    if (!apiReady) {
      setReleaseError("请先配置并启动真实 SysDialogue Web API。");
      setSurface("settings");
      return;
    }
    setReleaseLoading(true);
    setReleaseError("");
    try {
      const response = await api.buildReleaseReadiness(releaseInput, "web-console");
      setReleaseReport(response);
    } catch (error) {
      setReleaseError(errorMessage(error));
    } finally {
      setReleaseLoading(false);
    }
  }

  async function runAcceptanceRunner(mode: "safe-preflight" | "model-check" | "conversation-check" | "ui-review" | "read-only-collect" | "recovery-drill" = "safe-preflight") {
    if (!apiReady) {
      setReleaseError("请先配置并启动真实 SysDialogue Web API。");
      setSurface("settings");
      return;
    }
    setReleaseLoading(true);
    setReleaseError("");
    try {
      const response = await api.getAcceptanceRunner(server.id || "", mode);
      setAcceptanceChecklist({
        text: response.artifact,
        target: response.target,
        connected: response.connected,
      });
      setReleaseInput(response.artifact);
      setReleaseReport({ report: response.report, readiness: response.readiness });
    } catch (error) {
      setReleaseError(errorMessage(error));
    } finally {
      setReleaseLoading(false);
    }
  }

  function setAcceptanceDrillWorkflow(workflowName: AcceptanceDrillWorkflow) {
    setReleaseDrillForm((current) => ({
      ...defaultAcceptanceDrillForm(workflowName),
      approvalPhrase: current.approvalPhrase,
      impact: current.impact,
      rollback: current.rollback,
      verification: current.verification,
      disposableTarget: current.disposableTarget,
    }));
  }

  async function runAcceptanceMutationDrill() {
    if (!apiReady) {
      setReleaseError("Please configure and start the real SysDialogue Web API first.");
      setSurface("settings");
      return;
    }
    if (server.status !== "online") {
      setReleaseError("A07 mutation drill requires a connected target.");
      return;
    }
    let args: Record<string, unknown>;
    try {
      const parsed = JSON.parse(releaseDrillForm.argsText || "{}") as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("args must be a JSON object");
      args = parsed as Record<string, unknown>;
    } catch (error) {
      setReleaseError(`Invalid A07 args JSON: ${errorMessage(error)}`);
      return;
    }
    setReleaseLoading(true);
    setReleaseError("");
    try {
      const response = await api.runAcceptanceMutationDrill({
        serverId: server.id || "",
        workflowName: releaseDrillForm.workflowName,
        args,
        approvalPhrase: releaseDrillForm.approvalPhrase,
        impact: releaseDrillForm.impact,
        rollback: releaseDrillForm.rollback,
        verification: releaseDrillForm.verification,
        disposableTarget: releaseDrillForm.disposableTarget,
      });
      setAcceptanceChecklist({
        text: response.artifact,
        target: response.target,
        connected: response.connected,
      });
      setReleaseInput(response.artifact);
      setReleaseReport({ report: response.report, readiness: response.readiness });
      setReleaseDrillOpen(false);
    } catch (error) {
      setReleaseError(errorMessage(error));
    } finally {
      setReleaseLoading(false);
    }
  }

  async function exportAcceptanceBundle() {
    if (!apiReady) {
      setReleaseError("请先配置并启动真实 SysDialogue Web API。");
      setSurface("settings");
      return;
    }
    setReleaseLoading(true);
    setReleaseError("");
    try {
      const bundle = await api.buildAcceptanceBundle(releaseInput, "web-console", server.id || "");
      setReleaseReport({ report: bundle.report, readiness: bundle.readiness });
      const blob = new Blob([bundle.content], { type: "application/zip" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = bundle.fileName;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setReleaseError(errorMessage(error));
    } finally {
      setReleaseLoading(false);
    }
  }

  function selectPaletteItem(kind: "surface" | "tool" | "workflow", value: string) {
    setPaletteOpen(false);
    setPaletteQuery("");
    if (kind === "surface") {
      setSurface(value as Surface);
      return;
    }
    if (kind === "tool") {
      const tool = tools.find((item) => item.name === value);
      if (tool) openToolRunner(tool);
      return;
    }
    const workflow = workflowList.find((item) => item.name === value);
    if (workflow) openWorkflowRunner(workflow);
  }

  function clearTerminal() {
    setTerminal([]);
    setTerminalNotice("");
  }

  function startNewTask() {
    setMessages([]);
    setTasks([]);
    setPrompt("");
    setOperationError("");
    setPendingApproval(null);
    setSurface("workbench");
  }

  function flashTerminalNotice(text: string) {
    setTerminalNotice(text);
    window.setTimeout(() => {
      setTerminalNotice((current) => (current === text ? "" : current));
    }, 1800);
  }

  async function copyTerminalTranscript() {
    if (!terminalTranscript) return;
    try {
      await copyText(terminalTranscript);
      flashTerminalNotice("已复制终端记录");
    } catch (error) {
      setOperationError(errorMessage(error));
    }
  }

  async function copyLatestTerminalOutput() {
    if (!terminalOutput) return;
    try {
      await copyText(terminalOutput);
      flashTerminalNotice("已复制最近输出");
    } catch (error) {
      setOperationError(errorMessage(error));
    }
  }

  function reuseTerminalCommand(command: string) {
    setCommandText(command);
    flashTerminalNotice("已填入命令");
  }

  function applyRecentConnection(connection: RecentConnection) {
    setDraftServer({
      ...disconnectedServer,
      ...connection,
      password: "",
      sudoPassword: "",
      fingerprint: "",
      latencyMs: 0,
      distro: "",
      kernel: "",
      status: "offline",
      lastSeen: new Date(0),
    });
  }

  function removeRecentConnection(id: string) {
    setRecentConnections((current) => {
      const next = current.filter((item) => item.id !== id);
      saveRecentConnections(next);
      return next;
    });
  }

  return (
    <TooltipProvider delayDuration={180}>
      <div ref={rootRef} className="min-h-screen text-neutral-950">
        <header className="flex h-[54px] items-center gap-2 overflow-hidden border-b border-neutral-200/80 bg-white/86 px-3 backdrop-blur-xl md:gap-3">
          <div className="hidden items-center gap-2 md:flex">
            <Button variant="ghost" size="icon" aria-label="工作区" onClick={() => setSurface("workbench")}>
              <LayoutGrid />
            </Button>
            <Button variant="subtle" size="icon" aria-label="新任务" onClick={startNewTask}>
              <FileCode2 />
            </Button>
            <Button disabled={!canGoBack} variant="ghost" size="icon" aria-label="后退" onClick={goBackSurface} type="button">
              <ChevronRight className="rotate-180" />
            </Button>
            <Button disabled={!canGoForward} variant="ghost" size="icon" aria-label="前进" onClick={goForwardSurface} type="button">
              <ChevronRight />
            </Button>
          </div>

          <button
            className="mx-auto flex h-9 min-w-0 flex-1 items-center justify-between rounded-lg border border-neutral-200 bg-white px-3 text-left text-sm text-neutral-500 shadow-sm transition hover:border-neutral-300 md:w-[min(42vw,420px)] md:flex-none"
            onClick={openCommandPalette}
            type="button"
          >
            <span className="flex min-w-0 items-center gap-2">
              <Search className="size-4 shrink-0" />
              <span className="truncate">搜索 SysDialogue</span>
            </span>
            <span className="hidden text-xs text-neutral-400 sm:block">Ctrl+K</span>
          </button>

          <div className="flex shrink-0 items-center gap-1 md:gap-2">
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className="hidden h-8 min-w-24 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md border border-neutral-200 bg-white px-3 text-sm font-medium text-neutral-900 outline-none transition-colors hover:bg-neutral-100 focus-visible:ring-2 focus-visible:ring-cyan-500/45 md:inline-flex [&_svg]:size-4"
                  onClick={copyWorkspacePath}
                  onPointerDown={() => setTopbarNotice("正在复制")}
                  type="button"
                >
                  <Copy className="size-4" />
                  {topbarNotice || "复制路径"}
                </button>
              </TooltipTrigger>
              <TooltipContent>复制当前工作区路径</TooltipContent>
            </Tooltip>
            <ServerPopover
              server={server}
              metrics={metrics}
              setSurface={setSurface}
              toolCount={tools.length}
              workflowCount={workflowList.length}
            />
            <Button variant="ghost" size="icon" aria-label="命令" onClick={openCommandPalette} type="button">
              <Command />
            </Button>
            <Button variant="ghost" size="icon" aria-label="刷新 API 状态" onClick={() => void refreshOverview()}>
              <RefreshCcw className={apiStatus === "loading" ? "animate-spin" : ""} />
            </Button>
          </div>
        </header>

        <div className="app-grid">
          <aside className="side-rail flex flex-col items-center border-r border-neutral-200/80 bg-white/72 py-4">
            <button
              className="mb-7 flex size-12 items-center justify-center rounded-lg border-2 border-neutral-950 bg-white text-lg font-semibold text-cyan-700 shadow-sm"
              type="button"
              onClick={() => setSurface("workbench")}
            >
              S
            </button>
            <nav className="flex flex-1 flex-col items-center gap-3">
              {navItems.map((item) => (
                <Tooltip key={item.id}>
                  <TooltipTrigger asChild>
                    <button
                      aria-label={item.label}
                      className={cn(
                        "rail-button dock-item flex size-11 items-center justify-center rounded-lg text-neutral-500 transition hover:bg-neutral-100 hover:text-neutral-950",
                        surface === item.id && "bg-neutral-100 text-neutral-950",
                      )}
                      data-active={surface === item.id}
                      onClick={() => setSurface(item.id)}
                      type="button"
                    >
                      <item.icon className="size-5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="right">{item.label}</TooltipContent>
                </Tooltip>
              ))}
            </nav>
            <div className="flex flex-col gap-3">
              <RailNotificationPopover
                audit={audit}
                pendingApproval={pendingApproval}
                setSurface={setSurface}
                tasks={tasks}
              />
              <RailHistoryPopover audit={audit} setSurface={setSurface} tasks={tasks} />
            </div>
          </aside>

          <main className="main-scroll">
            <ScrollArea className="h-full">
              <div className="content-shell mx-auto w-full max-w-[1180px] px-5 py-5">
                <ApiConnectionBanner
                  applyRuntimeConfig={applyRuntimeConfig}
                  apiError={apiError}
                  apiStatus={apiStatus}
                  draftConfig={draftConfig}
                  operationError={operationError}
                  setDraftConfig={setDraftConfig}
                  setSurface={setSurface}
                  readiness={readiness}
                  server={server}
                />
                {surface === "workbench" && (
                  <WorkbenchView
                    apiReady={apiReady}
                    activeTask={activeTask}
                    canCopyTerminalOutput={Boolean(terminalOutput)}
                    clearTerminal={clearTerminal}
                    commandText={commandText}
                    copyLatestTerminalOutput={copyLatestTerminalOutput}
                    copyTerminalTranscript={copyTerminalTranscript}
                    handlePromptSubmit={handlePromptSubmit}
                    handleTerminalSubmit={handleTerminalSubmit}
                    hasOpenTask={hasOpenTask}
                    messages={messages}
                    operationError={operationError}
                    pendingApproval={pendingApproval}
                    prompt={prompt}
                    reuseTerminalCommand={reuseTerminalCommand}
                    running={running}
                    server={server}
                    setCommandText={setCommandText}
                    setPrompt={setPrompt}
                    setSurface={setSurface}
                    tasks={tasks}
                    terminal={terminal}
                    terminalCommands={terminalCommands}
                    terminalNotice={terminalNotice}
                  />
                )}
                {surface === "servers" && (
                  <ServersView
                    apiReady={apiReady}
                    applyRuntimeConfig={applyRuntimeConfig}
                    connectionError={connectionError}
                    connectServer={connectServer}
                    draftConfig={draftConfig}
                    draftServer={draftServer}
                    metrics={metrics}
                    recentConnections={recentConnections}
                    removeRecentConnection={removeRecentConnection}
                    runtimeConfig={runtimeConfig}
                    server={server}
                    applyRecentConnection={applyRecentConnection}
                    setDraftConfig={setDraftConfig}
                    setDraftServer={setDraftServer}
                  />
                )}
                {surface === "tools" && (
                  <ToolsView
                    apiReady={apiReady}
                    activeToolCount={activeToolCount}
                    groupedTools={groupedTools}
                    query={toolQuery}
                    runTool={openToolRunner}
                    setQuery={setToolQuery}
                    toolCount={tools.length}
                  />
                )}
                {surface === "workflows" && (
                  <WorkflowsView
                    apiReady={apiReady}
                    query={workflowQuery}
                    runWorkflow={openWorkflowRunner}
                    setQuery={setWorkflowQuery}
                    totalWorkflows={workflowList.length}
                    workflows={filteredWorkflows}
                  />
                )}
                {surface === "audit" && (
                  <AuditView audit={audit} exportAudit={exportAudit} />
                )}
                {surface === "release" && (
                  <ReleaseView
                    acceptanceChecklist={acceptanceChecklist}
                    apiReady={apiReady}
                    exportAudit={exportAudit}
                    exportAcceptanceBundle={exportAcceptanceBundle}
                    buildReleaseReport={buildReleaseReport}
                    loadAcceptanceChecklist={loadAcceptanceChecklist}
                    openMutationDrill={() => setReleaseDrillOpen(true)}
                    runAcceptanceRunner={runAcceptanceRunner}
                    releaseError={releaseError}
                    releaseInput={releaseInput}
                    releaseLoading={releaseLoading}
                    releaseReport={releaseReport}
                    server={server}
                    setReleaseInput={setReleaseInput}
                    setSurface={setSurface}
                  />
                )}
                {surface === "settings" && (
                  <SettingsView
                    applyRuntimeConfig={applyRuntimeConfig}
                    draftConfig={draftConfig}
                    setDraftConfig={setDraftConfig}
                    workflowCount={workflowList.length}
                    toolCount={tools.length}
                  />
                )}
              </div>
            </ScrollArea>
          </main>

          <aside className="right-dock border-l border-neutral-200/80 bg-white/74">
            <RightDock
              activeTask={activeTask}
              audit={audit}
              clock={clock}
              metrics={metrics}
              pendingApproval={pendingApproval}
              readiness={readiness}
              server={server}
              setSurface={setSurface}
              tasks={tasks}
            />
          </aside>
        </div>

        <MobileNav server={server} setSurface={setSurface} surface={surface} />
        <ApprovalDialog pendingApproval={pendingApproval} resolveApproval={resolveApproval} />
        <RunTargetDialog
          argsError={runArgsError}
          argsMode={runArgsMode}
          argsText={runArgsText}
          formValues={runFormValues}
          onOpenChange={(open) => {
            if (!open) setRunTarget(null);
          }}
          onFieldChange={updateRunField}
          onJsonChange={updateRunJson}
          onReset={resetRunArgs}
          onSetArgsMode={setRunArgsMode}
          onSubmit={submitRunTarget}
          open={Boolean(runTarget)}
          running={running}
          target={runTarget}
        />
        <AcceptanceMutationDrillDialog
          form={releaseDrillForm}
          loading={releaseLoading}
          onOpenChange={setReleaseDrillOpen}
          onSubmit={runAcceptanceMutationDrill}
          onWorkflowChange={setAcceptanceDrillWorkflow}
          open={releaseDrillOpen}
          server={server}
          setForm={setReleaseDrillForm}
        />
        <CommandPalette
          items={paletteItems}
          open={paletteOpen}
          query={paletteQuery}
          selectPaletteItem={selectPaletteItem}
          setOpen={setPaletteOpen}
          setQuery={setPaletteQuery}
        />
      </div>
    </TooltipProvider>
  );
}

function ApiConnectionBanner(props: {
  applyRuntimeConfig: (config?: RuntimeConfig) => void;
  apiError: string;
  apiStatus: ApiStatus;
  draftConfig: RuntimeConfig;
  operationError: string;
  readiness: ReadinessItem[];
  server: ServerConnection;
  setDraftConfig: React.Dispatch<React.SetStateAction<RuntimeConfig>>;
  setSurface: (surface: Surface) => void;
}) {
  const { applyRuntimeConfig, apiError, apiStatus, draftConfig, operationError, readiness, server, setDraftConfig, setSurface } = props;
  if (apiStatus === "ready" && server.status === "online" && !operationError) return null;
  const showApiEditor = apiStatus !== "ready";

  const statusText =
    apiStatus === "missing_config"
      ? "未配置真实 API"
      : apiStatus === "loading"
        ? "正在连接 API"
        : apiStatus === "error"
          ? "API 不可用"
          : "未连接目标";
  const detail =
    apiStatus === "missing_config"
      ? "请在这里输入真实 SysDialogue Web API 地址并应用；无需改文件或重启前端。"
      : apiStatus === "error"
        ? apiError
        : apiStatus === "loading"
          ? `正在读取 ${normalizeApiUrl(draftConfig.apiUrl)}。`
          : "请进入服务器页建立真实 SSH 或本地执行目标连接。";

  return (
    <div className="gsap-in mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-950 shadow-sm md:p-4">
      <div className="flex flex-col gap-3 md:gap-4">
        <ReadinessStrip items={readiness} />
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="flex gap-3">
            <ShieldAlert className="mt-0.5 size-5 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-semibold">{statusText}</div>
              <div className="mt-1 text-xs leading-5 text-amber-800 md:text-sm">{operationError || detail}</div>
            </div>
          </div>
          <div className={cn("min-w-0 flex-1 flex-col gap-2 md:flex md:max-w-xl md:flex-row", showApiEditor ? "flex" : "hidden")}>
            <Input
              aria-label="SysDialogue API URL"
              className="bg-white"
              onChange={(event) => setDraftConfig((current) => ({ ...current, apiUrl: event.target.value }))}
              placeholder="http://127.0.0.1:8000/api"
              value={draftConfig.apiUrl}
            />
            <Button variant="secondary" size="sm" onClick={() => applyRuntimeConfig()}>
              <RefreshCcw />
              应用热更新
            </Button>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="secondary" size="sm" onClick={() => setSurface("settings")}>更多配置</Button>
            <Button variant="warning" size="sm" onClick={() => setSurface("servers")}>连接目标</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ReadinessStrip({ items }: { items: ReadinessItem[] }) {
  return (
    <div className="grid grid-cols-4 gap-1.5 md:gap-2">
      {items.map((item, index) => (
        <div
          className={cn(
            "min-w-0 rounded-lg border bg-white/70 px-2 py-2 md:px-3",
            item.state === "done" && "border-emerald-200",
            item.state === "current" && "border-cyan-300 bg-cyan-50/80",
            item.state === "blocked" && "border-amber-200",
          )}
          key={item.label}
        >
          <div className="flex min-w-0 flex-col items-center gap-1 text-center text-[10px] font-semibold leading-3 md:flex-row md:gap-2 md:text-left md:text-xs">
            <span
              className={cn(
                "flex size-5 shrink-0 items-center justify-center rounded-full border text-[11px]",
                item.state === "done" && "border-emerald-500 bg-emerald-500 text-white",
                item.state === "current" && "border-cyan-600 bg-cyan-600 text-white",
                item.state === "blocked" && "border-amber-300 bg-white text-amber-800",
              )}
            >
              {item.state === "done" ? <Check className="size-3" /> : index + 1}
            </span>
            <span className="max-w-full truncate">{item.label}</span>
          </div>
          <div className="mt-1 hidden truncate text-xs text-neutral-500 md:block">{item.detail}</div>
        </div>
      ))}
    </div>
  );
}

function MobileNav(props: {
  server: ServerConnection;
  setSurface: (surface: Surface) => void;
  surface: Surface;
}) {
  const { server, setSurface, surface } = props;
  return (
    <nav
      aria-label="移动端主导航"
      className="mobile-nav fixed inset-x-0 bottom-0 z-40 border-t border-neutral-200/90 bg-white/95 px-3 pb-[calc(env(safe-area-inset-bottom)+10px)] pt-2 shadow-[0_-14px_34px_rgba(23,23,23,0.08)] backdrop-blur-xl md:hidden"
    >
      <div className="mb-2 flex items-center justify-between gap-3 px-1 text-[11px] text-neutral-500">
        <span className="truncate">{server.status === "online" ? `${server.name} · online` : "未连接目标"}</span>
        <span className="shrink-0 font-mono">{server.mode === "ssh" ? compactHost(server.host, server.port) : "local"}</span>
      </div>
      <div className="grid grid-cols-7 gap-1">
        {navItems.map((item) => {
          const active = surface === item.id;
          return (
            <button
              aria-current={active ? "page" : undefined}
              aria-label={item.label}
              className={cn(
                "flex min-w-0 flex-col items-center justify-center gap-1 rounded-lg border px-1 py-2 text-[10px] transition",
                active
                  ? "border-neutral-950 bg-neutral-950 text-white"
                  : "border-transparent text-neutral-500 hover:border-neutral-200 hover:bg-neutral-50 hover:text-neutral-950",
              )}
              key={item.id}
              onClick={() => setSurface(item.id)}
              type="button"
            >
              <item.icon className="size-4" />
              <span className="max-w-full truncate">{mobileNavLabel(item)}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

function mobileNavLabel(item: NavigationItem) {
  return item.id === "release" ? "发布" : item.label;
}

function RailNotificationPopover(props: {
  audit: AuditRecord[];
  pendingApproval: ApprovalRequest | null;
  setSurface: (surface: Surface) => void;
  tasks: TaskRun[];
}) {
  const { audit, pendingApproval, setSurface, tasks } = props;
  const [open, setOpen] = useState(false);
  const openTasks = tasks.filter(isOpenTask).slice(0, 3);
  const riskyAudit = audit.filter((record) => record.risk === "WARN-HIGH" || record.risk === "HARD-BLOCK").slice(0, 3);
  const count = Number(Boolean(pendingApproval)) + openTasks.length + riskyAudit.length;
  function navigate(surface: Surface) {
    setSurface(surface);
    setOpen(false);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <button aria-label="通知" className="relative flex size-10 items-center justify-center rounded-lg text-neutral-500 hover:bg-neutral-100 hover:text-neutral-950" type="button">
              <BellRing className="size-5" />
              {count > 0 && (
                <span className="absolute right-1.5 top-1.5 flex min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-4 text-neutral-950">
                  {count}
                </span>
              )}
            </button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="right">通知</TooltipContent>
      </Tooltip>
      <PopoverContent side="right" align="end" className="w-80 p-3">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">通知</div>
            <div className="text-xs text-neutral-500">{count ? `${count} 条需要关注` : "当前没有需要处理的事项"}</div>
          </div>
          <Badge variant={count ? "warning" : "success"}>{count ? "attention" : "clear"}</Badge>
        </div>

        <div className="grid gap-2">
          {pendingApproval && (
            <button
              className="rounded-md border border-amber-200 bg-amber-50 p-3 text-left text-sm text-amber-950 hover:border-amber-300"
              onClick={() => navigate("workbench")}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold">待审批</span>
                <RiskBadge risk={pendingApproval.risk} />
              </div>
              <div className="mt-1 line-clamp-2 text-xs leading-5">{pendingApproval.tool} · {pendingApproval.reason}</div>
            </button>
          )}

          {openTasks.map((task) => (
            <button
              className="rounded-md border border-cyan-200 bg-cyan-50 p-3 text-left text-sm text-cyan-950 hover:border-cyan-300"
              key={task.id}
              onClick={() => navigate("workbench")}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-semibold">{task.title}</span>
                <StatusTaskBadge status={task.status} />
              </div>
              <div className="mt-1 text-xs text-cyan-800">{task.source} · {formatRelativeTime(task.startedAt)}</div>
            </button>
          ))}

          {riskyAudit.map((record, index) => (
            <button
              className="rounded-md border border-neutral-200 bg-neutral-50 p-3 text-left text-sm hover:border-neutral-300"
              key={auditRecordKey(record, index)}
              onClick={() => navigate("audit")}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-semibold">{record.type}</span>
                <RiskBadge risk={record.risk} />
              </div>
              <div className="mt-1 line-clamp-2 text-xs leading-5 text-neutral-500">{record.result || record.target}</div>
            </button>
          ))}

          {count === 0 && (
            <EmptyPanel title="没有新通知" detail="审批、运行中任务和高风险审计会在这里集中显示。" />
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function RailHistoryPopover(props: {
  audit: AuditRecord[];
  setSurface: (surface: Surface) => void;
  tasks: TaskRun[];
}) {
  const { audit, setSurface, tasks } = props;
  const [open, setOpen] = useState(false);
  const recentTasks = tasks.slice(0, 5);
  const recentAudit = audit.slice(0, 3);
  function navigate(surface: Surface) {
    setSurface(surface);
    setOpen(false);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <button aria-label="历史" className="flex size-10 items-center justify-center rounded-lg text-neutral-500 hover:bg-neutral-100 hover:text-neutral-950" type="button">
              <History className="size-5" />
            </button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="right">历史</TooltipContent>
      </Tooltip>
      <PopoverContent side="right" align="end" className="w-80 p-3">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">历史</div>
            <div className="text-xs text-neutral-500">{recentTasks.length} 个任务 · {recentAudit.length} 条审计</div>
          </div>
          <Button variant="secondary" size="sm" onClick={() => navigate("audit")} type="button">
            <ClipboardList />
            审计
          </Button>
        </div>

        <div className="grid gap-3">
          <div>
            <div className="mb-2 text-xs font-semibold uppercase text-neutral-500">Tasks</div>
            <div className="grid gap-2">
              {recentTasks.length === 0 && <EmptyPanel title="无任务历史" detail="运行自然语言任务、工具或工作流后会在这里出现。" />}
              {recentTasks.map((task) => (
                <button
                  className="rounded-md border border-neutral-200 bg-white p-2 text-left hover:border-neutral-300 hover:bg-neutral-50"
                  key={task.id}
                  onClick={() => navigate("workbench")}
                  type="button"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">{task.title}</span>
                    <StatusTaskBadge status={task.status} />
                  </div>
                  <div className="mt-1 text-xs text-neutral-500">{task.source} · {formatRelativeTime(task.startedAt)}</div>
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-2 text-xs font-semibold uppercase text-neutral-500">Audit</div>
            <div className="grid gap-2">
              {recentAudit.length === 0 && <EmptyPanel title="无审计记录" detail="审计必须来自真实后端，前端不会补假记录。" />}
              {recentAudit.map((record, index) => (
                <button
                  className="rounded-md border border-neutral-200 bg-white p-2 text-left hover:border-neutral-300 hover:bg-neutral-50"
                  key={auditRecordKey(record, index)}
                  onClick={() => navigate("audit")}
                  type="button"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">{record.type}</span>
                    <RiskBadge risk={record.risk} />
                  </div>
                  <div className="mt-1 truncate text-xs text-neutral-500">{record.target || record.result}</div>
                </button>
              ))}
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function WorkbenchView(props: {
  apiReady: boolean;
  activeTask: TaskRun | undefined;
  canCopyTerminalOutput: boolean;
  clearTerminal: () => void;
  commandText: string;
  copyLatestTerminalOutput: () => void;
  copyTerminalTranscript: () => void;
  handlePromptSubmit: (event: FormEvent) => void;
  handleTerminalSubmit: (event: FormEvent) => void;
  hasOpenTask: boolean;
  messages: ChatMessage[];
  operationError: string;
  pendingApproval: ApprovalRequest | null;
  prompt: string;
  reuseTerminalCommand: (command: string) => void;
  running: boolean;
  server: ServerConnection;
  setCommandText: (value: string) => void;
  setPrompt: (value: string) => void;
  setSurface: (surface: Surface) => void;
  tasks: TaskRun[];
  terminal: TerminalLine[];
  terminalCommands: string[];
  terminalNotice: string;
}) {
  const {
    apiReady,
    activeTask,
    canCopyTerminalOutput,
    clearTerminal,
    commandText,
    copyLatestTerminalOutput,
    copyTerminalTranscript,
    handlePromptSubmit,
    handleTerminalSubmit,
    hasOpenTask,
    messages,
    operationError,
    pendingApproval,
    prompt,
    reuseTerminalCommand,
    running,
    server,
    setCommandText,
    setPrompt,
    setSurface,
    tasks,
    terminal,
    terminalCommands,
    terminalNotice,
  } = props;
  const showQuickStart = apiReady && server.status === "online" && messages.length === 0 && !hasOpenTask;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
      <section className="gsap-in flex min-h-[calc(100vh-94px)] flex-col rounded-lg border border-neutral-200 bg-white/82 shadow-sm">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-neutral-950 text-white">
              <Bot className="size-5" />
            </div>
            <div>
              <h1 className="text-base font-semibold">构建任何东西</h1>
              <p className="text-xs text-neutral-500">{WORKSPACE_PATH} · {server.mode === "ssh" ? compactHost(server.host, server.port) : "local"}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={server.status} />
            {running && <Badge variant="info"><Loader2 className="size-3 animate-spin" />运行中</Badge>}
          </div>
        </div>

        <ScrollArea className="min-h-0 flex-1">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6">
            {!apiReady && <WorkbenchEmptyState kind="api" />}
            {apiReady && server.status !== "online" && <WorkbenchEmptyState kind="server" />}
            {operationError && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                {operationError}
              </div>
            )}
            {showQuickStart && (
              <WorkbenchQuickStart
                setCommandText={setCommandText}
                setPrompt={setPrompt}
                setSurface={setSurface}
              />
            )}
            {messages.map((message) => <MessageBubble key={message.id} message={message} />)}
            {activeTask && <TaskTimeline task={activeTask} />}
            {tasks.length > 1 && <TaskHistoryPanel tasks={tasks} activeTaskId={activeTask?.id} />}
          </div>
        </ScrollArea>

        <form className="border-t border-neutral-200 bg-white/90 p-4" onSubmit={handlePromptSubmit}>
          <div className="rounded-lg border border-neutral-300 bg-white shadow-sm focus-within:border-neutral-500">
            <Textarea
              className="min-h-24 border-0 shadow-none focus:ring-0"
              onChange={(event) => setPrompt(event.target.value)}
              placeholder='随便问点什么... "修复失败的测试"'
              disabled={!apiReady || server.status !== "online"}
              value={prompt}
            />
            <div className="flex items-center justify-between border-t border-neutral-100 px-3 py-2">
              <div className="flex items-center gap-2 text-sm text-neutral-500">
                <Button type="button" variant="ghost" size="icon" aria-label="添加上下文">
                  <Plus />
                </Button>
                <span>Build</span>
                <ChevronRight className="size-3 rotate-90" />
                <span>SysDialogue</span>
              </div>
              <Button disabled={!prompt.trim() || Boolean(pendingApproval) || !apiReady || server.status !== "online"} size="icon" type="submit">
                {running ? <Loader2 className="animate-spin" /> : <ArrowUp />}
              </Button>
            </div>
          </div>
        </form>
      </section>

      <section className="gsap-in flex min-h-[520px] flex-col rounded-lg border border-neutral-900 bg-neutral-950 text-white shadow-sm">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div className="flex items-center gap-2">
            <SquareTerminal className="size-4 text-cyan-300" />
            <span className="text-sm font-medium">SSH Control</span>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {terminalNotice && <span className="text-xs text-cyan-200">{terminalNotice}</span>}
            {terminal.length > 0 && (
              <>
                <Button className="text-white/70 hover:bg-white/10 hover:text-white" variant="ghost" size="sm" onClick={copyTerminalTranscript} type="button">
                  <Copy />
                  复制
                </Button>
                <Button className="text-white/70 hover:bg-white/10 hover:text-white" disabled={!canCopyTerminalOutput} variant="ghost" size="sm" onClick={copyLatestTerminalOutput} type="button">
                  <FileText />
                  输出
                </Button>
                <Button className="text-white/70 hover:bg-white/10 hover:text-white" variant="ghost" size="sm" onClick={clearTerminal} type="button">
                  <Trash2 />
                  清空
                </Button>
              </>
            )}
            <Badge variant={server.status === "online" ? "success" : "danger"}>{server.status}</Badge>
          </div>
        </div>
        <ScrollArea className="terminal-grid min-h-0 flex-1">
          <div className="space-y-2 p-4 font-mono text-xs leading-6">
            {terminal.length === 0 && (
              <div className="text-amber-200">
                &gt; {server.status === "online" ? "已连接真实目标 · 终端空闲。" : "等待真实 SysDialogue API 与目标连接；未连接时不会模拟 SSH 输出。"}
              </div>
            )}
            {terminal.map((line) => (
              <div
                key={line.id}
                className={cn(
                  "break-words",
                  line.kind === "input" && "text-cyan-200",
                  line.kind === "output" && "text-neutral-200",
                  line.kind === "error" && "text-rose-300",
                  line.kind === "system" && "text-amber-200",
                )}
              >
                <span className="mr-2 text-white/35">{line.kind === "input" ? "$" : ">"}</span>
                {line.text}
              </div>
            ))}
          </div>
        </ScrollArea>
        <div className="flex flex-wrap gap-2 border-t border-white/10 px-3 py-2">
          {terminalPresets.map((preset) => (
            <button
              className="rounded-md border border-white/10 bg-white/8 px-2 py-1 font-mono text-[11px] text-cyan-100 transition hover:border-cyan-300 disabled:opacity-40"
              disabled={!apiReady || server.status !== "online"}
              key={preset.label}
              onClick={() => setCommandText(preset.command)}
              type="button"
            >
              {preset.label}
            </button>
          ))}
          {terminalCommands.slice(0, 4).map((command) => (
            <button
              className="max-w-full truncate rounded-md border border-cyan-300/20 bg-cyan-300/10 px-2 py-1 font-mono text-[11px] text-cyan-50 transition hover:border-cyan-200 disabled:opacity-40"
              disabled={!apiReady || server.status !== "online"}
              key={command}
              onClick={() => reuseTerminalCommand(command)}
              title={command}
              type="button"
            >
              <RefreshCcw className="mr-1 inline size-3" />
              {command}
            </button>
          ))}
        </div>
        <form className="flex gap-2 border-t border-white/10 p-3" onSubmit={handleTerminalSubmit}>
          <Input
            className="border-white/10 bg-white/8 font-mono text-sm text-white placeholder:text-white/35 focus:border-cyan-300"
            onChange={(event) => setCommandText(event.target.value)}
            placeholder="systemctl status nginx"
            disabled={!apiReady || server.status !== "online"}
            value={commandText}
          />
          <Button disabled={!apiReady || server.status !== "online" || !commandText.trim()} type="submit" variant="secondary" size="icon">
            <Send />
          </Button>
        </form>
      </section>
    </div>
  );
}

function WorkbenchQuickStart(props: {
  setCommandText: (value: string) => void;
  setPrompt: (value: string) => void;
  setSurface: (surface: Surface) => void;
}) {
  const { setCommandText, setPrompt, setSurface } = props;
  return (
    <div className="grid gap-4">
      <div className="rounded-lg border border-cyan-200 bg-cyan-50/70 p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-cyan-950">常用入口</div>
            <div className="text-xs text-cyan-800">目标已连接 · 只读巡检、服务排查、工具和工作流</div>
          </div>
          <Badge variant="success">ready</Badge>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {promptPresets.map((preset) => (
            <button
              className="rounded-lg border border-cyan-200 bg-white p-3 text-left shadow-sm transition hover:border-cyan-400 hover:shadow-md"
              key={preset.label}
              onClick={() => setPrompt(preset.text)}
              type="button"
            >
              <div className="text-sm font-semibold text-neutral-950">{preset.label}</div>
              <div className="mt-1 line-clamp-2 text-xs leading-5 text-neutral-500">{preset.text}</div>
            </button>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={() => setSurface("tools")} type="button">
            <Wrench />
            打开工具
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setSurface("workflows")} type="button">
            <Workflow />
            打开工作流
          </Button>
        </div>
      </div>
      <div className="rounded-lg border border-neutral-200 bg-white p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <SquareTerminal className="size-4 text-cyan-600" />
          终端命令
        </div>
        <div className="flex flex-wrap gap-2">
          {terminalPresets.map((preset) => (
            <button
              className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 font-mono text-xs text-neutral-700 transition hover:border-neutral-400 hover:bg-white"
              key={preset.label}
              onClick={() => setCommandText(preset.command)}
              type="button"
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ServersView(props: {
  apiReady: boolean;
  applyRecentConnection: (connection: RecentConnection) => void;
  applyRuntimeConfig: (config?: RuntimeConfig) => void;
  connectionError: string;
  connectServer: (event: FormEvent) => void;
  draftConfig: RuntimeConfig;
  draftServer: ServerConnection;
  metrics: Metric[];
  recentConnections: RecentConnection[];
  removeRecentConnection: (id: string) => void;
  runtimeConfig: RuntimeConfig;
  server: ServerConnection;
  setDraftConfig: React.Dispatch<React.SetStateAction<RuntimeConfig>>;
  setDraftServer: (server: ServerConnection) => void;
}) {
  const {
    apiReady,
    applyRecentConnection,
    applyRuntimeConfig,
    connectionError,
    connectServer,
    draftConfig,
    draftServer,
    metrics,
    recentConnections,
    removeRecentConnection,
    runtimeConfig,
    server,
    setDraftConfig,
    setDraftServer,
  } = props;
  const connectReason = getConnectDisabledReason(apiReady, draftServer, server.status);
  const canConnect = !connectReason;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,560px)_1fr]">
      <section className="gsap-in rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <div className="mb-5 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">服务器</h2>
            <p className="text-sm text-neutral-500">{server.name} · {compactHost(server.host, server.port)}</p>
          </div>
          <StatusBadge status={server.status} />
        </div>
        {!apiReady && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            可以先编辑 SSH 连接参数；点击连接前需要在顶部或设置页应用真实 API 地址。前端不会模拟 SSH 连接。
          </div>
        )}
        <ConnectionRoutePanel apiReady={apiReady} draftServer={draftServer} runtimeConfig={runtimeConfig} server={server} />
        {recentConnections.length > 0 && (
          <div className="mb-4 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-neutral-800">
              <History className="size-4 text-cyan-600" />
              最近目标
            </div>
            <div className="grid gap-2">
              {recentConnections.map((connection) => (
                <div className="flex items-center gap-2 rounded-md border border-neutral-200 bg-white p-2" key={connection.id}>
                  <button
                    className="min-w-0 flex-1 text-left"
                    onClick={() => applyRecentConnection(connection)}
                    type="button"
                  >
                    <div className="truncate font-mono text-xs text-neutral-950">
                      {connection.user ? `${connection.user}@` : ""}{compactHost(connection.host, connection.port)}
                    </div>
                    <div className="truncate text-xs text-neutral-500">
                      {recentCredentialLabel(connection)} · {formatRelativeTime(new Date(connection.lastUsed))}
                    </div>
                  </button>
                  <Button
                    aria-label="删除最近目标"
                    onClick={() => removeRecentConnection(connection.id)}
                    size="icon"
                    type="button"
                    variant="ghost"
                  >
                    <Trash2 />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        )}
        <form className="grid gap-4" onSubmit={connectServer}>
          <div className="grid gap-2">
            <Label>目标模式</Label>
            <div className="grid grid-cols-2 gap-2 rounded-lg border border-neutral-200 bg-neutral-50 p-1" role="radiogroup" aria-label="目标模式">
              {(["local", "ssh"] as const).map((mode) => {
                const active = draftServer.mode === mode;
                return (
                  <button
                    aria-checked={active}
                    className={cn(
                      "flex h-10 items-center justify-center gap-2 rounded-md border text-sm font-medium transition",
                      active
                        ? "border-neutral-950 bg-neutral-950 text-white shadow-sm"
                        : "border-transparent text-neutral-500 hover:border-neutral-200 hover:bg-white hover:text-neutral-950",
                    )}
                    key={mode}
                    onClick={() => setDraftServer(draftServerForMode(draftServer, mode))}
                    role="radio"
                    type="button"
                  >
                    {mode === "local" ? <Laptop /> : <Server />}
                    {mode === "local" ? "Local" : "SSH"}
                  </button>
                );
              })}
            </div>
          </div>
          {draftServer.mode === "local" ? (
            <LocalTargetSummary server={server} />
          ) : (
            <div className="grid gap-4">
              <div className="grid gap-3 sm:grid-cols-[1fr_120px]">
                <div className="grid gap-2">
                  <Label htmlFor="server-host">Host</Label>
                  <Input
                    id="server-host"
                    onChange={(event) => setDraftServer({ ...draftServer, host: event.target.value })}
                    value={draftServer.host}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="server-port">Port</Label>
                  <Input
                    id="server-port"
                    min={1}
                    onChange={(event) => setDraftServer({ ...draftServer, port: Number(event.target.value) })}
                    type="number"
                    value={draftServer.port}
                  />
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor="server-user">User</Label>
                  <Input
                    id="server-user"
                    onChange={(event) => setDraftServer({ ...draftServer, user: event.target.value })}
                    value={draftServer.user}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="server-key">SSH key</Label>
                  <Input
                    id="server-key"
                    onChange={(event) => setDraftServer({ ...draftServer, keyFile: event.target.value })}
                    placeholder="~/.ssh/id_ed25519"
                    value={draftServer.keyFile}
                  />
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor="server-password">Password</Label>
                  <Input
                    id="server-password"
                    onChange={(event) => setDraftServer({ ...draftServer, password: event.target.value })}
                    type="password"
                    value={draftServer.password ?? ""}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="server-sudo-password">Sudo password</Label>
                  <Input
                    id="server-sudo-password"
                    onChange={(event) => setDraftServer({ ...draftServer, sudoPassword: event.target.value })}
                    type="password"
                    value={draftServer.sudoPassword ?? ""}
                  />
                </div>
              </div>
              <SshCredentialPanel draftServer={draftServer} />
            </div>
          )}
          <div className="flex items-center justify-between rounded-lg border border-neutral-200 p-3">
            <div>
              <div className="text-sm font-medium">Break glass</div>
              <div className="text-xs text-neutral-500">启用后仍保留 HARD-BLOCK。</div>
            </div>
            <Switch
              checked={draftConfig.safetyProfile === "break_glass"}
              onCheckedChange={(checked) => {
                const next = { ...draftConfig, safetyProfile: checked ? "break_glass" as const : "standard" as const };
                setDraftConfig(next);
                applyRuntimeConfig(next);
              }}
            />
          </div>
          {connectionError && <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{connectionError}</div>}
          {connectReason && (
            <div className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
              {connectReason}
            </div>
          )}
          <Button className="w-full" disabled={!canConnect} type="submit">
            {server.status === "connecting" ? <Loader2 className="animate-spin" /> : <PlugZap />}
            {draftServer.mode === "local" ? "连接本机执行器" : "连接 SSH 目标"}
          </Button>
        </form>
      </section>

      <section className="gsap-in rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-lg font-semibold">运行状态</h2>
          <Badge variant="info">{server.latencyMs}ms</Badge>
        </div>
        <div className="grid gap-4">
          {metrics.length === 0 ? (
            <EmptyPanel title="无真实指标" detail="连接真实 SysDialogue API 和目标后，这里才会显示后端返回的运行状态。" />
          ) : (
            metrics.map((metric) => <MetricRow key={metric.label} metric={metric} />)
          )}
        </div>
        <Separator className="my-5" />
        <div className="grid gap-3 text-sm">
          <InfoRow label="Fingerprint" value={server.fingerprint} />
          <InfoRow label="Distro" value={server.distro} />
          <InfoRow label="Kernel" value={server.kernel} />
          <InfoRow label="Safety" value={runtimeConfig.safetyProfile} />
        </div>
      </section>
    </div>
  );
}

function ConnectionRoutePanel(props: {
  apiReady: boolean;
  draftServer: ServerConnection;
  runtimeConfig: RuntimeConfig;
  server: ServerConnection;
}) {
  const { apiReady, draftServer, runtimeConfig, server } = props;
  const online = server.status === "online";
  const mode = online ? server.mode : draftServer.mode;
  const apiUrl = normalizeApiUrl(runtimeConfig.apiUrl) || "未配置";
  const target = online ? connectedTargetLabel(server) : draftTargetLabel(draftServer);
  const executor = mode === "ssh" ? "Paramiko SSH" : "LocalExecutor";
  const targetMeta = online
    ? `${server.distro || "unknown distro"} · ${server.kernel || "unknown kernel"}`
    : mode === "ssh"
      ? "待后端握手"
      : "待本机探测";
  return (
    <div className="mb-4 overflow-hidden rounded-lg border border-neutral-950 bg-neutral-950 text-white">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Network className="size-4 text-cyan-300" />
          控制链路
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant={apiReady ? "success" : "warning"}>{apiReady ? "API ready" : "API missing"}</Badge>
          <Badge variant={online ? "success" : "default"}>{online ? "target online" : "target pending"}</Badge>
        </div>
      </div>
      <div className="grid gap-3 px-3 py-3 text-xs sm:grid-cols-3">
        <RouteStep icon={PlugZap} label="Web API" value={apiUrl} detail={apiReady ? "已应用" : "未应用"} active={apiReady} />
        <RouteStep icon={mode === "ssh" ? Server : Laptop} label="后端执行器" value={executor} detail={mode === "ssh" ? "远程 SSH" : "本机"} active={apiReady} />
        <RouteStep icon={Database} label="目标" value={target} detail={targetMeta} active={online} />
      </div>
    </div>
  );
}

function RouteStep(props: {
  active: boolean;
  detail: string;
  icon: LucideIcon;
  label: string;
  value: string;
}) {
  const { active, detail, icon: Icon, label, value } = props;
  return (
    <div className="min-w-0 border-l border-white/15 pl-3">
      <div className="mb-1 flex items-center gap-2 text-[11px] uppercase tracking-normal text-white/50">
        <Icon className={cn("size-3.5", active ? "text-cyan-300" : "text-white/35")} />
        {label}
      </div>
      <div className="truncate font-mono text-[12px] text-white">{value}</div>
      <div className="mt-1 truncate text-[11px] text-white/50">{detail}</div>
    </div>
  );
}

function SshCredentialPanel({ draftServer }: { draftServer: ServerConnection }) {
  const hasKey = Boolean(draftServer.keyFile.trim());
  const hasPassword = Boolean((draftServer.password ?? "").trim());
  const hasSudoPassword = Boolean((draftServer.sudoPassword ?? "").trim());
  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-neutral-800">
          <KeyRound className="size-4 text-cyan-600" />
          SSH 凭据状态
        </div>
        <Badge variant="info">secrets not saved</Badge>
      </div>
      <div className="grid gap-2 sm:grid-cols-4">
        <CredentialState label="Key" value={hasKey ? "path set" : "not set"} active={hasKey} />
        <CredentialState label="Password" value={hasPassword ? "filled" : "empty"} active={hasPassword} />
        <CredentialState label="Sudo" value={hasSudoPassword ? "filled" : "empty"} active={hasSudoPassword} />
        <CredentialState label="Storage" value="safe fields only" active />
      </div>
    </div>
  );
}

function CredentialState({ active, label, value }: { active: boolean; label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md bg-white px-2.5 py-2 ring-1 ring-neutral-200">
      <div className="text-[11px] uppercase tracking-normal text-neutral-400">{label}</div>
      <div className={cn("truncate font-mono text-xs", active ? "text-neutral-950" : "text-neutral-400")}>{value}</div>
    </div>
  );
}

function LocalTargetSummary({ server }: { server: ServerConnection }) {
  const connected = server.mode === "local" && server.status === "online";
  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-white text-neutral-700 ring-1 ring-neutral-200">
            <Laptop className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-neutral-900">本机执行目标</div>
            <div className="truncate text-xs text-neutral-500">{connected ? "已连接" : "待连接"}</div>
          </div>
        </div>
        <Badge variant={connected ? "success" : "warning"}>{connected ? "local" : "ready"}</Badge>
      </div>
      <div className="grid gap-2 rounded-md bg-white px-3 py-2 text-sm ring-1 ring-neutral-200">
        <InfoRow label="Host" value="localhost" />
        <InfoRow label="Executor" value="LocalExecutor" />
        <InfoRow label="User" value={connected ? server.user || "unknown" : "连接后读取"} />
      </div>
    </div>
  );
}

function ToolsView(props: {
  apiReady: boolean;
  activeToolCount: number;
  groupedTools: Record<string, ToolCapability[]>;
  query: string;
  runTool: (tool: ToolCapability) => void;
  setQuery: (value: string) => void;
  toolCount: number;
}) {
  const { apiReady, activeToolCount, groupedTools, query, runTool, setQuery, toolCount } = props;
  const visibleToolCount = Object.values(groupedTools).reduce((total, items) => total + items.length, 0);
  const hasQuery = Boolean(query.trim());
  return (
    <section className="gsap-in rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
      <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold">工具</h2>
          <p className="text-sm text-neutral-500">
            {apiReady ? "来自真实 API" : "未接入 API"} · {visibleToolCount}/{toolCount} shown · {activeToolCount} mutable
          </p>
        </div>
        <div className="relative w-full md:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-neutral-400" />
          <Input className="pl-9 pr-9" onChange={(event) => setQuery(event.target.value)} placeholder="搜索工具" value={query} />
          {hasQuery && (
            <button
              aria-label="清空工具搜索"
              className="absolute right-2 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
              onClick={() => setQuery("")}
              type="button"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="grid gap-5">
        {Object.keys(groupedTools).length === 0 && (
          <EmptyPanel
            title={hasQuery ? "没有匹配工具" : apiReady ? "API 未返回工具目录" : "需要真实 API"}
            detail={
              hasQuery
                ? "换一个关键词，或清空搜索查看后端返回的完整工具目录。"
                : apiReady
                  ? "后端 /overview 没有返回 tools，前端不会用本地假工具补齐。"
                  : "在顶部或设置页输入 API URL 并应用后，工具目录必须由后端返回。"
            }
          />
        )}
        {Object.entries(groupedTools).map(([category, items]) => {
          const Icon = categoryIcons[category] ?? Wrench;
          return (
            <div key={category}>
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-neutral-700">
                <Icon className="size-4" />
                {category}
              </div>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {items.map((tool) => (
                  <button
                    className="rounded-lg border border-neutral-200 bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-neutral-300 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0 disabled:hover:border-neutral-200 disabled:hover:shadow-sm"
                    key={tool.name}
                    disabled={!apiReady}
                    onClick={() => runTool(tool)}
                    title={apiReady ? `运行 ${tool.name}` : "请先接入真实 SysDialogue API"}
                    type="button"
                  >
                    <div className="mb-2 flex items-start justify-between gap-2">
                      <span className="font-mono text-sm font-semibold text-neutral-950">{tool.name}</span>
                      <RiskBadge risk={tool.risk} />
                    </div>
                    <p className="line-clamp-2 min-h-10 text-sm text-neutral-600">{tool.description}</p>
                    <div className="mt-3 flex flex-wrap gap-1">
                      {inputFieldsForTarget({ kind: "tool", item: tool }).slice(0, 4).map((arg) => (
                        <Badge key={arg.name} variant={arg.required ? "warning" : "default"}>{arg.name}{arg.required ? "*" : ""}</Badge>
                      ))}
                      {tool.args.length === 0 && <Badge variant="success">no args</Badge>}
                    </div>
                    <div className={cn("mt-3 text-xs font-medium", apiReady ? "text-cyan-700" : "text-neutral-500")}>
                      {apiReady ? "配置参数后运行" : "等待真实 API"}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function WorkflowsView(props: {
  apiReady: boolean;
  query: string;
  runWorkflow: (workflow: WorkflowDefinition) => void;
  setQuery: (value: string) => void;
  totalWorkflows: number;
  workflows: WorkflowDefinition[];
}) {
  const { apiReady, query, runWorkflow, setQuery, totalWorkflows, workflows: visibleWorkflows } = props;
  const hasQuery = Boolean(query.trim());
  return (
    <section className="gsap-in rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
      <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold">工作流</h2>
          <p className="text-sm text-neutral-500">
            {apiReady ? `${visibleWorkflows.length}/${totalWorkflows} shown · observe · approve · act · verify · audit` : "未接入 API"}
          </p>
        </div>
        <div className="relative w-full md:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-neutral-400" />
          <Input className="pl-9 pr-9" onChange={(event) => setQuery(event.target.value)} placeholder="搜索 workflow" value={query} />
          {hasQuery && (
            <button
              aria-label="清空工作流搜索"
              className="absolute right-2 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
              onClick={() => setQuery("")}
              type="button"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {visibleWorkflows.length === 0 && (
          <EmptyPanel
            className="md:col-span-2"
            title={hasQuery ? "没有匹配工作流" : apiReady ? "API 未返回工作流" : "需要真实 API"}
            detail={
              hasQuery
                ? "换一个关键词，或清空搜索查看后端返回的完整工作流目录。"
                : apiReady
                  ? "后端 /overview 没有返回 workflows，前端不会用本地假 workflow 补齐。"
                  : "在顶部或设置页输入 API URL 并应用后，工作流列表必须由后端返回。"
            }
          />
        )}
        {visibleWorkflows.map((workflow) => (
          <button
            className="rounded-lg border border-neutral-200 bg-white p-4 text-left shadow-sm transition hover:border-neutral-300 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:border-neutral-200 disabled:hover:shadow-sm"
            key={workflow.name}
            disabled={!apiReady}
            onClick={() => runWorkflow(workflow)}
            title={apiReady ? `运行 ${workflow.label}` : "请先接入真实 SysDialogue API"}
            type="button"
          >
            <div className="mb-3 flex items-start justify-between gap-3">
              <div>
                <div className="text-base font-semibold">{workflow.label}</div>
                <div className="font-mono text-xs text-neutral-500">{workflow.name}</div>
              </div>
              <RiskBadge risk={workflow.risk} />
            </div>
            <p className="text-sm text-neutral-600">{workflow.description}</p>
            <div className="mt-4 flex items-center justify-between text-xs text-neutral-500">
              <span>{workflow.steps} steps</span>
              <span>{workflow.inputs.join(" · ") || "no input"}</span>
            </div>
            <div className={cn("mt-3 text-xs font-medium", apiReady ? "text-cyan-700" : "text-neutral-500")}>
              {apiReady ? "检查参数后运行" : "等待真实 API"}
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

function AuditView(props: {
  audit: AuditRecord[];
  exportAudit: (format: "jsonl" | "replay") => void;
}) {
  const { audit, exportAudit } = props;
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<AuditTypeFilter>("all");
  const [riskFilter, setRiskFilter] = useState<AuditRiskFilter>("all");
  const stats = useMemo(() => auditStats(audit), [audit]);
  const filteredAudit = useMemo(
    () => filterAuditRecords(audit, query, typeFilter, riskFilter),
    [audit, query, typeFilter, riskFilter],
  );
  return (
    <section className="gsap-in rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
      <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold">审计</h2>
          <p className="text-sm text-neutral-500">{audit.length} records · sanitized · {filteredAudit.length} shown</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => exportAudit("jsonl")}>
            <Archive />
            JSONL
          </Button>
          <Button variant="secondary" onClick={() => exportAudit("replay")}>
            <UploadCloud />
            Replay
          </Button>
        </div>
      </div>
      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <AuditStatCard label="全部记录" value={stats.total} detail="后端审计返回" tone="info" />
        <AuditStatCard label="命令轨迹" value={stats.commands} detail="terminal / tool" tone="success" />
        <AuditStatCard label="高风险" value={stats.risky} detail="WARN-HIGH / HARD" tone={stats.risky ? "warning" : "success"} />
        <AuditStatCard label="工作流" value={stats.workflows} detail="workflow steps" tone="info" />
      </div>
      <div className="mb-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_180px_180px]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-neutral-400" />
          <Input
            className="pl-9"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索目标、结果或规则"
            value={query}
          />
        </div>
        <select
          aria-label="审计类型筛选"
          className="h-9 rounded-md border border-neutral-200 bg-white px-3 text-sm outline-none focus:ring-2 focus:ring-cyan-500/20"
          onChange={(event) => setTypeFilter(event.target.value as AuditTypeFilter)}
          value={typeFilter}
        >
          {auditTypeOptions.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
        <select
          aria-label="审计风险筛选"
          className="h-9 rounded-md border border-neutral-200 bg-white px-3 text-sm outline-none focus:ring-2 focus:ring-cyan-500/20"
          onChange={(event) => setRiskFilter(event.target.value as AuditRiskFilter)}
          value={riskFilter}
        >
          {auditRiskOptions.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>
      {audit.length === 0 && (
        <EmptyPanel title="无审计记录" detail="前端不会预置审计样例；连接真实 API 并执行任务后才会显示后端返回的审计数据。" />
      )}
      {audit.length > 0 && filteredAudit.length === 0 && (
        <EmptyPanel title="没有匹配记录" detail="调整搜索词、类型或风险筛选后再查看。" />
      )}
      {filteredAudit.length > 0 && (
        <div className="hidden overflow-hidden rounded-lg border border-neutral-200 lg:block">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="bg-neutral-100 text-xs uppercase text-neutral-500">
            <tr>
              <th className="px-3 py-2 font-medium">时间</th>
              <th className="px-3 py-2 font-medium">类型</th>
              <th className="px-3 py-2 font-medium">目标</th>
              <th className="px-3 py-2 font-medium">结果</th>
              <th className="px-3 py-2 font-medium">风险</th>
              <th className="px-3 py-2 font-medium">规则</th>
            </tr>
          </thead>
          <tbody>
            {filteredAudit.map((record, index) => (
              <tr className="border-t border-neutral-100" key={auditRecordKey(record, index)}>
                <td className="px-3 py-3 text-neutral-500">{formatRelativeTime(record.time)}</td>
                <td className="px-3 py-3">{record.type}</td>
                <td className="max-w-[260px] break-words px-3 py-3 font-mono text-xs">{record.target}</td>
                <td className="max-w-[320px] break-words px-3 py-3">{record.result}</td>
                <td className="px-3 py-3"><RiskBadge risk={record.risk} /></td>
                <td className="max-w-[220px] break-words px-3 py-3 text-neutral-500">{record.ruleIds.join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {filteredAudit.length > 0 && (
        <div className="grid gap-3 lg:hidden">
          {filteredAudit.map((record, index) => (
            <AuditRecordCard key={auditRecordKey(record, index)} record={record} />
          ))}
        </div>
      )}
    </section>
  );
}

function AuditStatCard(props: {
  detail: string;
  label: string;
  tone: "success" | "warning" | "danger" | "info";
  value: number;
}) {
  const { detail, label, tone, value } = props;
  return (
    <div className={cn("rounded-lg border p-3", auditToneClass(tone))}>
      <div className="text-xs font-medium text-neutral-500">{label}</div>
      <div className="mt-1 flex items-end justify-between gap-2">
        <div className="text-2xl font-semibold text-neutral-950">{value}</div>
        <div className="truncate text-xs text-neutral-500">{detail}</div>
      </div>
    </div>
  );
}

function AuditRecordCard({ record }: { record: AuditRecord }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold">{record.type}</div>
          <div className="text-xs text-neutral-500">{formatRelativeTime(record.time)}</div>
        </div>
        <RiskBadge risk={record.risk} />
      </div>
      <div className="grid gap-3 text-sm">
        <div>
          <div className="mb-1 text-xs font-medium text-neutral-500">目标</div>
          <div className="break-words font-mono text-xs text-neutral-800">{record.target || "—"}</div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-neutral-500">结果</div>
          <div className="break-words text-neutral-800">{record.result || "—"}</div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-neutral-500">规则</div>
          <div className="break-words text-xs text-neutral-500">{record.ruleIds.join(", ") || "—"}</div>
        </div>
      </div>
    </div>
  );
}

function ReleaseView(props: {
  acceptanceChecklist: AcceptanceChecklistPayload | null;
  apiReady: boolean;
  buildReleaseReport: () => void;
  exportAudit: (format: "jsonl" | "replay") => void;
  exportAcceptanceBundle: () => void;
  loadAcceptanceChecklist: () => void;
  openMutationDrill: () => void;
  runAcceptanceRunner: (mode?: "safe-preflight" | "model-check" | "conversation-check" | "ui-review" | "read-only-collect" | "recovery-drill") => void;
  releaseError: string;
  releaseInput: string;
  releaseLoading: boolean;
  releaseReport: ReleaseReadinessResponse | null;
  server: ServerConnection;
  setReleaseInput: (value: string) => void;
  setSurface: (surface: Surface) => void;
}) {
  const {
    acceptanceChecklist,
    apiReady,
    buildReleaseReport,
    exportAudit,
    exportAcceptanceBundle,
    loadAcceptanceChecklist,
    openMutationDrill,
    runAcceptanceRunner,
    releaseError,
    releaseInput,
    releaseLoading,
    releaseReport,
    server,
    setReleaseInput,
    setSurface,
  } = props;
  const readiness = releaseReport?.readiness;
  const counts = readiness?.counts;
  const releaseGate = readiness?.releaseGate;

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_430px]">
      <section className="gsap-in rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-lg font-semibold">Release acceptance</h2>
            <p className="text-sm text-neutral-500">
              {acceptanceChecklist?.target ?? (server.status === "online" ? compactHost(server.host, server.port) : "no connected target")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button disabled={!apiReady || releaseLoading} onClick={loadAcceptanceChecklist} type="button" variant="secondary">
              {releaseLoading ? <Loader2 className="animate-spin" /> : <ClipboardList />}
              Checklist
            </Button>
            <Button disabled={!apiReady || releaseLoading} onClick={() => runAcceptanceRunner()} type="button" variant="secondary">
              {releaseLoading ? <Loader2 className="animate-spin" /> : <Workflow />}
              Runner
            </Button>
            <Button
              disabled={!apiReady || releaseLoading}
              onClick={() => runAcceptanceRunner("ui-review")}
              type="button"
              variant="secondary"
            >
              {releaseLoading ? <Loader2 className="animate-spin" /> : <LayoutGrid />}
              UI
            </Button>
            <Button
              disabled={!apiReady || releaseLoading || server.status !== "online"}
              onClick={() => runAcceptanceRunner("model-check")}
              type="button"
              variant="secondary"
            >
              {releaseLoading ? <Loader2 className="animate-spin" /> : <Bot />}
              Model
            </Button>
            <Button
              disabled={!apiReady || releaseLoading || server.status !== "online"}
              onClick={() => runAcceptanceRunner("conversation-check")}
              type="button"
              variant="secondary"
            >
              {releaseLoading ? <Loader2 className="animate-spin" /> : <Send />}
              Chat
            </Button>
            <Button
              disabled={!apiReady || releaseLoading || server.status !== "online"}
              onClick={() => runAcceptanceRunner("read-only-collect")}
              type="button"
              variant="secondary"
            >
              {releaseLoading ? <Loader2 className="animate-spin" /> : <Gauge />}
              Collect
            </Button>
            <Button
              disabled={!apiReady || releaseLoading || server.status !== "online"}
              onClick={() => runAcceptanceRunner("recovery-drill")}
              type="button"
              variant="secondary"
            >
              {releaseLoading ? <Loader2 className="animate-spin" /> : <History />}
              Recovery
            </Button>
            <Button
              disabled={!apiReady || releaseLoading || server.status !== "online"}
              onClick={openMutationDrill}
              type="button"
              variant="warning"
            >
              {releaseLoading ? <Loader2 className="animate-spin" /> : <ShieldAlert />}
              Drill
            </Button>
            <Button
              disabled={!apiReady || releaseLoading || server.status !== "online"}
              onClick={() => exportAudit("replay")}
              type="button"
              variant="secondary"
            >
              {releaseLoading ? <Loader2 className="animate-spin" /> : <UploadCloud />}
              Replay
            </Button>
            <Button disabled={!apiReady || releaseLoading || !releaseInput.trim()} onClick={buildReleaseReport} type="button">
              {releaseLoading ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
              Readiness
            </Button>
            <Button disabled={!apiReady || releaseLoading || !releaseInput.trim()} onClick={exportAcceptanceBundle} type="button" variant="secondary">
              {releaseLoading ? <Loader2 className="animate-spin" /> : <Archive />}
              Bundle
            </Button>
            {!apiReady && (
              <Button onClick={() => setSurface("settings")} type="button" variant="warning">
                <Settings />
                API
              </Button>
            )}
          </div>
        </div>

        {releaseError && (
          <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
            {releaseError}
          </div>
        )}

        <div className="grid gap-3">
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="release-acceptance-input">Acceptance artifact</Label>
            {acceptanceChecklist && (
              <Badge variant={acceptanceChecklist.connected ? "success" : "warning"}>
                {acceptanceChecklist.connected ? "connected target" : "template target"}
              </Badge>
            )}
          </div>
          <Textarea
            className="min-h-[520px] resize-y font-mono text-xs leading-5"
            id="release-acceptance-input"
            onChange={(event) => setReleaseInput(event.target.value)}
            placeholder="- [x] A01 startup self-check passed..."
            value={releaseInput}
          />
        </div>
      </section>

      <section className="gsap-in rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <div className="mb-5 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Readiness report</h2>
            <p className="text-sm text-neutral-500">{readiness?.source ?? "waiting for acceptance artifact"}</p>
          </div>
          {readiness && <ReadinessOverallBadge value={readiness.overall} />}
        </div>

        {counts && (
          <div className="mb-4 grid grid-cols-5 gap-2">
            {(["pass", "partial", "fail", "missing", "unknown"] as const).map((status) => (
              <div className="rounded-lg border border-neutral-200 px-2 py-2 text-center" key={status}>
                <div className="text-lg font-semibold">{counts[status] ?? 0}</div>
                <div className="text-[11px] uppercase text-neutral-500">{status}</div>
              </div>
            ))}
          </div>
        )}

        {releaseGate && <ReleaseGatePanel gate={releaseGate} />}

        {!readiness && (
          <EmptyPanel title="No readiness report" detail="Generate a checklist, mark A01-A10, then build the report from the edited artifact." />
        )}

        {readiness && (
          <div className="space-y-4">
            <div className="space-y-2">
              {readiness.checks.map((check) => (
                <div className="rounded-lg border border-neutral-200 p-3" key={check.stepId}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-mono text-xs font-semibold">{check.stepId}</div>
                      <div className="text-sm font-medium">{check.title}</div>
                      <div className="mt-1 text-xs text-neutral-500">{check.gate}</div>
                    </div>
                    <ReadinessStatusBadge status={check.status} />
                  </div>
                  {check.evidence && <div className="mt-2 break-words text-xs text-neutral-600">{check.evidence}</div>}
                </div>
              ))}
            </div>

            <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
                <FileText className="size-4" />
                Release note
              </div>
              <pre className="whitespace-pre-wrap break-words text-xs text-neutral-700">{releaseReport.report}</pre>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function SettingsView(props: {
  applyRuntimeConfig: (config?: RuntimeConfig) => void;
  draftConfig: RuntimeConfig;
  setDraftConfig: React.Dispatch<React.SetStateAction<RuntimeConfig>>;
  toolCount: number;
  workflowCount: number;
}) {
  const { applyRuntimeConfig, draftConfig, setDraftConfig, toolCount, workflowCount } = props;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="gsap-in rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-5 text-lg font-semibold">真实 API 接入</h2>
        <div className="grid gap-4">
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
            <div className="grid gap-3">
              <div className="grid gap-2">
                <Label htmlFor="runtime-api-url">SysDialogue API URL</Label>
                <Input
                  id="runtime-api-url"
                  onChange={(event) => setDraftConfig((current) => ({ ...current, apiUrl: event.target.value }))}
                  placeholder="http://127.0.0.1:8000/api"
                  value={draftConfig.apiUrl}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="runtime-model">模型</Label>
                <Input
                  id="runtime-model"
                  onChange={(event) => setDraftConfig((current) => ({ ...current, model: event.target.value }))}
                  placeholder="后端默认模型"
                  value={draftConfig.model}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="runtime-openai-base">OpenAI-compatible Base URL</Label>
                <Input
                  id="runtime-openai-base"
                  onChange={(event) => setDraftConfig((current) => ({ ...current, openaiBaseUrl: event.target.value }))}
                  placeholder="后端默认 base_url"
                  value={draftConfig.openaiBaseUrl}
                />
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor="runtime-max-iterations">Max iterations</Label>
                  <Input
                    id="runtime-max-iterations"
                    min={20}
                    max={300}
                    onChange={(event) => setDraftConfig((current) => ({ ...current, maxIterations: Number(event.target.value) }))}
                    type="number"
                    value={draftConfig.maxIterations}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="runtime-safety-profile">安全档位</Label>
                  <select
                    id="runtime-safety-profile"
                    className="h-9 rounded-md border border-neutral-200 bg-white px-3 text-sm outline-none focus:ring-2 focus:ring-cyan-500/20"
                    onChange={(event) => setDraftConfig((current) => ({ ...current, safetyProfile: event.target.value as SafetyProfile }))}
                    value={draftConfig.safetyProfile}
                  >
                    <option value="standard">standard</option>
                    <option value="operator">operator</option>
                    <option value="break_glass">break_glass</option>
                  </select>
                </div>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="runtime-workflows-dir">Workflows dir</Label>
                <Input
                  id="runtime-workflows-dir"
                  onChange={(event) => setDraftConfig((current) => ({ ...current, workflowsDir: event.target.value }))}
                  placeholder="后端默认 sysdialogue/workflows"
                  value={draftConfig.workflowsDir}
                />
              </div>
              <ToggleRow
                checked={draftConfig.streamEvents}
                label="事件流"
                onCheckedChange={(checked) => setDraftConfig((current) => ({ ...current, streamEvents: checked }))}
              />
              <Button onClick={() => applyRuntimeConfig()} type="button">
                <RefreshCcw />
                应用热更新
              </Button>
            </div>
          </div>
        </div>
      </section>
      <section className="gsap-in rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-5 text-lg font-semibold">扩展面</h2>
        <div className="grid gap-3">
          <CapabilityRow icon={Wrench} label="API tools" value={`${toolCount}`} />
          <CapabilityRow icon={Workflow} label="API workflows" value={`${workflowCount}`} />
          <CapabilityRow icon={Sparkles} label="DynTool" value="由后端声明" />
          <CapabilityRow icon={Layers3} label="Role handoff" value="由后端声明" />
          <CapabilityRow icon={KeyRound} label="Remote lockout" value="由后端执行" />
          <CapabilityRow icon={Database} label="Memory / trace" value="由后端持久化" />
        </div>
      </section>
    </div>
  );
}

function RightDock(props: {
  activeTask: TaskRun | undefined;
  audit: AuditRecord[];
  clock: string;
  metrics: Metric[];
  pendingApproval: ApprovalRequest | null;
  readiness: ReadinessItem[];
  server: ServerConnection;
  setSurface: (surface: Surface) => void;
  tasks: TaskRun[];
}) {
  const { activeTask, audit, clock, metrics, pendingApproval, readiness, server, setSurface, tasks } = props;
  return (
    <div className="flex h-[calc(100vh-54px)] flex-col">
      <div className="border-b border-neutral-200 p-4">
        <Tabs defaultValue="server">
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="server">服务器</TabsTrigger>
            <TabsTrigger value="risk">审批</TabsTrigger>
            <TabsTrigger value="audit">审计</TabsTrigger>
            <TabsTrigger value="env">环境</TabsTrigger>
          </TabsList>
          <TabsContent value="server">
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{server.name}</div>
                  <div className="truncate text-xs text-neutral-500">{compactHost(server.host, server.port)}</div>
                </div>
                <StatusBadge status={server.status} />
              </div>
              <Button className="w-full" variant="secondary" onClick={() => setSurface("servers")}>
                管理服务器
              </Button>
              <div className="space-y-2">
                {readiness.map((item) => (
                  <div className="flex items-start gap-2 text-xs" key={item.label}>
                    <span className={cn("mt-1 size-2 rounded-full", readinessDotClass(item.state))} />
                    <div className="min-w-0">
                      <div className="font-medium">{item.label}</div>
                      <div className="truncate text-neutral-500">{item.detail}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </TabsContent>
          <TabsContent value="risk">
            {pendingApproval ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <div className="font-semibold">{pendingApproval.tool}</div>
                <div className="mt-1">{pendingApproval.reason}</div>
              </div>
            ) : (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">无待处理审批</div>
            )}
          </TabsContent>
          <TabsContent value="audit">
            <div className="space-y-2">
              {audit.length === 0 && (
                <EmptyPanel title="无审计记录" detail="审计记录必须来自真实后端。" />
              )}
              {audit.slice(0, 4).map((record, index) => (
                <div className="rounded-md border border-neutral-200 p-2 text-xs" key={auditRecordKey(record, index)}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{record.type}</span>
                    <RiskBadge risk={record.risk} />
                  </div>
                  <div className="mt-1 truncate text-neutral-500">{record.target}</div>
                </div>
              ))}
            </div>
          </TabsContent>
          <TabsContent value="env">
            <div className="grid gap-3">
              {metrics.length === 0 ? (
                <EmptyPanel title="无环境指标" detail="等待真实 API 返回 metrics。" />
              ) : (
                metrics.map((metric) => <MetricRow key={metric.label} metric={metric} compact />)
              )}
            </div>
          </TabsContent>
        </Tabs>
      </div>

      <div className="border-b border-neutral-200 p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm font-semibold">当前任务</span>
          <span className="font-mono text-xs text-neutral-500">{clock}</span>
        </div>
        {activeTask ? (
          <div className="space-y-2">
            <div className="text-sm font-medium">{activeTask.title}</div>
            <StatusTaskBadge status={activeTask.status} />
            <Progress value={taskProgress(activeTask)} />
          </div>
        ) : (
          <div className="text-sm text-neutral-500">idle</div>
        )}
        {tasks.length > 0 && (
          <div className="mt-4">
            <TaskHistoryPanel tasks={tasks} activeTaskId={activeTask?.id} compact />
          </div>
        )}
      </div>

      <ScrollArea className="min-h-0 flex-1 p-4">
        <div className="space-y-3">
          {activeTask?.events.map((event) => (
            <div className="flex gap-3 text-sm" key={event.id}>
              <span className={cn("mt-1 size-2 rounded-full", eventToneClass(event.tone))} />
              <div className="min-w-0">
                <div className="font-medium">{event.stage}</div>
                <div className="text-neutral-500">{event.message}</div>
              </div>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

function ServerPopover(props: {
  metrics: Metric[];
  server: ServerConnection;
  setSurface: (surface: Surface) => void;
  toolCount: number;
  workflowCount: number;
}) {
  const { metrics, server, setSurface, toolCount, workflowCount } = props;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="secondary" size="icon" aria-label="服务器状态">
          <span className="relative">
            <Server className="size-4" />
            <span className={cn("activity-pulse absolute -right-1 -top-1 size-2 rounded-full", server.status === "online" ? "bg-emerald-500" : "bg-rose-500")} />
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[450px]">
        <Tabs defaultValue="server">
          <TabsList>
            <TabsTrigger value="server">{server.status === "online" ? 1 : 0} 服务器</TabsTrigger>
            <TabsTrigger value="tools">{toolCount} 工具</TabsTrigger>
            <TabsTrigger value="flow">{workflowCount} 工作流</TabsTrigger>
            <TabsTrigger value="audit">审计</TabsTrigger>
          </TabsList>
          <TabsContent value="server">
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <span className={cn("size-2 rounded-full", server.status === "online" ? "bg-emerald-500" : "bg-rose-500")} />
                    {compactHost(server.host, server.port)}
                  </div>
                  <div className="mt-1 text-xs text-neutral-500">{server.distro} · {server.kernel}</div>
                </div>
                {server.status === "online" ? <Check className="size-4 text-emerald-600" /> : <ShieldAlert className="size-4 text-amber-600" />}
              </div>
              <Button variant="secondary" onClick={() => setSurface("servers")}>管理服务器</Button>
            </div>
          </TabsContent>
          <TabsContent value="tools">
            <div className="grid grid-cols-2 gap-2 text-sm">
              <InfoPill icon={ShieldCheck} label="工具目录" value={toolCount ? `${toolCount}` : "未返回"} />
              <InfoPill icon={ShieldAlert} label="风险策略" value={server.safetyProfile || "未知"} />
              <InfoPill icon={Braces} label="DynTool" value="后端决定" />
              <InfoPill icon={LockKeyhole} label="HARD" value="后端决定" />
            </div>
          </TabsContent>
          <TabsContent value="flow">
            <div className="grid gap-2 text-sm">
              <EmptyPanel
                title={workflowCount ? `${workflowCount} 个工作流已由 API 返回` : "未返回工作流目录"}
                detail="工作流名称和状态不在前端硬编码，必须来自真实 /overview 响应。"
              />
            </div>
          </TabsContent>
          <TabsContent value="audit">
            <div className="grid gap-3">
              {metrics.length === 0 ? (
                <EmptyPanel title="无指标" detail="等待真实 API 返回 metrics。" />
              ) : (
                metrics.map((metric) => <MetricRow key={metric.label} metric={metric} compact />)
              )}
            </div>
          </TabsContent>
        </Tabs>
      </PopoverContent>
    </Popover>
  );
}

function ApprovalDialog(props: {
  pendingApproval: ApprovalRequest | null;
  resolveApproval: (approved: boolean) => void;
}) {
  const { pendingApproval, resolveApproval } = props;
  return (
    <Dialog open={Boolean(pendingApproval)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>需要审批</DialogTitle>
          <DialogDescription>{pendingApproval?.tool ?? "tool"} · {pendingApproval?.risk ?? "WARN-HIGH"}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
          <div>{pendingApproval?.reason}</div>
          <div className="text-amber-800">回滚：{pendingApproval?.rollback}</div>
        </div>
        <DialogFooter>
          <Button variant="secondary" onClick={() => resolveApproval(false)}>拒绝</Button>
          <Button variant="warning" onClick={() => resolveApproval(true)}>批准执行</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AcceptanceMutationDrillDialog(props: {
  form: AcceptanceDrillFormState;
  loading: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: () => void;
  onWorkflowChange: (workflowName: AcceptanceDrillWorkflow) => void;
  open: boolean;
  server: ServerConnection;
  setForm: React.Dispatch<React.SetStateAction<AcceptanceDrillFormState>>;
}) {
  const { form, loading, onOpenChange, onSubmit, onWorkflowChange, open, server, setForm } = props;
  const update = (patch: Partial<AcceptanceDrillFormState>) => setForm((current) => ({ ...current, ...patch }));
  const canSubmit =
    server.status === "online" &&
    form.disposableTarget &&
    form.approvalPhrase.trim() === A07_APPROVAL_PHRASE &&
    form.impact.trim().length >= 12 &&
    form.rollback.trim().length >= 12 &&
    form.verification.trim().length >= 12 &&
    form.argsText.trim().length > 2;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>A07 mutation drill</DialogTitle>
          <DialogDescription>
            {server.status === "online" ? compactHost(server.host, server.port) : "connect a target before running a drill"}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="flex flex-wrap gap-2">
            {(["service_restart", "safe_config_patch"] as const).map((workflowName) => (
              <Button
                key={workflowName}
                onClick={() => onWorkflowChange(workflowName)}
                type="button"
                variant={form.workflowName === workflowName ? "default" : "secondary"}
              >
                <Workflow />
                {workflowName}
              </Button>
            ))}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="a07-args">Workflow args JSON</Label>
            <Textarea
              className="min-h-[118px] font-mono text-xs"
              id="a07-args"
              onChange={(event) => update({ argsText: event.target.value })}
              value={form.argsText}
            />
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="a07-impact">Impact</Label>
              <Textarea
                id="a07-impact"
                onChange={(event) => update({ impact: event.target.value })}
                value={form.impact}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="a07-rollback">Rollback</Label>
              <Textarea
                id="a07-rollback"
                onChange={(event) => update({ rollback: event.target.value })}
                value={form.rollback}
              />
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="a07-verification">Post-change verification</Label>
            <Textarea
              id="a07-verification"
              onChange={(event) => update({ verification: event.target.value })}
              value={form.verification}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="a07-phrase">Approval phrase</Label>
            <Input
              id="a07-phrase"
              onChange={(event) => update({ approvalPhrase: event.target.value })}
              placeholder={A07_APPROVAL_PHRASE}
              value={form.approvalPhrase}
            />
          </div>

          <div className="flex items-center justify-between gap-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
            <div>
              <div className="text-sm font-semibold text-amber-950">Disposable or explicitly low-risk target</div>
              <div className="text-xs text-amber-800">Required before SysDialogue will approve the constrained workflow prompts.</div>
            </div>
            <Switch checked={form.disposableTarget} onCheckedChange={(checked) => update({ disposableTarget: checked })} />
          </div>
        </div>
        <DialogFooter>
          <Button disabled={loading} onClick={() => onOpenChange(false)} type="button" variant="secondary">
            Cancel
          </Button>
          <Button disabled={!canSubmit || loading} onClick={onSubmit} type="button" variant="warning">
            {loading ? <Loader2 className="animate-spin" /> : <ShieldAlert />}
            Run drill
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RunTargetDialog(props: {
  argsError: string;
  argsMode: RunArgsMode;
  argsText: string;
  formValues: Record<string, string>;
  onFieldChange: (field: InputFieldDefinition, value: string) => void;
  onJsonChange: (value: string) => void;
  onOpenChange: (open: boolean) => void;
  onReset: () => void;
  onSetArgsMode: (mode: RunArgsMode) => void;
  onSubmit: () => void;
  open: boolean;
  running: boolean;
  target: RunTarget | null;
}) {
  const {
    argsError,
    argsMode,
    argsText,
    formValues,
    onFieldChange,
    onJsonChange,
    onOpenChange,
    onReset,
    onSetArgsMode,
    onSubmit,
    open,
    running,
    target,
  } = props;
  const fields = target ? inputFieldsForTarget(target) : [];
  const requiredFields = fields.filter((field) => field.required);
  const missingRequired = argsMode === "form"
    ? missingRequiredFields(fields, formValues)
    : missingRequiredFieldsFromJson(fields, argsText);
  const jsonDraftError = argsMode === "json" ? jsonArgsDraftError(argsText) : "";
  const canSubmit = Boolean(target) && !running && !jsonDraftError && missingRequired.length === 0;
  const title = target?.kind === "tool"
    ? `运行工具 ${target.item.name}`
    : target
      ? `运行工作流 ${target.item.label}`
      : "运行";
  const description = target?.kind === "tool" ? target.item.description : target?.item.description;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description || "参数会随请求提交给真实 SysDialogue 后端。"}</DialogDescription>
        </DialogHeader>
        {target && (
          <div className="grid gap-4">
            <div className="flex flex-wrap items-center gap-2">
              <RiskBadge risk={target.item.risk} />
              <Badge variant={target.kind === "tool" && target.item.readOnly ? "success" : "info"}>
                {target.kind === "tool" && target.item.readOnly ? "read only" : target.kind}
              </Badge>
              <Badge>{fields.length ? `${fields.length} 参数` : "无参数"}</Badge>
              {requiredFields.length > 0 && (
                <Badge variant={jsonDraftError || missingRequired.length ? "warning" : "success"}>
                  {jsonDraftError ? "JSON 待修正" : missingRequired.length ? `缺 ${missingRequired.length} 必填` : "必填已填"}
                </Badge>
              )}
            </div>

            {(missingRequired.length > 0 || jsonDraftError) && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
                {jsonDraftError ? (
                  <span>{jsonDraftError}</span>
                ) : (
                  <span>还需要填写：<span className="font-mono">{missingRequired.join(", ")}</span></span>
                )}
              </div>
            )}

            <Tabs value={argsMode} onValueChange={(value) => onSetArgsMode(value as RunArgsMode)}>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <TabsList className="w-fit">
                  <TabsTrigger value="form">表单</TabsTrigger>
                  <TabsTrigger value="json">JSON</TabsTrigger>
                </TabsList>
                <Button type="button" variant="ghost" size="sm" onClick={onReset}>
                  <RefreshCcw />
                  重置参数
                </Button>
              </div>
              <TabsContent value="form">
                {fields.length === 0 ? (
                  <EmptyPanel title="无参数" detail="这个入口可以直接运行，仍会经过后端安全策略、审计和目标执行器。" />
                ) : (
                  <div className="grid gap-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                    {fields.map((field) => (
                      <RunFieldInput
                        field={field}
                        key={field.name}
                        onChange={(value) => onFieldChange(field, value)}
                        value={formValues[field.name] ?? ""}
                      />
                    ))}
                  </div>
                )}
              </TabsContent>
              <TabsContent value="json">
                <div className="grid gap-2">
                  <Label htmlFor="run-target-args">JSON 参数</Label>
                  <Textarea
                    id="run-target-args"
                    className="min-h-44 font-mono text-xs"
                    onChange={(event) => onJsonChange(event.target.value)}
                    spellCheck={false}
                    value={argsText}
                  />
                </div>
              </TabsContent>
            </Tabs>
            {argsError && <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{argsError}</div>}
          </div>
        )}
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)} type="button">取消</Button>
          <Button disabled={!canSubmit} onClick={onSubmit} type="button">
            {running ? <Loader2 className="animate-spin" /> : <Send />}
            运行
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RunFieldInput(props: {
  field: InputFieldDefinition;
  onChange: (value: string) => void;
  value: string;
}) {
  const { field, onChange, value } = props;
  const fieldId = `run-field-${field.name}`;
  const type = normalizeFieldType(field.type);
  return (
    <div className="grid gap-2 rounded-md border border-neutral-200 bg-white p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <Label className="font-mono" htmlFor={fieldId}>
            {field.name}
            {field.required && <span className="ml-1 text-amber-600">*</span>}
          </Label>
          {field.description && <div className="mt-1 text-xs leading-5 text-neutral-500">{field.description}</div>}
        </div>
        <Badge variant={field.required ? "warning" : "default"}>{field.type || "string"}</Badge>
      </div>
      {type === "boolean" ? (
        <div className="flex items-center justify-between rounded-md border border-neutral-200 px-3 py-2">
          <span className="text-sm text-neutral-600">{value === "true" ? "true" : "false"}</span>
          <Switch checked={value === "true"} onCheckedChange={(checked) => onChange(String(checked))} />
        </div>
      ) : type === "array" || type === "object" ? (
        <Textarea
          id={fieldId}
          className="min-h-24 font-mono text-xs"
          onChange={(event) => onChange(event.target.value)}
          placeholder={type === "array" ? "[]" : "{}"}
          spellCheck={false}
          value={value}
        />
      ) : (
        <Input
          id={fieldId}
          onChange={(event) => onChange(event.target.value)}
          placeholder={field.required ? "必填" : "可选"}
          type={type === "number" || type === "integer" ? "number" : "text"}
          value={value}
        />
      )}
    </div>
  );
}

function CommandPalette(props: {
  items: PaletteItem[];
  open: boolean;
  query: string;
  selectPaletteItem: (kind: "surface" | "tool" | "workflow", value: string) => void;
  setOpen: (open: boolean) => void;
  setQuery: (query: string) => void;
}) {
  const { items, open, query, selectPaletteItem, setOpen, setQuery } = props;
  const hasQuery = Boolean(query.trim());
  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (!nextOpen) setQuery("");
  }
  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="w-[min(calc(100vw-2rem),720px)] min-w-0 max-w-[calc(100vw-2rem)] gap-0 overflow-hidden p-0">
        <DialogTitle className="sr-only">命令面板</DialogTitle>
        <DialogDescription className="sr-only">搜索视图、工具或 workflow，然后选择一项打开。</DialogDescription>
        <div className="flex items-center gap-2 border-b border-neutral-200 px-4 py-3">
          <Search className="size-4 text-neutral-400" />
          <Input
            autoFocus
            className="border-0 px-0 shadow-none focus:ring-0"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入视图、工具或 workflow"
            value={query}
          />
          {hasQuery && (
            <button
              aria-label="清空命令搜索"
              className="flex size-7 shrink-0 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
              onClick={() => setQuery("")}
              type="button"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        <div className="max-h-[420px] min-w-0 max-w-full overflow-x-hidden overflow-y-auto">
          <div className="min-w-0 p-2">
            {items.length === 0 && (
              <div className="grid min-h-36 place-items-center rounded-lg border border-dashed border-neutral-300 bg-neutral-50 px-4 py-6 text-center">
                <div>
                  <div className="text-sm font-semibold text-neutral-800">没有匹配命令</div>
                  <div className="mt-1 text-xs leading-5 text-neutral-500">换一个关键词，或清空搜索查看全部视图、工具和 workflow。</div>
                  {hasQuery && (
                    <Button className="mt-3" variant="secondary" size="sm" onClick={() => setQuery("")} type="button">
                      <X />
                      清空搜索
                    </Button>
                  )}
                </div>
              </div>
            )}
            {items.map((item) => (
              <button
                className="flex w-full min-w-0 items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm hover:bg-neutral-100"
                key={`${item.kind}_${item.value}`}
                onClick={() => selectPaletteItem(item.kind, item.value)}
                type="button"
              >
                <item.icon className="size-4 shrink-0 text-neutral-500" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{item.label}</span>
                  <span className="block truncate text-xs text-neutral-500">{item.detail}</span>
                </span>
                <span className="ml-auto flex shrink-0 items-center gap-1">
                  {item.risk && <RiskBadge risk={item.risk} />}
                  <Badge>{paletteKindLabel(item.kind)}</Badge>
                </span>
              </button>
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function WorkbenchEmptyState({ kind }: { kind: "api" | "server" }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-neutral-950 text-white">
          {kind === "api" ? <PlugZap className="size-5" /> : <Server className="size-5" />}
        </div>
        <div>
          <div className="font-semibold">{kind === "api" ? "等待接入真实 Web API" : "等待连接真实目标"}</div>
          <p className="mt-1 text-sm leading-6 text-neutral-600">
            {kind === "api"
              ? "前端不会使用假数据或 demo fallback。请先在前端输入 API URL 并应用，让工具、工作流、审计和执行结果全部来自后端。"
              : "未连接 SSH/本地执行目标前，自然语言任务和终端命令会保持禁用，不会模拟执行结果。"}
          </p>
        </div>
      </div>
    </div>
  );
}

function EmptyPanel({ className, title, detail }: { className?: string; title: string; detail: string }) {
  return (
    <div className={cn("rounded-lg border border-dashed border-neutral-300 bg-neutral-50 p-5 text-sm", className)}>
      <div className="font-semibold text-neutral-800">{title}</div>
      <div className="mt-1 leading-6 text-neutral-500">{detail}</div>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[82%] rounded-lg border px-4 py-3 text-sm shadow-sm",
          isUser ? "border-neutral-950 bg-neutral-950 text-white" : "border-neutral-200 bg-white text-neutral-800",
        )}
      >
        <div className="mb-1 text-xs opacity-60">{isUser ? "你" : "SysDialogue"} · {formatRelativeTime(message.at)}</div>
        <div className="whitespace-pre-wrap leading-6">{message.text}</div>
      </div>
    </div>
  );
}

function TaskTimeline({ task }: { task: TaskRun }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="font-semibold">{task.title}</div>
          <div className="text-xs text-neutral-500">{task.source} · {formatRelativeTime(task.startedAt)}</div>
        </div>
        <StatusTaskBadge status={task.status} />
      </div>
      <div className="space-y-3">
        {task.events.map((event) => (
          <div className="flex gap-3" key={event.id}>
            <span className={cn("mt-1.5 size-2 rounded-full", eventToneClass(event.tone))} />
            <div>
              <div className="text-sm font-medium">{event.stage}</div>
              <div className="text-sm text-neutral-600">{event.message}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TaskHistoryPanel({ activeTaskId, compact = false, tasks }: { activeTaskId?: string; compact?: boolean; tasks: TaskRun[] }) {
  const visible = tasks.filter((task) => task.id !== activeTaskId).slice(0, compact ? 4 : 6);
  if (visible.length === 0) return null;
  return (
    <div className={cn("rounded-lg border border-neutral-200 bg-white", compact ? "p-3" : "p-4")}>
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <History className="size-4 text-cyan-600" />
        任务历史
      </div>
      <div className="grid gap-2">
        {visible.map((task) => (
          <div className="rounded-md border border-neutral-200 bg-neutral-50 p-2" key={task.id}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className={cn("truncate font-medium", compact ? "text-xs" : "text-sm")}>{task.title}</div>
                <div className="mt-1 text-xs text-neutral-500">
                  {task.source} · {formatRelativeTime(task.startedAt)}
                </div>
              </div>
              <StatusTaskBadge status={task.status} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MetricRow({ metric, compact = false }: { metric: Metric; compact?: boolean }) {
  return (
    <div className={cn("grid gap-1", compact ? "text-xs" : "text-sm")}>
      <div className="flex items-center justify-between">
        <span className="font-medium">{metric.label}</span>
        <span className="text-neutral-500">{metric.detail}</span>
      </div>
      <Progress className={metricProgressClass(metric.tone)} value={metric.value} />
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-neutral-500">{label}</span>
      <span className="truncate font-mono text-xs">{value}</span>
    </div>
  );
}

function ToggleRow(props: {
  checked: boolean;
  label: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  const { checked, label, onCheckedChange } = props;
  return (
    <div className="flex items-center justify-between rounded-lg border border-neutral-200 p-3">
      <span className="text-sm font-medium">{label}</span>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

function CapabilityRow({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-neutral-200 px-3 py-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Icon className="size-4 text-neutral-500" />
        {label}
      </div>
      <Badge variant="info">{value}</Badge>
    </div>
  );
}

function InfoPill({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="rounded-lg border border-neutral-200 p-3">
      <Icon className="mb-2 size-4 text-neutral-500" />
      <div className="text-xs text-neutral-500">{label}</div>
      <div className="font-semibold">{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: ServerConnection["status"] }) {
  const variant = status === "online" ? "success" : status === "connecting" ? "warning" : "danger";
  return (
    <Badge variant={variant}>
      {status === "connecting" && <Loader2 className="size-3 animate-spin" />}
      {status}
    </Badge>
  );
}

function StatusTaskBadge({ status }: { status: TaskRun["status"] }) {
  const variant = status === "completed" ? "success" : status === "waiting_approval" ? "warning" : status === "failed" || status === "cancelled" ? "danger" : "info";
  return <Badge variant={variant}>{status}</Badge>;
}

function RiskBadge({ risk }: { risk: RiskLevel }) {
  const variant = risk === "SAFE" ? "success" : risk === "LOW" ? "info" : risk === "WARN-HIGH" ? "warning" : "danger";
  return <Badge variant={variant}>{risk}</Badge>;
}

function paletteKindLabel(kind: "surface" | "tool" | "workflow") {
  if (kind === "surface") return "视图";
  if (kind === "tool") return "工具";
  return "工作流";
}

function ReadinessOverallBadge({ value }: { value: ReleaseReadinessResponse["readiness"]["overall"] }) {
  const variant = value === "pass" ? "success" : value === "fail" ? "danger" : "warning";
  return <Badge variant={variant}>{value}</Badge>;
}

function ReadinessStatusBadge({ status }: { status: ReleaseReadinessStatus }) {
  const variant =
    status === "pass" ? "success" :
    status === "fail" ? "danger" :
    status === "partial" ? "warning" :
    status === "unknown" ? "info" :
    "default";
  return <Badge variant={variant}>{status}</Badge>;
}

function ReleaseGatePanel({ gate }: { gate: ReleaseReadinessResponse["readiness"]["releaseGate"] }) {
  const blocking = gate.blockingReasons ?? [];
  const nextActions = gate.nextActions ?? [];
  return (
    <div
      className={cn(
        "mb-4 rounded-lg border p-3",
        gate.passed ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className={cn("flex items-center gap-2 text-sm font-semibold", gate.passed ? "text-emerald-950" : "text-amber-950")}>
          {gate.passed ? <ShieldCheck className="size-4" /> : <ShieldAlert className="size-4" />}
          Release gate
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant={gate.passed ? "success" : "warning"}>{gate.passed ? "pass" : "blocked"}</Badge>
          <Badge variant="info">exit {gate.exitCode}</Badge>
        </div>
      </div>
      {blocking.length > 0 && (
        <div className="mt-3 grid gap-2">
          {blocking.slice(0, 5).map((reason, index) => (
            <div className="rounded-md border border-amber-200 bg-white/70 px-2 py-1.5 text-xs leading-5 text-amber-950" key={`${index}_${reason}`}>
              {reason}
            </div>
          ))}
          {blocking.length > 5 && (
            <div className="text-xs text-amber-800">+{blocking.length - 5} more blocking reason(s) in the report.</div>
          )}
        </div>
      )}
      {nextActions.length > 0 && (
        <div className={cn("mt-3 rounded-md border bg-white/70 p-2", gate.passed ? "border-emerald-200" : "border-amber-200")}>
          <div className={cn("mb-2 text-xs font-semibold uppercase", gate.passed ? "text-emerald-900" : "text-amber-900")}>
            Next actions
          </div>
          <div className="grid gap-1.5">
            {nextActions.slice(0, 4).map((action, index) => (
              <div
                className={cn("flex gap-1.5 text-xs leading-5", gate.passed ? "text-emerald-950" : "text-amber-950")}
                key={`${index}_${action}`}
              >
                <ChevronRight className="mt-0.5 size-3.5 shrink-0" />
                <span className="min-w-0 break-words">{action}</span>
              </div>
            ))}
            {nextActions.length > 4 && (
              <div className={cn("text-xs", gate.passed ? "text-emerald-800" : "text-amber-800")}>
                +{nextActions.length - 4} more next action(s) in the report.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function filterTools(items: ToolCapability[], query: string) {
  const needle = query.toLowerCase().trim();
  if (!needle) return items;
  return items.filter((tool) =>
    `${tool.name} ${tool.category} ${tool.description} ${tool.args.join(" ")}`.toLowerCase().includes(needle),
  );
}

function groupTools(items: ToolCapability[]) {
  return items.reduce<Record<string, ToolCapability[]>>((groups, tool) => {
    groups[tool.category] = [...(groups[tool.category] ?? []), tool];
    return groups;
  }, {});
}

function taskProgress(task: TaskRun) {
  if (task.status === "completed") return 100;
  if (task.status === "waiting_approval") return 42;
  if (task.status === "cancelled" || task.status === "failed") return 100;
  return Math.min(92, 18 + task.events.length * 16);
}

function eventToneClass(tone: TaskEvent["tone"]) {
  if (tone === "success") return "bg-emerald-500";
  if (tone === "warning") return "bg-amber-500";
  if (tone === "danger") return "bg-rose-500";
  if (tone === "info") return "bg-cyan-500";
  return "bg-neutral-400";
}

function metricProgressClass(tone: Metric["tone"]) {
  if (tone === "success") return "[&>div]:bg-emerald-500";
  if (tone === "warning") return "[&>div]:bg-amber-500";
  if (tone === "danger") return "[&>div]:bg-rose-500";
  return "[&>div]:bg-cyan-500";
}

function auditToneClass(tone: Metric["tone"]) {
  if (tone === "success") return "border-emerald-200 bg-emerald-50";
  if (tone === "warning") return "border-amber-200 bg-amber-50";
  if (tone === "danger") return "border-rose-200 bg-rose-50";
  return "border-cyan-200 bg-cyan-50";
}

function connectedTargetLabel(server: ServerConnection) {
  if (server.mode === "local") return "localhost";
  const user = server.user ? `${server.user}@` : "";
  return `${user}${compactHost(server.host, server.port)}`;
}

function draftTargetLabel(server: ServerConnection) {
  if (server.mode === "local") return "localhost";
  if (!server.host.trim()) return "待填写 host";
  const user = server.user ? `${server.user}@` : "";
  return `${user}${compactHost(server.host, server.port)}`;
}

function recentCredentialLabel(connection: RecentConnection) {
  if (connection.keyFile.trim()) return `key · ${connection.keyFile}`;
  return "agent / 手输密码";
}

function readinessDotClass(state: ReadinessState) {
  if (state === "done") return "bg-emerald-500";
  if (state === "current") return "bg-cyan-500";
  return "bg-amber-500";
}

function buildReadinessItems(
  apiUrl: string,
  apiConfigured: boolean,
  apiStatus: ApiStatus,
  server: ServerConnection,
  toolCount: number,
  workflowCount: number,
): ReadinessItem[] {
  const apiReachable = apiStatus === "ready";
  const targetOnline = server.status === "online" && Boolean(server.id);
  const catalogReady = toolCount > 0 || workflowCount > 0;
  return [
    {
      label: "API 地址",
      detail: apiConfigured ? normalizeApiUrl(apiUrl) || "已配置" : "填写 Web API URL",
      state: apiConfigured ? "done" : "current",
    },
    {
      label: "API 可达",
      detail: apiReachable ? `${toolCount} 工具 · ${workflowCount} 工作流` : apiStatus === "loading" ? "正在刷新 /overview" : "等待后端响应",
      state: apiReachable ? "done" : apiConfigured ? "current" : "blocked",
    },
    {
      label: "执行目标",
      detail: targetOnline ? compactHost(server.host, server.port) : "连接 SSH 或本机目标",
      state: targetOnline ? "done" : apiReachable ? "current" : "blocked",
    },
    {
      label: "可执行目录",
      detail: catalogReady ? "工具和 workflow 已载入" : "等待真实后端目录",
      state: catalogReady && targetOnline ? "done" : targetOnline ? "current" : "blocked",
    },
  ];
}

function getConnectDisabledReason(apiReady: boolean, draftServer: ServerConnection, status: ServerConnection["status"]) {
  if (status === "connecting") return "正在连接目标，请等待后端返回。";
  if (!apiReady) return "请先让 SysDialogue Web API 处于可用状态。";
  if (draftServer.mode === "ssh" && !draftServer.host.trim()) return "SSH 模式需要填写 Host。";
  return "";
}

function draftServerForMode(current: ServerConnection, mode: ServerConnection["mode"]): ServerConnection {
  if (mode === "local") {
    return localConnectionDraft(current);
  }
  return sshConnectionDraft(current.mode === "local" ? { safetyProfile: current.safetyProfile } : current);
}

function connectionRequestFromDraft(draft: ServerConnection, safetyProfile: SafetyProfile): ServerConnection {
  if (draft.mode === "local") {
    return {
      ...localConnectionDraft(draft),
      id: draft.id,
      status: draft.status,
      safetyProfile,
    };
  }
  return {
    ...draft,
    host: draft.host.trim(),
    port: Number(draft.port) || 22,
    user: draft.user.trim(),
    keyFile: draft.keyFile.trim(),
    password: draft.password ?? "",
    sudoPassword: draft.sudoPassword ?? "",
    safetyProfile,
  };
}

function localConnectionDraft(current?: Partial<ServerConnection>): ServerConnection {
  return {
    ...disconnectedServer,
    id: current?.id ?? "",
    name: "Local executor",
    mode: "local",
    host: "localhost",
    port: 0,
    user: "",
    keyFile: "",
    password: "",
    sudoPassword: "",
    fingerprint: "",
    status: current?.status === "connecting" ? "connecting" : "offline",
    latencyMs: 0,
    distro: "",
    kernel: "",
    safetyProfile: normalizeSafetyProfile(current?.safetyProfile),
    lastSeen: new Date(0),
  };
}

function sshConnectionDraft(current?: Partial<ServerConnection>): ServerConnection {
  const host = typeof current?.host === "string" && current.host !== "localhost" ? current.host.trim() : "";
  const user = typeof current?.user === "string" ? current.user.trim() : "";
  return {
    ...disconnectedServer,
    ...current,
    id: current?.id ?? "",
    name: host && user ? `${user}@${host}` : "未连接",
    mode: "ssh",
    host,
    port: Number(current?.port) || 22,
    user,
    keyFile: typeof current?.keyFile === "string" ? current.keyFile.trim() : "",
    password: "",
    sudoPassword: "",
    fingerprint: "",
    status: current?.status === "connecting" ? "connecting" : "offline",
    latencyMs: 0,
    distro: "",
    kernel: "",
    safetyProfile: normalizeSafetyProfile(current?.safetyProfile),
    lastSeen: new Date(0),
  };
}

function auditStats(records: AuditRecord[]) {
  return {
    total: records.length,
    commands: records.filter((record) => record.type === "command_trace").length,
    risky: records.filter((record) => record.risk === "WARN-HIGH" || record.risk === "HARD-BLOCK").length,
    workflows: records.filter((record) => record.type === "workflow_step").length,
  };
}

function filterAuditRecords(
  records: AuditRecord[],
  query: string,
  typeFilter: AuditTypeFilter,
  riskFilter: AuditRiskFilter,
) {
  const needle = query.trim().toLowerCase();
  return records.filter((record) => {
    if (typeFilter !== "all" && record.type !== typeFilter) return false;
    if (riskFilter !== "all" && record.risk !== riskFilter) return false;
    if (!needle) return true;
    return [
      record.type,
      record.target,
      record.result,
      record.risk,
      record.ruleIds.join(" "),
      formatRelativeTime(record.time),
    ].join(" ").toLowerCase().includes(needle);
  });
}

function auditRecordKey(record: AuditRecord, index: number) {
  return `${record.id || "audit"}-${index}`;
}

function isOpenTask(task: TaskRun) {
  return task.status === "running" || task.status === "waiting_approval";
}

function recentTerminalCommands(lines: TerminalLine[]) {
  const seen = new Set<string>();
  const commands: string[] = [];
  for (const line of [...lines].reverse()) {
    if (line.kind !== "input") continue;
    const command = line.text.trim();
    if (!command || seen.has(command)) continue;
    seen.add(command);
    commands.push(command);
    if (commands.length >= 6) break;
  }
  return commands;
}

function latestTerminalOutput(lines: TerminalLine[]) {
  const lastInputIndex = lines.map((line) => line.kind).lastIndexOf("input");
  const outputLines = lines
    .slice(lastInputIndex + 1)
    .filter((line) => line.kind !== "input")
    .map((line) => line.text);
  return outputLines.join("\n").trim();
}

function formatTerminalTranscript(lines: TerminalLine[]) {
  return lines.map((line) => `${line.kind === "input" ? "$" : ">"} ${line.text}`).join("\n").trim();
}

async function copyText(text: string) {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    try {
      await Promise.race([
        navigator.clipboard.writeText(text),
        new Promise<never>((_, reject) => {
          window.setTimeout(() => reject(new Error("Clipboard API timeout")), 700);
        }),
      ]);
      return;
    } catch {
      // Some embedded browsers expose the Clipboard API but do not grant it promptly.
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!copied) throw new Error("无法写入剪贴板");
}

function defaultAcceptanceDrillForm(workflowName: AcceptanceDrillWorkflow): AcceptanceDrillFormState {
  return {
    workflowName,
    argsText: JSON.stringify(defaultAcceptanceDrillArgs(workflowName), null, 2),
    approvalPhrase: "",
    impact: "",
    rollback: "",
    verification: "",
    disposableTarget: false,
  };
}

function defaultAcceptanceDrillArgs(workflowName: AcceptanceDrillWorkflow) {
  if (workflowName === "safe_config_patch") {
    return {
      file_path: "/tmp/sysdialogue-a07.conf",
      search_text: "before",
      replace_text: "after",
      validator: "auto",
    };
  }
  return {
    service_name: "sysdialogue-a07-test",
  };
}

function inputFieldsForTarget(target: RunTarget): InputFieldDefinition[] {
  const rawFields = target.kind === "tool" ? target.item.inputSchema : target.item.inputSchema;
  if (rawFields?.length) return rawFields;
  const names = target.kind === "tool" ? target.item.args : target.item.inputs;
  return names.map((name) => {
    const cleanName = name.replace(/\*$/, "");
    return {
      name: cleanName,
      label: cleanName,
      type: "string",
      required: name.endsWith("*"),
    };
  });
}

function argsToFormValues(fields: InputFieldDefinition[], args: Record<string, unknown>) {
  const values: Record<string, string> = {};
  for (const field of fields) {
    if (!field.name) continue;
    if (Object.prototype.hasOwnProperty.call(args, field.name)) {
      values[field.name] = valueToFormString(args[field.name]);
    } else {
      values[field.name] = "";
    }
  }
  return values;
}

function argsFromFormValues(fields: InputFieldDefinition[], values: Record<string, string>, validate: boolean) {
  const args: Record<string, unknown> = {};
  for (const field of fields) {
    const name = field.name;
    if (!name) continue;
    const raw = values[name] ?? "";
    const type = normalizeFieldType(field.type);
    if (raw === "" && type !== "boolean") {
      if (validate && field.required) {
        throw new Error(`请填写必填参数 ${name}。`);
      }
      continue;
    }
    args[name] = formStringToValue(name, raw, type, validate);
  }
  return args;
}

function missingRequiredFields(fields: InputFieldDefinition[], values: Record<string, string>) {
  return fields
    .filter((field) => {
      if (!field.required || !field.name) return false;
      const type = normalizeFieldType(field.type);
      if (type === "boolean") return false;
      return !hasFilledValue(values[field.name], type);
    })
    .map((field) => field.name);
}

function missingRequiredFieldsFromJson(fields: InputFieldDefinition[], text: string) {
  try {
    const args = parseArgsObject(text);
    return fields
      .filter((field) => {
        if (!field.required || !field.name) return false;
        const type = normalizeFieldType(field.type);
        if (!Object.prototype.hasOwnProperty.call(args, field.name)) return true;
        return !hasFilledJsonValue(args[field.name], type);
      })
      .map((field) => field.name);
  } catch {
    return [];
  }
}

function jsonArgsDraftError(text: string) {
  try {
    parseArgsObject(text);
    return "";
  } catch (error) {
    return errorMessage(error);
  }
}

function hasFilledValue(value: string | undefined, type: string) {
  const raw = value ?? "";
  if (raw.trim() === "") return false;
  if (type === "array" || type === "object") {
    try {
      const parsed = JSON.parse(raw) as unknown;
      return hasFilledJsonValue(parsed, type);
    } catch {
      return true;
    }
  }
  return true;
}

function hasFilledJsonValue(value: unknown, type: string) {
  if (type === "boolean") return typeof value === "boolean";
  if (type === "array") return Array.isArray(value);
  if (type === "object") return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  if (type === "integer" || type === "number") return typeof value === "number" && Number.isFinite(value);
  return value !== undefined && value !== null && String(value).trim() !== "";
}

function defaultArgsForFields(fields: InputFieldDefinition[]) {
  const args: Record<string, unknown> = {};
  for (const field of fields) {
    if (!field.name) continue;
    if (field.default !== undefined) {
      args[field.name] = field.default;
      continue;
    }
    if (!field.required) continue;
    args[field.name] = emptyValueForType(field.type);
  }
  return args;
}

function valueToFormString(value: unknown) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}

function formStringToValue(name: string, raw: string, type: string, validate: boolean) {
  if (type === "boolean") return raw === "true";
  if (type === "integer" || type === "number") {
    const numeric = Number(raw);
    if (!Number.isFinite(numeric)) {
      if (validate) throw new Error(`${name} 必须是数字。`);
      return raw;
    }
    return type === "integer" ? Math.trunc(numeric) : numeric;
  }
  if (type === "array" || type === "object") {
    try {
      const parsed = JSON.parse(raw || (type === "array" ? "[]" : "{}")) as unknown;
      if (type === "array" && !Array.isArray(parsed)) throw new Error();
      if (type === "object" && (!parsed || typeof parsed !== "object" || Array.isArray(parsed))) throw new Error();
      return parsed;
    } catch {
      if (validate) throw new Error(`${name} 必须是有效的 ${type === "array" ? "JSON 数组" : "JSON 对象"}。`);
      return raw;
    }
  }
  return raw;
}

function normalizeFieldType(type: string | undefined) {
  const normalized = (type || "string").toLowerCase();
  if (normalized === "bool") return "boolean";
  if (normalized === "int") return "integer";
  if (normalized === "float") return "number";
  if (normalized === "text") return "string";
  if (normalized === "boolean" || normalized === "integer" || normalized === "number" || normalized === "array" || normalized === "object") {
    return normalized;
  }
  return "string";
}

function emptyValueForType(type: string | undefined) {
  const normalized = normalizeFieldType(type);
  if (normalized === "boolean") return false;
  if (normalized === "integer" || normalized === "number") return 0;
  if (normalized === "array") return [];
  if (normalized === "object") return {};
  return "";
}

function parseArgsObject(value: string): Record<string, unknown> {
  const text = value.trim();
  if (!text) return {};
  const parsed = JSON.parse(text) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("参数必须是 JSON object，例如 {\"path\":\"/var/log\"}。");
  }
  return parsed as Record<string, unknown>;
}

function formatJson(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2);
}

function buildPaletteItems(query: string, tools: ToolCapability[], workflowList: WorkflowDefinition[]) {
  const needle = query.toLowerCase().trim();
  const surfaceItems: PaletteItem[] = navItems.map((item) => ({
    kind: "surface" as const,
    label: item.label,
    value: item.id,
    icon: item.icon,
    detail: "切换视图",
  }));
  const toolItems: PaletteItem[] = tools.map((tool) => ({
    kind: "tool" as const,
    label: tool.name,
    value: tool.name,
    icon: categoryIcons[tool.category] ?? Wrench,
    risk: tool.risk,
    detail: `${tool.category} · ${tool.readOnly ? "read only" : "mutable"} · ${tool.description}`,
  }));
  const workflowItems: PaletteItem[] = workflowList.map((workflow) => ({
    kind: "workflow" as const,
    label: workflow.label,
    value: workflow.name,
    icon: Workflow,
    risk: workflow.risk,
    detail: `${workflow.name} · ${workflow.steps} steps · ${workflow.description}`,
  }));
  const allItems = [...surfaceItems, ...toolItems, ...workflowItems];
  if (!needle) return allItems.slice(0, 18);
  return allItems.filter((item) => `${item.label} ${item.value} ${item.detail}`.toLowerCase().includes(needle)).slice(0, 18);
}

function errorMessage(error: unknown) {
  if (error instanceof MissingApiConfigurationError) {
    return "未配置 SysDialogue Web API URL，无法连接真实后端。";
  }
  if (error instanceof Error) return error.message;
  return "操作失败。";
}

function loadRuntimeConfig(): RuntimeConfig {
  if (typeof window === "undefined") return defaultRuntimeConfig;
  try {
    const raw = window.localStorage.getItem(RUNTIME_CONFIG_STORAGE_KEY);
    if (!raw) return defaultRuntimeConfig;
    const parsed = JSON.parse(raw) as Partial<RuntimeConfig>;
    return {
      ...defaultRuntimeConfig,
      ...parsed,
      apiUrl: typeof parsed.apiUrl === "string" ? parsed.apiUrl : defaultRuntimeConfig.apiUrl,
      maxIterations: clampNumber(parsed.maxIterations, 20, 300, defaultRuntimeConfig.maxIterations),
      safetyProfile: normalizeSafetyProfile(parsed.safetyProfile),
      streamEvents: typeof parsed.streamEvents === "boolean" ? parsed.streamEvents : defaultRuntimeConfig.streamEvents,
    };
  } catch {
    return defaultRuntimeConfig;
  }
}

function saveRuntimeConfig(config: RuntimeConfig) {
  if (typeof window === "undefined") return;
  const persisted: RuntimeConfig = {
    ...config,
    apiUrl: normalizeApiUrl(config.apiUrl),
    maxIterations: clampNumber(config.maxIterations, 20, 300, defaultRuntimeConfig.maxIterations),
    safetyProfile: normalizeSafetyProfile(config.safetyProfile),
  };
  window.localStorage.setItem(RUNTIME_CONFIG_STORAGE_KEY, JSON.stringify(persisted));
}

function loadServerDraft(): ServerConnection {
  if (typeof window === "undefined") return disconnectedServer;
  try {
    const raw = window.localStorage.getItem(SERVER_DRAFT_STORAGE_KEY);
    if (!raw) return disconnectedServer;
    const parsed = JSON.parse(raw) as Partial<ServerConnection>;
    return parsed.mode === "local" ? localConnectionDraft(parsed) : sshConnectionDraft(parsed);
  } catch {
    return disconnectedServer;
  }
}

function saveServerDraft(server: ServerConnection) {
  if (typeof window === "undefined") return;
  const persisted: Partial<ServerConnection> = server.mode === "local"
    ? {
        mode: "local",
        host: "localhost",
        port: 0,
        user: "",
        keyFile: "",
        safetyProfile: server.safetyProfile,
      }
    : {
        mode: "ssh",
        host: server.host,
        port: Number(server.port) || 22,
        user: server.user,
        keyFile: server.keyFile,
        safetyProfile: server.safetyProfile,
      };
  window.localStorage.setItem(SERVER_DRAFT_STORAGE_KEY, JSON.stringify(persisted));
}

function loadRecentConnections(): RecentConnection[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENT_CONNECTIONS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item) => normalizeRecentConnection(item))
      .filter((item): item is RecentConnection => Boolean(item))
      .slice(0, 6);
  } catch {
    return [];
  }
}

function saveRecentConnections(connections: RecentConnection[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(RECENT_CONNECTIONS_STORAGE_KEY, JSON.stringify(connections));
}

function rememberRecentConnection(current: RecentConnection[], server: ServerConnection) {
  const nextConnection = toRecentConnection(server);
  if (!nextConnection) return current;
  const next = [nextConnection, ...current.filter((item) => item.id !== nextConnection.id)].slice(0, 6);
  saveRecentConnections(next);
  return next;
}

function toRecentConnection(server: ServerConnection): RecentConnection | null {
  if (server.mode !== "ssh" || !server.host.trim()) return null;
  return {
    id: recentConnectionId(server.mode, server.host, server.port, server.user),
    mode: "ssh",
    host: server.host.trim(),
    port: Number(server.port) || 22,
    user: server.user.trim(),
    keyFile: server.keyFile.trim(),
    safetyProfile: normalizeSafetyProfile(server.safetyProfile),
    lastUsed: new Date().toISOString(),
  };
}

function normalizeRecentConnection(value: unknown): RecentConnection | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<RecentConnection>;
  if (item.mode !== "ssh" || typeof item.host !== "string" || !item.host.trim()) return null;
  const port = Number(item.port) || 22;
  const user = typeof item.user === "string" ? item.user : "";
  return {
    id: typeof item.id === "string" && item.id ? item.id : recentConnectionId("ssh", item.host, port, user),
    mode: "ssh",
    host: item.host.trim(),
    port,
    user,
    keyFile: typeof item.keyFile === "string" ? item.keyFile : "",
    safetyProfile: normalizeSafetyProfile(item.safetyProfile),
    lastUsed: typeof item.lastUsed === "string" ? item.lastUsed : new Date(0).toISOString(),
  };
}

function recentConnectionId(mode: ConnectionMode, host: string, port: number, user: string) {
  return `${mode}:${user.trim().toLowerCase()}@${host.trim().toLowerCase()}:${Number(port) || 22}`;
}

function normalizeSafetyProfile(value: unknown): SafetyProfile {
  return value === "operator" || value === "break_glass" || value === "standard"
    ? value
    : "standard";
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(min, Math.min(max, numeric));
}
