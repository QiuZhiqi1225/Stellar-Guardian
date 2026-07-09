const api = require("../../utils/api");
const storage = require("../../utils/storage");

Page({
  data: {
    appUserId: "",
    emergencyCallNumber: "",
    feedback: "",
    historyItems: [],
    pendingItems: [],
  },

  onShow() {
    this.bootstrap();
  },

  onHide() {
    this.stopPolling();
  },

  onUnload() {
    this.stopPolling();
  },

  onPullDownRefresh() {
    this.refreshSessions().finally(() => {
      wx.stopPullDownRefresh();
    });
  },

  bootstrap() {
    this.setData({
      appUserId: storage.getAppUserId(),
    });
    this.loadMobileConfig();
    this.refreshSessions();
    this.startPolling();
  },

  setFeedback(message) {
    this.setData({ feedback: message || "" });
  },

  loadMobileConfig() {
    return api.get("/api/mobile/app-config")
      .then((data) => {
        this.setData({
          emergencyCallNumber: data.emergency_call_number || "",
        });
      })
      .catch(() => {});
  },

  refreshSessions() {
    const appUserId = storage.getAppUserId();
    this.setData({ appUserId });
    if (!appUserId) {
      this.setData({
        pendingItems: [],
        historyItems: [],
      });
      this.setFeedback("Please log in and register this mini program device first.");
      return Promise.resolve();
    }

    return Promise.all([
      api.get(`/api/app-users/${encodeURIComponent(appUserId)}/pending-sessions`),
      api.get(`/api/app-users/${encodeURIComponent(appUserId)}/sessions`),
    ])
      .then(([pending, history]) => {
        this.setData({
          pendingItems: pending.items || [],
          historyItems: history.items || [],
        });
        this.setFeedback("");
      })
      .catch((error) => {
        this.setFeedback(error.message || "Please check the backend URL.");
      });
  },

  startPolling() {
    this.stopPolling();
    this.pollTimer = setInterval(() => {
      this.refreshSessions();
    }, 5000);
  },

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  updateSessionStatus(sessionId, status) {
    return api.post(`/api/call-sessions/${encodeURIComponent(sessionId)}/status`, {
      status,
    })
      .then(() => {
        this.setFeedback(`Session ${sessionId} updated to ${status}.`);
        return this.refreshSessions();
      })
      .catch((error) => {
        this.setFeedback(error.message || "Failed to update session status.");
      });
  },

  markAccepted(event) {
    const sessionId = event.currentTarget.dataset.sessionId;
    this.updateSessionStatus(sessionId, "accepted");
  },

  markRejected(event) {
    const sessionId = event.currentTarget.dataset.sessionId;
    this.updateSessionStatus(sessionId, "rejected");
  },

  openDetail(event) {
    const sessionId = event.currentTarget.dataset.sessionId;
    wx.navigateTo({
      url: `/pages/detail/detail?sessionId=${encodeURIComponent(sessionId)}`,
    });
  },

  openAccountPage() {
    wx.navigateTo({ url: "/pages/account/account" });
  },

  openSettingsPage() {
    wx.navigateTo({ url: "/pages/settings/settings" });
  },

  makeEmergencyCall() {
    if (!this.data.emergencyCallNumber) {
      this.setFeedback("Emergency callback number is not configured yet.");
      return;
    }

    wx.makePhoneCall({
      phoneNumber: this.data.emergencyCallNumber,
      fail: (error) => {
        this.setFeedback(`Call failed: ${error.errMsg || "please try again"}`);
      },
    });
  },
});
