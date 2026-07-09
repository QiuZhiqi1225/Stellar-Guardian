const storage = require("./storage");

function buildUrl(path) {
  const baseUrl = storage.getBaseUrl();
  const cleanedPath = String(path || "").startsWith("/") ? path : `/${path}`;
  return `${baseUrl}${cleanedPath}`;
}

function formatHttpError(statusCode, detail) {
  if (statusCode === 404) {
    return `API not found: ${detail}`;
  }
  if (statusCode === 401 || statusCode === 403) {
    return `Permission denied: ${detail}`;
  }
  if (statusCode >= 500) {
    return `Server error: ${detail}`;
  }
  return String(detail || `HTTP ${statusCode}`);
}

function formatNetworkError(error, url) {
  const raw = error && (error.errMsg || error.message) ? (error.errMsg || error.message) : "unknown";
  if (String(raw).includes("url not in domain list")) {
    return `微信已拦截该请求。当前地址 ${url} 不在小程序 request 合法域名列表中。真机预览/体验版必须使用已在微信后台配置的 HTTPS 备案域名；开发者工具里的“不校验合法域名”对这里不生效。仅做调试时，请在手机端打开调试模式后再试。`;
  }
  if (String(raw).includes("request:fail")) {
    return `Request failed. URL: ${url}. Raw: ${raw}`;
  }
  return String(raw);
}

function request(path, options = {}) {
  const {
    method = "GET",
    data = undefined,
    header = {},
  } = options;

  return new Promise((resolve, reject) => {
    const url = buildUrl(path);
    console.info("[api] request", { method, url, data });
    wx.request({
      url,
      method,
      data,
      header: {
        "Content-Type": "application/json",
        ...header,
      },
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          console.info("[api] response", {
            method,
            url,
            statusCode: response.statusCode,
            data: response.data,
          });
          resolve(response.data);
          return;
        }

        const detail =
          response.data && response.data.detail
            ? response.data.detail
            : `HTTP ${response.statusCode}`;
        console.error("[api] http error", {
          method,
          url,
          statusCode: response.statusCode,
          data: response.data,
        });
        reject(new Error(formatHttpError(response.statusCode, detail)));
      },
      fail(error) {
        console.error("[api] network error", { method, url, error });
        reject(new Error(formatNetworkError(error, url)));
      },
    });
  });
}

function get(path) {
  return request(path, { method: "GET" });
}

function post(path, data) {
  return request(path, { method: "POST", data });
}

module.exports = {
  buildUrl,
  get,
  post,
  request,
};
