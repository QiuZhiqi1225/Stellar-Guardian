const storage = require("../../utils/storage");

Page({
  data: {
    appUserId: "",
    displayName: "",
    recipientName: "",
  },

  onShow() {
    this.setData({
      appUserId: storage.getAppUserId(),
      displayName: storage.getDisplayName(),
      recipientName: storage.getRecipientName(),
    });
  },

  openRegister() {
    wx.navigateTo({ url: "/pages/register/register" });
  },

  openLogin() {
    wx.navigateTo({ url: "/pages/login/login" });
  },

  openDevice() {
    wx.navigateTo({ url: "/pages/device/device" });
  },

  openSettings() {
    wx.navigateTo({ url: "/pages/settings/settings" });
  },

  openHome() {
    wx.navigateTo({ url: "/pages/home/home" });
  },
});
