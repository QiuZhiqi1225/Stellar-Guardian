const STORAGE_KEY = "backend_results_device_status_user_id";

const elements = {
  status: document.getElementById("debug-service-status"),
  statusDot: document.getElementById("debug-status-dot"),
  webhookCount: document.getElementById("metric-webhook-count"),
  eventCount: document.getElementById("metric-event-count"),
  refreshButton: document.getElementById("debug-refresh-button"),
  saveDeviceUserButton: document.getElementById("save-device-user-button"),
  deviceStatusUserId: document.getElementById("device-status-user-id"),
  debugFeedback: document.getElementById("debug-feedback"),
  debugHuaweiUrl: document.getElementById("debug-huawei-url"),
  debugEventsUrl: document.getElementById("debug-events-url"),
  debugDeviceUrl: document.getElementById("debug-device-url"),
  debugSummaryCards: document.getElementById("debug-summary-cards"),
  huaweiAuditEmpty: document.getElementById("huawei-audit-empty"),
  huaweiAuditList: document.getElementById("huawei-audit-list"),
  dispatchEventsEmpty: document.getElementById("dispatch-events-empty"),
  dispatchEventsList: document.getElementById("dispatch-events-list"),
  deviceStatusEmpty: document.getElementById("device-status-empty"),
  deviceStatusCard: document.getElementById("device-status-card"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function loadTrackedUserId() {
  return localStorage.getItem(STORAGE_KEY) || "bajixiang";
}

function saveTrackedUserId() {
  const userId = elements.deviceStatusUserId.value.trim() || "bajixiang";
  elements.deviceStatusUserId.value = userId;
  localStorage.setItem(STORAGE_KEY, userId);
  return userId;
}

function setFeedback(message, isError = false) {
  elements.debugFeedback.textContent = message;
  elements.debugFeedback.style.color = isError ? "var(--accent-dark)" : "var(--muted)";
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return `${date.toLocaleString("zh-CN", { hour12: false })}`;
}

function formatBool(value) {
  return value ? "是" : "否";
}

function formatCount(value) {
  return Number.isFinite(Number(value)) ? String(value) : "0";
}

function trimText(value, maxLength = 120) {
  const text = String(value ?? "");
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength)}...`;
}

function isLikelyManualWebhook(item) {
  const clientHost = String(item?.request?.client_host ?? "").trim().toLowerCase();
  const userAgent = String(item?.request?.user_agent ?? "").trim().toLowerCase();

  if (clientHost === "127.0.0.1" || clientHost === "::1" || clientHost === "localhost") {
    return true;
  }

  return [
    "windowspowershell",
    "python-urllib",
    "curl/",
    "postmanruntime",
    "insomnia/",
    "thunder client",
  ].some((keyword) => userAgent.includes(keyword));
}

function pickLatestWebhookForSummary(items) {
  if (!Array.isArray(items) || items.length === 0) return null;

  return items.find((item) => !isLikelyManualWebhook(item)) || null;
}

function getWebhookDeviceKey(item) {
  if (!item) return "-";

  return (
    item?.result?.event?.target_external_key ||
    item?.result?.event?.raw_payload?.fall_detection?.device_key ||
    item?.accel_samples?.[0]?.device_key ||
    item?.payload?.notify_data?.header?.device_id ||
    "-"
  );
}

function getWebhookFallCount(item) {
  if (!item) return "-";

  return (
    item?.result?.event?.raw_payload?.fall_detection?.fall_count_current ??
    item?.accel_samples?.[0]?.fall_count ??
    "-"
  );
}

function renderSummaryCards(huaweiItems, eventItems, deviceStatus) {
  const latestHuawei = pickLatestWebhookForSummary(huaweiItems) || {};
  const latestEvent = eventItems[0] || {};
  const latestResult = latestHuawei.result || {};
  const notificationSummary = latestEvent.notification_summary || {};
  const latestSample = (latestHuawei.accel_samples || [])[0] || {};
  const hasManualWebhookOnly =
    !latestHuawei.received_at && Array.isArray(huaweiItems) && huaweiItems.length > 0 && huaweiItems.every(isLikelyManualWebhook);
  const latestHuaweiDeviceKey = getWebhookDeviceKey(latestHuawei);
  const latestHuaweiFallCount = getWebhookFallCount(latestHuawei);

  const cards = [
    {
      title: "最新设备编号",
      value: latestHuaweiDeviceKey,
      note:
        latestHuawei.received_at
          ? `真实云端回调，收到于 ${formatTime(latestHuawei.received_at)}`
          : hasManualWebhookOnly
            ? "最近只有测试回调，真实华为云回调还没有到后端"
            : "还没有收到华为云设备回调",
    },
    {
      title: "最新 fall_count",
      value: latestHuaweiFallCount,
      note:
        latestSample.accel_g !== undefined
          ? `这是后端最近收到的华为上报值，accel ${latestSample.accel_g}g`
          : latestResult.event?.occurred_at
            ? `告警时间 ${formatTime(latestResult.event.occurred_at)}`
            : "还没有加速度样本",
    },
    {
      title: "最近通知结果",
      value:
        notificationSummary.sent > 0
          ? "已发送"
          : notificationSummary.failed > 0
            ? "发送失败"
            : notificationSummary.attempted > 0
              ? "已尝试"
              : "-",
      note:
        deviceStatus?.last_notification_status
          ? `设备状态 ${deviceStatus.last_notification_status}`
          : "还没有设备通知状态",
    },
    {
      title: "模板授权次数",
      value: deviceStatus ? formatCount(deviceStatus.granted_template_count) : "-",
      note: deviceStatus?.notification_enabled ? "当前允许发订阅通知" : "当前不能发订阅通知",
    },
  ];

  elements.debugSummaryCards.innerHTML = cards
    .map(
      (card) => `
        <div class="server-box summary-box">
          <p class="label">${escapeHtml(card.title)}</p>
          <strong class="summary-value">${escapeHtml(card.value)}</strong>
          <p class="panel-note">${escapeHtml(card.note)}</p>
        </div>
      `
    )
    .join("");
}

function renderHuaweiAudit(items) {
  elements.huaweiAuditList.innerHTML = "";
  const hasItems = Array.isArray(items) && items.length > 0;
  elements.huaweiAuditEmpty.style.display = hasItems ? "none" : "block";
  if (!hasItems) return;

  items.slice(0, 10).forEach((item) => {
    const firstSample = (item.accel_samples || [])[0] || {};
    const deviceKey = getWebhookDeviceKey(item);
    const fallCount = getWebhookFallCount(item);
    const sourceIp = item.request?.client_host || "-";
    const sourceLabel = isLikelyManualWebhook(item) ? "测试回调" : "云端回调";
    const card = document.createElement("article");
    card.className = "event-card";
    card.innerHTML = `
      <div class="event-meta">
        <span class="severity-pill">${escapeHtml(item.status || "received")}</span>
        <span class="label">${escapeHtml(formatTime(item.received_at))}</span>
      </div>
      <h3>${escapeHtml(deviceKey || "未知设备")}</h3>
      <p class="panel-note">
        ${escapeHtml(sourceLabel)} /
        来源 IP ${escapeHtml(sourceIp)} /
        ${escapeHtml(item.message_type || "custom")} /
        属性上报 ${item.property_report_detected ? "是" : "否"} /
        样本数 ${formatCount(item.accel_sample_count)}
      </p>
      <div class="attempt-tags">
        <span class="chip">设备 ${escapeHtml(deviceKey)}</span>
        <span class="chip">fall_count ${escapeHtml(fallCount)}</span>
        <span class="chip">accel ${escapeHtml(firstSample.accel_g ?? "-")}g</span>
      </div>
      <details class="raw-json-block">
        <summary>展开看原始回调与处理结果</summary>
        <pre class="raw-json">${escapeHtml(
          JSON.stringify(
            {
              request: item.request,
              accel_samples: item.accel_samples,
              result: item.result,
            },
            null,
            2
          )
        )}</pre>
      </details>
    `;
    elements.huaweiAuditList.appendChild(card);
  });
}

function renderDispatchEvents(items) {
  elements.dispatchEventsList.innerHTML = "";
  const hasItems = Array.isArray(items) && items.length > 0;
  elements.dispatchEventsEmpty.style.display = hasItems ? "none" : "block";
  if (!hasItems) return;

  items.slice(0, 12).forEach((item) => {
    const event = item.event || {};
    const notificationSummary = item.notification_summary || {};
    const sessions = item.sessions || [];
    const lastSession = sessions[0] || {};
    const reasons = Array.isArray(notificationSummary.reasons) ? notificationSummary.reasons : [];
    const card = document.createElement("article");
    card.className = "event-card";
    card.innerHTML = `
      <div class="event-meta">
        <span class="severity-pill">${escapeHtml(event.severity || "info")}</span>
        <span class="label">${escapeHtml(formatTime(event.occurred_at))}</span>
      </div>
      <h3>${escapeHtml(event.title || "未命名告警")}</h3>
      <p class="panel-note">${escapeHtml(event.body || "")}</p>
      <div class="attempt-tags">
        <span class="chip">设备 ${escapeHtml(event.target_external_key || "-")}</span>
        <span class="chip">接收人 ${escapeHtml((item.recipients || []).join(", ") || "-")}</span>
        <span class="chip">sent ${formatCount(notificationSummary.sent)}</span>
        <span class="chip">failed ${formatCount(notificationSummary.failed)}</span>
      </div>
      <div class="attempt-list">
        <div class="attempt-item">
          <div class="attempt-head">
            <strong>最近会话</strong>
            <span class="${lastSession.status === "accepted" ? "attempt-status-ok" : "attempt-status-pending"}">
              ${escapeHtml(lastSession.status || "none")}
            </span>
          </div>
          <p class="attempt-detail">
            ${escapeHtml(lastSession.recipient_name || "-")} / ${escapeHtml(lastSession.app_user_id || "-")}
          </p>
          <div class="attempt-tags">
            <span class="chip">session ${escapeHtml(lastSession.session_id || "-")}</span>
            <span class="chip">平台 ${escapeHtml(lastSession.platform || "-")}</span>
          </div>
          ${
            reasons.length
              ? `<p class="panel-note">失败原因：${escapeHtml(reasons.join(" | "))}</p>`
              : ""
          }
        </div>
      </div>
      <details class="raw-json-block">
        <summary>展开看完整派发结果</summary>
        <pre class="raw-json">${escapeHtml(JSON.stringify(item, null, 2))}</pre>
      </details>
    `;
    elements.dispatchEventsList.appendChild(card);
  });
}

function renderDeviceStatus(item, trackedUserId) {
  elements.deviceStatusCard.innerHTML = "";
  const hasItem = Boolean(item);
  elements.deviceStatusEmpty.style.display = hasItem ? "none" : "block";
  if (!hasItem) return;

  const templateCounts = item.granted_template_counts || {};
  const templateCountText = Object.keys(templateCounts).length
    ? Object.entries(templateCounts)
        .map(([templateId, count]) => `${templateId}: ${count}`)
        .join(" | ")
    : "暂无剩余模板次数";
  const permissionResult = item.last_permission_result || {};
  const permissionText = Object.keys(permissionResult).length
    ? Object.entries(permissionResult)
        .map(([key, value]) => `${key}: ${value}`)
        .join(" | ")
    : "暂无最近一次授权记录";

  const card = document.createElement("article");
  card.className = "profile-card";
  card.innerHTML = `
    <div class="profile-head">
      <div class="profile-meta">
        <h3>${escapeHtml(item.recipient_name || trackedUserId)}</h3>
        <div class="attempt-tags">
          <span class="chip">${escapeHtml(item.app_user_id || trackedUserId)}</span>
          <span class="chip">openid ${formatBool(item.has_wechat_openid)}</span>
          <span class="chip">通知可用 ${formatBool(item.notification_enabled)}</span>
        </div>
        <p class="panel-note">device_token: ${escapeHtml(item.device_token || "-")}</p>
      </div>
    </div>
    <div class="user-summary">
      <div class="server-box">
        <strong>最近通知结果</strong>
        <p class="panel-note">状态：${escapeHtml(item.last_notification_status || "-")}</p>
        <p class="panel-note">错误：${escapeHtml(item.last_notification_error || "-")}</p>
        <p class="panel-note">最后发送：${escapeHtml(formatTime(item.last_notification_at))}</p>
      </div>
      <div class="server-box">
        <strong>授权与绑定</strong>
        <p class="panel-note">模板授权总次数：${formatCount(item.granted_template_count)}</p>
        <p class="panel-note">模板明细：${escapeHtml(templateCountText)}</p>
        <p class="panel-note">最近授权结果：${escapeHtml(permissionText)}</p>
        <p class="panel-note">上次授权提交：${escapeHtml(formatTime(item.subscription_updated_at))}</p>
      </div>
    </div>
    <div class="server-box">
      <strong>当前判断</strong>
      <p class="panel-note">
        ${
          item.notification_enabled
            ? "后端判断当前还有可用的订阅通知次数。"
            : "后端判断当前没有可用的订阅通知次数，所以新的告警会直接失败。"
        }
      </p>
      <p class="panel-note">微信订阅通知是按次数消耗的，每成功发送 1 次就会扣掉 1 次。</p>
    </div>
    <details class="raw-json-block">
      <summary>展开看完整设备状态</summary>
      <pre class="raw-json">${escapeHtml(JSON.stringify(item, null, 2))}</pre>
    </details>
  `;
  elements.deviceStatusCard.appendChild(card);
}

async function fetchJson(path) {
  const response = await AppApi.apiFetch(path);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${path}`);
  }
  return response.json();
}

async function loadBackendResults() {
  const trackedUserId = saveTrackedUserId();
  elements.debugHuaweiUrl.textContent = AppApi.buildDisplayUrl("/api/local/debug/huawei-recent");
  elements.debugEventsUrl.textContent = AppApi.buildDisplayUrl("/events");
  elements.debugDeviceUrl.textContent = AppApi.buildDisplayUrl(
    `/api/mobile/devices/${encodeURIComponent(trackedUserId)}/status`
  );

  const [huaweiResult, eventsResult, deviceResult] = await Promise.allSettled([
    fetchJson("/api/local/debug/huawei-recent"),
    fetchJson("/events"),
    fetchJson(`/api/mobile/devices/${encodeURIComponent(trackedUserId)}/status`),
  ]);

  const huaweiItems = huaweiResult.status === "fulfilled" ? huaweiResult.value.items || [] : [];
  const eventItems = eventsResult.status === "fulfilled" ? eventsResult.value.items || [] : [];
  const deviceItem =
    deviceResult.status === "fulfilled" && deviceResult.value ? deviceResult.value.item || null : null;

  elements.status.textContent = "在线";
  elements.statusDot.style.background = "var(--ok)";
  elements.webhookCount.textContent = String(huaweiItems.length);
  elements.eventCount.textContent = String(eventItems.length);

  renderSummaryCards(huaweiItems, eventItems, deviceItem);
  renderHuaweiAudit(huaweiItems);
  renderDispatchEvents(eventItems);
  renderDeviceStatus(deviceItem, trackedUserId);

  const errors = [];
  if (huaweiResult.status === "rejected") {
    errors.push("华为云回调审计读取失败");
  }
  if (eventsResult.status === "rejected") {
    errors.push("后端事件结果读取失败");
  }
  if (deviceResult.status === "rejected") {
    errors.push(`用户 ${trackedUserId} 的设备状态读取失败`);
  }

  if (errors.length) {
    elements.status.textContent = "部分失败";
    elements.statusDot.style.background = "var(--accent)";
    setFeedback(`${errors.join("；")}。如果是华为云回调审计失败，请确认你打开的是本机后端页面。`, true);
    return;
  }

  const latestEvent = eventItems[0] || {};
  const latestNotificationStatus = deviceItem?.last_notification_status || "暂无";
  const latestSent = latestEvent.notification_summary?.sent ?? 0;
  const latestFailed = latestEvent.notification_summary?.failed ?? 0;
  setFeedback(`最近一次通知状态：${latestNotificationStatus}；最近事件 sent=${latestSent}，failed=${latestFailed}。`);
}

elements.refreshButton.addEventListener("click", () => {
  loadBackendResults().catch((error) => {
    elements.status.textContent = "加载失败";
    elements.statusDot.style.background = "var(--accent)";
    setFeedback(`刷新失败：${trimText(error.message, 180)}`, true);
  });
});

elements.saveDeviceUserButton.addEventListener("click", () => {
  loadBackendResults().catch((error) => {
    elements.status.textContent = "加载失败";
    elements.statusDot.style.background = "var(--accent)";
    setFeedback(`读取失败：${trimText(error.message, 180)}`, true);
  });
});

elements.deviceStatusUserId.value = loadTrackedUserId();

loadBackendResults().catch((error) => {
  elements.status.textContent = "加载失败";
  elements.statusDot.style.background = "var(--accent)";
  setFeedback(`页面初始化失败：${trimText(error.message, 180)}`, true);
});

setInterval(() => {
  loadBackendResults().catch(() => {});
}, 5000);
