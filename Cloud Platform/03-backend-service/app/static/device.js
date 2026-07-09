const deviceElements = {
  form: document.getElementById("device-profile-form"),
  label: document.getElementById("device-label"),
  participantId: document.getElementById("device-participant-id"),
  feedback: document.getElementById("device-feedback"),
  status: document.getElementById("device-status"),
  statusDot: document.getElementById("device-status-dot"),
  labelPreview: document.getElementById("device-label-preview"),
  sessionCount: document.getElementById("device-session-count"),
  refreshButton: document.getElementById("device-refresh-button"),
  sessionList: document.getElementById("device-session-list"),
  empty: document.getElementById("device-empty"),
  serverBaseUrl: document.getElementById("device-server-base-url"),
  serverFeedback: document.getElementById("device-server-feedback"),
  saveServerButton: document.getElementById("device-save-server-button"),
  resetServerButton: document.getElementById("device-reset-server-button"),
  agentSaveButton: document.getElementById("device-agent-save-button"),
  agentStartupOnButton: document.getElementById("device-agent-startup-on-button"),
  agentStartupOffButton: document.getElementById("device-agent-startup-off-button"),
  agentFeedback: document.getElementById("device-agent-feedback"),
  alertOverlay: document.getElementById("device-alert-overlay"),
  alertTitle: document.getElementById("device-alert-title"),
  alertMessage: document.getElementById("device-alert-message"),
  alertMeta: document.getElementById("device-alert-meta"),
  alertOpenButton: document.getElementById("device-alert-open-button"),
  alertDismissButton: document.getElementById("device-alert-dismiss-button"),
};

const defaultTitle = document.title;
let liveSessions = [];
let alertedSessionIds = new Set();
let alertQueue = [];
let activeAlertSessionId = "";
let currentAlertStoreKey = "";
let desktopAgentAutoStartup = false;

function loadDeviceProfile() {
  return {
    label: localStorage.getItem("device_demo_label") || "",
    participant_id: localStorage.getItem("device_demo_participant_id") || `device-${crypto.randomUUID()}`,
  };
}

function saveDeviceProfile(profile) {
  localStorage.setItem("device_demo_label", profile.label);
  localStorage.setItem("device_demo_participant_id", profile.participant_id);
}

function applyDeviceProfile(profile) {
  deviceElements.label.value = profile.label;
  deviceElements.participantId.value = profile.participant_id;
  deviceElements.labelPreview.textContent = profile.label || "-";
}

function updateDeviceStatus(isReady) {
  deviceElements.status.textContent = isReady ? "已就绪" : "待命中";
  deviceElements.statusDot.style.background = isReady ? "var(--ok)" : "var(--accent)";
}

function openVoiceRoom(sessionId, profile) {
  AppApi.openRoomWindow(sessionId, {
    role: "device",
    label: profile.label || "设备端",
    participant_id: profile.participant_id,
  });
}

function localDesktopFetch(path, options) {
  return fetch(new URL(path, `${window.location.origin}/`).toString(), options);
}

function buildDesktopAgentConfig(autoStartup = desktopAgentAutoStartup) {
  const profile = loadDeviceProfile();
  return {
    enabled: true,
    role: "device",
    backend_base_url: AppApi.getBaseUrl(),
    app_user_id: "",
    recipient_name: "",
    participant_id: profile.participant_id,
    label: profile.label,
    platform: "web",
    auto_startup: autoStartup,
  };
}

function renderDesktopAgentFeedback(payload) {
  const running = payload?.running ? "运行中" : "未运行";
  const startup = payload?.startup_enabled ? "已开启开机自启" : "未开启开机自启";
  const role = payload?.config?.role || "device";
  deviceElements.agentFeedback.textContent = `后台预警状态：${running}；${startup}；当前模式：${role}。`;
}

async function loadDesktopAgentState() {
  const response = await localDesktopFetch("/api/local/desktop-alert-agent");
  if (!response.ok) {
    throw new Error("Failed to load desktop alert agent state.");
  }
  const data = await response.json();
  desktopAgentAutoStartup = Boolean(data.config?.auto_startup || data.startup_enabled);
  renderDesktopAgentFeedback(data);
  return data;
}

async function saveDesktopAgentConfig(autoStartup = desktopAgentAutoStartup) {
  const response = await localDesktopFetch("/api/local/desktop-alert-agent/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildDesktopAgentConfig(autoStartup)),
  });
  if (!response.ok) {
    throw new Error("Failed to save desktop alert agent config.");
  }
  const data = await response.json();
  desktopAgentAutoStartup = Boolean(data.config?.auto_startup || data.startup_enabled);
  renderDesktopAgentFeedback(data);
  return data;
}

async function startDesktopAgent(autoStartup = desktopAgentAutoStartup) {
  await saveDesktopAgentConfig(autoStartup);
  const response = await localDesktopFetch("/api/local/desktop-alert-agent/start", {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("Failed to start desktop alert agent.");
  }
  await loadDesktopAgentState();
}

async function setDesktopAgentStartup(enabled) {
  const response = await localDesktopFetch("/api/local/desktop-alert-agent/startup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    throw new Error("Failed to update desktop alert agent startup.");
  }
  desktopAgentAutoStartup = enabled;
  await saveDesktopAgentConfig(enabled);
  await loadDesktopAgentState();
}

function syncDesktopAgentConfigSilently() {
  saveDesktopAgentConfig().catch(() => {});
}

function alertStoreKey() {
  const base = AppApi.getBaseUrl().replace(/[^\w]/g, "_");
  return `device_alerted_sessions::${base}`;
}

function syncAlertStore() {
  const nextKey = alertStoreKey();
  if (nextKey === currentAlertStoreKey) {
    return;
  }
  currentAlertStoreKey = nextKey;
  try {
    const raw = localStorage.getItem(currentAlertStoreKey);
    const parsed = raw ? JSON.parse(raw) : [];
    alertedSessionIds = new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    alertedSessionIds = new Set();
  }
  alertQueue = [];
  activeAlertSessionId = "";
  hideEmergencyAlert(false);
}

function persistAlertStore() {
  const recentIds = Array.from(alertedSessionIds).slice(-80);
  localStorage.setItem(currentAlertStoreKey, JSON.stringify(recentIds));
}

function updateWindowTitle(forceAlert = false) {
  if (forceAlert || activeAlertSessionId) {
    document.title = `【紧急会话】${defaultTitle}`;
    return;
  }
  if (liveSessions.length) {
    document.title = `(${liveSessions.length}) ${defaultTitle}`;
    return;
  }
  document.title = defaultTitle;
}

function renderSessions(items) {
  liveSessions = items;
  deviceElements.sessionList.innerHTML = "";
  deviceElements.sessionCount.textContent = String(items.length);
  deviceElements.empty.style.display = items.length ? "none" : "block";
  updateWindowTitle();

  items.forEach((session) => {
    const card = document.createElement("article");
    card.className = "event-card";
    card.innerHTML = `
      <div class="event-meta">
        <span class="severity-pill">${session.status}</span>
        <span class="label">${session.created_at || "-"}</span>
      </div>
      <h3>${session.recipient_name}</h3>
      <p class="panel-note">${session.detail || ""}</p>
      <div class="attempt-tags">
        <span class="chip">会话: ${session.session_id}</span>
        <span class="chip">家属账号: ${session.app_user_id}</span>
      </div>
      <div class="button-row">
        <button class="primary-button" data-session-id="${session.session_id}" type="button">加入设备端语音</button>
      </div>
    `;
    deviceElements.sessionList.appendChild(card);
  });
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function buildAlertChips(session) {
  const chips = [
    `设备名称: ${loadDeviceProfile().label || "未设置"}`,
    `家属账号: ${session.app_user_id || "-"}`,
    `会话 ID: ${session.session_id}`,
    `时间: ${session.created_at || "-"}`,
  ];
  return chips
    .map((item) => `<span class="chip">${escapeHtml(item)}</span>`)
    .join("");
}

function hideEmergencyAlert(showNext = true) {
  deviceElements.alertOverlay.classList.remove("is-visible");
  deviceElements.alertOverlay.setAttribute("aria-hidden", "true");
  document.body.classList.remove("alert-open");
  activeAlertSessionId = "";
  updateWindowTitle();
  if (showNext) {
    window.setTimeout(() => {
      showNextEmergencyAlert();
    }, 120);
  }
}

function tryDesktopNotification(session) {
  if (!("Notification" in window)) {
    return;
  }

  if (Notification.permission === "default") {
    Notification.requestPermission().then(() => {
      tryDesktopNotification(session);
    }).catch(() => {});
    return;
  }

  if (Notification.permission !== "granted") {
    return;
  }

  const note = new Notification("新的紧急会话", {
    body: `${session.detail || "主机已经发起新的紧急语音会话，请尽快进入。"}`,
    tag: `device-${session.session_id}`,
  });
  note.onclick = () => {
    window.focus();
  };
}

function tryPlayAlertTone() {
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) {
    return;
  }

  try {
    const context = new AudioContextCtor();
    if (context.state === "suspended") {
      context.resume().catch(() => {});
    }
    const start = context.currentTime;
    [0, 0.26, 0.52].forEach((offset, index) => {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "triangle";
      oscillator.frequency.value = index % 2 === 0 ? 680 : 830;
      gain.gain.setValueAtTime(0.0001, start + offset);
      gain.gain.exponentialRampToValueAtTime(0.03, start + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.18);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start(start + offset);
      oscillator.stop(start + offset + 0.2);
    });
    window.setTimeout(() => {
      context.close().catch(() => {});
    }, 1200);
  } catch {
    // Ignore audio failures in embedded desktop webviews.
  }
}

function showEmergencyAlert(session) {
  activeAlertSessionId = session.session_id;
  deviceElements.alertTitle.textContent = "收到新的紧急会话";
  deviceElements.alertMessage.textContent = session.detail || "主机已经发起新的紧急呼叫，请尽快进入对应会话。";
  deviceElements.alertMeta.innerHTML = buildAlertChips(session);
  deviceElements.alertOpenButton.dataset.sessionId = session.session_id;
  deviceElements.alertOverlay.classList.add("is-visible");
  deviceElements.alertOverlay.setAttribute("aria-hidden", "false");
  document.body.classList.add("alert-open");
  updateWindowTitle(true);
  tryDesktopNotification(session);
  tryPlayAlertTone();
}

function showNextEmergencyAlert() {
  if (activeAlertSessionId || !alertQueue.length) {
    return;
  }
  const nextSession = alertQueue.shift();
  if (!nextSession) {
    return;
  }
  showEmergencyAlert(nextSession);
}

function queueEmergencyAlerts(items) {
  syncAlertStore();
  let hasNewAlert = false;

  items
    .slice()
    .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")))
    .forEach((session) => {
      const alreadyQueued = alertQueue.some((item) => item.session_id === session.session_id);
      const alreadyActive = activeAlertSessionId === session.session_id;
      if (alertedSessionIds.has(session.session_id) || alreadyQueued || alreadyActive) {
        return;
      }
      alertedSessionIds.add(session.session_id);
      alertQueue.push(session);
      hasNewAlert = true;
    });

  if (hasNewAlert) {
    persistAlertStore();
  }

  showNextEmergencyAlert();
}

async function loadLiveSessions() {
  syncAlertStore();
  const response = await AppApi.apiFetch("/api/live-sessions");
  if (!response.ok) {
    throw new Error("Failed to load live sessions.");
  }
  const data = await response.json();
  const items = data.items || [];
  renderSessions(items);
  queueEmergencyAlerts(items);
}

async function saveServerBaseUrl() {
  const normalized = AppApi.setBaseUrl(deviceElements.serverBaseUrl.value);
  deviceElements.serverBaseUrl.value = normalized;
  deviceElements.serverFeedback.textContent = `已连接共享后端：${normalized}`;
  syncAlertStore();
  syncDesktopAgentConfigSilently();
  await loadLiveSessions();
}

function resetServerBaseUrl() {
  AppApi.clearBaseUrl();
  deviceElements.serverBaseUrl.value = AppApi.getBaseUrl();
  deviceElements.serverFeedback.textContent = "已恢复为当前电脑本地后端。";
  syncAlertStore();
  syncDesktopAgentConfigSilently();
}

function ensureDeviceReady(profile) {
  if (profile.label) {
    return true;
  }
  deviceElements.feedback.textContent = "请先填写设备显示名称，再加入语音房间。";
  deviceElements.label.focus();
  return false;
}

deviceElements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const profile = {
    label: deviceElements.label.value.trim(),
    participant_id: deviceElements.participantId.value.trim(),
  };
  saveDeviceProfile(profile);
  applyDeviceProfile(profile);
  updateDeviceStatus(Boolean(profile.label));
  deviceElements.feedback.textContent = "设备端本地身份已保存。";
  syncDesktopAgentConfigSilently();
});

deviceElements.refreshButton.addEventListener("click", () => {
  loadLiveSessions().catch(() => {
    deviceElements.feedback.textContent = "刷新会话失败，请检查共享后端地址。";
  });
});

deviceElements.saveServerButton.addEventListener("click", () => {
  saveServerBaseUrl().catch(() => {
    deviceElements.serverFeedback.textContent = "连接共享后端失败，请检查地址和端口。";
  });
});

deviceElements.resetServerButton.addEventListener("click", () => {
  resetServerBaseUrl();
  loadLiveSessions().catch(() => {});
});

deviceElements.agentSaveButton.addEventListener("click", () => {
  startDesktopAgent().catch(() => {
    deviceElements.agentFeedback.textContent = "启动后台预警失败，请先确认你是在本机桌面版里操作。";
  });
});

deviceElements.agentStartupOnButton.addEventListener("click", () => {
  startDesktopAgent(true).catch(() => {
    deviceElements.agentFeedback.textContent = "开启开机自启失败，请稍后再试。";
  });
});

deviceElements.agentStartupOffButton.addEventListener("click", () => {
  setDesktopAgentStartup(false).catch(() => {
    deviceElements.agentFeedback.textContent = "关闭开机自启失败，请稍后再试。";
  });
});

deviceElements.sessionList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-session-id]");
  if (!button) return;
  const profile = loadDeviceProfile();
  if (!ensureDeviceReady(profile)) {
    return;
  }
  openVoiceRoom(button.dataset.sessionId, profile);
});

deviceElements.alertOpenButton.addEventListener("click", () => {
  const sessionId = deviceElements.alertOpenButton.dataset.sessionId;
  if (!sessionId) return;
  const profile = loadDeviceProfile();
  if (!ensureDeviceReady(profile)) {
    return;
  }
  hideEmergencyAlert(false);
  openVoiceRoom(sessionId, profile);
});

deviceElements.alertDismissButton.addEventListener("click", () => {
  hideEmergencyAlert();
});

deviceElements.alertOverlay.addEventListener("click", (event) => {
  if (event.target === deviceElements.alertOverlay || event.target.classList.contains("alert-backdrop")) {
    hideEmergencyAlert();
  }
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && activeAlertSessionId) {
    hideEmergencyAlert();
  }
});

const deviceProfile = loadDeviceProfile();
saveDeviceProfile(deviceProfile);
applyDeviceProfile(deviceProfile);
updateDeviceStatus(Boolean(deviceProfile.label));
deviceElements.serverBaseUrl.value = AppApi.getBaseUrl();
syncAlertStore();
loadDesktopAgentState().catch(() => {
  deviceElements.agentFeedback.textContent = "当前环境未连接到本机桌面守护接口；如果你用的是桌面版，这里稍后可重试。";
});
loadLiveSessions().catch(() => {});

setInterval(() => {
  loadLiveSessions().catch(() => {});
}, 3500);
