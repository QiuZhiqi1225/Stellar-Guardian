const api = require("../../utils/api");

Page({
  data: {
    feedback: "",
    mapMarkers: [],
    session: null,
    sessionId: "",
  },

  onLoad(options) {
    this.setData({
      sessionId: options.sessionId || "",
    });
  },

  onShow() {
    this.refreshSession();
  },

  setFeedback(message) {
    this.setData({ feedback: message || "" });
  },

  refreshSession() {
    if (!this.data.sessionId) {
      this.setFeedback("缺少会话 ID。");
      return;
    }

    api.get(`/api/call-sessions/${encodeURIComponent(this.data.sessionId)}`)
      .then((data) => {
        const session = this.decorateSession(data.item || null);
        this.setData({
          mapMarkers: session && session.location ? [this.buildMapMarker(session)] : [],
          session,
        });
        this.setFeedback("");
      })
      .catch((error) => {
        this.setFeedback(`读取详情失败：${error.message || "请重试"}`);
      });
  },

  decorateSession(session) {
    if (!session) {
      return null;
    }

    const location = this.normalizeLocation(session.location);
    return {
      ...session,
      hasLocation: !!location,
      location,
      locationLatitudeText: location ? location.latitude.toFixed(6) : "",
      locationLongitudeText: location ? location.longitude.toFixed(6) : "",
      locationText: location ? `${location.latitude.toFixed(6)}, ${location.longitude.toFixed(6)}` : "",
    };
  },

  normalizeLocation(location) {
    if (!location || typeof location !== "object") {
      return null;
    }

    const latitude = Number(location.latitude);
    const longitude = Number(location.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
      return null;
    }
    if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
      return null;
    }

    return {
      latitude,
      longitude,
      label: location.label || "",
    };
  },

  buildMapMarker(session) {
    const markerLabel = session.location.label || session.profile_display_name || session.target_external_key || "跌倒位置";
    return {
      id: 1,
      latitude: session.location.latitude,
      longitude: session.location.longitude,
      width: 28,
      height: 28,
      callout: {
        content: markerLabel,
        display: "ALWAYS",
        padding: 10,
        borderRadius: 12,
      },
    };
  },

  updateStatus(status) {
    return api.post(`/api/call-sessions/${encodeURIComponent(this.data.sessionId)}/status`, { status })
      .then(() => {
        this.setFeedback(`当前会话已更新为 ${status}。`);
        this.refreshSession();
      })
      .catch((error) => {
        this.setFeedback(`更新失败：${error.message || "请重试"}`);
      });
  },

  markAccepted() {
    this.updateStatus("accepted");
  },

  markEnded() {
    this.updateStatus("ended");
  },

  markRejected() {
    this.updateStatus("rejected");
  },

  makeEmergencyCall() {
    const phoneNumber = this.data.session && this.data.session.callback_phone;
    if (!phoneNumber) {
      this.setFeedback("后端还没有配置回拨号码。");
      return;
    }

    wx.makePhoneCall({
      phoneNumber,
      fail: (error) => {
        this.setFeedback(`拨号失败：${error.errMsg || "请重试"}`);
      },
    });
  },

  openLocation() {
    const session = this.data.session;
    if (!session || !session.location) {
      this.setFeedback("当前还没有可打开的跌倒位置。");
      return;
    }

    wx.openLocation({
      latitude: session.location.latitude,
      longitude: session.location.longitude,
      name: session.location.label || session.profile_display_name || session.target_external_key || "跌倒位置",
      address: session.location.label || "",
      scale: 18,
      fail: (error) => {
        this.setFeedback(`打开地图失败：${error.errMsg || "请稍后重试"}`);
      },
    });
  },

  copyCoordinates() {
    const session = this.data.session;
    if (!session || !session.locationText) {
      this.setFeedback("当前没有可复制的坐标。");
      return;
    }

    wx.setClipboardData({
      data: session.locationText,
      success: () => {
        this.setFeedback("坐标已复制。");
      },
      fail: (error) => {
        this.setFeedback(`复制失败：${error.errMsg || "请稍后重试"}`);
      },
    });
  },
});
