const POLL_MS = 1200;

let activeSessionId = window.SYSDIALOGUE_INITIAL_SESSION || "default";
let activeTab = "tasks";
let lastState = null;
let apiConfigTouched = false;
let targetFormTouched = false;
let targetProfiles = [];
const collapsedSessionGroups = new Set();

const $ = (id) => document.getElementById(id);

const STATUS_LABEL = {
  ready: "就绪",
  running: "执行中",
  waiting_confirm: "待确认",
  waiting_input: "待输入",
  cancelling: "取消中",
  interrupted: "已中断",
  failed: "失败",
  completed: "完成",
  blocked: "已阻塞",
  cancelled: "已取消",
  rolled_back: "已回滚",
  partial: "部分完成",
  need_info: "需要信息",
};

const CANCEL_STATUSES = new Set(["running", "waiting_confirm", "waiting_input", "cancelling"]);
const INPUT_STATUSES = new Set([
  "ready",
  "waiting_input",
  "completed",
  "failed",
  "blocked",
  "cancelled",
  "interrupted",
  "partial",
  "rolled_back",
  "need_info",
]);

async function fetchJson(url, method = "GET", body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const response = await fetch(url, opts);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function api(path, method = "GET", body) {
  return fetchJson(`/api/session/${encodeURIComponent(activeSessionId)}${path}`, method, body);
}

function rootApi(path, method = "GET", body) {
  return fetchJson(`/api${path}`, method, body);
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function trimText(value, max = 180) {
  const text = String(value ?? "").trim();
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function fmtTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 19);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function setActiveSession(sessionId) {
  activeSessionId = sessionId || "default";
  apiConfigTouched = false;
  targetFormTouched = false;
  $("session-subtitle").textContent = `当前会话：${activeSessionId}`;
  refreshAll();
}

async function refreshSessions() {
  try {
    const payload = await rootApi("/sessions");
    renderSessions(payload.sessions || []);
  } catch (error) {
    showLocalError(`会话列表加载失败：${error.message}`);
  }
}

function renderSessions(sessions) {
  const list = $("session-list");
  if (!sessions.length) {
    list.innerHTML = `<div class="session-item active"><span class="session-dot ready"></span><span class="session-title">默认 Web 会话</span><span class="session-meta">ready</span></div>`;
    return;
  }
  const groups = groupSessionsByTarget(sessions);
  list.innerHTML = groups.map((group) => `
    <div class="session-group${collapsedSessionGroups.has(group.key) ? " collapsed" : ""}">
      <button class="session-group-title" type="button" data-session-group="${esc(group.key)}">
        <span>${esc(group.label)}</span>
        <small>${group.sessions.length}</small>
      </button>
      <div class="session-group-items">${group.sessions.map(renderSessionItem).join("")}</div>
    </div>
  `).join("");
  list.querySelectorAll("[data-session-group]").forEach((item) => {
    item.addEventListener("click", () => {
      const key = item.dataset.sessionGroup || "";
      if (collapsedSessionGroups.has(key)) collapsedSessionGroups.delete(key);
      else collapsedSessionGroups.add(key);
      renderSessions(sessions);
    });
  });
  list.querySelectorAll("[data-session]").forEach((item) => {
    item.addEventListener("click", () => setActiveSession(item.dataset.session));
  });
}

function groupSessionsByTarget(sessions) {
  const buckets = new Map();
  sessions.forEach((session) => {
    const mode = session.target_mode === "ssh" ? "ssh" : "local";
    const label = mode === "ssh"
      ? (session.target_group || session.target_summary || "SSH 目标")
      : "本机会话";
    const key = `${mode}:${label}`;
    if (!buckets.has(key)) {
      buckets.set(key, { key, mode, label, sessions: [] });
    }
    buckets.get(key).sessions.push(session);
  });
  return Array.from(buckets.values()).sort((a, b) => {
    if (a.mode !== b.mode) return a.mode === "local" ? -1 : 1;
    return a.label.localeCompare(b.label, "zh-CN");
  });
}

function renderSessionItem(session) {
  const active = session.session_id === activeSessionId ? " active" : "";
  const title = esc(session.title || session.session_id);
  const status = esc(STATUS_LABEL[session.status] || session.status || "unknown");
  const last = esc(trimText(session.last_user_message || session.session_id, 42));
  const target = esc(session.target_summary || "");
  return `
    <div class="session-item${active}" data-session="${esc(session.session_id)}">
      <span class="session-dot ${esc(session.status || "ready")}"></span>
      <span class="session-title" title="${last}${target ? ` · ${target}` : ""}">${title}</span>
      <span class="session-meta">${status}</span>
    </div>`;
}

async function createSession() {
  try {
    const payload = await rootApi("/sessions", "POST", {});
    setActiveSession(payload.session.session_id);
  } catch (error) {
    showLocalError(`新建会话失败：${error.message}`);
  }
}

async function poll() {
  try {
    const state = await api("/state");
    lastState = state;
    renderState(state);
  } catch (error) {
    showLocalError(`状态刷新失败：${error.message}`);
  }
}

function renderState(state) {
  $("session-subtitle").textContent = `当前会话：${state.session_id}`;
  renderStatus(state);
  renderWarnings(state.ui_warnings || []);
  targetProfiles = state.target_profiles || targetProfiles;
  renderSavedTargets(targetProfiles);
  renderApiConfig(state.api_config || {});
  renderTargetConfig(state.target_config || {});
  renderTaskCards(state);
  renderInteractions(state);
  renderInspector();
}

function renderStatus(state) {
  const status = state.status || "ready";
  const pill = $("status-pill");
  pill.textContent = STATUS_LABEL[status] || status;
  pill.className = `status-pill ${status}`;
  $("btn-cancel").classList.toggle("hidden", !CANCEL_STATUSES.has(status));
  $("btn-resume").classList.toggle("hidden", !state.resume_available);
  $("msg-input").disabled = !INPUT_STATUSES.has(status);
  $("btn-send").disabled = !INPUT_STATUSES.has(status);
}

function renderWarnings(warnings) {
  const box = $("warnings");
  if (!warnings.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = warnings.map((item) => `<div>${esc(item)}</div>`).join("");
}

function renderInteractions(state) {
  const confirm = state.pending_confirmation;
  const confirmBanner = $("confirm-banner");
  if (confirm) {
    $("confirm-title").textContent = `${confirm.risk_level || "WARN"} · ${confirm.tool || "tool"}`;
    $("confirm-reason").textContent = [
      confirm.reason || "该操作需要确认。",
      confirm.rollback_hint ? `回滚提示：${confirm.rollback_hint}` : "",
      confirm.recoverable === false ? "该审批来自已失效的后台执行，请重新发起任务。" : "",
    ].filter(Boolean).join("\n");
    confirmBanner.classList.remove("hidden");
  } else {
    confirmBanner.classList.add("hidden");
  }

  const pendingInput = state.pending_input;
  const inputBanner = $("input-banner");
  if (pendingInput) {
    $("input-prompt").textContent = pendingInput.prompt || "请补充必要信息。";
    $("msg-input").placeholder = pendingInput.prompt || "请输入补充信息";
    inputBanner.classList.remove("hidden");
  } else {
    $("msg-input").placeholder = "输入任务，例如：检查系统版本和负载";
    inputBanner.classList.add("hidden");
  }
}

function renderApiConfig(config) {
  $("api-key-pill").textContent = config.api_key_configured ? "已配置" : "未配置";
  if (apiConfigTouched) return;
  $("api-base-url").value = config.base_url || "";
  $("api-model").value = config.model || "";
  $("api-key").value = config.api_key || "";
}

function renderTargetConfig(target) {
  $("target-mode-pill").textContent = target.mode === "ssh" ? "SSH" : "本机";
  $("target-summary").textContent = [
    target.summary || "本机控制端",
    target.password_configured ? "password=已配置（不回显）" : "",
    target.env ? `os=${target.env.os || "unknown"} distro=${target.env.distro || "unknown"}` : "",
    target.env ? `init=${target.env.init_system || "unknown"} pkg=${target.env.package_manager || "unknown"}` : "",
  ].filter(Boolean).join("\n");
  if (targetFormTouched) return;
  const mode = target.mode === "ssh" ? "ssh" : "local";
  const radio = document.querySelector(`input[name="target-mode"][value="${mode}"]`);
  if (radio) radio.checked = true;
  $("target-label").value = "";
  $("target-host").value = target.host || "";
  $("target-user").value = target.user || "";
  $("target-port").value = target.port || 22;
  $("target-password").value = "";
  $("target-key").value = target.ssh_key_file || "";
}

function renderSavedTargets(targets) {
  const select = $("saved-targets");
  if (!select) return;
  const current = select.value;
  const sshTargets = (targets || []).filter((target) => (target.facts || {}).mode === "ssh");
  select.innerHTML = `<option value="">选择已保存的 SSH 目标</option>` + sshTargets.map((target) => {
    const facts = target.facts || {};
    const label = target.label || facts.summary || target.target_id;
    const detail = facts.host ? `${facts.host}:${facts.port || 22}` : target.target_id;
    const password = facts.password_configured ? " · 含密码" : "";
    return `<option value="${esc(target.target_id)}">${esc(label)} · ${esc(detail)}${password}</option>`;
  }).join("");
  if ([...select.options].some((option) => option.value === current)) {
    select.value = current;
  }
}

function selectedTargetProfile() {
  const targetId = $("saved-targets")?.value || "";
  return (targetProfiles || []).find((target) => target.target_id === targetId) || null;
}

function fillTargetFromProfile(profile) {
  if (!profile) return;
  const facts = profile.facts || {};
  const radio = document.querySelector('input[name="target-mode"][value="ssh"]');
  if (radio) radio.checked = true;
  $("target-label").value = profile.label || facts.summary || "";
  $("target-host").value = facts.host || "";
  $("target-user").value = facts.user || "";
  $("target-port").value = facts.port || 22;
  $("target-key").value = facts.ssh_key_file || "";
  $("target-password").value = "";
  $("target-password").placeholder = facts.password_configured
    ? "已保存密码，留空沿用"
    : "SSH password，可留空使用 key/agent";
  targetFormTouched = true;
}

function renderTaskCards(state) {
  const cards = $("cards");
  const entries = state.entries || [];
  const events = state.task_events || [];
  if (!entries.length && !events.length && !state.active_task) {
    cards.innerHTML = `
      <article class="empty-state">
        <h2>选择一个会话，或直接发起运维任务</h2>
        <p>所有请求都会经过 ReAct、安全门、审批、审计和任务状态持久化。复杂任务会在这里拆成请求、计划、工具、验证和结果。</p>
      </article>`;
    return;
  }

  const turns = splitTurns(entries);
  if (!turns.length) turns.push({ user: state.active_task?.goal || "系统任务", taskId: state.active_task?.task_id || "", replies: [] });
  const eventsByTask = groupEventsByTask(events);
  turns.forEach((turn, index) => {
    const isLatest = index === turns.length - 1;
    const taskId = turn.taskId || (isLatest ? state.active_task?.task_id : "");
    turn.events = taskId ? (eventsByTask.get(taskId) || []) : (turns.length === 1 ? (eventsByTask.get("") || []) : []);
    turn.activeTask = isLatest ? state.active_task : null;
  });

  cards.innerHTML = turns.map((turn, index) => renderTaskCard(turn, index === turns.length - 1)).join("");
  cards.scrollTop = cards.scrollHeight;
}

function splitTurns(entries) {
  const turns = [];
  for (const entry of entries) {
    if (entry.role === "user" || !turns.length) {
      turns.push({
        user: entry.role === "user" ? entry.text : "系统事件",
        taskId: entry.task_id || "",
        replies: [],
      });
      if (entry.role !== "user") turns[turns.length - 1].replies.push(entry);
      continue;
    }
    if (!turns[turns.length - 1].taskId && entry.task_id) {
      turns[turns.length - 1].taskId = entry.task_id;
    }
    turns[turns.length - 1].replies.push(entry);
  }
  return turns;
}

function groupEventsByTask(events) {
  const grouped = new Map();
  for (const event of events || []) {
    const taskId = event.task_id || event.data?.task_id || "";
    if (!grouped.has(taskId)) grouped.set(taskId, []);
    grouped.get(taskId).push(event);
  }
  return grouped;
}

function renderTaskCard(turn, isLatest) {
  const grouped = groupEvents(turn.events || []);
  const replies = turn.replies || [];
  const task = turn.activeTask;
  const result = replies.filter((entry) => entry.role === "assistant" || entry.role === "error").slice(-2);
  const tech = replies.filter((entry) => entry.technical_details).map((entry) => entry.technical_details);
  const processCount = grouped.plan.length + grouped.tool.length + grouped.approval.length + grouped.verification.length;
  const errorCount = grouped.error.length + tech.length;
  return `
    <article class="task-card">
      <header>
        <h3>${isLatest ? "当前任务" : "历史请求"}</h3>
        <p>${task ? `${esc(task.status)} · ${esc(task.current_phase || "")}` : "会话记录"}</p>
      </header>
      <div class="task-grid">
        <section class="task-section full">
          <h4>请求</h4>
          <div class="message user">${esc(turn.user || "")}</div>
        </section>
        <section class="task-section full">
          <h4>结果</h4>
          ${result.length ? result.map(renderMessage).join("") : renderEventList(grouped.result)}
        </section>
        <section class="task-section full compact-details">
          <details ${isLatest && processCount ? "open" : ""}>
            <summary>执行过程 ${processCount ? `(${processCount})` : ""}</summary>
            <div class="task-detail-grid">
              <div><h4>计划/思考摘要</h4>${renderEventList(grouped.plan, 4)}</div>
              <div><h4>工具</h4>${renderEventList(grouped.tool, 4)}</div>
              <div><h4>审批</h4>${renderEventList(grouped.approval, 4)}</div>
              <div><h4>验证</h4>${renderEventList(grouped.verification, 4)}</div>
            </div>
          </details>
          <details class="error-details" ${errorCount ? "open" : ""}>
            <summary>错误详情 ${errorCount ? `(${errorCount})` : ""}</summary>
            ${renderErrorDetails(grouped.error, tech)}
          </details>
        </section>
      </div>
    </article>`;
}

function groupEvents(events) {
  const grouped = { plan: [], tool: [], approval: [], verification: [], result: [], error: [] };
  for (const event of events) {
    const stage = event.stage || "";
    if (["model_response", "correction", "skill_activated", "role_handoff"].includes(stage)) grouped.plan.push(event);
    else if (stage.includes("tool") || stage.includes("workflow")) grouped.tool.push(event);
    else if (stage.includes("confirm") || stage.includes("approval")) grouped.approval.push(event);
    else if (stage.includes("verification")) grouped.verification.push(event);
    else if (stage.includes("failed") || stage.includes("error")) grouped.error.push(event);
    else if (stage.includes("finished")) grouped.result.push(event);
    else grouped.plan.push(event);
  }
  return grouped;
}

function renderEventList(events, limit = 6) {
  if (!events || !events.length) return `<p class="hint">暂无记录</p>`;
  return `<div class="event-list">${events.slice(-limit).map((event) => `
    <div class="event">
      <span class="event-stage">${esc(event.stage || "event")}</span>
      <span>${esc(event.message || event.data?.summary || "")}</span>
    </div>`).join("")}</div>`;
}

function renderMessage(entry) {
  return `<div class="message ${esc(entry.role)}">${esc(entry.text || "")}</div>`;
}

function renderErrorDetails(events, details) {
  const hasEvents = events && events.length;
  const hasDetails = details && details.length;
  if (!hasEvents && !hasDetails) return `<p class="hint">暂无错误</p>`;
  const eventHtml = hasEvents ? renderEventList(events) : "";
  const detailHtml = hasDetails ? `
    <details>
      <summary>技术详情</summary>
      <pre class="code-box">${esc(details.join("\n\n"))}</pre>
    </details>` : "";
  return `${eventHtml}${detailHtml}`;
}

async function renderInspector() {
  if (!lastState) return;
  const body = $("inspector-body");
  try {
    if (activeTab === "tasks") {
      const payload = await api("/tasks");
      body.innerHTML = renderTasksTab(payload.tasks || []);
    } else if (activeTab === "trace") {
      const payload = await api("/traces");
      body.innerHTML = renderSimpleCards(payload.spans || [], "span_type", ["name", "status", "task_id"]);
    } else if (activeTab === "audit") {
      const payload = await api("/audit");
      body.innerHTML = renderAuditTab(payload);
    } else if (activeTab === "locks") {
      const payload = await rootApi("/locks");
      body.innerHTML = renderSimpleCards(payload.locks || [], "scope", ["task_id", "session_id", "surface"]);
    } else if (activeTab === "skills") {
      const payload = await api("/skills");
      body.innerHTML = renderSkillsTab(payload.skills || []);
    } else if (activeTab === "hooks") {
      const payload = await api("/hooks");
      body.innerHTML = renderSimpleCards(payload.hooks || [], "event", ["action", "source", "enabled"]);
    } else if (activeTab === "memory") {
      const payload = await api("/memory");
      body.innerHTML = renderSimpleCards(payload.records || [], "key", ["scope", "updated_at"]);
    } else if (activeTab === "permissions") {
      const payload = await api("/permissions/explain");
      body.innerHTML = renderPermissionTab(payload);
    } else if (activeTab === "targets") {
      const payload = await rootApi("/targets");
      body.innerHTML = renderTargetsTab(payload.targets || []);
    }
  } catch (error) {
    body.innerHTML = `<div class="inspector-card"><h3>加载失败</h3><p>${esc(error.message)}</p></div>`;
  }
  bindInspectorActions();
}

function renderTasksTab(tasks) {
  if (!tasks.length) return `<div class="inspector-card"><h3>暂无任务</h3><p class="hint">执行请求后会在这里出现任务状态。</p></div>`;
  return tasks.map((task) => `
    <div class="inspector-card">
      <h3>${esc(task.goal || task.task_id)}</h3>
      <div class="kv">
        <span>状态</span><span>${esc(STATUS_LABEL[task.status] || task.status)}</span>
        <span>阶段</span><span>${esc(task.current_phase || "")}</span>
        <span>模式</span><span>${esc(task.mode || "")}</span>
        <span>预算</span><span>${esc(task.iteration_budget || 0)} / ${esc(task.iteration_limit || 0)}</span>
        <span>步骤</span><span>${esc(task.steps_count || 0)}</span>
      </div>
      <details><summary>任务详情</summary><pre class="code-box">${esc(JSON.stringify(task, null, 2))}</pre></details>
    </div>`).join("");
}

function renderAuditTab(payload) {
  const lines = payload.table || [];
  const actions = `<button class="soft-btn" onclick="exportAudit()">导出 replay package</button>`;
  return `
    <div class="inspector-card">
      <h3>Audit tail · ${esc(payload.count || 0)} 条</h3>
      ${actions}
      <pre class="code-box">${esc(lines.join("\n") || "暂无审计记录")}</pre>
    </div>`;
}

function renderSkillsTab(skills) {
  if (!skills.length) return `<div class="inspector-card"><h3>暂无 Skills</h3><p class="hint">可通过 /skills 或放置 SKILL.md 添加。</p></div>`;
  return skills.map((skill) => `
    <div class="inspector-card">
      <h3>${esc(skill.name || "skill")}</h3>
      <p>${esc(skill.description || "")}</p>
      <div class="kv">
        <span>来源</span><span>${esc(skill.scope || "")}</span>
        <span>用户调用</span><span>${esc(skill.user_invocable)}</span>
        <span>模型调用</span><span>${esc(skill.model_invocable)}</span>
      </div>
      <button class="soft-btn" data-skill="${esc(skill.name || "")}">加载技能</button>
    </div>`).join("");
}

function renderTargetsTab(targets) {
  if (!targets.length) return `<div class="inspector-card"><h3>暂无目标画像</h3><p class="hint">应用目标机器后会自动生成无密画像。</p></div>`;
  return targets.map((target) => `
    <div class="inspector-card">
      <h3>${esc(target.label || target.target_id)}</h3>
      <div class="kv">
        <span>ID</span><span>${esc(target.target_id)}</span>
        <span>更新</span><span>${esc(fmtTime(target.updated_at))}</span>
      </div>
      <details><summary>画像事实</summary><pre class="code-box">${esc(JSON.stringify(target.facts || {}, null, 2))}</pre></details>
    </div>`).join("");
}

function renderPermissionTab(payload) {
  return `
    <div class="inspector-card">
      <h3>权限解释</h3>
      <div class="kv">
        <span>决策</span><span>${esc(payload.action || payload.decision || "")}</span>
        <span>规则</span><span>${esc(payload.rule_id || payload.matched_rule || "")}</span>
        <span>原因</span><span>${esc(payload.reason || payload.decision_reason || "")}</span>
      </div>
      <details><summary>完整数据</summary><pre class="code-box">${esc(JSON.stringify(payload, null, 2))}</pre></details>
    </div>`;
}

function renderSimpleCards(items, titleKey, fields) {
  if (!items.length) return `<div class="inspector-card"><h3>暂无数据</h3></div>`;
  return items.slice(-80).reverse().map((item) => `
    <div class="inspector-card">
      <h3>${esc(item[titleKey] || titleKey)}</h3>
      <div class="kv">
        ${fields.map((field) => `<span>${esc(field)}</span><span>${esc(item[field] ?? "")}</span>`).join("")}
      </div>
      <details><summary>完整数据</summary><pre class="code-box">${esc(JSON.stringify(item, null, 2))}</pre></details>
    </div>`).join("");
}

function bindInspectorActions() {
  $("inspector-body").querySelectorAll("[data-skill]").forEach((button) => {
    button.addEventListener("click", () => activateSkill(button.dataset.skill || ""));
  });
}

async function sendMessage() {
  const input = $("msg-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  try {
    if (text.startsWith("/")) {
      const result = await api("/command", "POST", { command: text });
      if (result.reply) showLocalSystem(result.reply);
    } else {
      await api("/turn", "POST", { message: text });
    }
    await poll();
  } catch (error) {
    showLocalError(`发送失败：${error.message}`);
  }
}

async function doConfirm(approved, decision = "once") {
  try {
    await api("/confirm", "POST", { approved, decision: approved ? decision : "deny" });
    await poll();
  } catch (error) {
    showLocalError(`审批提交失败：${error.message}`);
  }
}

async function doCancel() {
  try {
    await api("/cancel", "POST", {});
    await poll();
  } catch (error) {
    showLocalError(`取消失败：${error.message}`);
  }
}

async function doResume() {
  try {
    await api("/resume", "POST", {});
    await poll();
  } catch (error) {
    showLocalError(`恢复失败：${error.message}`);
  }
}

function targetPayload() {
  return {
    target_id: $("saved-targets")?.value || "",
    mode: document.querySelector('input[name="target-mode"]:checked')?.value || "local",
    label: $("target-label").value.trim(),
    host: $("target-host").value.trim(),
    user: $("target-user").value.trim(),
    port: Number($("target-port").value || 22),
    password: $("target-password").value,
    ssh_key_file: $("target-key").value.trim(),
  };
}

function apiConfigPayload() {
  return {
    base_url: $("api-base-url").value.trim(),
    model: $("api-model").value.trim(),
    api_key: $("api-key").value.trim(),
  };
}

async function applyApiConfig() {
  try {
    const result = await api("/api-config", "POST", apiConfigPayload());
    apiConfigTouched = false;
    $("api-key").value = result.api_config?.api_key || "";
    showLocalSystem(`模型配置已更新：${result.api_config?.model || "current model"}`);
    await poll();
  } catch (error) {
    showLocalError(`模型配置失败：${error.message}`);
  }
}

async function testTarget() {
  try {
    const result = await rootApi("/targets/test", "POST", targetPayload());
    if (result.ok) {
      showLocalSystem(`目标连接测试成功：${result.summary}`);
    } else {
      showLocalError(`目标连接测试失败（${result.category}）：${result.message}`);
    }
  } catch (error) {
    showLocalError(`目标连接测试失败：${error.message}`);
  }
}

async function applyTarget() {
  try {
    const result = await api("/target", "POST", targetPayload());
    targetFormTouched = false;
    $("target-password").value = "";
    showLocalSystem(`目标机器已切换：${result.summary || "本机控制端"}`);
    await poll();
  } catch (error) {
    showLocalError(`目标机器配置失败：${error.message}`);
  }
}

async function saveTarget() {
  try {
    const result = await rootApi("/targets", "POST", targetPayload());
    targetProfiles = [result.target, ...targetProfiles.filter((item) => item.target_id !== result.target.target_id)];
    renderSavedTargets(targetProfiles);
    $("saved-targets").value = result.target.target_id;
    showLocalSystem(`SSH 目标已保存：${result.target.label || result.target.target_id}`);
    await poll();
  } catch (error) {
    showLocalError(`保存目标失败：${error.message}`);
  }
}

async function deleteSavedTarget() {
  const profile = selectedTargetProfile();
  if (!profile) {
    showLocalError("请先选择一个已保存的 SSH 目标。");
    return;
  }
  try {
    await rootApi(`/targets/${encodeURIComponent(profile.target_id)}`, "DELETE");
    targetProfiles = targetProfiles.filter((item) => item.target_id !== profile.target_id);
    renderSavedTargets(targetProfiles);
    showLocalSystem(`已删除保存目标：${profile.label || profile.target_id}`);
    await poll();
  } catch (error) {
    showLocalError(`删除目标失败：${error.message}`);
  }
}

async function activateSkill(name) {
  if (!name) return;
  try {
    const result = await api("/skill", "POST", { name, args: {} });
    showLocalSystem(result.reply || `已加载技能 ${name}`);
    await poll();
  } catch (error) {
    showLocalError(`加载技能失败：${error.message}`);
  }
}

async function exportAudit() {
  try {
    const result = await api("/audit/export", "POST", {});
    showLocalSystem(`审计 replay package 已导出：${result.path}`);
  } catch (error) {
    showLocalError(`导出失败：${error.message}`);
  }
}

function showLocalError(text) {
  const cards = $("cards");
  cards.insertAdjacentHTML("beforeend", `<article class="task-card"><header><h3>错误</h3></header><div class="task-grid"><section class="task-section full"><div class="message error">${esc(text)}</div></section></div></article>`);
  cards.scrollTop = cards.scrollHeight;
}

function showLocalSystem(text) {
  const cards = $("cards");
  cards.insertAdjacentHTML("beforeend", `<article class="task-card"><header><h3>系统提示</h3></header><div class="task-grid"><section class="task-section full"><div class="message system">${esc(text)}</div></section></div></article>`);
  cards.scrollTop = cards.scrollHeight;
}

function openSettings() {
  $("settings-modal")?.classList.remove("hidden");
}

function closeSettings() {
  $("settings-modal")?.classList.add("hidden");
}

function bindEvents() {
  $("btn-open-settings").addEventListener("click", openSettings);
  $("btn-close-settings").addEventListener("click", closeSettings);
  document.querySelectorAll("[data-close-settings]").forEach((el) => {
    el.addEventListener("click", closeSettings);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSettings();
  });
  $("btn-new-session").addEventListener("click", createSession);
  $("btn-refresh-sessions").addEventListener("click", refreshSessions);
  $("btn-refresh-all").addEventListener("click", refreshAll);
  $("btn-send").addEventListener("click", sendMessage);
  $("btn-cancel").addEventListener("click", doCancel);
  $("btn-resume").addEventListener("click", doResume);
  $("btn-confirm-once").addEventListener("click", () => doConfirm(true, "once"));
  $("btn-confirm-always").addEventListener("click", () => doConfirm(true, "always_this_session"));
  $("btn-confirm-deny").addEventListener("click", () => doConfirm(false, "deny"));
  $("btn-api-apply").addEventListener("click", applyApiConfig);
  $("btn-target-test").addEventListener("click", testTarget);
  $("btn-target-save").addEventListener("click", saveTarget);
  $("btn-target-delete").addEventListener("click", deleteSavedTarget);
  $("btn-target-apply").addEventListener("click", applyTarget);
  $("saved-targets").addEventListener("change", () => fillTargetFromProfile(selectedTargetProfile()));
  document.querySelectorAll("#api-config input").forEach((el) => {
    el.addEventListener("input", () => { apiConfigTouched = true; });
    el.addEventListener("change", () => { apiConfigTouched = true; });
  });
  $("msg-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      activeTab = tab.dataset.tab;
      renderInspector();
    });
  });
  document.querySelectorAll("#target-config input").forEach((el) => {
    el.addEventListener("input", () => { targetFormTouched = true; });
    el.addEventListener("change", () => { targetFormTouched = true; });
  });
  document.querySelectorAll("#quick-actions button").forEach((button) => {
    button.addEventListener("click", () => {
      const prompt = button.dataset.prompt || button.dataset.fill || "";
      $("msg-input").value = prompt;
      if (button.dataset.prompt) sendMessage();
    });
  });
}

async function refreshAll() {
  await refreshSessions();
  await poll();
}

bindEvents();
refreshAll();
setInterval(poll, POLL_MS);
