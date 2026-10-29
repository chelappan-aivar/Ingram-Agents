// Wires the tabs, the Pipeline Run panel and the SOW tab's opportunities bar to the shared store.
// The agent tabs are unmodified copies of the deployed UIs, loaded in same-origin frames on first use.
import { $, el, fill, sleep } from "./util.js";
import * as S from "./store.js";
import * as A from "./agents.js";
import * as C from "./context.js";
import * as R from "./render.js";
import * as P from "./pipeline.js";
import { fillToc } from "./docx.js";

const TABS = ["pipeline", "intake", "live", "discovery", "sow", "arch", "cost"];
let stepsDefs = P.FULL_STEPS;
let mode = "full";

// The embedded agents remember their theme per browser; start them bright (a later choice inside an agent is kept).
const THEME_KEYS = ["deskTheme", "discoveryTheme", "sowTheme", "archTheme", "costTheme", "live-assist-theme"];
try { for (const k of THEME_KEYS) if (!localStorage.getItem(k)) localStorage.setItem(k, "light"); } catch { /* storage blocked: agents fall back to their default */ }

function loadFrames(view) { view.querySelectorAll("iframe[data-src]").forEach((f) => { if (!f.getAttribute("src")) f.setAttribute("src", f.dataset.src); }); }
function show(tab) {
  for (const t of TABS) $(`v-${t}`).hidden = t !== tab;
  document.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  loadFrames($(`v-${tab}`));
}
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
const say = (text, bad = false) => { $("p-msg").textContent = text; $("p-msg").className = `status ${bad ? "bad" : ""}`; };

/* ------------------------------------------------------- Microsoft / Outlook */
async function refreshMs() {
  try {
    const r = await A.msStatus();
    S.update((s) => { s.ms = r.microsoft || null; });
  } catch { S.update((s) => { s.ms = null; }); }
}
$("p-connect").addEventListener("click", async () => {
  const box = $("p-code"), btn = $("p-connect");
  btn.disabled = true;
  try {
    const d = await A.msConnectStart();
    box.hidden = false;
    fill(box, "Open ", el("a", { href: d.verification_uri, target: "_blank", rel: "noopener noreferrer" }, d.verification_uri), " and enter the code ", el("span", { class: "code" }, d.user_code), ". This page continues by itself once you finish.");
    const deadline = Date.now() + 15 * 60000;
    for (;;) {
      await sleep((d.interval || 5) * 1000);
      const r = await A.msConnectPoll(d.login_id);
      if (r.status === "done") break;
      if (Date.now() > deadline) throw new Error("The sign-in code expired. Try again.");
    }
    box.hidden = true; await refreshMs(); say("");
  } catch (e) { box.hidden = true; say(`Outlook sign-in failed: ${e.message}`, true); }
  finally { btn.disabled = false; }
});

/* --------------------------------------------------------------- Run / gates */
function readInputs() {
  const outlook = $("p-outlook").checked, dyn = $("p-dyn").checked;
  const limit = Math.min(50, Math.max(1, parseInt($("p-limit").value, 10) || 25));
  S.update((s) => Object.assign(s.inputs, {
    notesText: $("p-notes").value, files: [...$("p-files").files], laKey: $("p-lakey").value, pause: $("p-pause").checked,
    outlookDays: outlook ? parseInt($("p-days").value, 10) : null, dynamicsLimit: dyn ? limit : null,
  }));
  const i = S.get().inputs;
  if (!i.outlookDays && !i.dynamicsLimit && !i.notesText.trim() && !i.files.length) { say("Pick at least one source: Outlook, Dynamics 365, notes or a file.", true); return false; }
  if (i.outlookDays && !S.get().ms) { say("Connect Outlook first, or untick Outlook.", true); return false; }
  say(""); return true;
}
$("p-run").addEventListener("click", async () => { if (!readInputs()) return; stepsDefs = P.FULL_STEPS; mode = "full"; await P.runPipeline("full"); });
$("p-continue").addEventListener("click", () => P.approve(mode));
$("p-resume").addEventListener("click", () => P.runPipeline(mode));
$("p-cancel").addEventListener("click", () => P.cancel());

/* ------------------------------------------------------------ SOW tab bar */
$("s-clear").addEventListener("click", () => {
  if (S.get().mode === "running") return;
  if (!confirm("Clear the opportunities and the Intake, Live Assist, Discovery and SOW outputs? Your inputs are kept.")) return;
  S.clearChain(); S.log("Opportunities and downstream outputs cleared by user. Inputs retained.", "info", "Pipeline");
  $("s-note").className = "status"; $("s-note").textContent = "Cleared. Run the four agents again when ready.";
});
$("s-chain").addEventListener("click", async () => {
  const note = (t) => { $("s-note").textContent = t; $("s-note").className = "status bad"; };
  const i = S.get().inputs;
  if (!i.outlookDays && !i.dynamicsLimit && !i.notesText.trim() && !i.files.length && !readInputs()) return note($("p-msg").textContent || "Choose sources on the Pipeline Run tab first.");
  $("s-note").textContent = ""; stepsDefs = P.CHAIN_STEPS; mode = "chain"; show("pipeline");
  await P.runPipeline("chain");
});

/* ---------------------------------------------------------------- Export */
$("p-docx").addEventListener("click", async () => {
  const sow = S.get().sow, btn = $("p-docx"), out = $("p-docx-status");
  btn.disabled = true; out.textContent = "Exporting…";
  try {
    const r = await A.exportSowDocx({ markdown: sow.markdown, customer: sow.customer || "Customer", session: sow.session, images: sow.images || [] });
    const name = r.filename || "SOW.docx";
    try {   // fill the Table of Contents (the exporter leaves it as an empty field) before offering the file
      const res = await fetch(r.download_url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { buffer, filled, entries } = await fillToc(await res.arrayBuffer());
      const href = URL.createObjectURL(new Blob([buffer], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }));
      fill(out, el("a", { href, download: name }, `Download ${name}`), el("span", { class: "hint" }, filled ? ` · table of contents filled (${entries} entries); Word may ask to update fields on open, choose Yes to add page numbers` : " · table of contents left as exported"));
    } catch {
      fill(out, el("a", { href: r.download_url, rel: "noopener noreferrer" }, `Download ${name}`), el("span", { class: "hint" }, " · in Word, right-click the Table of Contents and choose Update Field"));
    }
  } catch (e) { out.textContent = `Export failed: ${e.message}`; } finally { btn.disabled = false; }
});
$("p-export-log").addEventListener("click", () => {
  const blob = new Blob([R.logText(S.get().log)], { type: "text/plain" });
  const a = el("a", { href: URL.createObjectURL(blob), download: `pipeline-run-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.log` }); a.click(); URL.revokeObjectURL(a.href);
});

// Exit pipeline: empty the store and the form, and reload every agent frame so each returns to its empty state.
$("p-exit").addEventListener("click", () => {
  P.cancel(); S.resetAll(); A.resetIntakeSession();
  document.querySelectorAll("#v-pipeline textarea, #v-pipeline input[type=password]").forEach((n) => { n.value = ""; });
  $("p-files").value = ""; $("p-pause").checked = false; $("p-outlook").checked = true; $("p-dyn").checked = true; $("p-days").value = "7"; $("p-limit").value = "25";
  $("p-docx-status").textContent = ""; $("s-note").textContent = ""; say(""); stepsDefs = P.FULL_STEPS; mode = "full";
  document.querySelectorAll("iframe[data-src]").forEach((f) => f.removeAttribute("src"));
  show("pipeline"); refreshMs();
});

/* -------------------------------------------------------------- Rendering */
function renderLive(root, s) {
  const m = s.laMatch, la = s.la2 || s.la1;
  fill(root, m ? el("p", {}, `Live Assist holds ${m.call_count} call(s) for ${m.customer || m.name}${m.last_call_at ? ` (latest ${new Date(m.last_call_at).toLocaleString()})` : ""}. Their transcripts and minutes were sent to Discovery.`) : null,
    la ? el("div", {}, el("div", { class: "hint" }, s.la2 ? "Pre-call questions, refreshed with the Discovery report" : "Pre-call questions from the Intake output"), el("ol", {}, la.questions.map((q) => el("li", {}, q.question)))) : null);
}
function render(s) {
  const o = s.selected || C.pickOpportunity(s.opps);
  const select = (x) => { if (S.get().mode !== "running") S.update((st) => { st.selected = x; }); };
  R.renderOpps($("s-opps"), s.opps, o, select);
  R.renderOpps($("p-opps"), s.opps, o, select);
  $("p-opps-hint").textContent = s.mode === "select" ? "Click a row to choose the opportunity, then press “Continue with the selected opportunity”." : o ? `Selected: ${C.customerOf(o)}` : "";
  renderLive($("p-la"), s);
  R.renderDiscovery($("p-disc"), s.discovery);
  R.renderArch($("p-arch"), s.arch);
  R.renderCost($("p-cost"), s.cost);
  R.renderSow($("p-final"), $("p-checks"), s.sow, { diagramB64: s.arch?.diagramB64, calcUrl: s.steps.combined?.status === "done" ? s.cost?.calculatorUrl : "" });
  $("p-sow-title").textContent = s.steps.final?.status === "done" ? "Final SOW (combined with architecture and cost)" : "SOW draft";

  const running = s.mode === "running", started = Object.keys(s.steps).length > 0;
  const failed = Object.values(s.steps).some((x) => x.status === "failed");
  $("p-stage-card").hidden = !started; $("p-log-card").hidden = !s.log.length;
  $("p-opps-card").hidden = !s.opps.length; $("p-la-card").hidden = !(s.laMatch || s.la1 || s.la2);
  $("p-disc-card").hidden = !s.discovery; $("p-arch-card").hidden = !s.arch; $("p-cost-card").hidden = !s.cost; $("p-final-card").hidden = !s.sow;
  $("p-docx").hidden = !s.sow || !!s.sow.streaming;
  document.querySelectorAll(".overview li").forEach((li) => { li.className = s.steps[li.dataset.step] ? `s-${s.steps[li.dataset.step].status}` : ""; });
  R.renderSteps($("p-steps"), s.steps, stepsDefs); R.renderLog($("p-log"), s.log);
  const waiting = s.mode === "select" || s.mode === "gate";
  $("p-run").hidden = started;   // after the first run use Resume / Exit; Exit returns to a clean start
  $("p-continue").hidden = !waiting; $("p-continue").textContent = s.mode === "gate" ? "Approve the SOW draft and continue" : "Continue with the selected opportunity";
  $("p-cancel").hidden = !running; $("p-resume").hidden = !(started && s.mode === "idle");
  $("p-exit").hidden = !(started || s.opps.length || s.sow) || running;
  $("s-clear").disabled = running; $("s-chain").disabled = running;
  $("p-connect").textContent = s.ms ? "Reconnect Outlook" : "Connect Outlook";
  $("p-ms").textContent = s.ms ? `Connected as ${s.ms.name || s.ms.email || "Microsoft user"}` : "Not connected";
}
S.subscribe(render);
render(S.get());
show("pipeline");
refreshMs();
