/** Discovery agent client: presigned upload -> start run -> poll -> report. */
import { ENDPOINTS, POLLING } from "../config.js";
import { ApiError, createClient } from "../core/http.js";
import { pollUntil } from "../core/poll.js";

const TERMINAL = new Set(["succeeded", "failed", "awaiting_clarification"]);
const encoder = new TextEncoder();

export function createDiscoveryAgent() {
  const http = createClient({ baseUrl: ENDPOINTS.discovery, label: "The Discovery agent" });

  async function upload(engagement, documents, signal) {
    const files = documents.map((d) => ({ name: d.name, size: encoder.encode(d.content).length }));
    const grant = await http.post(`/engagements/${encodeURIComponent(engagement)}/upload-urls`, { files }, { signal });
    for (const doc of documents) {
      const url = grant.urls?.[doc.name];
      if (!url) throw new ApiError(`Discovery did not issue an upload URL for ${doc.name}.`, { code: "upload_url" });
      // The signed headers must be sent exactly; adding others breaks the signature.
      const res = await fetch(url, { method: "PUT", headers: grant.required_headers ?? {}, body: new Blob([doc.content]), signal });
      if (!res.ok) throw new ApiError(`Uploading ${doc.name} failed (${res.status}).`, { status: res.status, code: "upload_failed" });
    }
  }

  return {
    quota: (signal) => http.get("/session", { signal }),

    /** Runs Discovery end to end. `clarify` stays off in a pipeline: a pause for answers would stall it. */
    async run({ engagement, documents, instructions = "", narrative = true }, { signal, onUpdate } = {}) {
      if (!documents.length) throw new ApiError("Discovery needs at least one document.", { code: "no_input" });
      await upload(engagement, documents, signal);
      const started = await http.post("/runs", { engagement, instructions: instructions.slice(0, 4000), clarify: false, narrative }, { signal });
      const runId = started.run_id;
      const status = await pollUntil(
        () => http.get(`/runs/${encodeURIComponent(runId)}`, { query: { engagement }, signal }),
        { isDone: (s) => TERMINAL.has(s.state), ...POLLING.discovery, signal, onTick: onUpdate, what: "The Discovery agent" },
      );
      if (status.state !== "succeeded") {
        throw new ApiError(status.error || `Discovery ended as "${status.state}".`, { code: "discovery_failed" });
      }
      const body = await http.get(`/runs/${encodeURIComponent(runId)}/report`, { query: { engagement }, signal });
      return { runId, engagement, report: body.report, narrative: body.narrative, status };
    },
  };
}
