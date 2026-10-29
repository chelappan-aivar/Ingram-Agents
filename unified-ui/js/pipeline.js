// The pipeline engine. DOM-free: it reads and writes the store only.
//   full : fetch (Outlook, Dynamics 365, notes) -> Intake -> [you select the opportunity] -> Live Assist call check -> Discovery
//          -> Live Assist refresh -> SOW draft + review -> Architecture -> Cost Estimate -> combined SOW + review
//   chain: the same up to the SOW draft (the four-agent re-run from the SOW tab)
// Every stage is the standalone agent. Stages that already finished are skipped, so Resume continues where it stopped.
import * as S from "./store.js";
import * as A from "./agents.js";
import * as C from "./context.js";
import { SkipError } from "./util.js";

const HEAD = [
  { id: "intake", label: "Intake Agent", note: "Fetch from Outlook / Dynamics 365 / notes and extract opportunities" },
  { id: "select", label: "Select the opportunity", note: "You choose which opportunity goes through the rest of the pipeline" },
  { id: "la1", label: "Live Assist Agent", note: "Check Live Assist for calls on this customer (or prepare pre-call questions)" },
  { id: "discovery", label: "Discovery Agent", note: "Intake record + call transcripts / minutes → discovery report" },
  { id: "la2", label: "Live Assist Agent (refresh)", note: "Send the Discovery report back to Live Assist" },
];
export const FULL_STEPS = [...HEAD,
  { id: "sow", label: "SOW Generator Agent", note: "Draft from Intake, Live Assist and Discovery, then review" },
  { id: "arch", label: "Architecture Agent", note: "Design from the SOW draft" },
  { id: "cost", label: "Cost Estimate Agent", note: "Price the architecture" },
  { id: "combined", label: "Combined SOW", note: "Regenerate the SOW with the architecture and cost estimate" },
  { id: "final", label: "Final review + delivery", note: "Well-Architected review, rule check, export" },
];
export const CHAIN_STEPS = [...HEAD, { id: "sow", label: "SOW Generator Agent", note: "Draft from Intake, Live Assist and Discovery, then review" }];

let controller = null;
export const cancel = () => controller?.abort();

export async function runPipeline(mode) {
  if (S.get().mode === "running") return;
  controller = new AbortController();
  const { signal } = controller;
  const steps = mode === "chain" ? CHAIN_STEPS : FULL_STEPS;
  S.update((s) => { s.mode = "running"; for (const x of steps) if (!["done", "skipped"].includes(s.steps[x.id]?.status)) s.steps[x.id] = { status: "pending", detail: "" }; });
  const finished = (id) => ["done", "skipped"].includes(S.get().steps[id]?.status);
  const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
  const opp = () => S.get().selected || C.pickOpportunity(S.get().opps);
  const g = () => S.get();
  S.log(`Run started (${mode === "chain" ? "four-agent re-run" : "full pipeline"}).`, "info", "Pipeline");
  const stage = async (id, label, fn) => {
    if (finished(id)) return;
    const t0 = Date.now(), took = () => { const sec = Math.round((Date.now() - t0) / 1000); return sec >= 60 ? `${Math.floor(sec / 60)}m ${sec % 60}s` : `${sec}s`; };
    S.setStep(id, "running", ""); S.log("Started.", "info", label);
    try { const detail = await fn(); S.setStep(id, "done", detail || ""); S.log(`Completed in ${took()}.${detail ? ` ${cap(detail)}.` : ""}`, "info", label); }
    catch (e) {
      if (e.name === "AbortError") throw e;
      if (e instanceof SkipError) { e.stage = label; throw e; }
      S.setStep(id, "failed", e.message); e.stage = label; throw e;
    }
  };

  try {
    // 1 · Intake: Outlook + Dynamics 365 + notes ----------------------------------------------
    await stage("intake", "Intake", async () => {
      const i = g().inputs;
      const run = await A.runIntake({ notesText: i.notesText, files: i.files, outlookDays: i.outlookDays, dynamicsLimit: i.dynamicsLimit }, {
        signal, onProgress: (r) => S.update((s) => { s.opps = r.opportunities || []; s.steps.intake.detail = r.phase || r.status; }),
      });
      const real = (run.opportunities || []).filter((o) => o.is_opportunity !== false);
      if (!real.length) throw new Error("Intake found no opportunity in the fetched sources. Widen the Outlook window, raise the Dynamics limit or add notes, then run again.");
      S.update((s) => { s.opps = run.opportunities; s.intakeRun = run; s.selected = C.pickOpportunity(run.opportunities) || real[0]; });
      return `${real.length} opportunit${real.length === 1 ? "y" : "ies"} found${run.outlook_error ? ` (Outlook: ${run.outlook_error})` : ""}`;
    });

    // 2 · You select the opportunity ----------------------------------------------------------
    if (!g().confirmed) {
      S.setStep("select", "blocked", "Select an opportunity in the table, then continue");
      S.update((s) => { s.mode = "select"; });
      S.log("Awaiting selection. Choose an opportunity in the table to continue.", "warn", "Selection");
      return;
    }
    if (!finished("select")) S.setStep("select", "done", C.customerOf(opp()));

    // 3 · Live Assist: is there a call for this customer? -------------------------------------
    await stage("la1", "Live Assist", async () => {
      const customer = C.customerOf(opp());
      const match = await A.findLiveAssistCalls(customer, { signal }).catch(() => null);
      if (match?.call_count > 0) { S.update((s) => { s.laMatch = match; }); return `${match.call_count} call(s) found; their transcripts and minutes feed Discovery`; }
      if (!g().inputs.laKey) throw new SkipError("No Live Assist call found for this customer and no access key for pre-call questions");
      const res = await A.runLiveAssist({ key: g().inputs.laKey, title: `${customer}: pre-call prep`, description: C.liveAssistDescription(opp(), null) }, { signal });
      S.update((s) => { s.la1 = res; });
      return `no call yet; ${res.questions.length} pre-call questions generated`;
    }).catch((e) => skipOrThrow(e, "la1"));

    // 4 · Discovery ---------------------------------------------------------------------------
    await stage("discovery", "Discovery", async () => {
      const o = opp();
      const docs = [{ name: "00_hub_intake_record.md", text: C.intakeMarkdown(o) }];
      if (g().la1?.questions?.length) docs.push({ name: "01_hub_live_assist_prep.md", text: "# Live Assist pre-call questions\n\n" + g().la1.questions.map((q) => `- ${q.question}${q.why ? ` (${q.why})` : ""}`).join("\n") });
      const res = await A.runDiscovery({ engagement: C.customerOf(o), documents: docs, pipelineOpp: g().laMatch?.intake_opportunity_id || null },
        { signal, onProgress: (r) => S.setStep("discovery", "running", r.state + (r.completed_steps?.length ? ` · ${r.completed_steps.length} steps` : "")) });
      S.update((s) => { s.discovery = res; });
      return `${(res.report?.use_cases || []).length} use cases${res.linkedCalls ? `, ${res.linkedCalls} Live Assist call(s) included` : ""}`;
    });

    // 5 · Live Assist refresh with the Discovery report ----------------------------------------
    await stage("la2", "Live Assist refresh", async () => {
      if (!g().inputs.laKey) throw new SkipError("No Live Assist access key; skipped");
      const res = await A.runLiveAssist({ key: g().inputs.laKey, title: `${C.customerOf(opp())}: discovery refresh`, description: C.liveAssistDescription(opp(), g().discovery) }, { signal });
      S.update((s) => { s.la2 = res; });
      return `${res.questions.length} questions`;
    }).catch((e) => skipOrThrow(e, "la2"));

    // 6 · SOW draft + review ------------------------------------------------------------------
    const tpl = g().inputs.templateId, name = () => C.customerOf(opp());
    const draft = async (upstream, details, onStatus, review = true, images = []) => {
      const session = await A.runSowDraft({ customer: name(), templateId: tpl, details, upstream, opportunityId: upstream.meta.opportunity_id, images },
        { signal, onProgress: (md) => S.update((s) => { s.sow = { ...(s.sow || {}), markdown: md, streaming: true }; }) });
      if (!review) return { markdown: session.markdown, check: null, session, customer: name(), upstream, images };
      onStatus("Reviewing the draft");
      const check = await A.runSowCheck({ markdown: session.markdown, upstream, verify: true }, { signal });
      return { markdown: session.markdown, check, session, customer: name() };
    };
    const reviewText = (c) => `${c.summary?.passed ?? "?"}/${c.summary?.total ?? "?"} checks passed${(c.issues || []).length ? `, ${c.issues.length} finding(s)` : ""}`;
    const baseUp = () => C.upstreamFor(opp(), tpl, { discovery: g().discovery, la: g().la2 || g().la1 });

    await stage("sow", "SOW Generator", async () => {
      const sow = await draft(baseUp(), C.projectDetails(opp(), g().discovery), (t) => S.setStep("sow", "running", t));
      S.update((s) => { s.sow = sow; s.sow1 = sow; });
      return reviewText(sow.check);
    });
    if (mode === "chain") return finish();
    if (g().inputs.pause && !g().reviewed) {
      S.update((s) => { s.mode = "gate"; });
      S.log("Awaiting approval. Review the SOW draft, then approve to continue to Architecture.", "warn", "SOW Generator");
      return;
    }

    // 7 · Architecture ------------------------------------------------------------------------
    await stage("arch", "Architecture", async () => {
      const res = await A.runArchitecture({ title: name(), srs: g().sow1.markdown.slice(0, 190000) }, { signal, onProgress: (d) => S.setStep("arch", "running", d.run?.status || "running") });
      S.update((s) => { s.arch = res; });
      return res.status === "partial" ? "ready with gaps" : "design ready";
    });

    // 8 · Cost estimate -----------------------------------------------------------------------
    await stage("cost", "Cost Estimate", async () => {
      const res = await A.runCost({ requirements: C.costRequirements(opp(), g().arch) }, { signal, onProgress: () => S.setStep("cost", "running", "estimating") });
      S.update((s) => { s.cost = res; });
      return res.calculatorUrl ? "estimate ready" : "estimate ready (no calculator link returned)";
    });

    // 9 · Combined SOW + final review ---------------------------------------------------------
    await stage("combined", "Combined SOW", async () => {
      const up = C.upstreamFor(opp(), tpl, { discovery: g().discovery, la: g().la2 || g().la1, arch: g().arch, cost: g().cost });
      // The diagram goes to the SOW agent (so it can place it) and to the .docx export; images over Bedrock's 3.75 MB limit skip the model.
      const png = g().arch?.diagramB64;
      const images = png ? [{ filename: "architecture-diagram.png", content_b64: `data:image/png;base64,${png}` }] : [];
      const forModel = images.filter((i) => i.content_b64.length * 0.75 < 3.6 * 1024 * 1024);
      const sow = await draft(up, C.projectDetails(opp(), g().discovery, g().arch), () => {}, false, forModel);
      sow.images = images;
      const before = sow.markdown;
      sow.markdown = C.finalizeSow(sow.markdown, { calcUrl: g().cost?.calculatorUrl, hasDiagram: images.length > 0 });
      S.update((s) => { s.sow = sow; });
      const added = [sow.markdown !== before && images.length && !/\]\(arch:\d+\)/.test(before) ? "diagram" : "", sow.markdown !== before && g().cost?.calculatorUrl && !before.includes(g().cost.calculatorUrl) ? "calculator link" : ""].filter(Boolean);
      return `regenerated with architecture and cost${added.length ? `; added ${added.join(" and ")}` : ""}`;
    });

    // 10 · Final review (Well-Architected AI review + rule check); the .docx export is then ready -----
    await stage("final", "Final review", async () => {
      const cur = g().sow;
      const check = await A.runSowCheck({ markdown: cur.markdown, upstream: cur.upstream || null, verify: true }, { signal });
      S.update((s) => { s.sow = { ...s.sow, check }; });
      return `${reviewText(check)}; ready to export`;
    });
    finish();
  } catch (e) {
    const cancelled = e.name === "AbortError";
    const failing = Object.entries(g().steps).find(([, v]) => v.status === "running");
    if (failing) S.setStep(failing[0], cancelled ? "pending" : "failed", cancelled ? "Cancelled" : e.message);
    S.log(cancelled ? "Run cancelled by user. Requests already submitted to an agent will complete on their own." : `Failed. ${e.message}`, cancelled ? "warn" : "error", cancelled ? "Pipeline" : (e.stage || "Pipeline"));
    S.update((s) => { s.mode = "idle"; });
  }
}

// Live Assist stages are optional: a known skip is recorded and the run carries on.
function skipOrThrow(e, id) {
  if (e instanceof SkipError) { S.setStep(id, "skipped", e.message); S.log(`Skipped. ${e.message}.`, "warn", e.stage || "Live Assist"); return; }
  throw e;
}
function finish() { S.update((s) => { s.mode = "finished"; }); S.log("Pipeline completed. All stages finished.", "info", "Pipeline"); }

// The two human gates. "select": the opportunity has been chosen. "gate": the SOW draft is approved.
export function approve(mode) {
  const kind = S.get().mode;
  S.update((s) => { if (kind === "select") s.confirmed = true; else s.reviewed = true; s.mode = "idle"; });
  return runPipeline(mode);
}
