const state = {
  conversations: [],
  activeId: null,
  activeMode: "quick",
  eventSource: null,
  lastSeq: 0,
  messages: new Map(),
};

const els = {
  sidebar: document.getElementById("sidebar"),
  conversationList: document.getElementById("conversationList"),
  newChat: document.getElementById("newChat"),
  searchInput: document.getElementById("searchInput"),
  openSidebar: document.getElementById("openSidebar"),
  collapseSidebar: document.getElementById("collapseSidebar"),
  welcome: document.getElementById("welcome"),
  messages: document.getElementById("messages"),
  composer: document.getElementById("composer"),
  promptInput: document.getElementById("promptInput"),
  sendButton: document.getElementById("sendButton"),
};

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return entities[char];
  });
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
  if (diff <= 7 * 86400000) return "7天内";
  return "30天内";
}

function renderConversations() {
  const keyword = els.searchInput.value.trim().toLowerCase();
  const items = state.conversations.filter((item) => item.title.toLowerCase().includes(keyword));
  let currentGroup = "";
  els.conversationList.innerHTML = "";

  for (const item of items) {
    const label = groupLabel(item.updated_at);
    if (label !== currentGroup) {
      currentGroup = label;
      const group = document.createElement("div");
      group.className = "conversation-group";
      group.textContent = label;
      els.conversationList.appendChild(group);
    }

    const button = document.createElement("button");
    button.className = "conversation-item";
    if (item.id === state.activeId) button.classList.add("active");
    button.textContent = item.title;
    button.addEventListener("click", () => selectConversation(item.id));
    els.conversationList.appendChild(button);
  }
}

function setMode(mode) {
  state.activeMode = mode;
  document.querySelectorAll(".mode-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mode === mode);
  });
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
  bubble.innerHTML = escapeHtml(message.content || "");
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
  els.welcome.style.display = data.messages.length ? "none" : "grid";
  data.messages.forEach(renderMessage);
  connectEvents();
  scrollToBottom();
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
  els.welcome.style.display = "none";

  if (event.type === "user_message" || event.type === "assistant_message_started") {
    renderMessage(event.payload.message);
  }

  if (event.type === "assistant_delta") {
    const id = event.message_id;
    const node = state.messages.get(id);
    if (node) {
      const bubble = node.querySelector(".bubble");
      bubble.classList.add("streaming");
      bubble.innerHTML += escapeHtml(event.payload.delta);
    }
  }

  if (event.type === "assistant_message_completed") {
    const node = state.messages.get(event.message_id);
    if (node) node.querySelector(".bubble").classList.remove("streaming");
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
}

els.newChat.addEventListener("click", () => createConversation());
els.searchInput.addEventListener("input", renderConversations);
els.openSidebar.addEventListener("click", () => els.sidebar.classList.add("open"));
els.collapseSidebar.addEventListener("click", () => els.sidebar.classList.toggle("open"));
els.composer.addEventListener("submit", sendPrompt);
els.promptInput.addEventListener("input", () => {
  els.sendButton.classList.toggle("ready", Boolean(els.promptInput.value.trim()));
  els.promptInput.style.height = "auto";
  els.promptInput.style.height = `${els.promptInput.scrollHeight}px`;
});
els.promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.composer.requestSubmit();
  }
});
document.querySelectorAll(".mode-tab").forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));
document.querySelectorAll("[data-mode-shortcut]").forEach((button) => {
  button.addEventListener("click", () => {
    setMode(button.dataset.modeShortcut);
    els.promptInput.focus();
  });
});

loadConversations().then(async () => {
  const first = state.conversations[0];
  if (first) {
    await selectConversation(first.id);
  }
});
