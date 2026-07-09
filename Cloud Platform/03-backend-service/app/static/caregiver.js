const mobileElements = {
  form: document.getElementById("mobile-register-form"),
  recipientName: document.getElementById("mobile-recipient-name"),
  appUserId: document.getElementById("mobile-app-user-id"),
  externalKey: document.getElementById("mobile-external-key"),
  platform: document.getElementById("mobile-platform"),
  deviceToken: document.getElementById("mobile-device-token"),
  feedback: document.getElementById("mobile-register-feedback"),
  status: document.getElementById("mobile-status"),
  statusDot: document.getElementById("mobile-status-dot"),
  currentUser: document.getElementById("mobile-app-user"),
  pendingCount: document.getElementById("mobile-pending-count"),
  refreshButton: document.getElementById("mobile-refresh-button"),
  pendingList: document.getElementById("mobile-pending-list"),
  pendingEmpty: document.getElementById("mobile-pending-empty"),
  historyList: document.getElementById("mobile-history-list"),
  historyEmpty: document.getElementById("mobile-history-empty"),
  demoHint: document.getElementById("mobile-demo-hint"),
  demoAccounts: document.getElementById("mobile-demo-accounts"),
  serverBaseUrl: document.getElementById("mobile-server-base-url"),
  serverFeedback: document.getElementById("mobile-server-feedback"),
  saveServerButton: document.getElementById("mobile-save-server-button"),
  resetServerButton: document.getElementById("mobile-reset-server-button"),
  agentSaveButton: document.getElementById("mobile-agent-save-button"),
  agentStartupOnButton: document.getElementById("mobile-agent-startup-on-button"),
  agentStartupOffButton: document.getElementById("mobile-agent-startup-off-button"),
  agentFeedback: document.getElementById("mobile-agent-feedback"),
  alertOverlay: document.getElementById("caregiver-alert-overlay"),
  alertTitle: document.getElementById("caregiver-alert-title"),
  alertMessage: document.getElementById("caregiver-alert-message"),
  alertMeta: document.getElementById("caregiver-alert-meta"),
  alertAcceptButton: document.getElementById("caregiver-alert-accept-button"),
  alertDismissButton: document.getElementById("caregiver-alert-dismiss-button"),
  alertRejectButton: document.getElementById("caregiver-alert-reject-button"),
};

const defaultTitle = document.title;
let availableRecipients = [];
let pendingSessions = [];
let alertedSessionIds = new Set();
let alertQueue = [];
let activeAlertSessionId = "";
let currentAlertStoreKey = "";
let desktopAgentAutoStartup = false;

function createParticipantId() {
  return `caregiver-${crypto.randomUUID()}`;
}

function loadLocalProfile() {
  return {
    app_user_id: localStorage.getItem("caregiver_app_user_id") || "",
    recipient_name: localStorage.getItem("caregiver_recipient_name") || "",
    external_key: localStorage.getItem("caregiver_external_key") || "",
    platform: localStorage.getItem("caregiver_platform") || "web",
    device_token: localStorage.getItem("caregiver_device_token") || `web-demo-${crypto.randomUUID()}`,
    participant_id: localStorage.getItem("caregiver_participant_id") || createParticipantId(),
  };
}

function saveLocalProfile(profile) {
  localStorage.setItem("caregiver_app_user_id", profile.app_user_id);
  localStorage.setItem("caregiver_recipient_name", profile.recipient_name);
  localStorage.setItem("caregiver_external_key", profile.external_key || "");
  localStorage.setItem("caregiver_platform", profile.platform);
  localStorage.setItem("caregiver_device_token", profile.device_token);
  localStorage.setItem("caregiver_participant_id", profile.participant_id);
}

function fillRegistrationForm(profile) {
  mobileElements.appUserId.value = profile.app_user_id;
  mobileElements.recipientName.value = profile.recipient_name;
  mobileElements.externalKey.value = profile.external_key || "";
  mobileElements.platform.value = profile.platform;
  mobileElements.deviceToken.value = profile.device_token;
}

function applyRecipient(recipient, keepDeviceToken = false) {
  const current = loadLocalProfile();
  const nextProfile = {
    app_user_id: recipient.app_user_id,
    recipient_name: recipient.recipient_name,
    external_key: recipient.profile_external_key || current.external_key || "",
    platform: recipient.platform || "web",
    device_token: keepDeviceToken ? current.device_token : mobileElements.deviceToken.value.trim(),
    participant_id: current.participant_id,
  };
  saveLocalProfile(nextProfile);
  fillRegistrationForm(nextProfile);
  syncAlertStore(nextProfile.app_user_id);
  syncDesktopAgentConfigSilently();
}

function findAvailableRecipient(appUserId) {
  return availableRecipients.find((item) => item.app_user_id === appUserId) || null;
}

function alertStoreKey(appUserId = loadLocalProfile().app_user_id) {
  const base = AppApi.getBaseUrl().replace(/[^\w]/g, "_");
  return `caregiver_alerted_sessions::${base}::${appUserId || "anonymous"}`;
}

function syncAlertStore(appUserId = loadLocalProfile().app_user_id) {
  const nextKey = alertStoreKey(appUserId);
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
    document.title = `【紧急告警】${defaultTitle}`;
    return;
  }
  if (pendingSessions.length) {
    document.title = `(${pendingSessions.length}) ${defaultTitle}`;
    return;
  }
  document.title = defaultTitle;
}

function renderDemoAccounts() {
  mobileElements.demoAccounts.innerHTML = "";

  if (!availableRecipients.length) {
    mobileElements.demoHint.textContent = "当前还没有现成账号。你也可以直接手动填写自己的 APP 用户 ID，并在第一次注册时补上关联对象编号。";
    return;
  }

  mobileElements.demoHint.textContent = `当前可直接注册的家属账号：${availableRecipients
    .map((item) => `${item.recipient_name}（${item.app_user_id}）`)
    .join("、")}。如果你想用自己的新 ID，也可以直接手动输入，并在第一次注册时填“关联对象编号”。`;

  availableRecipients.forEach((recipient) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost-button";
    button.dataset.appUserId = recipient.app_user_id;
    button.textContent = `${recipient.recipient_name} / ${recipient.app_user_id}`;
    mobileElements.demoAccounts.appendChild(button);
  });
}

function localDesktopFetch(path, options) {
  return fetch(new URL(path, `${window.location.origin}/`).toString(), options);
}

function buildDesktopAgentConfig(autoStartup = desktopAgentAutoStartup) {
  const profile = loadLocalProfile();
  return {
    enabled: true,
    role: "caregiver",
    backend_base_url: AppApi.getBaseUrl(),
    app_user_id: profile.app_user_id,
    recipient_name: profile.recipient_name,
    external_key: profile.external_key || "",
    participant_id: profile.participant_id,
    label: profile.recipient_name || profile.app_user_id,
    platform: profile.platform || "web",
    auto_startup: autoStartup,
  };
}

function renderDesktopAgentFeedback(payload) {
  const running = payload?.running ? "运行中" : "未运行";
  const startup = payload?.startup_enabled ? "已开启开机自启" : "未开启开机自启";
  const role = payload?.config?.role || "caregiver";
  mobileElements.agentFeedback.textContent = `后台预警状态：${running}；${startup}；当前模式：${role}。`;
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

async function loadAvailableRecipients() {
  const response = await AppApi.apiFetch("/api/dashboard");
  if (!response.ok) {
    throw new Error("Failed to load dashboard.");
  }

  const data = await response.json();
  availableRecipients = (data.profiles || []).flatMap((profile) => profile.app_recipients || []);
  renderDemoAccounts();

  const localProfile = loadLocalProfile();
  const currentRecipient = findAvailableRecipient(localProfile.app_user_id);
  if (currentRecipient) {
    applyRecipient(currentRecipient, true);
    return;
  }

  if (availableRecipients.length) {
    applyRecipient(availableRecipients[0], true);
    mobileElements.feedback.textContent = `已自动切换到可用家属账号 ${availableRecipients[0].app_user_id}。`;
  }
}

function openVoiceRoom(sessionId, profile) {
  AppApi.openRoomWindow(sessionId, {
    role: "caregiver",
    label: profile.recipient_name || profile.app_user_id || "家属端",
    participant_id: profile.participant_id,
  });
}

function setRegistrationState(registered, appUserId = "") {
  mobileElements.status.textContent = registered ? "已注册" : "未注册";
  mobileElements.currentUser.textContent = appUserId || "-";
  mobileElements.statusDot.style.background = registered ? "var(--ok)" : "var(--accent)";
}

function renderPendingSessions(items) {
  pendingSessions = items;
  mobileElements.pendingList.innerHTML = "";
  mobileElements.pendingEmpty.style.display = items.length ? "none" : "block";
  mobileElements.pendingCount.textContent = String(items.length);
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
        <span class="chip">平台: ${session.platform}</span>
      </div>
      <div class="button-row">
        <button class="primary-button" data-status="accepted" data-session-id="${session.session_id}" type="button">接听并进入语音</button>
        <button class="ghost-button" data-status="rejected" data-session-id="${session.session_id}" type="button">拒绝</button>
        <button class="ghost-button" data-status="ended" data-session-id="${session.session_id}" type="button">挂断</button>
      </div>
    `;
    mobileElements.pendingList.appendChild(card);
  });
}

function renderHistorySessions(items) {
  mobileElements.historyList.innerHTML = "";
  mobileElements.historyEmpty.style.display = items.length ? "none" : "block";

  items.forEach((session) => {
    const canJoin = session.status === "accepted" || session.status === "ringing" || session.status === "pending";
    const card = document.createElement("article");
    card.className = "attempt-item";
    card.innerHTML = `
      <div class="attempt-head">
        <strong>${session.recipient_name} / ${session.session_id}</strong>
        <span class="${session.status === "accepted" ? "attempt-status-ok" : "attempt-status-pending"}">${session.status}</span>
      </div>
      <p class="attempt-detail">${session.detail || ""}</p>
      ${
        canJoin
          ? `<div class="button-row"><button class="ghost-button" data-action="rejoin" data-session-id="${session.session_id}" type="button">重新进入语音房间</button></div>`
          : ""
      }
    `;
    mobileElements.historyList.appendChild(card);
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
    `家属账号: ${session.app_user_id || "-"}`,
    `会话 ID: ${session.session_id}`,
    `状态: ${session.status || "-"}`,
    `时间: ${session.created_at || "-"}`,
  ];
  return chips
    .map((item) => `<span class="chip">${escapeHtml(item)}</span>`)
    .join("");
}

function hideEmergencyAlert(showNext = true) {
  mobileElements.alertOverlay.classList.remove("is-visible");
  mobileElements.alertOverlay.setAttribute("aria-hidden", "true");
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

  const note = new Notification("紧急告警", {
    body: `${session.recipient_name || "家属端"}：${session.detail || "收到新的紧急呼叫，请立即查看。"}`,
    tag: `caregiver-${session.session_id}`,
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
    [0, 0.32, 0.64].forEach((offset) => {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "square";
      oscillator.frequency.value = 720;
      gain.gain.setValueAtTime(0.0001, start + offset);
      gain.gain.exponentialRampToValueAtTime(0.035, start + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.2);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start(start + offset);
      oscillator.stop(start + offset + 0.22);
    });
    window.setTimeout(() => {
      context.close().catch(() => {});
    }, 1400);
  } catch {
    // Ignore audio failures in embedded desktop webviews.
  }
}

function showEmergencyAlert(session) {
  activeAlertSessionId = session.session_id;
  mobileElements.alertTitle.textContent = "收到新的紧急告警";
  mobileElements.alertMessage.textContent = session.detail || "主机已发出新的紧急呼叫，请立即查看并决定是否接听。";
  mobileElements.alertMeta.innerHTML = buildAlertChips(session);
  mobileElements.alertAcceptButton.dataset.sessionId = session.session_id;
  mobileElements.alertRejectButton.dataset.sessionId = session.session_id;
  mobileElements.alertOverlay.classList.add("is-visible");
  mobileElements.alertOverlay.setAttribute("aria-hidden", "false");
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

async function fetchPendingAndHistory(appUserId) {
  syncAlertStore(appUserId);

  if (!appUserId) {
    renderPendingSessions([]);
    renderHistorySessions([]);
    return;
  }

  const [pendingResponse, historyResponse] = await Promise.all([
    AppApi.apiFetch(`/api/app-users/${encodeURIComponent(appUserId)}/pending-sessions`),
    AppApi.apiFetch(`/api/app-users/${encodeURIComponent(appUserId)}/sessions`),
  ]);

  if (!pendingResponse.ok || !historyResponse.ok) {
    throw new Error("Failed to fetch caregiver sessions.");
  }

  const pending = await pendingResponse.json();
  const history = await historyResponse.json();
  renderPendingSessions(pending.items || []);
  renderHistorySessions(history.items || []);
  queueEmergencyAlerts(pending.items || []);
}

async function registerCurrentDevice(event) {
  event.preventDefault();
  const existing = loadLocalProfile();
  const profile = {
    app_user_id: mobileElements.appUserId.value.trim(),
    recipient_name: mobileElements.recipientName.value.trim(),
    external_key: mobileElements.externalKey.value.trim(),
    platform: mobileElements.platform.value,
    device_token: mobileElements.deviceToken.value.trim(),
    participant_id: existing.participant_id,
  };

  saveLocalProfile(profile);
  syncAlertStore(profile.app_user_id);

  const response = await AppApi.apiFetch("/api/mobile/register-device", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    mobileElements.feedback.textContent =
      body.detail || "注册失败。如果这是第一次使用自定义 ID，请补上“关联对象编号”。";
    setRegistrationState(false);
    return;
  }

  mobileElements.feedback.textContent = "设备注册成功，家属端已经进入可接收紧急弹窗的状态。";
  setRegistrationState(true, profile.app_user_id);
  syncDesktopAgentConfigSilently();
  await fetchPendingAndHistory(profile.app_user_id);
}

async function updateSessionStatus(sessionId, status) {
  const response = await AppApi.apiFetch(`/api/call-sessions/${encodeURIComponent(sessionId)}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!response.ok) {
    throw new Error("Failed to update session status.");
  }
}

async function acceptSession(sessionId) {
  const profile = loadLocalProfile();
  await updateSessionStatus(sessionId, "accepted");
  hideEmergencyAlert(false);
  openVoiceRoom(sessionId, profile);
  await fetchPendingAndHistory(profile.app_user_id);
}

async function saveServerBaseUrl() {
  const normalized = AppApi.setBaseUrl(mobileElements.serverBaseUrl.value);
  mobileElements.serverBaseUrl.value = normalized;
  mobileElements.serverFeedback.textContent = `已连接共享后端：${normalized}`;
  syncAlertStore();
  syncDesktopAgentConfigSilently();
  await loadAvailableRecipients();
  await fetchPendingAndHistory(loadLocalProfile().app_user_id);
}

function resetServerBaseUrl() {
  AppApi.clearBaseUrl();
  mobileElements.serverBaseUrl.value = AppApi.getBaseUrl();
  mobileElements.serverFeedback.textContent = "已恢复为当前电脑本地后端。";
  syncAlertStore();
  syncDesktopAgentConfigSilently();
}

mobileElements.pendingList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-session-id]");
  if (!button) return;
  const sessionId = button.dataset.sessionId;
  const status = button.dataset.status;
  const profile = loadLocalProfile();

  updateSessionStatus(sessionId, status)
    .then(() => {
      if (status === "accepted") {
        openVoiceRoom(sessionId, profile);
      }
      if (activeAlertSessionId === sessionId) {
        hideEmergencyAlert(false);
      }
      return fetchPendingAndHistory(profile.app_user_id);
    })
    .catch(() => {
      mobileElements.feedback.textContent = "更新会话状态失败。";
    });
});

mobileElements.historyList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action='rejoin']");
  if (!button) return;
  openVoiceRoom(button.dataset.sessionId, loadLocalProfile());
});

mobileElements.demoAccounts.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-app-user-id]");
  if (!button) return;
  const recipient = findAvailableRecipient(button.dataset.appUserId);
  if (!recipient) return;
  applyRecipient(recipient, true);
  mobileElements.feedback.textContent = `已切换到 ${recipient.recipient_name}。`;
  fetchPendingAndHistory(loadLocalProfile().app_user_id).catch(() => {});
});

mobileElements.refreshButton.addEventListener("click", () => {
  const profile = loadLocalProfile();
  fetchPendingAndHistory(profile.app_user_id).catch(() => {
    mobileElements.feedback.textContent = "刷新会话失败，请检查共享后端地址。";
  });
});

mobileElements.saveServerButton.addEventListener("click", () => {
  saveServerBaseUrl().catch(() => {
    mobileElements.serverFeedback.textContent = "连接共享后端失败，请检查地址和端口。";
  });
});

mobileElements.resetServerButton.addEventListener("click", () => {
  resetServerBaseUrl();
  loadAvailableRecipients().catch(() => {});
  fetchPendingAndHistory(loadLocalProfile().app_user_id).catch(() => {});
});

mobileElements.form.addEventListener("submit", (event) => {
  registerCurrentDevice(event).catch(() => {
    mobileElements.feedback.textContent = "注册失败，请确认共享后端在运行。";
  });
});

mobileElements.agentSaveButton.addEventListener("click", () => {
  startDesktopAgent().catch(() => {
    mobileElements.agentFeedback.textContent = "启动后台预警失败，请先确认你是在本机桌面版里操作。";
  });
});

mobileElements.agentStartupOnButton.addEventListener("click", () => {
  startDesktopAgent(true).catch(() => {
    mobileElements.agentFeedback.textContent = "开启开机自启失败，请稍后再试。";
  });
});

mobileElements.agentStartupOffButton.addEventListener("click", () => {
  setDesktopAgentStartup(false).catch(() => {
    mobileElements.agentFeedback.textContent = "关闭开机自启失败，请稍后再试。";
  });
});

mobileElements.alertAcceptButton.addEventListener("click", () => {
  const sessionId = mobileElements.alertAcceptButton.dataset.sessionId;
  if (!sessionId) return;
  acceptSession(sessionId).catch(() => {
    mobileElements.feedback.textContent = "接听失败，请稍后再试。";
  });
});

mobileElements.alertDismissButton.addEventListener("click", () => {
  hideEmergencyAlert();
});

mobileElements.alertRejectButton.addEventListener("click", () => {
  const sessionId = mobileElements.alertRejectButton.dataset.sessionId;
  if (!sessionId) return;
  updateSessionStatus(sessionId, "rejected")
    .then(() => {
      hideEmergencyAlert(false);
      return fetchPendingAndHistory(loadLocalProfile().app_user_id);
    })
    .catch(() => {
      mobileElements.feedback.textContent = "拒绝本次告警失败。";
    });
});

mobileElements.alertOverlay.addEventListener("click", (event) => {
  if (event.target === mobileElements.alertOverlay || event.target.classList.contains("alert-backdrop")) {
    hideEmergencyAlert();
  }
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && activeAlertSessionId) {
    hideEmergencyAlert();
  }
});

const initialProfile = loadLocalProfile();
saveLocalProfile(initialProfile);
fillRegistrationForm(initialProfile);
mobileElements.serverBaseUrl.value = AppApi.getBaseUrl();
setRegistrationState(Boolean(initialProfile.app_user_id), initialProfile.app_user_id);
syncAlertStore(initialProfile.app_user_id);

loadDesktopAgentState().catch(() => {
  mobileElements.agentFeedback.textContent = "当前环境未连接到本机桌面守护接口；如果你用的是桌面版，这里稍后可重试。";
});

loadAvailableRecipients()
  .then(() => fetchPendingAndHistory(loadLocalProfile().app_user_id))
  .catch(() => {
    mobileElements.feedback.textContent = "家属端初始化失败，请检查共享后端地址。";
  });

setInterval(() => {
  fetchPendingAndHistory(loadLocalProfile().app_user_id).catch(() => {});
}, 3500);
