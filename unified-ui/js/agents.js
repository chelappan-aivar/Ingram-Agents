// One function per agent backend. Each is standalone: it takes plain inputs and returns the agent's output.
// All of them accept {signal, onProgress} so the pipeline can cancel and display progress.
import { http, poll, sleep, SkipError, HttpError, hex, readB64, slug } from "./util.js";

const cfg = () => globalThis.UNIFIED_CONFIG;
const DONE_INTAKE = ["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"];

/* ---------------------------------------------------------------- Intake */
let intakeSession = null;
async function intakeAuth(signal, renew = false) {
  if (!intakeSession || renew) intakeSession = (await http(`${cfg().intake}/session`, { method: "POST", signal })).session;
  return { Authorization: `Bearer ${intakeSession}` };
}
const intakeCall = async (method, path, body, signal) => {
  try { return await http(`${cfg().intake}${path}`, { method, body, signal, headers: await intakeAuth(signal) }); }
  catch (err) {
    if (err.status !== 401) throw err;
    return http(`${cfg().intake}${path}`, { method, body, signal, headers: await intakeAuth(signal, true) });
  }
};
export const resetIntakeSession = () => { intakeSession = null; };

// Microsoft sign-in (device code) for Outlook. Dynamics 365 is read server-side by the Intake API.
export const msStatus = (signal) => intakeCall("GET", "/session", undefined, signal);
export const msConnectStart = (signal) => intakeCall("POST", "/auth/device/start", {}, signal);
export const msConnectPoll = (loginId, signal) => intakeCall("POST", "/auth/device/poll", { login_id: loginId }, signal);

export async function runIntake({ notesText = "", files = [], outlookDays = null, dynamicsLimit = null }, { signal, onProgress } = {}) {
  const body = { outlook: outlookDays ? { days: outlookDays } : null, crm_files: [], notes_files: [] };
  if (dynamicsLimit) body.dynamics = { limit: dynamicsLimit };
  if (notesText.trim()) body.notes_text = notesText.trim();
  for (const f of files) {
    const ext = f.name.split(".").pop().toLowerCase();
    const entry = { name: f.name, content_base64: await readB64(f) };
    if (ext === "csv" || ext === "xlsx") body.crm_files.push(entry);
    else if (ext === "pdf" || ext === "docx") body.notes_files.push(entry);
    else throw new Error(`Intake accepts .csv, .xlsx, .pdf and .docx files (got ${f.name}).`);
  }
  if (!body.outlook && !body.dynamics && !body.notes_text && !body.crm_files.length && !body.notes_files.length) throw new Error("Pick at least one source: Outlook, Dynamics 365, notes or a file.");
  const { run_id } = await intakeCall("POST", "/runs", body, signal);
  const run = await poll(() => intakeCall("GET", `/runs/${run_id}`, undefined, signal), {
    every: 4000, timeoutMs: 30 * 60000, signal, until: (r) => DONE_INTAKE.includes(r.status), onTick: onProgress,
  });
  if (run.status === "FAILED") throw new Error(run.error || "The Intake agent failed.");
  return run;
}

/* ----------------------------------------------------------- Live Assist */
export async function runLiveAssist({ key, title, description }, { signal, onProgress } = {}) {
  if (!key) throw new SkipError("No Live Assist access key entered; this stage was skipped.");
  const headers = { "X-Access-Key": key, "X-Console": "1" };
  const base = cfg().liveAssist;
  const call = await http(`${base}/calls`, { method: "POST", headers, signal, body: { title, description, source: { type: "manual" } } });
  try {
    const view = await poll(() => http(`${base}/calls/${call.session_id}`, { headers, signal }), {
      every: 2000, timeoutMs: 120000, signal, onTick: onProgress,
      until: (v) => ["ok", "error"].includes(v.prequestions?.status),
    });
    if (view.prequestions.status === "error") throw new Error("Live Assist could not generate pre-call questions.");
    return { session_id: call.session_id, questions: view.prequestions.questions || [], generated_at: view.prequestions.generated_at };
  } finally {
    // Leave no call open in the live system; the prep call is finished once questions exist.
    http(`${base}/calls/${call.session_id}/end`, { method: "POST", headers, body: { reason: "unified UI pre-call prep finished" } }).catch(() => {});
  }
}

// Calls Live Assist already holds for this customer (the Discovery API lists the linked Intake opportunities and their call counts).
export async function findLiveAssistCalls(customer, { signal } = {}) {
  const norm = (x) => String(x || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const c = norm(customer);
  const { opportunities = [] } = await http(`${cfg().discovery}/pipeline/opportunities`, { signal });
  const exact = opportunities.filter((o) => norm(o.customer) === c || norm(o.name) === c);
  const near = opportunities.filter((o) => c && norm(o.customer) && (norm(o.customer).includes(c) || c.includes(norm(o.customer))));
  const hits = exact.length ? exact : near;
  return hits.sort((a, b) => (b.call_count || 0) - (a.call_count || 0))[0] || null;
}

/* ------------------------------------------------------------- Discovery */
export async function runDiscovery({ engagement, documents, instructions = "", pipelineOpp = null }, { signal, onProgress } = {}) {
  const base = cfg().discovery;
  let name = null, imported = null;
  if (pipelineOpp) {   // pulls the Intake record plus call transcripts and minutes out of Live Assist into the engagement
    try {
      imported = await http(`${base}/pipeline/opportunities/${encodeURIComponent(pipelineOpp)}/import`, { method: "POST", body: {}, signal, timeoutMs: 120000 });
      name = imported.engagement;
    } catch (e) { if (![404, 409].includes(e.status)) throw e; }
  }
  name ||= `${slug(engagement)}-${new Date().toISOString().slice(0, 16).replace(/\D/g, "")}`;   // fresh S3 prefix per run
  const files = documents.map((d) => (d instanceof Blob && d.name ? d : new File([d.text], d.name, { type: "text/markdown" })));
  if (!files.length && !imported) throw new Error("Discovery needs at least one document.");
  if (files.length) {
    const up = await http(`${base}/engagements/${encodeURIComponent(name)}/upload-urls`, { method: "POST", signal, body: { files: files.map((f) => ({ name: f.name, size: f.size })) } });
    for (const f of files) {
      const put = await http(up.urls[f.name], { method: "PUT", headers: up.required_headers || {}, body: f, signal, raw: true, timeoutMs: 120000 });
      if (!put.ok) throw new Error(`Uploading ${f.name} failed (${put.status}).`);
    }
  }
  const { run_id } = await http(`${base}/runs`, { method: "POST", signal, body: { engagement: name, clarify: false, narrative: true,
    ...(instructions ? { instructions: instructions.slice(0, 4000) } : {}),
    ...(imported ? { pipeline_opportunity_id: pipelineOpp, pipeline_call_ids: imported.call_ids } : {}) } });
  const q = `?engagement=${encodeURIComponent(name)}`;
  const status = await poll(() => http(`${base}/runs/${run_id}${q}`, { signal }), {
    every: 5000, timeoutMs: 45 * 60000, signal, onTick: onProgress, until: (st) => ["succeeded", "failed"].includes(st.state),
  });
  if (status.state === "failed") throw new Error(status.error || "The Discovery agent failed.");
  const out = await http(`${base}/runs/${run_id}/report${q}`, { signal });
  return { run_id, engagement: name, report: out.report, narrative: out.narrative, context: out.context, linkedCalls: imported?.call_ids?.length || 0 };
}

/* ----------------------------------------------------- SOW (standalone) */
export async function runSowDraft({ customer, templateId, details, upstream = null, opportunityId = null, images = [] }, { signal, onProgress } = {}) {
  const base = cfg().sow;
  const userId = `web-${hex(24)}`;
  const h = { "X-User-Id": userId };
  const { session_id } = await http(`${base}/sessions`, { method: "POST", headers: h, body: {}, signal });
  const resp = await http(`${base}/generations/stream`, {
    method: "POST", headers: h, signal, raw: true, timeoutMs: 15 * 60000,
    body: { session_id, customer_name: customer, opportunity_id: opportunityId, template_id: templateId, project_details: details,
      transcript_files: [], architecture_image_b64s: images.map((i) => i.content_b64), arch_image_filenames: images.map((i) => i.filename), upstream },
  });
  if (!resp.ok) throw new HttpError(resp.status, resp.status === 429 ? "The SOW service is busy; try again shortly." : `SOW draft failed (HTTP ${resp.status}).`);
  const parts = [];
  let err = null, finished = false;
  const reader = resp.body.getReader(), dec = new TextDecoder();
  let buf = "";
  const flush = (block) => {
    const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
    if (!data) return;
    let evt; try { evt = JSON.parse(data); } catch { return; }
    if (evt.type === "chunk") { parts.push(String(evt.text || "")); onProgress?.(parts.join("")); }
    else if (evt.type === "done") finished = true;
    else if (evt.type === "error") { err = String(evt.message || "The draft failed."); finished = true; }
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true }).replace(/\r\n?/g, "\n");
    let at; while ((at = buf.indexOf("\n\n")) >= 0) { flush(buf.slice(0, at)); buf = buf.slice(at + 2); }
  }
  if (buf.trim()) flush(buf);
  if (err) throw new Error(err);
  const markdown = parts.join("").trim();
  if (!finished || !markdown) throw new Error("The SOW stream ended before a draft was produced.");
  return { markdown, session_id, userId };
}

export const runSowCheck = ({ markdown, upstream = null, verify = false }, { signal } = {}) =>
  http(`${cfg().sow}/checks`, { method: "POST", signal, timeoutMs: 5 * 60000, headers: { "X-User-Id": `web-${hex(24)}` }, body: { sow_markdown: markdown, verify, upstream } });

export const exportSowDocx = ({ markdown, customer, session, images = [] }, { signal } = {}) =>
  http(`${cfg().sow}/exports/docx`, { method: "POST", signal, headers: { "X-User-Id": session.userId }, timeoutMs: 120000,
    body: { session_id: session.session_id, customer_name: customer, opportunity_id: null, sow_markdown: markdown, persist_output: true, architecture_images: images } });

/* -------------------------------------------------------- Architecture */
export async function runArchitecture({ title, srs }, { signal, onProgress } = {}) {
  const base = cfg().architecture;
  const { run_id } = await http(`${base}/runs`, { method: "POST", signal, body: { title, srs_text: srs } });
  const doc = await poll(() => http(`${base}/runs/${run_id}`, { signal }), {
    every: 4000, timeoutMs: 20 * 60000, signal, onTick: onProgress, until: (d) => !["queued", "running"].includes(d.run?.status),
  });
  if (doc.run.status === "failed") throw new Error("The Architecture agent failed.");
  const version = doc.run.latest_complete_version || doc.versions?.at(-1)?.version || 1;
  const file = async (name) => http(`${base}/runs/${run_id}/files/${name}?version=${version}`, { signal }).catch(() => null);
  const [adr, diagram] = await Promise.all([file("adr.md"), file("diagram.png")]);
  return { run_id, version, status: doc.run.status, adr: adr?.data || "", diagramB64: diagram?.encoding === "base64" ? diagram.data : null };
}

/* ----------------------------------------------------------------- Cost */
export async function runCost({ requirements }, { signal, onProgress } = {}) {
  const url = cfg().cost, sid = `web-${hex(24)}`;
  await http(url, { method: "POST", signal, body: { user_input: requirements, session_id: sid } });
  const res = await poll(() => http(`${url}?session_id=${sid}`, { signal }).catch((e) => (e.status === 404 ? { status: "processing" } : Promise.reject(e))), {
    every: 5000, timeoutMs: 20 * 60000, signal, onTick: onProgress, until: (r) => r.status !== "processing",
  });
  if (res.status !== "success") throw new Error(res.error || res.data?.message || "The Cost agent failed.");
  const d = res.data?.data || res.data || {};
  const link = res.data?.calculator_url || d.calculator_url || "";
  return { environments: d.environments || {}, unsupported: d.unsupported_services || [], calculatorUrl: String(link).startsWith("https://calculator.aws/") ? link : "" };
}

export { sleep };
