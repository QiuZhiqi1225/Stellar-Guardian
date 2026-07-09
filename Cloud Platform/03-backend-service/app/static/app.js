const elements = {
  status: document.getElementById("service-status"),
  statusDot: document.getElementById("status-dot"),
  dispatchMode: document.getElementById("dispatch-mode"),
  eventCount: document.getElementById("event-count"),
  webhookUrl: document.getElementById("webhook-url"),
  healthUrl: document.getElementById("health-url"),
  eventsUrl: document.getElementById("events-url"),
  serverBaseUrl: document.getElementById("server-base-url"),
  serverPublicUrl: document.getElementById("server-public-url"),
  serverFeedback: document.getElementById("server-feedback"),
  saveServerButton: document.getElementById("save-server-button"),
  resetServerButton: document.getElementById("reset-server-button"),
  keepLatestButton: document.getElementById("keep-latest-button"),
  clearAllSessionsButton: document.getElementById("clear-all-sessions-button"),
  cleanupFeedback: document.getElementById("cleanup-feedback"),
  refreshButton: document.getElementById("refresh-button"),
  usersList: document.getElementById("users-list"),
  accountFeedback: document.getElementById("account-feedback"),
  userRegisterForm: document.getElementById("user-register-form"),
  userRegisterId: document.getElementById("user-register-id"),
  userRegisterName: document.getElementById("user-register-name"),
  userRegisterSecret: document.getElementById("user-register-secret"),
  userRegisterNotes: document.getElementById("user-register-notes"),
  userLoginForm: document.getElementById("user-login-form"),
  userLoginId: document.getElementById("user-login-id"),
  userLoginSecret: document.getElementById("user-login-secret"),
  contactLinkForm: document.getElementById("contact-link-form"),
  contactOwnerUserId: document.getElementById("contact-owner-user-id"),
  contactUserId: document.getElementById("contact-user-id"),
  contactRelationship: document.getElementById("contact-relationship"),
  directoryFeedback: document.getElementById("directory-feedback"),
  profilesList: document.getElementById("profiles-list"),
  profileForm: document.getElementById("profile-form"),
  profileId: document.getElementById("profile-id"),
  profileExternalKey: document.getElementById("profile-external-key"),
  profileDisplayName: document.getElementById("profile-display-name"),
  profileOwnerUserId: document.getElementById("profile-owner-user-id"),
  profileNotes: document.getElementById("profile-notes"),
  clearProfileButton: document.getElementById("clear-profile-button"),
  recipientForm: document.getElementById("recipient-form"),
  recipientId: document.getElementById("recipient-id"),
  recipientProfileId: document.getElementById("recipient-profile-id"),
  recipientName: document.getElementById("recipient-name"),
  recipientAppUserId: document.getElementById("recipient-app-user-id"),
  recipientDeviceToken: document.getElementById("recipient-device-token"),
  recipientPlatform: document.getElementById("recipient-platform"),
  recipientSeverity: document.getElementById("recipient-severity"),
  recipientPriority: document.getElementById("recipient-priority"),
  clearRecipientButton: document.getElementById("clear-recipient-button"),
  testForm: document.getElementById("test-form"),
  feedback: document.getElementById("form-feedback"),
  targetExternalKey: document.getElementById("target-external-key"),
  subject: document.getElementById("subject"),
  severity: document.getElementById("severity"),
  content: document.getElementById("content"),
  activeSessionsList: document.getElementById("active-sessions-list"),
  activeSessionsEmpty: document.getElementById("active-sessions-empty"),
  eventsList: document.getElementById("events-list"),
  eventsEmpty: document.getElementById("events-empty"),
};

let cachedProfiles = [];
let cachedUsers = [];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function currentBaseUrl() {
  return AppApi.getBaseUrl();
}

function setServerFeedback(message) {
  elements.serverFeedback.textContent = message;
}

function setAccountFeedback(message) {
  elements.accountFeedback.textContent = message;
}

function setDirectoryFeedback(message) {
  elements.directoryFeedback.textContent = message;
}

function setCleanupFeedback(message) {
  elements.cleanupFeedback.textContent = message;
}

function showServerConfig() {
  elements.serverBaseUrl.value = currentBaseUrl();
}

function resetUserRegisterForm() {
  elements.userRegisterId.value = "";
  elements.userRegisterName.value = "";
  elements.userRegisterSecret.value = "";
  elements.userRegisterNotes.value = "";
}

function resetContactLinkForm() {
  elements.contactUserId.value = "";
  elements.contactRelationship.value = "";
}

function resetProfileForm() {
  elements.profileId.value = "";
  elements.profileExternalKey.value = "";
  elements.profileDisplayName.value = "";
  elements.profileOwnerUserId.value = "";
  elements.profileNotes.value = "";
}

function resetRecipientForm() {
  elements.recipientId.value = "";
  elements.recipientProfileId.value = "";
  elements.recipientName.value = "";
  elements.recipientAppUserId.value = "";
  elements.recipientDeviceToken.value = "";
  elements.recipientPlatform.value = "android";
  elements.recipientSeverity.value = "all";
  elements.recipientPriority.value = "1";
}

function populateSelectors() {
  const ownerOptions = [
    "<option value=''>未指定所属用户</option>",
    ...cachedUsers.map(
      (user) =>
        `<option value="${escapeHtml(user.user_id)}">${escapeHtml(user.display_name)} (${escapeHtml(user.user_id)})</option>`
    ),
  ].join("");
  elements.profileOwnerUserId.innerHTML = ownerOptions;

  const contactOwnerOptions = cachedUsers.length
    ? cachedUsers
        .map(
          (user) =>
            `<option value="${escapeHtml(user.user_id)}">${escapeHtml(user.display_name)} (${escapeHtml(user.user_id)})</option>`
        )
        .join("")
    : "<option value=''>请先注册用户</option>";
  elements.contactOwnerUserId.innerHTML = contactOwnerOptions;

  const profileOptions = cachedProfiles.length
    ? cachedProfiles
        .map(
          (profile) =>
            `<option value="${profile.id}">${escapeHtml(profile.display_name)} (${escapeHtml(profile.external_key)})</option>`
        )
        .join("")
    : "<option value=''>请先创建对象</option>";
  elements.recipientProfileId.innerHTML = profileOptions;

  const targetOptions = cachedProfiles
    .map(
      (profile) =>
        `<option value="${escapeHtml(profile.external_key)}">${escapeHtml(profile.display_name)} (${escapeHtml(
          profile.external_key
        )})</option>`
    )
    .join("");
  elements.targetExternalKey.innerHTML = "<option value=''>请选择对象</option>" + targetOptions;
}

function renderUsers(users) {
  elements.usersList.innerHTML = "";

  if (!users.length) {
    elements.usersList.innerHTML =
      '<div class="empty-state">还没有注册任何正式账号。建议先注册“对象所属人”和“紧急联系人”两个账号，再去绑定关系。</div>';
    return;
  }

  users.forEach((user) => {
    const contactsHtml = user.contacts?.length
      ? user.contacts
          .map(
            (contact) => `
              <div class="contact-item">
                <div class="contact-row">
                  <div>
                    <strong>${escapeHtml(contact.contact_display_name)}</strong>
                    <p class="panel-note">${escapeHtml(contact.contact_user_id)}${
                      contact.relationship_label ? ` · ${escapeHtml(contact.relationship_label)}` : ""
                    }</p>
                  </div>
                  <button class="action-link" data-action="delete-contact-link" data-link-id="${contact.link_id}" type="button">解除绑定</button>
                </div>
              </div>
            `
          )
          .join("")
      : '<div class="empty-state">还没有绑定紧急联系人。</div>';

    const ownedProfilesHtml = user.owned_profiles?.length
      ? user.owned_profiles
          .map(
            (profile) =>
              `<span class="chip">${escapeHtml(profile.display_name)} / ${escapeHtml(profile.external_key)}</span>`
          )
          .join("")
      : '<span class="panel-note">当前没有归属对象</span>';

    const card = document.createElement("article");
    card.className = "profile-card";
    card.innerHTML = `
      <div class="profile-head">
        <div class="profile-meta">
          <h3>${escapeHtml(user.display_name)}</h3>
          <div class="attempt-tags">
            <span class="chip">${escapeHtml(user.user_id)}</span>
            <span class="chip">${user.has_device_token ? "已登记设备" : "未登记设备"}</span>
            <span class="chip">平台: ${escapeHtml(user.current_platform || "web")}</span>
          </div>
          <p class="panel-note">${escapeHtml(user.notes || "暂无备注")}</p>
        </div>
      </div>
      <div class="user-summary">
        <div class="server-box">
          <strong>已归属对象</strong>
          <div class="attempt-tags">${ownedProfilesHtml}</div>
        </div>
        <div class="server-box">
          <strong>紧急联系人</strong>
          <div class="contact-list">${contactsHtml}</div>
        </div>
      </div>
    `;
    elements.usersList.appendChild(card);
  });
}

function renderProfiles(profiles) {
  elements.profilesList.innerHTML = "";

  if (!profiles.length) {
    elements.profilesList.innerHTML =
      '<div class="empty-state">还没有保存任何对象。建议先创建对象，并为它指定所属用户，之后再绑定紧急联系人。</div>';
    return;
  }

  profiles.forEach((profile) => {
    const recipientsHtml = profile.app_recipients?.length
      ? profile.app_recipients
          .map((recipient) => {
            const isLinked = recipient.source_type === "linked";
            return `
              <div class="contact-item">
                <div class="contact-row">
                  <div>
                    <strong>${escapeHtml(recipient.recipient_name)}</strong>
                    <p class="panel-note">${escapeHtml(recipient.app_user_id)}</p>
                  </div>
                  ${
                    isLinked
                      ? '<span class="chip">自动同步</span>'
                      : `
                        <div class="button-row">
                          <button class="action-link" data-action="edit-recipient" data-id="${recipient.id}" type="button">编辑</button>
                          <button class="action-link" data-action="delete-recipient" data-id="${recipient.id}" type="button">删除</button>
                        </div>
                      `
                  }
                </div>
                <div class="attempt-tags">
                  <span class="chip">来源: ${isLinked ? "联系人绑定" : "手动配置"}</span>
                  <span class="chip">平台: ${escapeHtml(recipient.platform)}</span>
                  <span class="chip">级别: ${escapeHtml(recipient.severity_scope)}</span>
                  <span class="chip">优先级: ${recipient.priority}</span>
                </div>
                <p class="panel-note">Token: ${escapeHtml(recipient.device_token)}</p>
              </div>
            `;
          })
          .join("")
      : '<div class="empty-state">这个对象还没有任何可通知的联系人。</div>';

    const ownerText = profile.owner_user_id
      ? `${escapeHtml(profile.owner_display_name || profile.owner_user_id)} (${escapeHtml(profile.owner_user_id)})`
      : "未指定";

    const card = document.createElement("article");
    card.className = "profile-card";
    card.innerHTML = `
      <div class="profile-head">
        <div class="profile-meta">
          <h3>${escapeHtml(profile.display_name)}</h3>
          <div class="attempt-tags">
            <span class="chip">${escapeHtml(profile.external_key)}</span>
            <span class="chip">所属用户: ${ownerText}</span>
          </div>
          <p class="panel-note">${escapeHtml(profile.notes || "暂无备注")}</p>
        </div>
        <div class="button-row">
          <button class="action-link" data-action="edit-profile" data-id="${profile.id}" type="button">编辑对象</button>
          <button class="action-link" data-action="delete-profile" data-id="${profile.id}" type="button">删除对象</button>
        </div>
      </div>
      <div class="contact-list">${recipientsHtml}</div>
    `;
    elements.profilesList.appendChild(card);
  });
}

function renderActiveSessions(items) {
  elements.activeSessionsList.innerHTML = "";
  const hasItems = Array.isArray(items) && items.length > 0;
  elements.activeSessionsEmpty.style.display = hasItems ? "none" : "block";
  if (!hasItems) return;

  items.forEach((session) => {
    const card = document.createElement("article");
    card.className = "event-card";
    card.innerHTML = `
      <div class="event-meta">
        <span class="severity-pill">${escapeHtml(session.status)}</span>
        <span class="label">${escapeHtml(session.created_at || "-")}</span>
      </div>
      <h3>${escapeHtml(session.recipient_name)}</h3>
      <p class="panel-note">${escapeHtml(session.detail || "")}</p>
      <div class="attempt-tags">
        <span class="chip">会话 ID: ${escapeHtml(session.session_id)}</span>
        <span class="chip">平台: ${escapeHtml(session.platform)}</span>
      </div>
      <div class="button-row">
        <button class="primary-button" data-action="open-caregiver" data-session-id="${escapeHtml(
          session.session_id
        )}" data-label="${escapeHtml(session.recipient_name)}" type="button">以家属端进入</button>
        <button class="ghost-button" data-action="open-device" data-session-id="${escapeHtml(
          session.session_id
        )}" data-label="设备端" type="button">以设备端进入</button>
      </div>
    `;
    elements.activeSessionsList.appendChild(card);
  });
}

function renderEvents(items) {
  elements.eventsList.innerHTML = "";
  const hasItems = Array.isArray(items) && items.length > 0;
  elements.eventsEmpty.style.display = hasItems ? "none" : "block";
  if (!hasItems) return;

  items.forEach((item) => {
    const event = item.event || {};
    const sessions = item.sessions || [];
    const card = document.createElement("article");
    card.className = "event-card";
    card.innerHTML = `
      <div class="event-meta">
        <span class="severity-pill">${escapeHtml(event.severity || "info")}</span>
        <span class="label">${escapeHtml(event.occurred_at || "-")}</span>
      </div>
      <h3>${escapeHtml(event.title || "未命名告警")}</h3>
      <p class="panel-note">${escapeHtml(event.body || "")}</p>
      <div class="attempt-tags">
        <span class="chip">对象编号: ${escapeHtml(event.target_external_key || "-")}</span>
        <span class="chip">事件 ID: ${escapeHtml(event.event_id || "-")}</span>
      </div>
    `;

    const sessionList = document.createElement("div");
    sessionList.className = "attempt-list";

    if (!sessions.length) {
      sessionList.innerHTML = '<div class="empty-state">这条告警没有匹配到任何可呼叫的联系人。</div>';
    } else {
      sessions.forEach((session) => {
        const sessionEl = document.createElement("div");
        sessionEl.className = "attempt-item";
        sessionEl.innerHTML = `
          <div class="attempt-head">
            <strong>${escapeHtml(session.recipient_name)} / ${escapeHtml(session.app_user_id)}</strong>
            <span class="${session.status === "accepted" ? "attempt-status-ok" : "attempt-status-pending"}">${escapeHtml(
              session.status
            )}</span>
          </div>
          <p class="attempt-detail">${escapeHtml(session.detail || "")}</p>
          <div class="attempt-tags">
            <span class="chip">会话 ID: ${escapeHtml(session.session_id)}</span>
            <span class="chip">加入地址: ${escapeHtml(session.join_path)}</span>
          </div>
          <div class="button-row">
            <button class="primary-button" data-action="open-caregiver" data-session-id="${escapeHtml(
              session.session_id
            )}" data-label="${escapeHtml(session.recipient_name)}" type="button">打开家属端语音页</button>
            <button class="ghost-button" data-action="open-device" data-session-id="${escapeHtml(
              session.session_id
            )}" data-label="设备端" type="button">打开设备端语音页</button>
          </div>
        `;
        sessionList.appendChild(sessionEl);
      });
    }

    card.appendChild(sessionList);
    elements.eventsList.appendChild(card);
  });
}

function findProfile(profileId) {
  return cachedProfiles.find((item) => item.id === Number(profileId)) || null;
}

function findRecipient(recipientId) {
  for (const profile of cachedProfiles) {
    const recipient = (profile.app_recipients || []).find((item) => item.id === Number(recipientId));
    if (recipient) return recipient;
  }
  return null;
}

async function loadDashboard() {
  const response = await AppApi.apiFetch("/api/dashboard");
  if (!response.ok) {
    throw new Error("Failed to load dashboard.");
  }
  const data = await response.json();

  cachedProfiles = data.profiles || [];
  cachedUsers = data.users || [];

  elements.status.textContent = data.status === "running" ? "在线" : data.status;
  elements.dispatchMode.textContent = data.dispatch_mode || "app_voice_call";
  elements.eventCount.textContent = String(data.recent_events || 0);
  elements.webhookUrl.textContent = AppApi.buildDisplayUrl(data.webhook_url || "/webhooks/huawei");
  elements.healthUrl.textContent = AppApi.buildDisplayUrl(data.health_url || "/health");
  elements.eventsUrl.textContent = AppApi.buildDisplayUrl(data.events_url || "/events");
  elements.serverPublicUrl.textContent = data.public_base_url || currentBaseUrl();
  elements.statusDot.style.background = data.status === "running" ? "var(--ok)" : "var(--accent)";

  populateSelectors();
  renderUsers(cachedUsers);
  renderProfiles(cachedProfiles);
  renderEvents(data.items || []);
  renderActiveSessions(data.active_sessions || []);
}

async function submitUserRegister(event) {
  event.preventDefault();
  const payload = {
    user_id: elements.userRegisterId.value.trim(),
    display_name: elements.userRegisterName.value.trim(),
    secret: elements.userRegisterSecret.value,
    notes: elements.userRegisterNotes.value.trim(),
  };

  const response = await AppApi.apiFetch("/api/users/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    if (response.status === 409) {
      setAccountFeedback(`用户 ID ${payload.user_id} 已存在，请换一个新的 ID。`);
      return;
    }
    setAccountFeedback("注册失败，请检查输入后重试。");
    return;
  }

  setAccountFeedback(`账号 ${payload.user_id} 已创建成功。下一步可以把它绑定为对象所属人，或绑定其他联系人 ID。`);
  resetUserRegisterForm();
  await loadDashboard();
}

async function submitUserLogin(event) {
  event.preventDefault();
  const payload = {
    user_id: elements.userLoginId.value.trim(),
    secret: elements.userLoginSecret.value,
  };

  const response = await AppApi.apiFetch("/api/users/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    setAccountFeedback(response.status === 401 ? "口令不正确。" : "没有找到这个用户 ID。");
    return;
  }

  const data = await response.json();
  setAccountFeedback(`账号校验成功：${data.user.display_name} (${data.user.user_id})。`);
  elements.userLoginSecret.value = "";
}

async function submitContactLink(event) {
  event.preventDefault();
  const ownerUserId = elements.contactOwnerUserId.value.trim();
  const payload = {
    contact_user_id: elements.contactUserId.value.trim(),
    relationship_label: elements.contactRelationship.value.trim(),
  };

  const response = await AppApi.apiFetch(`/api/users/${encodeURIComponent(ownerUserId)}/contacts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    setAccountFeedback(body.detail || "绑定联系人失败，请确认两个用户 ID 都已注册。");
    return;
  }

  setAccountFeedback(`已把 ${payload.contact_user_id} 绑定到 ${ownerUserId} 名下。之后该用户所属对象告警时，会自动通知这个联系人。`);
  resetContactLinkForm();
  await loadDashboard();
}

async function submitProfile(event) {
  event.preventDefault();
  const payload = {
    external_key: elements.profileExternalKey.value.trim(),
    display_name: elements.profileDisplayName.value.trim(),
    owner_user_id: elements.profileOwnerUserId.value.trim() || null,
    notes: elements.profileNotes.value.trim(),
  };
  const isEdit = Boolean(elements.profileId.value);
  const url = isEdit ? `/api/profiles/${elements.profileId.value}` : "/api/profiles";
  const method = isEdit ? "PUT" : "POST";

  const response = await AppApi.apiFetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    setDirectoryFeedback(body.detail || "保存对象失败，请检查对象编号和所属用户。");
    return;
  }

  setDirectoryFeedback(isEdit ? "对象已更新。" : "对象已创建。");
  resetProfileForm();
  await loadDashboard();
}

async function submitRecipient(event) {
  event.preventDefault();
  const payload = {
    profile_id: Number(elements.recipientProfileId.value),
    recipient_name: elements.recipientName.value.trim(),
    app_user_id: elements.recipientAppUserId.value.trim(),
    device_token: elements.recipientDeviceToken.value.trim(),
    platform: elements.recipientPlatform.value,
    severity_scope: elements.recipientSeverity.value,
    priority: Number(elements.recipientPriority.value),
  };
  const isEdit = Boolean(elements.recipientId.value);
  const url = isEdit ? `/api/app-recipients/${elements.recipientId.value}` : "/api/app-recipients";
  const method = isEdit ? "PUT" : "POST";

  const response = await AppApi.apiFetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    setDirectoryFeedback(body.detail || "保存手动收件人失败，请检查 APP 用户 ID 和 Token。");
    return;
  }

  setDirectoryFeedback(isEdit ? "手动收件人已更新。" : "手动收件人已创建。");
  resetRecipientForm();
  await loadDashboard();
}

async function submitTestAlert(event) {
  event.preventDefault();
  const payload = {
    external_key: elements.targetExternalKey.value || null,
    subject: elements.subject.value.trim(),
    severity: elements.severity.value,
    content: elements.content.value.trim(),
  };

  elements.feedback.textContent = "正在发送测试告警...";
  const response = await AppApi.apiFetch("/api/test-alert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    elements.feedback.textContent = "发送失败，请先确认这个对象已经绑定了可通知的联系人。";
    return;
  }

  const data = await response.json();
  elements.feedback.textContent = `已创建 ${data.result.sessions.length} 个语音会话。现在可以去家属端和设备端进入同一个会话。`;
  await loadDashboard();
}

async function cleanupSessions(mode) {
  const response = await AppApi.apiFetch("/api/sessions/cleanup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!response.ok) {
    throw new Error("Failed to cleanup sessions.");
  }

  const data = await response.json();
  const summary = data.summary || {};
  if (mode === "keep_latest") {
    setCleanupFeedback(
      `已整理完成：删除 ${summary.removed_events || 0} 条旧事件、${summary.removed_sessions || 0} 条旧会话，保留最新事件 ${summary.kept_event_id || "-" }。`
    );
  } else {
    setCleanupFeedback(
      `已全部清空：删除 ${summary.removed_events || 0} 条事件、${summary.removed_sessions || 0} 条会话。`
    );
  }
  await loadDashboard();
}

async function saveServerBaseUrl() {
  const normalized = AppApi.setBaseUrl(elements.serverBaseUrl.value);
  elements.serverBaseUrl.value = normalized;
  setServerFeedback(`共享后端已切换到 ${normalized}`);
  await loadDashboard();
}

function resetServerBaseUrl() {
  AppApi.clearBaseUrl();
  showServerConfig();
  setServerFeedback("已恢复为当前电脑自己的本地后端。");
}

async function handlePageActions(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;

  const action = button.dataset.action;

  if (action === "open-caregiver") {
    AppApi.openRoomWindow(button.dataset.sessionId || "", {
      role: "caregiver",
      label: button.dataset.label || "家属端",
    });
    return;
  }

  if (action === "open-device") {
    AppApi.openRoomWindow(button.dataset.sessionId || "", {
      role: "device",
      label: button.dataset.label || "设备端",
    });
    return;
  }

  if (action === "edit-profile") {
    const profile = findProfile(button.dataset.id);
    if (!profile) return;
    elements.profileId.value = String(profile.id);
    elements.profileExternalKey.value = profile.external_key;
    elements.profileDisplayName.value = profile.display_name;
    elements.profileOwnerUserId.value = profile.owner_user_id || "";
    elements.profileNotes.value = profile.notes || "";
    return;
  }

  if (action === "delete-profile") {
    const response = await AppApi.apiFetch(`/api/profiles/${button.dataset.id}`, { method: "DELETE" });
    if (response.ok) {
      setDirectoryFeedback("对象已删除。");
      await loadDashboard();
    }
    return;
  }

  if (action === "edit-recipient") {
    const recipient = findRecipient(button.dataset.id);
    if (!recipient) return;
    if (recipient.source_type === "linked") {
      setDirectoryFeedback("自动同步的联系人请去“正式账号体系”里解除绑定，不建议在这里手动改。");
      return;
    }
    elements.recipientId.value = String(recipient.id);
    elements.recipientProfileId.value = String(recipient.profile_id);
    elements.recipientName.value = recipient.recipient_name;
    elements.recipientAppUserId.value = recipient.app_user_id;
    elements.recipientDeviceToken.value = recipient.device_token;
    elements.recipientPlatform.value = recipient.platform;
    elements.recipientSeverity.value = recipient.severity_scope;
    elements.recipientPriority.value = String(recipient.priority);
    return;
  }

  if (action === "delete-recipient") {
    const response = await AppApi.apiFetch(`/api/app-recipients/${button.dataset.id}`, { method: "DELETE" });
    if (response.ok) {
      setDirectoryFeedback("手动收件人已删除。");
      await loadDashboard();
    } else {
      const body = await response.json().catch(() => ({}));
      setDirectoryFeedback(body.detail || "删除失败。");
    }
    return;
  }

  if (action === "delete-contact-link") {
    const response = await AppApi.apiFetch(`/api/user-contact-links/${button.dataset.linkId}`, { method: "DELETE" });
    if (response.ok) {
      setAccountFeedback("联系人绑定已解除。");
      await loadDashboard();
    }
  }
}

elements.refreshButton.addEventListener("click", () => {
  loadDashboard().catch(() => {
    elements.feedback.textContent = "刷新失败，请确认后端仍在运行。";
  });
});

elements.saveServerButton.addEventListener("click", () => {
  saveServerBaseUrl().catch(() => {
    setServerFeedback("连接共享后端失败，请检查 IP 和端口。");
  });
});

elements.resetServerButton.addEventListener("click", () => {
  resetServerBaseUrl();
  loadDashboard().catch(() => {
    setServerFeedback("恢复本地后端后刷新失败。");
  });
});

elements.keepLatestButton.addEventListener("click", () => {
  cleanupSessions("keep_latest").catch(() => {
    setCleanupFeedback("整理会话失败，请稍后重试。");
  });
});

elements.clearAllSessionsButton.addEventListener("click", () => {
  cleanupSessions("clear_all").catch(() => {
    setCleanupFeedback("清空会话失败，请稍后重试。");
  });
});

elements.userRegisterForm.addEventListener("submit", (event) => {
  submitUserRegister(event).catch(() => {
    setAccountFeedback("注册失败。");
  });
});

elements.userLoginForm.addEventListener("submit", (event) => {
  submitUserLogin(event).catch(() => {
    setAccountFeedback("账号验证失败。");
  });
});

elements.contactLinkForm.addEventListener("submit", (event) => {
  submitContactLink(event).catch(() => {
    setAccountFeedback("绑定联系人失败。");
  });
});

elements.profileForm.addEventListener("submit", (event) => {
  submitProfile(event).catch(() => {
    setDirectoryFeedback("保存对象失败。");
  });
});

elements.recipientForm.addEventListener("submit", (event) => {
  submitRecipient(event).catch(() => {
    setDirectoryFeedback("保存手动收件人失败。");
  });
});

elements.testForm.addEventListener("submit", (event) => {
  submitTestAlert(event).catch(() => {
    elements.feedback.textContent = "发送失败，请确认共享后端正在运行。";
  });
});

elements.clearProfileButton.addEventListener("click", resetProfileForm);
elements.clearRecipientButton.addEventListener("click", resetRecipientForm);

document.body.addEventListener("click", (event) => {
  handlePageActions(event).catch(() => {
    setDirectoryFeedback("操作失败。");
  });
});

showServerConfig();
loadDashboard().catch(() => {
  elements.feedback.textContent = "页面加载失败，请确认共享后端地址是否正确。";
});

setInterval(() => {
  loadDashboard().catch(() => {});
}, 8000);
