import type {
  AuditRecord,
  ChatMessage,
  OverviewPayload,
  AcceptanceChecklistPayload,
  AcceptanceBundleResponse,
  AcceptanceMutationDrillRequest,
  AcceptanceRunnerResponse,
  ReleaseReadinessResponse,
  RuntimeConfig,
  ServerConnection,
  TaskEvent,
  TaskExecutionResponse,
  TaskRun,
  TerminalExecutionResponse,
  ToolCapability,
  WorkflowDefinition,
} from "@/lib/types";

export interface SysDialogueApi {
  getOverview(): Promise<OverviewPayload>;
  connectServer(connection: ServerConnection): Promise<ServerConnection>;
  runTask(serverId: string, goal: string): Promise<TaskExecutionResponse>;
  resolveApproval(approvalId: string, approved: boolean): Promise<TaskExecutionResponse>;
  runCommand(serverId: string, command: string): Promise<TerminalExecutionResponse>;
  runTool(serverId: string, tool: ToolCapability, args?: Record<string, unknown>): Promise<TaskExecutionResponse>;
  runWorkflow(serverId: string, workflow: WorkflowDefinition, args?: Record<string, unknown>): Promise<TaskExecutionResponse>;
  exportAudit(format: "jsonl" | "replay"): Promise<{ fileName: string; content: BlobPart }>;
  getAcceptanceChecklist(serverId?: string): Promise<AcceptanceChecklistPayload>;
  getAcceptanceRunner(serverId?: string, mode?: "safe-preflight" | "model-check" | "conversation-check" | "ui-review" | "read-only-collect" | "recovery-drill"): Promise<AcceptanceRunnerResponse>;
  runAcceptanceMutationDrill(request: AcceptanceMutationDrillRequest): Promise<AcceptanceRunnerResponse>;
  buildReleaseReadiness(content: string, source?: string): Promise<ReleaseReadinessResponse>;
  buildAcceptanceBundle(content: string, source?: string, serverId?: string): Promise<AcceptanceBundleResponse>;
}

export class MissingApiConfigurationError extends Error {
  constructor() {
    super("未配置 SysDialogue Web API URL，无法连接真实后端。");
    this.name = "MissingApiConfigurationError";
  }
}

class HttpSysDialogueApi implements SysDialogueApi {
  constructor(
    private readonly baseUrl: string,
    private readonly runtimeConfig: RuntimeConfig,
  ) {}

  async getOverview() {
    const payload = await this.request<WireOverviewPayload>("/overview");
    return normalizeOverview(payload);
  }

  async connectServer(connection: ServerConnection) {
    const payload = await this.request<WireServerConnection>("/connections", {
      method: "POST",
      body: JSON.stringify({ connection, runtimeConfig: this.runtimeConfig }),
    });
    return normalizeServer(payload);
  }

  async runTask(serverId: string, goal: string) {
    const payload = await this.request<WireTaskExecutionResponse>("/tasks", {
      method: "POST",
      body: JSON.stringify({ serverId, goal, runtimeConfig: this.runtimeConfig }),
    });
    return normalizeTaskExecution(payload);
  }

  async resolveApproval(approvalId: string, approved: boolean) {
    const payload = await this.request<WireTaskExecutionResponse>(`/approvals/${encodeURIComponent(approvalId)}`, {
      method: "POST",
      body: JSON.stringify({ approved, runtimeConfig: this.runtimeConfig }),
    });
    return normalizeTaskExecution(payload);
  }

  async runCommand(serverId: string, command: string) {
    const payload = await this.request<string[] | WireTerminalExecutionResponse>("/terminal/exec", {
      method: "POST",
      body: JSON.stringify({ serverId, command, runtimeConfig: this.runtimeConfig }),
    });
    if (Array.isArray(payload)) return { lines: payload };
    return {
      lines: Array.isArray(payload.lines) ? payload.lines.map(String) : [],
      audit: normalizeAuditList(payload.audit),
    };
  }

  async runTool(serverId: string, tool: ToolCapability, args: Record<string, unknown> = {}) {
    const payload = await this.request<WireTaskExecutionResponse>("/tools/run", {
      method: "POST",
      body: JSON.stringify({ serverId, name: tool.name, args, runtimeConfig: this.runtimeConfig }),
    });
    return normalizeTaskExecution(payload);
  }

  async runWorkflow(serverId: string, workflow: WorkflowDefinition, args: Record<string, unknown> = {}) {
    const payload = await this.request<WireTaskExecutionResponse>("/workflows/run", {
      method: "POST",
      body: JSON.stringify({ serverId, name: workflow.name, args, runtimeConfig: this.runtimeConfig }),
    });
    return normalizeTaskExecution(payload);
  }

  async exportAudit(format: "jsonl" | "replay") {
    const payload = await this.request<{ fileName: string; content: string; encoding?: "utf-8" | "base64" }>(`/audit/export?format=${format}`);
    return {
      fileName: payload.fileName,
      content: payload.encoding === "base64" ? base64ToBytes(payload.content) : payload.content,
    };
  }

  async getAcceptanceChecklist(serverId = "") {
    const suffix = serverId ? `?serverId=${encodeURIComponent(serverId)}` : "";
    return this.request<AcceptanceChecklistPayload>(`/release/acceptance${suffix}`);
  }

  async getAcceptanceRunner(serverId = "", mode: "safe-preflight" | "model-check" | "conversation-check" | "ui-review" | "read-only-collect" | "recovery-drill" = "safe-preflight") {
    const params = new URLSearchParams();
    if (serverId) params.set("serverId", serverId);
    if (mode !== "safe-preflight") params.set("mode", mode);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return this.request<AcceptanceRunnerResponse>(`/release/acceptance-runner${suffix}`);
  }

  async runAcceptanceMutationDrill(request: AcceptanceMutationDrillRequest) {
    return this.request<AcceptanceRunnerResponse>("/release/mutation-drill", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  async buildReleaseReadiness(content: string, source = "web-console") {
    return this.request<ReleaseReadinessResponse>("/release/readiness", {
      method: "POST",
      body: JSON.stringify({ content, source }),
    });
  }

  async buildAcceptanceBundle(content: string, source = "web-console", serverId = "") {
    const payload = await this.request<Omit<AcceptanceBundleResponse, "content"> & { content: string; encoding?: "base64" }>("/release/acceptance-bundle", {
      method: "POST",
      body: JSON.stringify({ content, source, serverId }),
    });
    return {
      ...payload,
      content: payload.encoding === "base64" ? base64ToBytes(payload.content) : payload.content,
    };
  }

  private async request<T>(pathName: string, init: RequestInit = {}) {
    const response = await fetch(`${this.baseUrl}${pathName}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        ...(init.headers ?? {}),
      },
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`SysDialogue API ${response.status}${detail ? `: ${detail}` : ""}`);
    }
    return response.json() as Promise<T>;
  }
}

class UnconfiguredSysDialogueApi implements SysDialogueApi {
  async getOverview(): Promise<OverviewPayload> {
    throw new MissingApiConfigurationError();
  }

  async connectServer(): Promise<ServerConnection> {
    throw new MissingApiConfigurationError();
  }

  async runTask(): Promise<TaskExecutionResponse> {
    throw new MissingApiConfigurationError();
  }

  async resolveApproval(): Promise<TaskExecutionResponse> {
    throw new MissingApiConfigurationError();
  }

  async runCommand(): Promise<TerminalExecutionResponse> {
    throw new MissingApiConfigurationError();
  }

  async runTool(): Promise<TaskExecutionResponse> {
    throw new MissingApiConfigurationError();
  }

  async runWorkflow(): Promise<TaskExecutionResponse> {
    throw new MissingApiConfigurationError();
  }

  async exportAudit(): Promise<{ fileName: string; content: BlobPart }> {
    throw new MissingApiConfigurationError();
  }

  async getAcceptanceChecklist(): Promise<AcceptanceChecklistPayload> {
    throw new MissingApiConfigurationError();
  }

  async getAcceptanceRunner(_serverId = "", _mode: "safe-preflight" | "model-check" | "conversation-check" | "ui-review" | "read-only-collect" | "recovery-drill" = "safe-preflight"): Promise<AcceptanceRunnerResponse> {
    throw new MissingApiConfigurationError();
  }

  async runAcceptanceMutationDrill(): Promise<AcceptanceRunnerResponse> {
    throw new MissingApiConfigurationError();
  }

  async buildReleaseReadiness(): Promise<ReleaseReadinessResponse> {
    throw new MissingApiConfigurationError();
  }

  async buildAcceptanceBundle(): Promise<AcceptanceBundleResponse> {
    throw new MissingApiConfigurationError();
  }
}

type WireDate = string | Date | undefined;
type WireAuditRecord = Omit<AuditRecord, "time"> & { time?: WireDate; ts?: WireDate };
type WireChatMessage = Omit<ChatMessage, "at"> & { at?: WireDate };
type WireTaskEvent = Omit<TaskEvent, "at"> & { at?: WireDate; ts?: WireDate };
type WireTaskRun = Omit<TaskRun, "startedAt" | "finishedAt" | "events"> & {
  startedAt?: WireDate;
  started_at?: WireDate;
  finishedAt?: WireDate;
  finished_at?: WireDate;
  events?: WireTaskEvent[];
};
type WireServerConnection = Omit<ServerConnection, "lastSeen"> & {
  lastSeen?: WireDate;
  last_seen?: WireDate;
};
type WireOverviewPayload = Omit<OverviewPayload, "audit"> & { audit?: WireAuditRecord[] };
type WireTaskExecutionResponse = Omit<TaskExecutionResponse, "task" | "messages" | "events" | "audit"> & {
  task?: WireTaskRun;
  messages?: WireChatMessage[];
  events?: WireTaskEvent[];
  audit?: WireAuditRecord[];
};
type WireTerminalExecutionResponse = {
  lines?: unknown[];
  audit?: WireAuditRecord[];
};

export function createSysDialogueApi(config: RuntimeConfig): SysDialogueApi {
  const apiBase = normalizeApiUrl(config.apiUrl);
  return apiBase ? new HttpSysDialogueApi(apiBase, config) : new UnconfiguredSysDialogueApi();
}

export function normalizeApiUrl(value: string) {
  return value.trim().replace(/\/$/, "");
}

function normalizeOverview(payload: WireOverviewPayload): OverviewPayload {
  return {
    tools: Array.isArray(payload.tools) ? payload.tools : [],
    workflows: Array.isArray(payload.workflows) ? payload.workflows : [],
    metrics: Array.isArray(payload.metrics) ? payload.metrics : [],
    audit: normalizeAuditList(payload.audit),
  };
}

function normalizeServer(payload: WireServerConnection): ServerConnection {
  const server: ServerConnection = {
    ...payload,
    lastSeen: toDate(payload.lastSeen ?? payload.last_seen),
  };
  if (server.mode !== "local") return server;
  return {
    ...server,
    host: server.host || "localhost",
    port: 0,
    keyFile: "",
    password: "",
    sudoPassword: "",
  };
}

function normalizeTaskExecution(payload: WireTaskExecutionResponse): TaskExecutionResponse {
  return {
    ...payload,
    task: payload.task ? normalizeTask(payload.task) : undefined,
    messages: normalizeMessages(payload.messages),
    events: normalizeEvents(payload.events),
    audit: normalizeAuditList(payload.audit),
  };
}

function normalizeTask(task: WireTaskRun): TaskRun {
  return {
    ...task,
    startedAt: toDate(task.startedAt ?? task.started_at),
    finishedAt: task.finishedAt || task.finished_at ? toDate(task.finishedAt ?? task.finished_at) : undefined,
    events: normalizeEvents(task.events),
  };
}

function normalizeMessages(messages: WireChatMessage[] | undefined): ChatMessage[] {
  if (!Array.isArray(messages)) return [];
  return messages.map((message) => ({
    ...message,
    at: toDate(message.at),
  }));
}

function normalizeEvents(events: WireTaskEvent[] | undefined): TaskEvent[] {
  if (!Array.isArray(events)) return [];
  return events.map((event) => ({
    ...event,
    at: toDate(event.at ?? event.ts),
  }));
}

function normalizeAuditList(records: WireAuditRecord[] | undefined): AuditRecord[] {
  if (!Array.isArray(records)) return [];
  return records.map((record) => ({
    ...record,
    time: toDate(record.time ?? record.ts),
  }));
}

function toDate(value: WireDate): Date {
  if (value instanceof Date) return value;
  if (typeof value === "string" && value) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  return new Date();
}

function base64ToBytes(value: string): ArrayBuffer {
  const binary = window.atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer as ArrayBuffer;
}
