/** Architecture agent client: SOW/SRS text in; design, usage, ADR and diagram out. */
import { ENDPOINTS, POLLING } from "../config.js";
import { ApiError, createClient } from "../core/http.js";
import { pollUntil } from "../core/poll.js";

const DONE = new Set(["completed", "partial", "failed"]);

export function createArchitectureAgent() {
  const http = createClient({ baseUrl: ENDPOINTS.architecture, label: "The Architecture agent" });

  async function file(runId, name, signal) {
    try {
      // Text files arrive as utf-8 strings; the diagram arrives base64-encoded. Either way `data` is a string.
      return (await http.get(`/runs/${encodeURIComponent(runId)}/files/${name}`, { signal })).data;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  }
  const json = (text) => { try { return text ? JSON.parse(text) : null; } catch { return null; } };

  return {
    quota: (signal) => http.get("/session", { signal }),

    async run({ title, srsText }, { signal, onUpdate } = {}) {
      const { run_id: runId } = await http.post("/runs", { title: String(title ?? "").slice(0, 200), srs_text: srsText }, { signal, timeoutMs: 60_000 });
      const { run } = await pollUntil(() => http.get(`/runs/${encodeURIComponent(runId)}`, { signal }), {
        isDone: ({ run: r }) => DONE.has(r?.status),
        ...POLLING.architecture,
        signal,
        onTick: ({ run: r }) => onUpdate?.(r),
        what: "The Architecture agent",
      });
      if (run.status === "failed") throw new ApiError(run.error || "The Architecture agent failed.", { code: "architecture_failed" });
      const [design, usage, adr, usageAssumptions, diagram] = await Promise.all([
        file(runId, "design.json", signal), file(runId, "usage.json", signal), file(runId, "adr.md", signal),
        file(runId, "usage_assumptions.md", signal), file(runId, "diagram.png", signal),
      ]);
      const parsedDesign = json(design);
      if (!parsedDesign) throw new ApiError("The Architecture agent finished without a design.", { code: "no_design" });
      return { runId, status: run.status, design: parsedDesign, usage: json(usage), adr, usageAssumptions, diagramBase64: diagram };
    },
  };
}
