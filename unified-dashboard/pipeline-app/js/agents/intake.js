/** Intake agent client. The anonymous session lives in memory only and is dropped by reset(). */
import { ENDPOINTS, POLLING } from "../config.js";
import { toBase64 } from "../core/files.js";
import { AbortedError, createClient } from "../core/http.js";
import { pollUntil } from "../core/poll.js";

export const INTAKE_DONE = new Set(["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"]);

export function createIntakeAgent() {
  const http = createClient({ baseUrl: ENDPOINTS.intake, label: "The Intake agent" });
  let token = null;

  async function auth(signal) {
    if (!token) token = (await http.post("/session", {}, { signal })).session;
    return { Authorization: `Bearer ${token}` };
  }

  return {
    /** Signed-in Outlook user, or null. Outlook is connected with a Microsoft device-code sign-in. */
    async outlookUser(signal) {
      const res = await http.get("/session", { headers: await auth(signal), signal });
      return res.microsoft ?? null;
    },

    /** Starts the device-code sign-in; `onCode({ user_code, verification_uri })` shows the code, resolves with the user. */
    async connectOutlook({ signal, onCode }) {
      const headers = await auth(signal);
      const login = await http.post("/auth/device/start", {}, { headers, signal });
      onCode(login);
      const deadline = Date.now() + login.expires_in * 1000;
      let wait = Math.max(login.interval, 3) * 1000;
      while (Date.now() < deadline) {
        await new Promise((resolve, reject) => {
          const id = setTimeout(resolve, wait);
          signal?.addEventListener("abort", () => { clearTimeout(id); reject(new AbortedError()); }, { once: true });
        });
        const res = await http.post("/auth/device/poll", { login_id: login.login_id }, { headers, signal });
        if (res.status === "done") return res.user;
        if (res.slow_down) wait += 5000;
      }
      throw new Error("The Microsoft sign-in code expired. Start again.");
    },

    async disconnectOutlook(signal) {
      await http.post("/auth/disconnect", {}, { headers: await auth(signal), signal });
    },

    /** Starts a run from any mix of Outlook, open Dynamics 365 opportunities, pasted notes and CRM/notes files. */
    async start({ notesText = "", crmFiles = [], notesFiles = [], outlookDays = null, dynamicsLimit = null }, signal) {
      const encode = (files) => Promise.all(files.map(async (f) => ({ name: f.name, content_base64: await toBase64(f) })));
      const body = {
        outlook: outlookDays ? { days: outlookDays } : null,
        dynamics: dynamicsLimit ? { limit: dynamicsLimit } : null,
        crm_files: await encode(crmFiles), notes_files: await encode(notesFiles), notes_text: notesText.trim(),
      };
      const res = await http.post("/runs", body, { headers: await auth(signal), signal, timeoutMs: 60_000 });
      return res.run_id;
    },

    async fetchRun(runId, signal) {
      return http.get(`/runs/${encodeURIComponent(runId)}`, { headers: await auth(signal), signal });
    },

    waitForRun(runId, { signal, onUpdate } = {}) {
      return pollUntil(() => this.fetchRun(runId, signal), {
        isDone: (run) => INTAKE_DONE.has(run.status),
        ...POLLING.intake,
        signal,
        onTick: onUpdate,
        what: "The Intake agent",
      });
    },

    reset() {
      token = null;
    },
  };
}
