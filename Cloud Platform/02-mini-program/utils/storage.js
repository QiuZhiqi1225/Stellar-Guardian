const BASE_URL_KEY = "mini_base_url";
const DEFAULT_BASE_URL = "https://api.fallguard.cn";
const LEGACY_BASE_URLS = new Set([
  "https://constructed-rca-don-shots.trycloudflare.com",
]);
const APP_USER_ID_KEY = "mini_app_user_id";
const DISPLAY_NAME_KEY = "mini_display_name";
const RECIPIENT_NAME_KEY = "mini_recipient_name";
const EXTERNAL_KEY = "mini_external_key";
const DEVICE_TOKEN_KEY = "mini_device_token";

function isDevtools() {
  try {
    return wx.getSystemInfoSync().platform === "devtools";
  } catch (error) {
    return false;
  }
}

function normalizeBaseUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return DEFAULT_BASE_URL;
  }

  if (LEGACY_BASE_URLS.has(raw.replace(/\/+$/, ""))) {
    return DEFAULT_BASE_URL;
  }

  if (/^https?:\/\/(127\.0\.0\.1|localhost):\d+$/i.test(raw) && !isDevtools()) {
    return DEFAULT_BASE_URL;
  }

  let normalized = raw;
  if (!/^https?:\/\//i.test(normalized)) {
    if (/^(127\.0\.0\.1|localhost):\d+$/i.test(normalized)) {
      normalized = `http://${normalized}`;
    } else {
      normalized = `https://${normalized}`;
    }
  }
  return normalized.replace(/\/+$/, "");
}

function getBaseUrl() {
  return normalizeBaseUrl(wx.getStorageSync(BASE_URL_KEY) || DEFAULT_BASE_URL);
}

function setBaseUrl(value) {
  const normalized = normalizeBaseUrl(value);
  wx.setStorageSync(BASE_URL_KEY, normalized);
  return normalized;
}

function getAppUserId() {
  return String(wx.getStorageSync(APP_USER_ID_KEY) || "");
}

function setAppUserId(value) {
  wx.setStorageSync(APP_USER_ID_KEY, String(value || "").trim());
}

function getDisplayName() {
  return String(wx.getStorageSync(DISPLAY_NAME_KEY) || "");
}

function setDisplayName(value) {
  wx.setStorageSync(DISPLAY_NAME_KEY, String(value || "").trim());
}

function getRecipientName() {
  return String(wx.getStorageSync(RECIPIENT_NAME_KEY) || "");
}

function setRecipientName(value) {
  wx.setStorageSync(RECIPIENT_NAME_KEY, String(value || "").trim());
}

function getExternalKey() {
  return String(wx.getStorageSync(EXTERNAL_KEY) || "");
}

function setExternalKey(value) {
  wx.setStorageSync(EXTERNAL_KEY, String(value || "").trim());
}

function getDeviceToken() {
  let token = String(wx.getStorageSync(DEVICE_TOKEN_KEY) || "");
  if (!token) {
    token = `wechat-mini-${Date.now()}`;
    wx.setStorageSync(DEVICE_TOKEN_KEY, token);
  }
  return token;
}

module.exports = {
  getAppUserId,
  getBaseUrl,
  getDeviceToken,
  getDisplayName,
  getExternalKey,
  getRecipientName,
  normalizeBaseUrl,
  setAppUserId,
  setBaseUrl,
  setDisplayName,
  setExternalKey,
  setRecipientName,
};
