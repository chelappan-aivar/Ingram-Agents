/** Cost Estimate agent client: requirements text in; AWS Pricing Calculator estimate out. */
import { ENDPOINTS, POLLING } from "../config.js";
import { ApiError, createClient } from "../core/http.js";
import { pollUntil } from "../core/poll.js";

const DONE = new Set(["completed", "failed"]);

export function createCostAgent() {
  const http = createClient({ baseUrl: ENDPOINTS.cost, label: "The Cost agent" });
  return {
    quota: (signal) => http.get("/session", { signal }),

    async run({ title, requirements }, { signal, onUpdate } = {}) {
      const { run_id: runId } = await http.post("/runs", { requirements, title: String(title ?? "").slice(0, 200) }, { signal, timeoutMs: 45_000 });
      const { run, result } = await pollUntil(() => http.get(`/runs/${encodeURIComponent(runId)}`, { signal }), {
        isDone: ({ run: r }) => DONE.has(r?.status),
        ...POLLING.cost,
        signal,
        onTick: ({ run: r }) => onUpdate?.(r),
        what: "The Cost agent",
      });
      if (run.status === "failed" || !result) throw new ApiError(run.error || "The Cost agent failed.", { code: "cost_failed" });
      if (!result.calculator_url) throw new ApiError("The Cost agent returned no calculator link.", { code: "no_calculator_url" });
      return { runId, ...result };
    },
  };
}
