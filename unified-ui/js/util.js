// Small DOM + HTTP helpers. Nothing here touches the DOM at import time, so agents.js can run under Node for tests.
export const $ = (id) => document.getElementById(id);

export function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v === null || v === undefined) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false || kid === "") continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}
export const fill = (node, ...kids) => { node.replaceChildren(); node.append(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false)); };

export class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export class SkipError extends Error {}  // a stage that cannot run for a known, non-fatal reason

export const sleep = (ms, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) return reject(new DOMException("Cancelled", "AbortError"));
  const onAbort = () => { clearTimeout(t); reject(new DOMException("Cancelled", "AbortError")); };
  const t = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, ms);
  signal?.addEventListener("abort", onAbort, { once: true });
});

// JSON request with a timeout. Only idempotent GETs are retried (network errors, 429, 5xx).
export async function http(url, { method = "GET", headers = {}, body, signal, timeoutMs = 60000, raw = false } = {}) {
  const attempts = method === "GET" ? 3 : 1;
  let last;
  for (let i = 0; i < attempts; i++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    const onAbort = () => ctl.abort();
    signal?.addEventListener("abort", onAbort, { once: true });
    try {
      const resp = await fetch(url, {
        method, signal: ctl.signal,
        headers: body !== undefined && !(body instanceof Blob) ? { "Content-Type": "application/json", ...headers } : headers,
        body: body === undefined || body instanceof Blob ? body : JSON.stringify(body),
      });
      if (raw) return resp;
      const text = await resp.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = { message: text.slice(0, 200) }; }
      if (resp.ok) return data;
      const msg = (data && (data.error?.message || data.message || (typeof data.error === "string" ? data.error : ""))) || `HTTP ${resp.status}`;
      last = new HttpError(resp.status, msg);
      if (!(resp.status === 429 || resp.status >= 500)) throw last;
    } catch (err) {
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      last = err instanceof HttpError ? err : new HttpError(0, err.name === "AbortError" ? "The request timed out." : `Network error: ${err.message}`);
      if (last.status && last.status < 500 && last.status !== 429) throw last;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
    if (i < attempts - 1) await sleep(1000 * (i + 1), signal);
  }
  throw last;
}

// Calls fn() every `every` ms until until(result) is true. Throws on timeout.
export async function poll(fn, { every, timeoutMs, signal, until, onTick }) {
  const started = Date.now();
  for (;;) {
    const r = await fn();
    onTick?.(r);
    if (until(r)) return r;
    if (Date.now() - started > timeoutMs) throw new Error("Timed out waiting for the agent to finish.");
    await sleep(every, signal);
  }
}

export const hex = (n) => Array.from(crypto.getRandomValues(new Uint8Array(n / 2)), (b) => b.toString(16).padStart(2, "0")).join("");
export const trunc = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
export const slug = (s) => String(s || "opportunity").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "opportunity";
export const readB64 = (file) => new Promise((resolve, reject) => {
  const r = new FileReader();
  r.onload = () => resolve(String(r.result).split(",")[1] || "");
  r.onerror = () => reject(new Error(`Could not read ${file.name}`));
  r.readAsDataURL(file);
});
