/**
 * One configured HTTP client per backend. Cross-cutting concerns live here, not at call sites:
 * timeouts, abort, error normalisation, retry of idempotent reads, and optional auth hooks.
 */

export class ApiError extends Error {
  constructor(message, { status = 0, code = "error", detail } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export class AbortedError extends Error {
  constructor() {
    super("Stopped.");
    this.name = "AbortedError";
  }
}

const RETRYABLE_STATUS = new Set([502, 503, 504]);
const sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new AbortedError());
    const id = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => { clearTimeout(id); reject(new AbortedError()); }, { once: true });
  });

/** Abort when either the caller's signal fires or the timeout elapses. */
function withTimeout(signal, timeoutMs) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const onAbort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", onAbort, { once: true });
  return {
    signal: controller.signal,
    didTimeOut: () => timedOut,
    done: () => { clearTimeout(timer); signal?.removeEventListener("abort", onAbort); },
  };
}

/** Pulls a human message out of the several error shapes these backends use. */
export function messageFrom(data, status) {
  const err = data?.error;
  if (typeof err === "string") return err;
  if (err && typeof err === "object" && err.message) return err.message;
  if (typeof data?.message === "string") return data.message;
  return status ? `Request failed (${status}).` : "Request failed.";
}

/**
 * @param {object} options
 * @param {string} options.baseUrl
 * @param {RequestCredentials} [options.credentials]
 * @param {(init: {method: string, headers: Headers}) => Promise<void>} [options.beforeRequest]
 * @param {(response: Response, retried: boolean) => Promise<boolean>} [options.onUnauthorized] return true to retry once
 * @param {string} [options.label] backend name used in error messages
 */
export function createClient({ baseUrl, credentials = "omit", beforeRequest, onUnauthorized, label = "The service", defaultTimeoutMs = 30_000 }) {
  async function request(method, path, { body, headers: extra, query, signal, timeoutMs = defaultTimeoutMs, raw = false } = {}) {
    const url = new URL(baseUrl.replace(/\/$/, "") + path, window.location.origin);
    for (const [k, v] of Object.entries(query ?? {})) if (v != null && v !== "") url.searchParams.set(k, String(v));
    const idempotent = method === "GET";
    const maxAttempts = idempotent ? 3 : 1;
    let unauthorizedRetried = false;

    for (let attempt = 1; ; attempt++) {
      const headers = new Headers({ Accept: "application/json", ...extra });
      if (body !== undefined) headers.set("Content-Type", "application/json");
      if (beforeRequest) await beforeRequest({ method, headers });
      const guard = withTimeout(signal, timeoutMs);
      let response;
      try {
        response = await fetch(url, { method, headers, credentials, body: body === undefined ? undefined : JSON.stringify(body), signal: guard.signal, cache: "no-store" });
      } catch (err) {
        guard.done();
        if (signal?.aborted) throw new AbortedError();
        const failure = new ApiError(guard.didTimeOut() ? `${label} took too long to answer.` : `Cannot reach ${label.toLowerCase()}. Check your connection and try again.`, { code: guard.didTimeOut() ? "timeout" : "network" });
        if (idempotent && attempt < maxAttempts) { await sleep(600 * attempt, signal); continue; }
        throw failure;
      }
      guard.done();

      if (response.status === 401 && onUnauthorized && !unauthorizedRetried) {
        unauthorizedRetried = true;
        if (await onUnauthorized(response, false)) continue;
      }
      if (RETRYABLE_STATUS.has(response.status) && idempotent && attempt < maxAttempts) { await sleep(800 * attempt, signal); continue; }

      if (raw) return response;
      const text = await response.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = null; }
      if (!response.ok) {
        throw new ApiError(messageFrom(data, response.status), { status: response.status, code: data?.error?.code ?? "http_error", detail: data });
      }
      return data;
    }
  }
  return {
    get: (path, opts) => request("GET", path, opts),
    post: (path, body, opts) => request("POST", path, { ...opts, body: body ?? {} }),
    request,
  };
}
