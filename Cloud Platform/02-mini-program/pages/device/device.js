const api = require("../../utils/api");
const storage = require("../../utils/storage");

Page({
  data: {
    appUserId: "",
    recipientName: "",
    externalKey: "",
    criticalBannerText: "",
    currentBaseUrl: "",
    emergencyCallNumber: "",
    runtimeAppId: "",
    runtimeEnvVersion: "",
    latestPendingSessionId: "",
    pendingCount: 0,
    subscribeTemplateIds: [],
    templateIdsText: "",
    feedback: "",
    lastSubscribeResultText: "",
    deviceStatus: null,
    wechatLoginReady: false,
    subscribeSendReady: false,
    subscribeTemplatePayloadReady: false,
    isRegistering: false,
    isBindingWechat: false,
    isSavingSubscribe: false,
  },

  onShow() {
    this.pendingModalOpen = false;
    this.loadRuntimeInfo();
    this.setData({
      appUserId: storage.getAppUserId(),
      externalKey: storage.getExternalKey(),
      currentBaseUrl: storage.getBaseUrl(),
      recipientName: storage.getRecipientName() || storage.getDisplayName(),
      feedback: "",
    });
    this.loadConfig()
      .then(() => this.loadDeviceStatus())
      .catch(() => {});
    this.refreshPendingAlerts();
    this.startPendingPolling();
  },

  onHide() {
    this.stopPendingPolling();
  },

  onUnload() {
    this.stopPendingPolling();
  },

  loadRuntimeInfo() {
    try {
      const accountInfo = wx.getAccountInfoSync();
      this.setData({
        runtimeAppId: (accountInfo.miniProgram && accountInfo.miniProgram.appId) || "",
        runtimeEnvVersion: (accountInfo.miniProgram && accountInfo.miniProgram.envVersion) || "",
      });
    } catch (error) {
      this.setData({
        runtimeAppId: "",
        runtimeEnvVersion: "",
      });
    }
  },

  handleAppUserIdInput(event) {
    this.setData({ appUserId: event.detail.value });
  },

  handleRecipientNameInput(event) {
    this.setData({ recipientName: event.detail.value });
  },

  handleExternalKeyInput(event) {
    this.setData({ externalKey: event.detail.value });
  },

  setFeedback(message) {
    this.setData({ feedback: message || "" });
  },

  formatSubscribeResult(result) {
    const entries = Object.entries(result || {});
    if (!entries.length) {
      return "";
    }
    return entries
      .map(([key, value]) => `${key}: ${value}`)
      .join(" | ");
  },

  fetchLatestConfig() {
    return api.get(`/api/mobile/app-config?ts=${Date.now()}`);
  },

  getFormPayload() {
    return {
      appUserId: String(this.data.appUserId || "").trim(),
      recipientName: String(this.data.recipientName || "").trim(),
      externalKey: String(this.data.externalKey || "").trim(),
      deviceToken: storage.getDeviceToken(),
    };
  },

  validateForm() {
    const payload = this.getFormPayload();
    if (!payload.appUserId || !payload.recipientName) {
      this.setFeedback("请先填写用户 ID 和接收人名称。");
      return null;
    }
    return payload;
  },

  applyConfig(data) {
    const subscribeTemplateIds = data.subscribe_template_ids || [];
    this.setData({
      currentBaseUrl: storage.getBaseUrl(),
      emergencyCallNumber: data.emergency_call_number || "",
      subscribeTemplateIds,
      templateIdsText: subscribeTemplateIds.join("、"),
      wechatLoginReady: !!data.wechat_login_ready,
      subscribeSendReady: !!data.subscribe_send_ready,
      subscribeTemplatePayloadReady: !!data.subscribe_template_payload_ready,
    });
    return subscribeTemplateIds;
  },

  loadConfig() {
    return this.fetchLatestConfig()
      .then((data) => {
        this.applyConfig(data);
        return data;
      })
      .catch((error) => {
        this.setFeedback(error.message || "读取配置失败");
        throw error;
      });
  },

  loadDeviceStatus() {
    const appUserId = String(this.data.appUserId || "").trim();
    if (!appUserId) {
      this.setData({
        deviceStatus: null,
        lastSubscribeResultText: "",
      });
      return Promise.resolve(null);
    }

    return api.get(`/api/mobile/devices/${encodeURIComponent(appUserId)}/status?ts=${Date.now()}`)
      .then((data) => {
        const item = data.item || null;
        this.setData({
          deviceStatus: item,
          lastSubscribeResultText: item ? this.formatSubscribeResult(item.last_permission_result || {}) : "",
        });
        return item;
      })
      .catch(() => {
        this.setData({
          deviceStatus: null,
          lastSubscribeResultText: "",
        });
        return null;
      });
  },

  startPendingPolling() {
    this.stopPendingPolling();
    this.pendingPollTimer = setInterval(() => {
      this.refreshPendingAlerts();
    }, 5000);
  },

  stopPendingPolling() {
    if (this.pendingPollTimer) {
      clearInterval(this.pendingPollTimer);
      this.pendingPollTimer = null;
    }
  },

  refreshPendingAlerts() {
    const appUserId = String(storage.getAppUserId() || "").trim();
    if (!appUserId) {
      this.setData({
        criticalBannerText: "",
        latestPendingSessionId: "",
        pendingCount: 0,
      });
      return Promise.resolve([]);
    }

    return api.get(`/api/app-users/${encodeURIComponent(appUserId)}/pending-sessions`)
      .then((response) => {
        const items = Array.isArray(response.items) ? response.items : [];
        const app = getApp();
        const acknowledgedIds = app.loadAcknowledgedSessionIds
          ? app.loadAcknowledgedSessionIds(appUserId)
          : new Set();
        const unreadItems = items.filter((item) => !acknowledgedIds.has(String(item.session_id || "")));
        console.log("[device] pending alerts", {
          appUserId,
          totalItems: items.length,
          sessionIds: items.map((item) => String(item.session_id || "")),
          unreadCount: unreadItems.length,
        });
        this.setData({
          criticalBannerText: items.length
            ? `当前有 ${items.length} 条待处理跌倒告警，请立即查看。`
            : "",
          latestPendingSessionId: items[0] ? String(items[0].session_id || "") : "",
          pendingCount: items.length,
        });
        if (unreadItems.length) {
          this.showPendingAlertModal(unreadItems, appUserId);
        }
        return items;
      })
      .catch((error) => {
        console.warn("[device] pending alert poll failed", error);
        return [];
      });
  },

  showPendingAlertModal(unreadItems, appUserId) {
    if (this.pendingModalOpen || !unreadItems.length) {
      console.log("[device] modal skipped", {
        modalOpen: !!this.pendingModalOpen,
        unreadCount: unreadItems.length,
      });
      return;
    }

    const app = getApp();
    const firstItem = unreadItems[0] || {};
    if (app.markSessionsAsAlerted) {
      app.markSessionsAsAlerted(appUserId, unreadItems);
    }
    console.log("[device] show unread alert", {
      appUserId,
      firstSessionId: String(firstItem.session_id || ""),
      unreadCount: unreadItems.length,
    });

    this.pendingModalOpen = true;
    wx.showModal({
      title: unreadItems.length > 1 ? `收到 ${unreadItems.length} 条跌倒告警` : "收到新的跌倒告警",
      content: String(firstItem.event_title || firstItem.event_body || firstItem.recipient_name || "请立即查看告警详情。").slice(0, 60),
      confirmText: "查看",
      cancelText: "稍后",
      success: (result) => {
        console.log("[device] modal result", {
          confirm: !!result.confirm,
          cancel: !!result.cancel,
          firstSessionId: String(firstItem.session_id || ""),
        });
        if (result.confirm) {
          this.openLatestPendingAlert(firstItem.session_id);
        }
      },
      complete: () => {
        this.pendingModalOpen = false;
      },
    });
  },

  openLatestPendingAlert(sessionIdOverride) {
    const sessionId = String(sessionIdOverride || this.data.latestPendingSessionId || "").trim();
    if (!sessionId) {
      wx.navigateTo({ url: "/pages/alerts/alerts" });
      return;
    }

    wx.navigateTo({
      url: `/pages/detail/detail?sessionId=${encodeURIComponent(sessionId)}`,
      fail: () => {
        wx.navigateTo({ url: "/pages/alerts/alerts" });
      },
    });
  },

  registerMiniDevice() {
    const payload = this.validateForm();
    if (!payload) {
      return;
    }

    this.setData({ isRegistering: true });
    api.post("/api/mobile/register-device", {
      app_user_id: payload.appUserId,
      recipient_name: payload.recipientName,
      device_token: payload.deviceToken,
      platform: "wechat_miniprogram",
      external_key: payload.externalKey || null,
    })
      .then(() => {
        storage.setAppUserId(payload.appUserId);
        storage.setRecipientName(payload.recipientName);
        storage.setExternalKey(payload.externalKey);
        this.setData({ currentBaseUrl: storage.getBaseUrl() });
        this.setFeedback("设备登记完成。接下来请绑定微信，然后再申请通知授权。");
        return this.loadDeviceStatus();
      })
      .catch((error) => {
        this.setFeedback(error.message || "设备登记失败");
      })
      .finally(() => {
        this.setData({ isRegistering: false });
      });
  },

  bindWechatAccount() {
    const payload = this.validateForm();
    if (!payload) {
      return;
    }
    if (!this.data.wechatLoginReady) {
      this.setFeedback("后端还没有配置微信小程序 AppID / AppSecret。");
      return;
    }

    this.setData({ isBindingWechat: true });
    wx.login({
      success: (loginResult) => {
        const code = String((loginResult && loginResult.code) || "").trim();
        if (!code) {
          this.setData({ isBindingWechat: false });
          this.setFeedback("微信登录失败，没拿到 code。");
          return;
        }

        api.post("/api/mobile/wechat/login", {
          app_user_id: payload.appUserId,
          recipient_name: payload.recipientName,
          device_token: payload.deviceToken,
          code,
          external_key: payload.externalKey || null,
        })
          .then(() => {
            storage.setAppUserId(payload.appUserId);
            storage.setRecipientName(payload.recipientName);
            storage.setExternalKey(payload.externalKey);
            this.setData({ currentBaseUrl: storage.getBaseUrl() });
            this.setFeedback("微信账号绑定完成。");
            return this.loadDeviceStatus();
          })
          .catch((error) => {
            this.setFeedback(error.message || "微信绑定失败");
          })
          .finally(() => {
            this.setData({ isBindingWechat: false });
          });
      },
      fail: (error) => {
        this.setData({ isBindingWechat: false });
        this.setFeedback(error.errMsg || "微信登录失败");
      },
    });
  },

  requestSubscribePermission() {
    const payload = this.validateForm();
    if (!payload) {
      return;
    }

    this.setData({ isSavingSubscribe: true });
    this.fetchLatestConfig()
      .then((data) => {
        const subscribeTemplateIds = this.applyConfig(data);

        if (!subscribeTemplateIds.length) {
          throw new Error("后端还没有配置订阅消息模板 ID。");
        }
        if (!data.subscribe_template_payload_ready) {
          throw new Error("后端还没有配置订阅消息模板字段映射。");
        }

        wx.requestSubscribeMessage({
          tmplIds: subscribeTemplateIds.slice(0, 3),
          success: (result) => {
            const lastSubscribeResultText = this.formatSubscribeResult(result);
            api.post("/api/mobile/subscribe-permission", {
              app_user_id: payload.appUserId,
              recipient_name: payload.recipientName,
              device_token: payload.deviceToken,
              platform: "wechat_miniprogram",
              permission_result: result,
              external_key: payload.externalKey || null,
            })
              .then(() => this.loadDeviceStatus())
              .then((deviceStatus) => {
                const granted = (deviceStatus && deviceStatus.granted_template_ids) || [];
                const grantedCount = (deviceStatus && deviceStatus.granted_template_count) || granted.length;
                const hasAccepted = Object.values(result || {}).some((value) => String(value).toLowerCase().startsWith("accept"));
                this.setData({
                  deviceStatus: deviceStatus || null,
                  lastSubscribeResultText,
                });

                if (grantedCount) {
                  this.setFeedback(`已向后端复查成功，当前剩余可发送次数 ${grantedCount} 次。`);
                  return;
                }
                if (hasAccepted) {
                  this.setFeedback("微信返回了 accept，但后端复查后当前可发送次数仍为 0。请保留本页截图继续排查。");
                  return;
                }
                this.setFeedback("本次没有获得可发送的订阅消息授权。");
              })
              .catch((error) => {
                this.setFeedback(error.message || "保存订阅授权失败");
              })
              .finally(() => {
                this.setData({ isSavingSubscribe: false });
              });
          },
          fail: (error) => {
            this.setData({ isSavingSubscribe: false });
            this.setFeedback(error.errMsg || "订阅授权失败");
          },
        });
      })
      .catch((error) => {
        this.setData({ isSavingSubscribe: false });
        this.setFeedback(error.message || "读取配置失败");
      });
  },
});
