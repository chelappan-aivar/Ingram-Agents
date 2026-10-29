/**
 * Sign-in for this page reuses the dashboard's own cookie session (`/api/v1`), so the Pipeline Runs
 * tab is no more open than the rest of the portal, and Live Assist is reached through the portal's
 * authenticated proxy with its access key held server-side.
 */
import { ENDPOINTS } from "../config.js";
import { ApiError, createClient } from "./http.js";

let csrfToken = null;
const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

const bare = createClient({ baseUrl: ENDPOINTS.dashboardApi, credentials: "include", label: "The sign-in service" });

async function csrf() {
  if (!csrfToken) csrfToken = (await bare.get("/auth/csrf"))?.csrf_token ?? null;
  if (!csrfToken) throw new ApiError("Could not start a secure session.", { code: "csrf" });
  return csrfToken;
}

async function refresh() {
  try {
    await bare.post("/auth/refresh", {}, { headers: { "X-CSRF-Token": await csrf() } });
    return true;
  } catch {
    return false;
  }
}

/** Resolves with the signed-in user, or redirects to the portal's login page. */
export async function requireSignIn() {
  const me = async () => bare.get("/me");
  try {
    return await me();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && (await refresh())) {
      try { return await me(); } catch { /* fall through to login */ }
    }
    if (err instanceof ApiError && err.status !== 401 && err.status !== 403) throw err;
  }
  window.location.replace("/login/");
  return new Promise(() => {}); // never resolves: the page is navigating away
}

/** HTTP client for the Live Assist proxy: cookies + CSRF header on writes, one refresh on 401. */
export function createLiveAssistHttp() {
  return createClient({
    baseUrl: ENDPOINTS.liveAssist,
    credentials: "include",
    label: "Live Assist",
    beforeRequest: async ({ method, headers }) => {
      if (MUTATING.has(method)) headers.set("X-CSRF-Token", await csrf());
    },
    onUnauthorized: async () => refresh(),
  });
}

export function resetCsrf() {
  csrfToken = null;
}
