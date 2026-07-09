(function () {
  const STORAGE_KEY = "shared_backend_base_url";

  function normalizeBaseUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) {
      return window.location.origin;
    }

    const withProtocol = /^https?:\/\//i.test(raw) ? raw : `http://${raw}`;
    const normalized = new URL(withProtocol);
    return normalized.toString().replace(/\/$/, "");
  }

  function getQueryBaseUrl() {
    const params = new URLSearchParams(window.location.search);
    const queryBase = params.get("api_base");
    return queryBase ? normalizeBaseUrl(queryBase) : "";
  }

  function getBaseUrl() {
    return getQueryBaseUrl() || normalizeBaseUrl(localStorage.getItem(STORAGE_KEY) || window.location.origin);
  }

  function setBaseUrl(value) {
    const normalized = normalizeBaseUrl(value);
    localStorage.setItem(STORAGE_KEY, normalized);
    return normalized;
  }

  function clearBaseUrl() {
    localStorage.removeItem(STORAGE_KEY);
  }

  function buildUrl(path, baseUrl = getBaseUrl()) {
    return new URL(path, `${baseUrl}/`).toString();
  }

  function buildDisplayUrl(path, baseUrl = getBaseUrl()) {
    const value = new URL(path, `${baseUrl}/`).toString();
    return value.replace(/\/$/, "");
  }

  function apiFetch(path, options) {
    return fetch(buildUrl(path), options);
  }

  function openRoomWindow(sessionId, params = {}) {
    const url = new URL(`/app-call/${encodeURIComponent(sessionId)}`, window.location.origin);
    url.searchParams.set("api_base", getBaseUrl());
    url.searchParams.set("autojoin", "1");
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value);
      }
    });
    window.open(url.toString(), "_blank", "noopener");
  }

  window.AppApi = {
    STORAGE_KEY,
    normalizeBaseUrl,
    getBaseUrl,
    setBaseUrl,
    clearBaseUrl,
    buildUrl,
    buildDisplayUrl,
    apiFetch,
    openRoomWindow,
  };
})();
