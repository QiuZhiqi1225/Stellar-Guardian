const api = require("../../utils/api");
const storage = require("../../utils/storage");

Page({
  data: {
    appUserId: "",
    baseUrl: "",
    recipientName: "",
    criticalBannerText: "",
    latestPendingSessionId: "",
    latestAlertTitle: "",
    pendingCount: 0,
    pendingSummary: "",
  },

  onShow() {
    this.modalOpen = false;
    this.setData({
      appUserId: storage.getAppUserId(),
      baseUrl: storage.getBaseUrl(),
      recipientName: storage.getRecipientName(),
    });
    this.refreshPendingOverview();
    this.startPolling();
  },

  onHide() {
    this.stopPolling();
  },

  onUnload() {
    this.stopPolling();
  },

  refreshPendingOverview() {
    const appUserId = storage.getAppUserId();
    if (!appUserId) {
      this.setData({
        criticalBannerText: "",
        latestPendingSessionId: "",
        pendingCount: 0,
        latestAlertTitle: "",
        pendingSummary: "",
      });
      return Promise.resolve();
    }

    return api.get(`/api/app-users/${encodeURIComponent(appUserId)}/pending-sessions`)
      .then((response) => {
        const items = response.items || [];
        const app = getApp();
        const acknowledgedIds = app.loadAcknowledgedSessionIds
          ? app.loadAcknowledgedSessionIds(appUserId)
          : new Set();
        const unreadItems = items.filter((item) => !acknowledgedIds.has(String(item.session_id || "")));
        console.log("[home] pending overview", {
          appUserId,
          totalItems: items.length,
          sessionIds: items.map((item) => String(item.session_id || "")),
          unreadCount: unreadItems.length,
          unreadSessionIds: unreadItems.map((item) => String(item.session_id || "")),
        });
        this.setData({
          criticalBannerText: items.length
            ? `当前有 ${items.length} 条待处理跌倒告警，请立即查看。`
            : "",
          latestPendingSessionId: items[0] ? String(items[0].session_id || "") : "",
          pendingCount: items.length,
          pendingSummary: items.length ? `待处理 ${items.length} 条` : "",
          latestAlertTitle: items[0] ? (items[0].event_title || items[0].recipient_name || "") : "",
        });
        if (unreadItems.length) {
          this.showUnreadAlert(unreadItems, appUserId);
        }
      })
      .catch(() => {});
  },

  showUnreadAlert(unreadItems, appUserId) {
    if (this.modalOpen || !unreadItems.length) {
      console.log("[home] modal skipped", {
        modalOpen: !!this.modalOpen,
        unreadCount: unreadItems.length,
      });
      return;
    }

    const app = getApp();
    const firstItem = unreadItems[0] || {};
    if (app.markSessionsAsAlerted) {
      app.markSessionsAsAlerted(appUserId, unreadItems);
    }

    console.log("[home] show unread alert", {
      appUserId,
      firstSessionId: String(firstItem.session_id || ""),
      unreadCount: unreadItems.length,
    });
    this.modalOpen = true;
    wx.showModal({
      title: unreadItems.length > 1 ? `收到 ${unreadItems.length} 条跌倒告警` : "收到新的跌倒告警",
      content: String(firstItem.event_title || firstItem.event_body || firstItem.recipient_name || "请立即查看告警详情。").slice(0, 60),
      confirmText: "查看",
      cancelText: "稍后",
      success: (result) => {
        console.log("[home] modal result", {
          confirm: !!result.confirm,
          cancel: !!result.cancel,
          firstSessionId: String(firstItem.session_id || ""),
        });
        if (result.confirm && firstItem.session_id) {
          wx.navigateTo({
            url: `/pages/detail/detail?sessionId=${encodeURIComponent(String(firstItem.session_id))}`,
          });
        }
      },
      complete: () => {
        this.modalOpen = false;
      },
    });
  },

  startPolling() {
    this.stopPolling();
    this.pollTimer = setInterval(() => {
      this.refreshPendingOverview();
    }, 5000);
  },

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  openAccount() {
    wx.navigateTo({
      url: "/pages/account/account",
    });
  },

  openAlerts() {
    wx.navigateTo({
      url: "/pages/alerts/alerts",
    });
  },

  openLatestPendingAlert() {
    const sessionId = String(this.data.latestPendingSessionId || "").trim();
    if (!sessionId) {
      this.openAlerts();
      return;
    }

    wx.navigateTo({
      url: `/pages/detail/detail?sessionId=${encodeURIComponent(sessionId)}`,
      fail: () => {
        this.openAlerts();
      },
    });
  },

  openSettings() {
    wx.navigateTo({
      url: "/pages/settings/settings",
    });
  },
});
