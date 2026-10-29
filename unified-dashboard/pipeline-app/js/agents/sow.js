/** SOW Generator client (standalone SAM API): templates, generate, Well-Architected review, final check, docx export. */
import { ENDPOINTS, POLLING } from "../config.js";
import { ApiError, createClient } from "../core/http.js";
import { pollUntil } from "../core/poll.js";

const ACTIVE = new Set(["queued", "running"]);

export function createSowAgent() {
  const http = createClient({ baseUrl: ENDPOINTS.sow, label: "The SOW agent" });

  const waitJob = (jobId, { signal, onUpdate } = {}) =>
    pollUntil(() => http.get(`/jobs/${encodeURIComponent(jobId)}`, { signal }), {
      isDone: ({ job }) => !ACTIVE.has(job?.status),
      ...POLLING.sow,
      signal,
      onTick: ({ job }) => onUpdate?.(job),
      what: "The SOW agent",
    }).then(({ job }) => {
      if (job.status !== "completed") throw new ApiError(job.error || "The SOW agent job failed.", { code: "sow_failed" });
      return job;
    });

  return {
    quota: (signal) => http.get("/session", { signal }),
    async templates(signal) {
      return (await http.get("/templates", { signal })).templates ?? [];
    },

    /** Generates a SOW draft. `upstream` carries every earlier agent's output. */
    async generate({ customerName, templateId, opportunityId, projectDetails, upstream }, { signal, onUpdate } = {}) {
      const body = { customer_name: customerName, template_id: templateId, upstream: upstream ?? undefined };
      if (opportunityId) body.opportunity_id = String(opportunityId).slice(0, 100);
      if (projectDetails) body.project_details = projectDetails;
      const { job_id: jobId } = await http.post("/generations", body, { signal, timeoutMs: 90_000 });
      const job = await waitJob(jobId, { signal, onUpdate });
      if (!String(job.markdown ?? "").trim()) throw new ApiError("The SOW agent returned an empty draft.", { code: "empty_draft" });
      return { jobId, markdown: job.markdown };
    },

    /** Deterministic Well-Architected review; `verify` adds one model pass. Synchronous. */
    async waReview({ markdown, upstream, templateId }, { signal } = {}) {
      const body = { sow_markdown: markdown, upstream: upstream ?? undefined, verify: false };
      if (templateId) body.template_id = templateId;
      return (await http.post("/wa-reviews", body, { signal, timeoutMs: 28_000 })).review ?? {};
    },

    /** Rule check of the final SOW against the full upstream (architecture + cost now exist). */
    async check({ markdown, upstream }, { signal, onUpdate } = {}) {
      const { job_id: jobId } = await http.post("/checks", { sow_markdown: markdown, upstream: upstream ?? undefined, verify: false }, { signal });
      const job = await waitJob(jobId, { signal, onUpdate });
      return job.check ?? {};
    },

    async exportDocx({ customerName, markdown, opportunityId, diagramBase64 }, { signal } = {}) {
      const body = { customer_name: customerName, sow_markdown: markdown, doc_title: `${customerName} Statement of Work`.slice(0, 200) };
      if (opportunityId) body.opportunity_id = String(opportunityId).slice(0, 100);
      if (diagramBase64) body.architecture_images = [{ filename: "architecture.png", content_b64: diagramBase64 }];
      return http.post("/exports/docx", body, { signal, timeoutMs: 60_000 });
    },
  };
}
