import type { LucideIcon } from "lucide-react";

export type Surface =
  | "workbench"
  | "servers"
  | "tools"
  | "workflows"
  | "audit"
  | "release"
  | "settings";

export type ConnectionMode = "local" | "ssh";
export type ServerStatus = "offline" | "connecting" | "online" | "locked";
export type RiskLevel = "SAFE" | "LOW" | "WARN-HIGH" | "HARD-BLOCK";
export type TaskStatus = "running" | "waiting_approval" | "completed" | "failed" | "cancelled";
export type MessageRole = "user" | "assistant" | "system";
export type ApiStatus = "missing_config" | "loading" | "ready" | "error";
export type SafetyProfile = "standard" | "operator" | "break_glass";
export type ReleaseReadinessStatus = "pass" | "partial" | "fail" | "missing" | "unknown";

export interface NavigationItem {
  id: Surface;
  label: string;
  icon: LucideIcon;
}

export interface ServerConnection {
  id: string;
  name: string;
  mode: ConnectionMode;
  host: string;
  port: number;
  user: string;
  keyFile: string;
  password?: string;
  sudoPassword?: string;
  fingerprint: string;
  status: ServerStatus;
  latencyMs: number;
  distro: string;
  kernel: string;
  safetyProfile: SafetyProfile;
  lastSeen: Date;
}

export interface RuntimeConfig {
  apiUrl: string;
  model: string;
  openaiBaseUrl: string;
  maxIterations: number;
  workflowsDir: string;
  safetyProfile: SafetyProfile;
  streamEvents: boolean;
}

export interface Metric {
  label: string;
  value: number;
  detail: string;
  tone: "success" | "warning" | "danger" | "info";
}

export interface InputFieldDefinition {
  name: string;
  label?: string;
  type?: string;
  required?: boolean;
  default?: unknown;
  description?: string;
}

export interface ToolCapability {
  name: string;
  category: string;
  description: string;
  risk: RiskLevel;
  readOnly: boolean;
  args: string[];
  inputSchema?: InputFieldDefinition[];
}

export interface WorkflowDefinition {
  name: string;
  label: string;
  description: string;
  risk: RiskLevel;
  steps: number;
  inputs: string[];
  inputSchema?: InputFieldDefinition[];
}

export interface TaskEvent {
  id: string;
  stage: string;
  message: string;
  tone: "neutral" | "success" | "warning" | "danger" | "info";
  at: Date;
}

export interface TaskRun {
  id: string;
  title: string;
  source: "agent" | "tool" | "workflow" | "terminal";
  status: TaskStatus;
  startedAt: Date;
  finishedAt?: Date;
  events: TaskEvent[];
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
  at: Date;
  taskId?: string;
}

export interface AuditRecord {
  id: string;
  time: Date;
  type: "decision" | "command_trace" | "workflow_step" | "env_profile" | "final";
  target: string;
  result: string;
  risk: RiskLevel;
  ruleIds: string[];
}

export interface ApprovalRequest {
  id: string;
  taskId: string;
  tool: string;
  reason: string;
  risk: RiskLevel;
  rollback: string;
}

export interface TerminalLine {
  id: string;
  kind: "input" | "output" | "error" | "system";
  text: string;
  at: Date;
}

export interface OverviewPayload {
  tools: ToolCapability[];
  workflows: WorkflowDefinition[];
  audit: AuditRecord[];
  metrics: Metric[];
}

export interface TaskExecutionResponse {
  task?: TaskRun;
  messages?: ChatMessage[];
  events?: TaskEvent[];
  reply?: string;
  audit?: AuditRecord[];
  approval?: ApprovalRequest | null;
}

export interface TerminalExecutionResponse {
  lines: string[];
  audit?: AuditRecord[];
}

export interface AcceptanceChecklistPayload {
  text: string;
  target: string;
  connected: boolean;
}

export interface AcceptanceRunnerStep {
  stepId: string;
  gate: string;
  title: string;
  command: string;
  expectedEvidence: string;
  releaseNote: string;
  mode: "auto-local" | "auto-model" | "auto-conversation" | "auto-read-only" | "auto-replay" | "auto-ui" | "manual" | "operator-approved";
  status: ReleaseReadinessStatus;
  evidence: string;
  manualAction: string;
}

export interface AcceptanceRunnerResponse {
  artifact: string;
  target: string;
  connected: boolean;
  run: {
    target: string;
    mode: "safe-preflight" | "model-check" | "conversation-check" | "ui-review" | "read-only-collect" | "recovery-drill" | "replay-export" | "operator-approved-drill";
    steps: AcceptanceRunnerStep[];
    notes: string[];
  };
  report: string;
  readiness: ReleaseReadinessPayload;
}

export interface AcceptanceMutationDrillRequest {
  serverId?: string;
  workflowName: "service_restart" | "safe_config_patch";
  args: Record<string, unknown>;
  approvalPhrase: string;
  impact: string;
  rollback: string;
  verification: string;
  disposableTarget: boolean;
}

export interface ReleaseReadinessCheck {
  stepId: string;
  title: string;
  gate: string;
  status: ReleaseReadinessStatus;
  evidence: string;
  source: string;
}

export interface ReleaseReadinessArtifact {
  kind: string;
  path: string;
  detail: string;
}

export interface ReleaseReadinessPayload {
  source: string;
  overall: "pass" | "partial" | "fail";
  counts: Record<ReleaseReadinessStatus, number>;
  checks: ReleaseReadinessCheck[];
  artifacts: ReleaseReadinessArtifact[];
  notes: string[];
  releaseGate: {
    passed: boolean;
    exitCode: number;
    blockingReasons: string[];
    nextActions: string[];
  };
}

export interface ReleaseReadinessResponse {
  report: string;
  readiness: ReleaseReadinessPayload;
}

export interface AcceptanceBundleResponse {
  fileName: string;
  content: BlobPart;
  report: string;
  readiness: ReleaseReadinessPayload;
  manifest: string[];
}
