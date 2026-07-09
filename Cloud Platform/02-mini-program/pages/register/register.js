const storage = require("../../utils/storage");
const api = require("../../utils/api");

Page({
  data: {
    appUserId: "",
    displayName: "",
    secret: "",
    externalKey: "",
    feedback: "",
  },

  onShow() {
    this.setData({
      appUserId: storage.getAppUserId(),
      displayName: storage.getDisplayName(),
      externalKey: storage.getExternalKey(),
      secret: "",
    });
  },

  handleAppUserIdInput(event) {
    this.setData({ appUserId: event.detail.value });
  },

  handleDisplayNameInput(event) {
    this.setData({ displayName: event.detail.value });
  },

  handleSecretInput(event) {
    this.setData({ secret: event.detail.value });
  },

  handleExternalKeyInput(event) {
    this.setData({ externalKey: event.detail.value });
  },

  setFeedback(message) {
    this.setData({ feedback: message || "" });
  },

  registerUser() {
    const payload = {
      user_id: String(this.data.appUserId || "").trim(),
      display_name: String(this.data.displayName || "").trim(),
      secret: String(this.data.secret || ""),
      notes: "wechat mini program user",
    };
    const externalKey = String(this.data.externalKey || "").trim();

    if (!payload.user_id || !payload.display_name || payload.secret.length < 4) {
      this.setFeedback("请填好用户 ID、显示名称和至少 4 位口令。");
      return;
    }

    api.post("/api/users/register", payload)
      .then(() => {
        storage.setAppUserId(payload.user_id);
        storage.setDisplayName(payload.display_name);
        storage.setRecipientName(payload.display_name);
        storage.setExternalKey(externalKey);
        if (!externalKey) {
          this.setFeedback(`已创建 ${payload.user_id}`);
          return null;
        }
        return api.post("/api/mobile/register-device", {
          app_user_id: payload.user_id,
          recipient_name: payload.display_name,
          device_token: storage.getDeviceToken(),
          platform: "wechat_miniprogram",
          external_key: externalKey,
        }).then(() => {
          this.setFeedback(`已创建 ${payload.user_id}，并绑定对象 ${externalKey}`);
          return null;
        }).catch((error) => {
          this.setFeedback(`已创建 ${payload.user_id}，但对象 ${externalKey} 绑定失败：${error.message || "请稍后重试"}`);
          return null;
        });
      })
      .catch((error) => {
        this.setFeedback(error.message || "注册失败");
      });
  },

  openLogin() {
    wx.navigateTo({ url: "/pages/login/login" });
  },
});
