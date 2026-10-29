// Calls to the Live Assist API. Two ways to reach it:
// - config.js apiBase empty (local): through the console server at /api/*, which adds the access key.
// - config.js apiBase set (Amplify): straight to API Gateway, with the access key the user enters in the
//   "Access key" field (kept in sessionStorage only, never in the page or in storage that outlives the tab).

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const apiBase = String((window.LIVE_ASSIST_CONFIG || {}).apiBase || "").replace(/\/+$/, "");
const KEY_STORE = "live-assist-access-key";

function endpoint(path) {
  return (apiBase || "/api") + path;
}

/** True when the console calls API Gateway itself, so the user supplies the access key (the "Access key" field). */
export const usesAccessKey = Boolean(apiBase);

export function getAccessKey() {
  return sessionStorage.getItem(KEY_STORE) || "";
}

/** Remembers the key for this browser tab only. An empty value forgets it. */
export function setAccessKey(value) {
  const key = String(value || "").trim();
  if (key) sessionStorage.setItem(KEY_STORE, key); else sessionStorage.removeItem(KEY_STORE);
}

function authHeaders() {
  if (!apiBase) return { "X-Console": "1" };
  const key = getAccessKey();
  return key ? { "X-Console": "1", "X-Access-Key": key } : { "X-Console": "1" };
}

function unauthorized() {
  return new ApiError(401, "unauthorized", "The access key is missing or wrong. Check the Access key field and try again.");
}

async function request(method, path, body, headers = {}) {
  let response;
  try {
    response = await fetch(endpoint(path), {
      method,
      headers: { ...authHeaders(), ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the server. Check your connection and try again.");
  }
  if (apiBase && response.status === 401) throw unauthorized();
  if (response.status === 304) return { status: 304, data: null, etag: response.headers.get("etag") };
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const err = data && data.error;
    throw new ApiError(response.status, err ? err.code : "error", err ? err.message : `Request failed (${response.status})`);
  }
  return { status: response.status, data, etag: response.headers.get("etag"), text };
}

export const api = {
  options: () => request("GET", "/options").then((r) => r.data),
  createUpload: (body) => request("POST", "/uploads", body).then((r) => r.data),
  startCall: (body) => request("POST", "/calls", body).then((r) => r.data),
  getCall: (id, etag) => request("GET", `/calls/${id}`, undefined, etag ? { "If-None-Match": etag } : {}),
  endCall: (id) => request("POST", `/calls/${id}/end`, {}).then((r) => r.data),
  intakeOpportunities: () => request("GET", "/intake/opportunities").then((r) => r.data),
  intakeOpportunity: (id) => request("GET", `/intake/opportunities/${encodeURIComponent(id)}`).then((r) => r.data),
  intakeLink: (id) => request("POST", `/intake/opportunities/${encodeURIComponent(id)}/link`, {}).then((r) => r.data),
  intakeRecordCall: (id, sessionId) => request("POST", `/intake/opportunities/${encodeURIComponent(id)}/calls`,
    { session_id: sessionId }).then((r) => r.data),
  intakeDiscovery: (id) => request("GET", `/intake/opportunities/${encodeURIComponent(id)}/discovery`).then((r) => r.data),
  minutesMarkdown: async (id) => {
    const response = await fetch(endpoint(`/calls/${id}/minutes?format=md`), { headers: authHeaders() });
    if (apiBase && response.status === 401) throw unauthorized();
    if (!response.ok) throw new ApiError(response.status, "minutes", "The minutes are not ready yet.");
    return response.text();
  },
};

// "proxy": a presigned upload goes through the console server, so the bucket needs no CORS rules (local use).
// "direct": the browser sends it to the bucket, which allows this page's origin (the hosted console).
let uploadMode = "proxy";
export function setUploadMode(mode) {
  uploadMode = mode === "direct" ? "direct" : "proxy";
}

export async function uploadFile(url, blob) {
  let response;
  try {
    response = uploadMode === "direct"
      ? await fetch(url, { method: "PUT", body: blob })
      : await fetch("/upload?url=" + encodeURIComponent(url), {
        method: "PUT", headers: { "X-Console": "1" }, body: blob,
      });
  } catch {
    throw new ApiError(0, "network", "The upload could not be sent.");
  }
  if (!response.ok) throw new ApiError(response.status, "upload", `The upload failed (${response.status}).`);
}
