import { AbortedError, ApiError } from "./http.js";

const sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new AbortedError());
    const id = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => { clearTimeout(id); reject(new AbortedError()); }, { once: true });
  });

/**
 * Calls `fetchOnce` until `isDone(result)` is true. Transient read errors are tolerated up to
 * `maxConsecutiveErrors`; the overall wait is bounded by `timeoutMs`.
 */
export async function pollUntil(fetchOnce, { isDone, intervalMs, timeoutMs, signal, onTick, what = "The agent", maxConsecutiveErrors = 5 }) {
  const started = Date.now();
  let errors = 0;
  for (;;) {
    try {
      const result = await fetchOnce();
      errors = 0;
      onTick?.(result, Date.now() - started);
      if (isDone(result)) return result;
    } catch (err) {
      if (err instanceof AbortedError) throw err;
      if (err instanceof ApiError && [404, 400, 403].includes(err.status)) throw err;
      if (++errors >= maxConsecutiveErrors) throw err;
    }
    if (Date.now() - started > timeoutMs) {
      throw new ApiError(`${what} did not finish within ${Math.round(timeoutMs / 60000)} minutes.`, { code: "timeout" });
    }
    await sleep(intervalMs, signal);
  }
}
