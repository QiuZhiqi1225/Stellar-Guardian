const api = require("../../utils/api");
const storage = require("../../utils/storage");

Page({
  data: {
    baseUrl: "",
    emergencyCallNumber: "",
    feedback: "",
    templateCount: 0,
    miniProgramState: "",
  },

  onShow() {
    this.setData({
      baseUrl: storage.getBaseUrl(),
    });
    this.refreshConfig();
  },

  handleBaseUrlInput(event) {
    this.setData({ baseUrl: event.detail.value });
  },

  setFeedback(message) {
    this.setData({ feedback: message || "" });
  },

  saveBaseUrl() {
    const normalized = storage.setBaseUrl(this.data.baseUrl);
    this.setData({ baseUrl: normalized });
    this.setFeedback(`Saved: ${normalized}`);
    this.refreshConfig();
  },

  refreshConfig() {
    return api.get(`/api/mobile/app-config?ts=${Date.now()}`)
      .then((data) => {
        this.setData({
          emergencyCallNumber: data.emergency_call_number || "",
          templateCount: (data.subscribe_template_ids || []).length,
          miniProgramState: data.mini_program_state || "",
        });
        this.setFeedback("Backend reachable");
      })
      .catch((error) => {
        this.setData({
          emergencyCallNumber: "",
          templateCount: 0,
          miniProgramState: "",
        });
        this.setFeedback(error.message || "Load failed");
      });
  },

  openHome() {
    wx.navigateTo({ url: "/pages/home/home" });
  },

  openAccount() {
    wx.navigateTo({ url: "/pages/account/account" });
  },
});
