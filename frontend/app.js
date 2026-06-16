const state = {
  conversations: [],
  activeId: null,
  activeMode: "expert",
  eventSource: null,
  lastSeq: 0,
  messages: new Map(),
};

const els = {
  sidebar: document.getElementById("sidebar"),
  conversationList: document.getElementById("conversationList"),
  newChat: document.getElementById("newChat"),
  openSidebar: document.getElementById("openSidebar"),
  messages: document.getElementById("messages"),
  composer: document.getElementById("composer"),
  promptInput: document.getElementById("promptInput"),
  sendButton: document.getElementById("sendButton"),
  openSettings: document.getElementById("openSettings"),
  closeSettings: document.getElementById("closeSettings"),
  settingsDialog: document.getElementById("settingsDialog"),
  settingsForm: document.getElementById("settingsForm"),
  settingsStatus: document.getElementById("settingsStatus"),
  apiKeyInput: document.getElementById("apiKeyInput"),
  baseUrlInput: document.getElementById("baseUrlInput"),
  modelInput: document.getElementById("modelInput"),
};

function on(element, eventName, handler) {
  if (element) {
    element.addEventListener(eventName, handler);
  }
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return entities[char];
  });
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderMarkdown(markdown) {
  const lines = markdown.trim().split(/\r?\n/);
  const html = [];
  let listOpen = false;
  let paragraph = [];

  function flushParagraph() {
    if (paragraph.length) {
      html.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  }

  function closeList() {
    if (listOpen) {
      html.push("</ul>");
      listOpen = false;
    }
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) {
      flushParagraph();
      closeList();
      continue;
    }

    if (line.startsWith("|") && lines[index + 1]?.trim().match(/^\|?\s*:?-{3,}/)) {
      flushParagraph();
      closeList();
      const headers = line.split("|").filter(Boolean).map((cell) => cell.trim());
      index += 1;
      const rows = [];
      while (lines[index + 1]?.trim().startsWith("|")) {
        index += 1;
        rows.push(lines[index].split("|").filter(Boolean).map((cell) => cell.trim()));
      }
      html.push("<table><thead><tr>");
      headers.forEach((cell) => html.push(`<th>${renderInlineMarkdown(cell)}</th>`));
      html.push("</tr></thead><tbody>");
      rows.forEach((row) => {
        html.push("<tr>");
        row.forEach((cell) => html.push(`<td>${renderInlineMarkdown(cell)}</td>`));
        html.push("</tr>");
      });
      html.push("</tbody></table>");
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length + 1;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${renderInlineMarkdown(bullet[1])}</li>`);
      continue;
    }

    paragraph.push(line);
  }

  flushParagraph();
  closeList();
  return html.join("");
}

function splitAssistantContent(content) {
  const marker = "## 裁判总结";
  const index = content.indexOf(marker);
  if (index === -1) {
    return { thinking: content, final: "" };
  }
  return {
    thinking: content.slice(0, index).trim(),
    final: content.slice(index + marker.length).trim(),
  };
}

function renderAssistantContent(content, isStreaming) {
  const { thinking, final } = splitAssistantContent(content || "");
  const parts = [];
  if (thinking) {
    parts.push(`
      <details class="thinking" ${final ? "" : "open"}>
        <summary>已思考</summary>
        <div class="thinking-body">${renderMarkdown(thinking)}</div>
      </details>
    `);
  }
  if (final) {
    parts.push(`<div class="final-answer">${renderMarkdown(final)}</div>`);
  }
  if (!thinking && !final) {
    parts.push("");
  }
  if (isStreaming) {
    parts.push('<span class="stream-caret"></span>');
  }
  return parts.join("");
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function groupLabel(timestamp) {
  const date = new Date(timestamp * 1000);
  const today = new Date();
  const diff = today.setHours(0, 0, 0, 0) - date.setHours(0, 0, 0, 0);
  if (diff <= 0) return "今天";
  if (diff <= 86400000) return "昨天";
  if (diff <= 7 * 86400000) return "7 天内";
  return "30 天内";
}

function renderConversations() {
  let currentGroup = "";
  els.conversationList.innerHTML = "";

  for (const item of state.conversations) {
    const label = groupLabel(item.updated_at);
    if (label !== currentGroup) {
      currentGroup = label;
      const group = document.createElement("div");
      group.className = "conversation-group";
      group.textContent = label;
      els.conversationList.appendChild(group);
    }

    const row = document.createElement("div");
    row.className = "conversation-item";
    if (item.id === state.activeId) row.classList.add("active");
    row.innerHTML = `
      <button class="conversation-title" type="button">${escapeHtml(item.title)}</button>
      <button class="delete-conversation" type="button">删除</button>
    `;
    row.querySelector(".conversation-title").addEventListener("click", () => selectConversation(item.id));
    row.querySelector(".delete-conversation").addEventListener("click", async (event) => {
      event.stopPropagation();
      await deleteConversation(item.id);
    });
    els.conversationList.appendChild(row);
  }
}

function scrollToBottom() {
  requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }));
}

function renderMessage(message) {
  let node = state.messages.get(message.id);
  if (!node) {
    node = document.createElement("article");
    node.className = `message ${message.role}`;
    node.innerHTML = `
      <div class="message-role">${message.role === "user" ? "你" : "Decision Arena"}</div>
      <div class="bubble"></div>
    `;
    els.messages.appendChild(node);
    state.messages.set(message.id, node);
  }
  const bubble = node.querySelector(".bubble");
  bubble.classList.toggle("streaming", message.status === "streaming");
  if (message.role === "assistant") {
    bubble.dataset.rawContent = message.content || "";
    bubble.innerHTML = renderAssistantContent(message.content || "", message.status === "streaming");
  } else {
    bubble.innerHTML = escapeHtml(message.content || "");
  }
}

function resetChat() {
  state.messages.clear();
  els.messages.innerHTML = "";
  state.lastSeq = 0;
}

async function loadConversations() {
  const data = await request("/api/conversations");
  state.conversations = data.conversations;
  renderConversations();
}

async function createConversation(title = "新的决策对话") {
  const data = await request("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  await loadConversations();
  await selectConversation(data.conversation.id);
  return data.conversation;
}

async function selectConversation(id) {
  state.activeId = id;
  resetChat();
  renderConversations();
  els.sidebar.classList.remove("open");
  if (state.eventSource) state.eventSource.close();

  const data = await request(`/api/conversations/${id}`);
  data.messages.forEach(renderMessage);
  connectEvents();
  scrollToBottom();
}

async function deleteConversation(id) {
  await request(`/api/conversations/${id}`, { method: "DELETE" });
  if (state.activeId === id) {
    state.activeId = null;
    resetChat();
    if (state.eventSource) state.eventSource.close();
  }
  await loadConversations();
  if (!state.activeId && state.conversations[0]) {
    await selectConversation(state.conversations[0].id);
  }
}

function connectEvents() {
  if (!state.activeId) return;
  if (state.eventSource) state.eventSource.close();
  state.eventSource = new EventSource(`/api/conversations/${state.activeId}/events?after=${state.lastSeq}`);
  state.eventSource.onmessage = handleEvent;
  ["user_message", "assistant_message_started", "assistant_delta", "assistant_message_completed", "assistant_error"].forEach(
    (name) => state.eventSource.addEventListener(name, handleEvent),
  );
  state.eventSource.onerror = () => {
    state.eventSource.close();
    setTimeout(connectEvents, 1200);
  };
}

function handleEvent(raw) {
  const event = JSON.parse(raw.data);
  state.lastSeq = Math.max(state.lastSeq, event.seq);

  if (event.type === "user_message" || event.type === "assistant_message_started") {
    renderMessage(event.payload.message);
  }

  if (event.type === "assistant_delta") {
    const node = state.messages.get(event.message_id);
    if (node) {
      const bubble = node.querySelector(".bubble");
      bubble.classList.add("streaming");
      const current = bubble.dataset.rawContent || "";
      const next = current + event.payload.delta;
      bubble.dataset.rawContent = next;
      bubble.innerHTML = renderAssistantContent(next, true);
    }
  }

  if (event.type === "assistant_message_completed") {
    const node = state.messages.get(event.message_id);
    if (node) {
      const bubble = node.querySelector(".bubble");
      bubble.classList.remove("streaming");
      bubble.innerHTML = renderAssistantContent(bubble.dataset.rawContent || bubble.textContent || "", false);
    }
    loadConversations();
  }

  if (event.type === "assistant_error") {
    const node = state.messages.get(event.message_id);
    if (node) {
      const bubble = node.querySelector(".bubble");
      bubble.classList.remove("streaming");
      bubble.innerHTML += `\n\n生成失败：${escapeHtml(event.payload.error)}`;
    }
  }

  scrollToBottom();
}

async function sendPrompt(event) {
  event.preventDefault();
  const content = els.promptInput.value.trim();
  if (!content) return;
  if (!state.activeId) {
    await createConversation(content.slice(0, 22));
  }
  els.promptInput.value = "";
  els.sendButton.classList.remove("ready");
  els.promptInput.style.height = "auto";
  await request(`/api/conversations/${state.activeId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content, mode: state.activeMode }),
  });
  await loadConversations();
}

async function loadSettings() {
  const settings = await request("/api/settings/model");
  els.baseUrlInput.value = settings.base_url || "https://api.deepseek.com";
  els.modelInput.value = settings.model || "deepseek-chat";
  els.apiKeyInput.value = "";
  els.apiKeyInput.placeholder = settings.configured ? `已配置：${settings.api_key_hint}` : "sk-...";
  els.settingsStatus.textContent = settings.configured ? `已配置 ${settings.api_key_hint}` : "未配置 API Key，将使用本地演示回复";
}

async function saveSettings(event) {
  event.preventDefault();
  const settings = await request("/api/settings/model", {
    method: "POST",
    body: JSON.stringify({
      api_key: els.apiKeyInput.value,
      base_url: els.baseUrlInput.value,
      model: els.modelInput.value,
    }),
  });
  els.apiKeyInput.value = "";
  els.apiKeyInput.placeholder = settings.configured ? `已配置：${settings.api_key_hint}` : "sk-...";
  els.settingsStatus.textContent = settings.configured ? `已保存 ${settings.api_key_hint}` : "未配置 API Key";
  setTimeout(() => els.settingsDialog.close(), 450);
}

window.openModelSettings = async function openModelSettings() {
  await loadSettings();
  if (!els.settingsDialog.open) {
    els.settingsDialog.showModal();
  }
};
on(els.closeSettings, "click", () => els.settingsDialog.close());
on(els.settingsForm, "submit", saveSettings);
on(els.newChat, "click", () => createConversation());
on(els.openSidebar, "click", () => els.sidebar.classList.add("open"));
on(els.composer, "submit", sendPrompt);
on(els.promptInput, "input", () => {
  els.sendButton.classList.toggle("ready", Boolean(els.promptInput.value.trim()));
  els.promptInput.style.height = "auto";
  els.promptInput.style.height = `${els.promptInput.scrollHeight}px`;
});
on(els.promptInput, "keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.composer.requestSubmit();
  }
});

loadConversations().then(async () => {
  const first = state.conversations[0];
  if (first) {
    await selectConversation(first.id);
  }
});

window.renderAssistantContent = renderAssistantContent;
