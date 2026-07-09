const api = require("./utils/api");
const storage = require("./utils/storage");

const ALERT_POLL_INTERVAL_MS = 5000;
const ALERT_ACK_STORAGE_PREFIX = "mini_alert_ack_sessions";
const MAX_ACK_SESSION_IDS = 30;

App({
  globalData: {
    baseUrl: "",
    appUserId: "",
    displayName: "",
    recipientName: "",
    pendingItems: [],
    pendingCount: 0,
    latestAlertTitle: "",
  },

  onLaunch() {
    this.refreshClientState();
  },

  onShow() {
    this.startAlertMonitor();
  },

  onHide() {
    this.stopAlertMonitor();
  },

  refreshClientState() {
    this.globalData.baseUrl = storage.getBaseUrl();
    this.globalData.appUserId = storage.getAppUserId();
    this.globalData.displayName = storage.getDisplayName();
    this.globalData.recipientName = storage.getRecipientName();
  },

  getAlertAckStorageKey(appUserId) {
    return `${ALERT_ACK_STORAGE_PREFIX}:${String(appUserId || "").trim()}`;
  },

  loadAcknowledgedSessionIds(appUserId) {
    try {
      const raw = wx.getStorageSync(this.getAlertAckStorageKey(appUserId));
      if (!Array.isArray(raw)) {
        return new Set();
      }
      return new Set(raw.map((item) => String(item || "").trim()).filter(Boolean));
    } catch (error) {
      console.warn("[app] loadAcknowledgedSessionIds failed", error);
      return new Set();
    }
  },

  saveAcknowledgedSessionIds(appUserId, sessionIds) {
    const nextIds = Array.from(sessionIds || []).map((item) => String(item || "").trim()).filter(Boolean);
    try {
      wx.setStorageSync(this.getAlertAckStorageKey(appUserId), nextIds.slice(-MAX_ACK_SESSION_IDS));
    } catch (error) {
      console.warn("[app] saveAcknowledgedSessionIds failed", error);
    }
  },

  markSessionsAsAlerted(appUserId, items) {
    const current = this.acknowledgedSessionIds || this.loadAcknowledgedSessionIds(appUserId);
    items.forEach((item) => {
      const sessionId = String(item && item.session_id ? item.session_id : "").trim();
      if (sessionId) {
        current.add(sessionId);
      }
    });
    this.acknowledgedSessionIds = current;
    this.saveAcknowledgedSessionIds(appUserId, current);
  },

  syncAppBadge(count) {
    const normalizedCount = Math.max(0, Number(count) || 0);
    if (typeof wx.setAppBadge !== "function" || typeof wx.removeAppBadge !== "function") {
      return;
    }

    if (normalizedCount > 0) {
      wx.setAppBadge({ count: Math.min(normalizedCount, 99) });
      return;
    }

    wx.removeAppBadge();
  },

  startAlertMonitor() {
    this.stopAlertMonitor();
    this.refreshClientState();
    this.acknowledgedSessionIds = this.loadAcknowledgedSessionIds(this.globalData.appUserId);
    this.pollPendingSessions();
    this.alertPollTimer = setInterval(() => {
      this.pollPendingSessions();
    }, ALERT_POLL_INTERVAL_MS);
  },

  stopAlertMonitor() {
    if (this.alertPollTimer) {
      clearInterval(this.alertPollTimer);
      this.alertPollTimer = null;
    }
  },

  pollPendingSessions() {
    this.refreshClientState();
    const appUserId = String(this.globalData.appUserId || "").trim();
    if (!appUserId) {
      console.log("[alert-monitor] skip poll: appUserId empty");
      this.pendingSessionIds = new Set();
      this.acknowledgedSessionIds = new Set();
      this.globalData.pendingItems = [];
      this.globalData.pendingCount = 0;
      this.globalData.latestAlertTitle = "";
      this.syncAppBadge(0);
      return Promise.resolve([]);
    }

    return api.get(`/api/app-users/${encodeURIComponent(appUserId)}/pending-sessions`)
      .then((response) => {
        const items = Array.isArray(response.items) ? response.items : [];
        const nextIds = new Set(items.map((item) => String(item.session_id)));
        const acknowledgedIds = this.acknowledgedSessionIds || this.loadAcknowledgedSessionIds(appUserId);
        const unreadItems = items.filter((item) => !acknowledgedIds.has(String(item.session_id)));
        console.log("[alert-monitor] poll result", {
          route: (getCurrentPages().slice(-1)[0] || {}).route || "",
          appUserId,
          totalItems: items.length,
          sessionIds: items.map((item) => String(item.session_id || "")),
          unreadCount: unreadItems.length,
          unreadSessionIds: unreadItems.map((item) => String(item.session_id || "")),
        });

        this.pendingSessionIds = nextIds;
        this.acknowledgedSessionIds = acknowledgedIds;
        this.globalData.pendingItems = items;
        this.globalData.pendingCount = items.length;
        this.globalData.latestAlertTitle = items[0]
          ? (items[0].event_title || items[0].recipient_name || "")
          : "";
        this.syncAppBadge(items.length);

        if (unreadItems.length) {
          this.notifyForegroundAlert(unreadItems, appUserId);
        }
        return items;
      })
      .catch((error) => {
        console.warn("[app] pending session poll failed", error);
        return [];
      });
  },

  notifyForegroundAlert(newItems, appUserId) {
    if (this.alertDialogOpen || !newItems.length) {
      console.log("[alert-monitor] modal skipped", {
        alertDialogOpen: !!this.alertDialogOpen,
        newItems: newItems.length,
      });
      return;
    }

    const firstItem = newItems[0] || {};
    const title = newItems.length > 1
      ? `收到 ${newItems.length} 条跌倒告警`
      : "收到新的跌倒告警";
    const content = String(
      firstItem.event_title || firstItem.event_body || firstItem.recipient_name || "请立即查看告警详情。"
    ).slice(0, 60);

    console.log("[alert-monitor] show modal", {
      appUserId,
      title,
      content,
      firstSessionId: String(firstItem.session_id || ""),
    });
    this.markSessionsAsAlerted(appUserId, newItems);
    this.alertDialogOpen = true;
    try {
      wx.vibrateShort();
    } catch (error) {
      console.warn("[app] vibrateShort unavailable", error);
    }

    wx.showModal({
      title,
      content,
      confirmText: "查看",
      cancelText: "稍后",
      success: (result) => {
        console.log("[alert-monitor] modal result", {
          confirm: !!result.confirm,
          cancel: !!result.cancel,
          firstSessionId: String(firstItem.session_id || ""),
        });
        if (result.confirm) {
          this.openAlertDetail(firstItem);
        }
      },
      complete: () => {
        this.alertDialogOpen = false;
      },
    });
  },

  openAlertDetail(item) {
    if (!item || !item.session_id) {
      console.log("[alert-monitor] open detail skipped: missing session");
      return;
    }

    const currentPages = getCurrentPages();
    const currentPage = currentPages[currentPages.length - 1];
    const sessionId = encodeURIComponent(String(item.session_id));

    if (currentPage && currentPage.route === "pages/detail/detail") {
      console.log("[alert-monitor] already on detail page", { sessionId });
      return;
    }

    console.log("[alert-monitor] navigate to detail", { sessionId });
    wx.navigateTo({
      url: `/pages/detail/detail?sessionId=${sessionId}`,
      fail: () => {
        console.log("[alert-monitor] navigate to detail failed, fallback alerts", { sessionId });
        wx.navigateTo({
          url: "/pages/alerts/alerts",
        });
      },
    });
  },
});
