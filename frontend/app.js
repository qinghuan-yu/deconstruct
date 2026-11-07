const state = {
  conversations: [], activeId: null, activeMode: "expert", lastSeq: 0,
  eventSource: null, reconnectTimer: null, epoch: 0, listEpoch: 0,
  messages: new Map(), nodes: new Map(), attachments: [],
  scrollPositions: new Map(), loadingId: null,
  submitting: false, uploading: false, loading: false, frame: null,
};
const els = Object.fromEntries([
  "sidebar", "sidebarBackdrop", "closeSidebar", "openSidebar", "conversationList", "newChat", "mobileNewChat",
  "modelSettings", "chat", "messages", "emptyState", "composer", "composerDock", "composerLabel", "composerError",
  "promptInput", "sendButton", "documentInput", "attachmentTray", "uploadStatus", "connectionStatus",
  "settingsForm", "settingsStatus", "apiKeyInput", "baseUrlInput", "modelInput", "modelBadge", "masthead", "mainContent",
].map(id => [id, document.getElementById(id)]));
const STEP_ORDER = ["problem_analyst", "red_team", "execution_evaluator", "evidence_auditor", "decision_ledger", "judge", "final_writer"];
const mobileLayout = matchMedia("(max-width: 767px)");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

function renderMarkdown(content) {
  const safe = DOMPurify.sanitize(marked.parse(content || "", { gfm: true, breaks: false }), {
    USE_PROFILES: { html: true }, FORBID_TAGS: ["img", "style", "input", "form", "button", "video", "audio"],
    FORBID_ATTR: ["style", "id", "name"],
  });
  const template = document.createElement("template");
  template.innerHTML = safe;
  for (const table of template.content.querySelectorAll("table")) {
    const wrapper = document.createElement("div");
    wrapper.className = "table-wrap";
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
  for (const link of template.content.querySelectorAll("a")) {
    link.rel = "noopener noreferrer";
    link.target = "_blank";
  }
  return template.innerHTML;
}

function legacyAnswer(content) {
  const marker = "<!-- FINAL_DECISION -->";
  const index = content.indexOf(marker);
  return index < 0 ? { final: content, audit: "" } : { final: content.slice(index + marker.length), audit: content.slice(0, index) };
}

function attachmentMarkup(item) {
  return `<div class="attachment-meta"><strong>${escapeHtml(item.filename)}</strong><span>${escapeHtml((item.extension || "文档").toUpperCase())}${item.truncated ? " · 已截取" : ""}</span></div>`;
}

function renderUserContent(message) {
  let text = message.content || "";
  const attachments = [...(message.attachments || [])];
  // 仅旧数据使用文本协议；新消息的方括号始终按正文展示。
  if (!attachments.length && message.format_version !== 2) {
    text = text.replace(/\n\n\[文档：([^\]\n]+)\]\n[\s\S]*?(?=\n\n\[文档：|$)/g, (_, filename) => {
      attachments.push({ filename, extension: filename.split(".").pop() });
      return "";
    });
  }
  return `<div class="user-attachments">${attachments.map(item => `<div class="user-attachment-card">${attachmentMarkup(item)}</div>`).join("")}</div><div class="user-text">${escapeHtml(text)}</div>`;
}

function setHtml(element, html) {
  if (element._html !== html) { element.innerHTML = html; element._html = html; }
}

function hasSelection(element) {
  const selection = window.getSelection();
  return selection && !selection.isCollapsed && element.contains(selection.anchorNode);
}

function renderAudit(node, message, legacy) {
  const panel = node.querySelector(".audit-panel");
  const steps = message.steps || {};
  panel.hidden = message.mode !== "expert" && !legacy;
  if (panel.hidden) return;
  const entries = STEP_ORDER.filter(key => steps[key]).map(key => [key, steps[key]]);
  const completed = entries.filter(([, step]) => step.status === "complete").length;
  const running = entries.filter(([, step]) => step.status === "running").map(([, step]) => step.title);
  const label = message.status === "streaming"
    ? `${running.length ? `正在${running.join("、")}` : "准备分析"} · ${completed}/7 已完成`
    : message.status === "complete" ? "查看审计记录" : "查看已完成的审计";
  node.querySelector(".audit-label").textContent = label;
  panel.querySelectorAll(".audit-progress > span").forEach((bar, index) => {
    const step = steps[STEP_ORDER[index]];
    bar.dataset.status = step?.status === "complete" ? "complete" : step && message.status === "streaming" ? "running" : "pending";
  });
  const body = panel.querySelector(".audit-body");
  for (const [key, step] of entries) {
    let detail = body.querySelector(`[data-step="${key}"]`);
    if (!detail) {
      detail = document.createElement("details");
      detail.className = "audit-step";
      detail.dataset.step = key;
      detail.innerHTML = '<summary><span class="audit-step-title"></span><span class="audit-step-state"></span></summary><div class="audit-step-content"></div>';
      body.append(detail);
    }
    detail.querySelector(".audit-step-title").textContent = step.title;
    detail.dataset.status = step.status === "complete" ? "complete" : message.status === "streaming" ? "running" : "interrupted";
    detail.querySelector(".audit-step-state").textContent = step.status === "complete"
      ? `完成${step.elapsed_seconds != null ? ` / ${Math.round(step.elapsed_seconds)} 秒` : ""}`
      : message.status === "streaming" ? "进行中" : "已中断";
    setHtml(detail.querySelector(".audit-step-content"), renderMarkdown(step.content || (key === "final_writer" && step.status === "complete" ? "决策书见正文。" : "尚未完成。")));
  }
  if (legacy && !body.querySelector(".legacy-audit")) {
    const block = document.createElement("div");
    block.className = "legacy-audit";
    block.innerHTML = renderMarkdown(legacy);
    body.append(block);
  }
}

function renderMessage(message, restored = false) {
  let node = state.nodes.get(message.id);
  if (!node) {
    node = document.createElement("article");
    node.className = `message ${message.role}${restored ? " is-restored" : ""}`;
    node.dataset.messageId = message.id;
    node.innerHTML = '<div class="message-head"><span class="message-role"></span><time></time></div><div class="bubble"></div>';
    if (message.role === "assistant") {
      node.querySelector(".bubble").innerHTML = `<details class="audit-panel" hidden><summary><span class="audit-summary"><span class="audit-label" role="status" aria-live="polite"></span><span class="audit-progress" aria-hidden="true">${STEP_ORDER.map(() => '<span></span>').join("")}</span></span></summary><div class="audit-body"></div></details><div class="final-answer"></div><div class="message-status" role="status"></div>`;
    }
    state.nodes.set(message.id, node);
    els.messages.append(node);
  }
  node.classList.toggle("streaming", message.status === "streaming");
  node.querySelector(".message-role").textContent = message.role === "user" ? "你的问题" : message.mode === "expert" ? "决策书" : "快速建议";
  if (message.created_at) {
    const date = new Date(message.created_at * 1000);
    const time = node.querySelector("time");
    time.dateTime = date.toISOString();
    time.textContent = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  if (message.role === "user") { setHtml(node.querySelector(".bubble"), renderUserContent(message)); return; }
  const { final, audit } = legacyAnswer(message.content || "");
  renderAudit(node, message, audit);
  const answer = node.querySelector(".final-answer");
  if (!hasSelection(answer)) {
    setHtml(answer, renderMarkdown(final));
    if (restored && final) answer.classList.add("is-restored");
    if (final) answer.classList.add("has-content");
  }
  const status = node.querySelector(".message-status");
  status.classList.toggle("is-error", message.status === "error");
  status.textContent = message.error || ({ cancelled: "已停止，已生成内容已保留。", interrupted: "本次生成已中断，可以重新发送。" }[message.status])
    || (message.status === "streaming" && !final && message.mode !== "expert" ? "正在组织回答…" : "");
}

function nearBottom() {
  return els.chat.scrollHeight - els.chat.clientHeight - els.chat.scrollTop < 120;
}

function scrollToBottom() { els.chat.scrollTo({ top: els.chat.scrollHeight, behavior: "instant" }); }

function renderMessages(restored = false) {
  for (const message of state.messages.values()) renderMessage(message, restored);
  els.emptyState.hidden = state.messages.size > 0;
  document.body.classList.toggle("is-empty", state.messages.size === 0);
  els.composerLabel.textContent = state.messages.size ? "继续对话" : "你的问题";
  updateComposer();
}

function scheduleRender(forceScroll = false) {
  if (state.frame || state.loading) return;
  const follow = forceScroll || nearBottom();
  state.frame = requestAnimationFrame(() => {
    state.frame = null;
    renderMessages();
    if (follow && !hasSelection(els.messages) && state.messages.size) scrollToBottom();
  });
}

function generating() { return [...state.messages.values()].some(message => message.status === "streaming"); }

function updateComposer() {
  const busy = generating();
  els.sendButton.disabled = state.submitting || state.uploading || state.loading || (!busy && !els.promptInput.value.trim() && !state.attachments.length);
  els.sendButton.classList.toggle("is-stopping", busy);
  els.sendButton.textContent = state.submitting ? "请稍候" : busy ? "停止 ■" : "发送 ↗";
  els.sendButton.setAttribute("aria-label", busy ? "停止生成" : "发送消息");
  document.querySelectorAll("[data-mode]").forEach(button => { button.setAttribute("aria-pressed", String(button.dataset.mode === state.activeMode)); });
}

function setError(message = "") { els.composerError.textContent = message; els.composerError.hidden = !message; }

async function request(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map(item => item.msg).join("；") : `请求失败（${response.status}）`);
  }
  return data;
}

function groupLabel(timestamp) {
  const date = new Date(timestamp * 1000);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.floor((today - date) / 86400000);
  return days < 0 ? "今天" : days < 7 ? "最近七天" : "更早";
}

function updateConversationSelection() {
  const selectedId = state.loadingId || state.activeId;
  for (const row of els.conversationList.querySelectorAll(".conversation-item")) {
    const active = row.dataset.conversationId === selectedId;
    row.classList.toggle("active", active);
    const button = row.querySelector(".conversation-title");
    if (active) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
}

function renderConversations() {
  const signature = JSON.stringify(state.conversations.map(item => [item.id, item.title, groupLabel(item.updated_at)]));
  if (els.conversationList._signature === signature) { updateConversationSelection(); return; }
  els.conversationList._signature = signature;
  const listTop = els.conversationList.scrollTop;
  const focusedRow = document.activeElement?.closest(".conversation-item");
  const focusedId = focusedRow?.dataset.conversationId;
  const focusedAction = document.activeElement?.className;
  els.conversationList.replaceChildren();
  if (!state.conversations.length) {
    els.conversationList.innerHTML = '<p class="history-empty">暂无对话</p>';
    return;
  }
  let group;
  for (const conversation of state.conversations) {
    const label = groupLabel(conversation.updated_at);
    if (label !== group) {
      group = label;
      const heading = document.createElement("div"); heading.className = "conversation-group"; heading.textContent = label; els.conversationList.append(heading);
    }
    const row = document.createElement("div");
    row.dataset.conversationId = conversation.id;
    row.className = `conversation-item${conversation.id === state.activeId ? " active" : ""}`;
    row.innerHTML = `<button class="conversation-title" type="button" title="${escapeHtml(conversation.title)}">${escapeHtml(conversation.title)}</button><button class="delete-conversation" type="button" aria-label="删除 ${escapeHtml(conversation.title)}">删除</button>`;
    if (conversation.id === state.activeId) row.querySelector(".conversation-title").setAttribute("aria-current", "true");
    row.querySelector(".conversation-title").onclick = () => selectConversation(conversation.id);
    row.querySelector(".delete-conversation").onclick = () => deleteConversation(conversation.id);
    els.conversationList.append(row);
  }
  updateConversationSelection();
  els.conversationList.scrollTop = listTop;
  if (focusedId) {
    const row = [...els.conversationList.children].find(item => item.dataset.conversationId === focusedId);
    row?.querySelector(focusedAction === "delete-conversation" ? ".delete-conversation" : ".conversation-title")?.focus({ preventScroll: true });
  }
}

async function loadConversations() {
  const epoch = ++state.listEpoch;
  const data = await request("/api/conversations");
  if (epoch !== state.listEpoch) return;
  state.conversations = data.conversations;
  renderConversations();
}

function closeEvents() {
  clearTimeout(state.reconnectTimer);
  state.eventSource?.close();
  state.eventSource = null;
}

function resetMessages() {
  if (state.frame) cancelAnimationFrame(state.frame);
  state.frame = null;
  state.messages.clear(); state.nodes.clear(); els.messages.replaceChildren(); state.lastSeq = 0;
}

function applySnapshot(data, { scrollTop, replace = false } = {}) {
  const previousTop = els.chat.scrollTop;
  const follow = nearBottom();
  if (replace) resetMessages();
  else {
    if (state.frame) cancelAnimationFrame(state.frame);
    state.frame = null;
    state.messages.clear();
  }
  state.lastSeq = data.cursor || 0;
  for (const message of data.messages) state.messages.set(message.id, message);
  for (const [id, node] of state.nodes) {
    if (!state.messages.has(id)) { node.remove(); state.nodes.delete(id); }
  }
  // 在同一帧完成正文替换与定位，避免先塌缩再滚动。
  renderMessages(true);
  els.chat.scrollTo({ top: scrollTop ?? (follow ? els.chat.scrollHeight : previousTop), behavior: "instant" });
}

function rememberScrollPosition() {
  if (state.activeId) state.scrollPositions.set(state.activeId, els.chat.scrollTop);
}

async function selectConversation(id, { refresh = false } = {}) {
  if (!refresh && (state.loadingId === id || (state.activeId === id && !state.loading))) { toggleSidebar(false); return; }
  rememberScrollPosition();
  const epoch = ++state.epoch;
  closeEvents();
  if (state.frame) cancelAnimationFrame(state.frame);
  state.frame = null;
  state.loadingId = id;
  state.loading = true;
  els.mainContent.setAttribute("aria-busy", "true");
  setError(); updateConversationSelection(); updateComposer();
  toggleSidebar(false);
  try {
    const data = await request(`/api/conversations/${id}`);
    if (epoch !== state.epoch) return;
    rememberScrollPosition();
    const replace = state.activeId !== id;
    state.activeId = id;
    applySnapshot(data, { scrollTop: state.scrollPositions.get(id) ?? 0, replace });
    connectEvents();
  } catch (error) {
    if (epoch === state.epoch) { setError(error.message); connectEvents(); }
  } finally {
    if (epoch === state.epoch) {
      state.loading = false; state.loadingId = null;
      els.mainContent.removeAttribute("aria-busy");
      updateConversationSelection(); updateComposer();
    }
  }
}

function startNewConversation() {
  rememberScrollPosition();
  ++state.epoch;
  closeEvents(); resetMessages();
  state.activeId = null; state.loading = false; state.loadingId = null; state.attachments = [];
  els.mainContent.removeAttribute("aria-busy");
  els.promptInput.value = "";
  setError(); renderAttachments(); scheduleRender(); renderConversations(); toggleSidebar(false);
  els.connectionStatus.textContent = "";
  els.chat.scrollTop = 0; els.promptInput.focus({ preventScroll: true });
}

async function deleteConversation(id) {
  try {
    await request(`/api/conversations/${id}`, { method: "DELETE" });
    if (state.activeId === id) startNewConversation();
    state.scrollPositions.delete(id);
    await loadConversations();
  } catch (error) { setError(error.message); }
}

function connectEvents() {
  if (!state.activeId) return;
  closeEvents();
  const id = state.activeId, epoch = state.epoch;
  const source = new EventSource(`/api/conversations/${id}/events?after=${state.lastSeq}`);
  state.eventSource = source;
  const current = () => source === state.eventSource && state.activeId === id && epoch === state.epoch;
  for (const name of ["conversation_snapshot", "conversation_renamed", "user_message", "assistant_message_started", "assistant_delta", "assistant_message_completed", "assistant_error", "debate_step_started", "debate_step_completed"]) {
    source.addEventListener(name, raw => { if (current()) handleEvent(raw); });
  }
  source.onopen = () => { if (current()) els.connectionStatus.textContent = ""; };
  source.onerror = () => {
    if (!current()) return;
    source.close();
    els.connectionStatus.textContent = "重连中";
    state.reconnectTimer = setTimeout(() => { if (current()) connectEvents(); }, 1500);
  };
}

function handleEvent(raw) {
  let event;
  try { event = JSON.parse(raw.data); } catch { return; }
  if (event.type === "conversation_snapshot") { applySnapshot(event.payload); return; }
  if (event.seq <= state.lastSeq) return;
  state.lastSeq = event.seq;
  if (event.type === "conversation_renamed") {
    const conversation = state.conversations.find(item => item.id === state.activeId);
    if (conversation) conversation.title = event.payload.title;
    renderConversations(); return;
  }
  if (event.type === "user_message" || event.type === "assistant_message_started") {
    const message = event.payload.message;
    state.messages.set(message.id, message);
  }
  const message = state.messages.get(event.message_id);
  if (message) {
    if (event.type === "assistant_delta") message.content += event.payload.delta;
    if (event.type === "debate_step_started" || event.type === "debate_step_completed") {
      message.steps ||= {};
      message.steps[event.payload.key] = { ...event.payload, status: event.type === "debate_step_completed" ? "complete" : "running" };
    }
    if (event.type === "assistant_message_completed" || event.type === "assistant_error") {
      message.status = event.type === "assistant_error" ? "error" : event.payload.status || "complete";
      message.error = event.payload.error || "";
      loadConversations().catch(error => setError(error.message));
    }
  }
  scheduleRender();
}

function renderAttachments() {
  els.attachmentTray.replaceChildren();
  for (const attachment of state.attachments) {
    const card = document.createElement("div");
    card.className = "attachment-card";
    card.innerHTML = `${attachmentMarkup(attachment)}<button class="attachment-remove" type="button" aria-label="移除 ${escapeHtml(attachment.filename)}">×</button>`;
    card.querySelector("button").onclick = () => {
      state.attachments = state.attachments.filter(item => item.id !== attachment.id);
      renderAttachments(); updateComposer(); els.uploadStatus.textContent = "";
    };
    els.attachmentTray.append(card);
  }
}

async function uploadDocument() {
  const file = els.documentInput.files?.[0];
  if (!file) return;
  if (file.size > 8 * 1024 * 1024 || state.attachments.length >= 4) {
    setError(file.size > 8 * 1024 * 1024 ? "文件不能超过 8 MB。" : "每次最多添加 4 份文档。");
    els.documentInput.value = ""; return;
  }
  state.uploading = true; updateComposer(); setError();
  const epoch = state.epoch;
  els.uploadStatus.textContent = `正在解析 ${file.name}`;
  const form = new FormData(); form.append("file", file);
  try {
    const response = await fetch("/api/documents/parse", { method: "POST", body: form });
    const parsed = await response.json();
    if (!response.ok) throw new Error(parsed.detail || "文档解析失败");
    if (epoch !== state.epoch) return;
    state.attachments.push({ ...parsed, size: file.size, id: crypto.randomUUID() });
    renderAttachments();
    els.uploadStatus.textContent = parsed.warning || (parsed.truncated ? "文档过长，已截取前 12 万字符。" : "文档已加入");
  } catch (error) { if (epoch === state.epoch) setError(error.message); }
  finally { state.uploading = false; els.documentInput.value = ""; updateComposer(); }
}

async function sendPrompt(event) {
  event.preventDefault();
  if (state.submitting || state.uploading || state.loading) return;
  state.submitting = true; setError(); updateComposer();
  let targetId = state.activeId;
  const epoch = state.epoch;
  try {
    if (generating()) {
      await request(`/api/conversations/${targetId}/cancel`, { method: "POST" });
      return;
    }
    const draft = els.promptInput.value;
    const attachments = [...state.attachments];
    if (!draft.trim() && !attachments.length) return;
    const mode = state.activeMode;
    if (!targetId) {
      const created = await request("/api/conversations", { method: "POST", body: JSON.stringify({ title: "新的决策对话" }) });
      targetId = created.conversation.id;
      if (epoch !== state.epoch) return;
      state.activeId = targetId;
      connectEvents();
    }
    await request(`/api/conversations/${targetId}/messages`, {
      method: "POST", body: JSON.stringify({ content: draft.trim(), mode, attachments: attachments.map(({ id, warning, ...item }) => item) }),
    });
    if (epoch === state.epoch && targetId === state.activeId) {
      if (els.promptInput.value === draft) els.promptInput.value = "";
      state.attachments = state.attachments.filter(item => !attachments.some(sent => sent.id === item.id));
      renderAttachments(); resizeInput();
      els.uploadStatus.textContent = "";
      await loadConversations();
      scrollToBottom();
    }
  } catch (error) { if (epoch === state.epoch) setError(error.message); }
  finally { state.submitting = false; updateComposer(); }
}

async function loadSettings() {
  const settings = await request("/api/settings/model");
  els.modelSettings.open = !settings.configured;
  els.baseUrlInput.value = settings.base_url || "https://api.deepseek.com";
  els.modelInput.value = settings.model || "deepseek-chat";
  els.apiKeyInput.value = "";
  els.apiKeyInput.placeholder = settings.configured ? "已配置，留空保留" : "sk-...";
  els.modelBadge.textContent = settings.configured ? settings.model : "演示模式";
  els.settingsStatus.textContent = settings.configured ? "" : "未配置 Key，使用演示回复。";
}

async function saveSettings(event) {
  event.preventDefault();
  const button = els.settingsForm.querySelector("button"); button.disabled = true;
  try {
    await request("/api/settings/model", { method: "POST", body: JSON.stringify({ api_key: els.apiKeyInput.value, base_url: els.baseUrlInput.value, model: els.modelInput.value }) });
    await loadSettings();
  } catch (error) { els.settingsStatus.textContent = error.message; }
  finally { button.disabled = false; }
}

function toggleSidebar(open) {
  const isMobile = mobileLayout.matches;
  open = Boolean(open && isMobile);
  const wasOpen = els.sidebar.classList.contains("open");
  els.sidebar.classList.toggle("open", open);
  document.body.classList.toggle("sidebar-open", open);
  els.sidebar.inert = isMobile && !open;
  els.openSidebar.setAttribute("aria-expanded", String(open));
  for (const element of [els.masthead, els.mainContent, els.composerDock]) element.inert = open;
  if (open) { els.sidebar.setAttribute("role", "dialog"); els.sidebar.setAttribute("aria-modal", "true"); if (!wasOpen) els.closeSidebar.focus({ preventScroll: true }); }
  else { els.sidebar.removeAttribute("role"); els.sidebar.removeAttribute("aria-modal"); if (wasOpen && isMobile) els.openSidebar.focus({ preventScroll: true }); }
}

function resizeInput() {
  els.promptInput.style.height = "auto";
  els.promptInput.style.height = `${Math.min(200, els.promptInput.scrollHeight)}px`;
  updateComposer();
}

els.composer.addEventListener("submit", sendPrompt);
els.promptInput.addEventListener("input", resizeInput);
els.promptInput.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!generating()) els.composer.requestSubmit();
  }
});
els.documentInput.addEventListener("change", uploadDocument);
els.settingsForm.addEventListener("submit", saveSettings);
els.newChat.addEventListener("click", startNewConversation);
els.mobileNewChat.addEventListener("click", startNewConversation);
els.openSidebar.addEventListener("click", () => toggleSidebar(true));
els.closeSidebar.addEventListener("click", () => toggleSidebar(false));
els.sidebarBackdrop.addEventListener("click", () => toggleSidebar(false));
document.addEventListener("keydown", event => {
  if (!els.sidebar.classList.contains("open")) return;
  if (event.key === "Escape") toggleSidebar(false);
  if (event.key === "Tab") {
    const items = [...els.sidebar.querySelectorAll('button, input, summary, a[href]')].filter(item => item.getClientRects().length && !item.disabled);
    const first = items[0], last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
document.addEventListener("selectionchange", () => { if (!window.getSelection()?.toString()) scheduleRender(); });
document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => { state.activeMode = button.dataset.mode; updateComposer(); }));
mobileLayout.addEventListener("change", () => toggleSidebar(false));
window.addEventListener("pagehide", closeEvents);
window.addEventListener("pageshow", event => { if (event.persisted && state.activeId) selectConversation(state.activeId, { refresh: true }); });

async function init() {
  toggleSidebar(false);
  try {
    await Promise.all([loadConversations(), loadSettings()]);
    els.connectionStatus.textContent = "";
    if (state.conversations[0]) await selectConversation(state.conversations[0].id);
    else scheduleRender();
  } catch (error) { setError(error.message); els.connectionStatus.textContent = "连接失败，请刷新重试"; }
}
init();
