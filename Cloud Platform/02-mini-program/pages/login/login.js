const storage = require("../../utils/storage");
const api = require("../../utils/api");

Page({
  data: {
    appUserId: "",
    secret: "",
    feedback: "",
  },

  onShow() {
    this.setData({
      appUserId: storage.getAppUserId(),
      secret: "",
    });
  },

  handleAppUserIdInput(event) {
    this.setData({ appUserId: event.detail.value });
  },

  handleSecretInput(event) {
    this.setData({ secret: event.detail.value });
  },

  setFeedback(message) {
    this.setData({ feedback: message || "" });
  },

  loginUser() {
    const payload = {
      user_id: String(this.data.appUserId || "").trim(),
      secret: String(this.data.secret || ""),
    };

    if (!payload.user_id || payload.secret.length < 4) {
      this.setFeedback("请输入用户 ID 和正确口令。");
      return;
    }

    api.post("/api/users/login", payload)
      .then((data) => {
        const user = data.user || {};
        storage.setAppUserId(user.user_id || payload.user_id);
        storage.setDisplayName(user.display_name || "");
        if (!storage.getRecipientName()) {
          storage.setRecipientName(user.display_name || user.user_id || payload.user_id);
        }
        this.setFeedback(`已登录 ${user.display_name || user.user_id}`);
      })
      .catch((error) => {
        this.setFeedback(error.message || "登录失败");
      });
  },

  openRegister() {
    wx.navigateTo({ url: "/pages/register/register" });
  },
});
