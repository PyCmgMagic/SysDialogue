import type { ServerConnection } from "@/lib/types";

export const disconnectedServer: ServerConnection = {
  id: "",
  name: "未连接",
  mode: "ssh",
  host: "",
  port: 22,
  user: "",
  keyFile: "",
  fingerprint: "",
  status: "offline",
  latencyMs: 0,
  distro: "",
  kernel: "",
  safetyProfile: "standard",
  lastSeen: new Date(0),
};
