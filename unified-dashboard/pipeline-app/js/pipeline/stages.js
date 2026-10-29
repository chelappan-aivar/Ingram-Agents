/**
 * One function per stage. Each reads the previous agents' outputs from the store, calls its agent,
 * and writes its own output back. The same function serves the full pipeline and a standalone run
 * of that agent, so there is a single code path per agent.
 */
import { validateIntakeFiles } from "../core/files.js";
import { AbortedError } from "../core/http.js";
import { briefingTranscript, discoveryDocuments, followUpTranscript } from "../domain/context.js";
import { chooseOpportunity, customerOf, opportunityId, rankOpportunities } from "../domain/opportunities.js";
import { applyCostLink, architectureBlock, buildUpstream, costBlock, costRequirements, discoveryBlock, intakeBlock, liveAssistBlock, metaBlock, reviewSummary } from "../domain/sow-inputs.js";
import { suggestTemplate } from "../domain/templates.js";
import { engagementName, truncate } from "../domain/text.js";
import { LIMITS } from "../config.js";

/** A problem the user can fix by changing their input (not a system failure). */
export class UserError extends Error {
  constructor(message) {
    super(message);
    this.name = "UserError";
  }
}

export function createStages({ store, agents, signal, detail, log, gate }) {
  const S = () => store.get();
  const selected = () => chooseOpportunity(S().intake.opportunities, S().intake.selectedKey);
  const customer = (opp) => (opp ? customerOf(opp) : "") || S().inputs.customerName.trim();
  const sources = (...names) => names.filter(Boolean);

  /** Everything the SOW agent should see, from whatever has been produced so far. */
  function upstreamNow({ withBuild = false } = {}) {
    const { inputs, liveAssist, discovery, architecture, cost } = S();
    const opp = selected();
    const intake = intakeBlock(opp, inputs.customerName);
    if (!opp && inputs.projectDetails.trim()) intake.problem_statement = truncate(inputs.projectDetails, 2000);
    const arch = withBuild && architecture.result ? architectureBlock(architecture.result.design, architecture.result.diagramBase64) : null;
    const price = withBuild && cost.result ? costBlock(cost.result) : null;
    return buildUpstream({
      meta: metaBlock({
        opportunity: opp,
        templateId: inputs.templateId,
        agents: sources(opp && "intake", liveAssist.brief && "live_assist", discovery.result && "discovery", arch && "architecture", price && "cost"),
      }),
      intake,
      liveAssist: liveAssistBlock(liveAssist.brief?.handoff, inputs.callTranscript, liveAssist.followup?.handoff),
      discovery: discoveryBlock(discovery.result?.report, discovery.result?.narrative),
      architecture: arch,
      cost: price,
    });
  }

  const requireTemplate = () => {
    if (!S().inputs.templateId) {
      const guess = suggestTemplate(S().templates, selected());
      if (!guess) throw new UserError("Choose a SOW template first.");
      store.update("inputs", { templateId: guess.id });
      log(`Template chosen from the opportunity: ${guess.label || guess.id}`);
    }
    return S().inputs.templateId;
  };
  const requireCustomer = (opp) => {
    const name = customer(opp);
    if (!name) throw new UserError("Enter the customer name for the SOW.");
    return name;
  };

  return {
    /* ---------------------------------------------------------------- 1. Intake */
    async intake(mode) {
      const { notesText, crmFiles, notesFiles, useOutlook, outlookDays, useDynamics, dynamicsLimit } = S().inputs;
      const outlookOn = useOutlook && !!S().outlook.user;
      if (useOutlook && !outlookOn) throw new UserError("Connect Outlook first, or untick it.");
      if (!notesText.trim() && !crmFiles.length && !notesFiles.length && !outlookOn && !useDynamics) throw new UserError("Choose a source for the Intake agent: connect Outlook or include Dynamics 365.");
      if (notesText.length > LIMITS.maxNotesChars) throw new UserError(`Pasted notes are limited to ${LIMITS.maxNotesChars.toLocaleString()} characters.`);
      const problems = validateIntakeFiles({ crmFiles, notesFiles });
      if (problems.length) throw new UserError(problems.join(" "));

      agents.intake.reset();
      const runId = await agents.intake.start({ notesText, crmFiles, notesFiles, outlookDays: outlookOn ? outlookDays : null, dynamicsLimit: useDynamics ? dynamicsLimit : null }, signal());
      store.update("intake", { runId });
      log(`Intake run ${runId.slice(0, 8)} started`);
      const run = await agents.intake.waitForRun(runId, {
        signal: signal(),
        onUpdate: (r) => {
          store.update("intake", { run: r, opportunities: r.opportunities ?? [] });
          detail(`${r.phase ?? r.status}: ${r.counts?.opportunities ?? 0} opportunities so far`);
        },
      });
      if (run.status === "FAILED") throw new Error(run.error || "The Intake agent failed.");
      const ranked = rankOpportunities(run.opportunities);
      if (!ranked.length) throw new UserError("Intake found no opportunities in these sources. Add more detail and run again.");
      store.update("intake", { run, opportunities: run.opportunities, selectedKey: ranked[0].thread_key });

      if (ranked.length > 1 && !S().inputs.autoSelect && mode !== "single") {
        const choice = await gate({ kind: "select", title: `${ranked.length} opportunities found`, message: "Choose the opportunity to take through the pipeline." });
        if (choice === "stop") throw new AbortedError();
      }
      const opp = selected();
      detail(`${ranked.length} opportunit${ranked.length === 1 ? "y" : "ies"} · using ${customerOf(opp)}`);
      log(`Working on "${customerOf(opp)}" (${opportunityId(opp)})`);
    },

    /* ---------------------------------------------------------------- 2. Live Assist */
    async live_assist() {
      const opp = selected();
      const text = briefingTranscript(opp, S().inputs.callTranscript);
      if (!text.trim()) throw new UserError("Live Assist needs an Intake opportunity or a call transcript to work from.");
      const result = await agents.liveAssist.runTranscript(
        { title: `${customer(opp) || "Pipeline"} – briefing`, text },
        { signal: signal(), onUpdate: (v) => detail(`${v.processing?.run_count ?? 0} agent runs · ${v.turns?.length ?? 0} turns · call ${v.status}`) },
      );
      store.update("liveAssist", { brief: result });
      detail(`${result.handoff.captured?.length ?? 0} fields captured · ${result.handoff.open_gaps?.length ?? 0} open gaps${result.partial ? " (partial)" : ""}`);
    },

    /* ---------------------------------------------------------------- 3. Discovery */
    async discovery() {
      const { liveAssist, inputs } = S();
      const opp = selected();
      const documents = discoveryDocuments({ opportunity: opp, handoff: liveAssist.brief?.handoff, callTranscript: inputs.callTranscript, extraText: inputs.discoveryText });
      if (!documents.length) throw new UserError("Discovery needs documents: run Intake and Live Assist first, or paste notes into the Discovery tab.");
      const engagement = engagementName(customer(opp) || "Pipeline");
      log(`Discovery engagement "${engagement}" with ${documents.length} document(s)`);
      const result = await agents.discovery.run(
        { engagement, documents },
        {
          signal: signal(),
          onUpdate: (s) => {
            const done = s.completed_steps?.length ?? 0;
            store.update("discovery", { progress: s });
            detail(`${s.state} · step ${done} of 7`);
          },
        },
      );
      store.update("discovery", { result });
      detail(`${result.report?.use_cases?.length ?? 0} use cases · ${result.report?.gaps_and_open_questions?.length ?? 0} open questions`);
    },

    /* ---------------------------------------------------------------- 4. Live Assist on the Discovery report */
    async live_assist_followup() {
      const d = S().discovery.result;
      if (!d) throw new UserError("Run Discovery first: Live Assist needs its report.");
      const text = followUpTranscript(d.report, d.narrative);
      if (!text.trim()) throw new UserError("The Discovery report was empty, so there is nothing to send to Live Assist.");
      const result = await agents.liveAssist.runTranscript(
        { title: `${customer(selected()) || "Pipeline"} – discovery follow-up`, text },
        { signal: signal(), onUpdate: (v) => detail(`${v.processing?.run_count ?? 0} agent runs · call ${v.status}`) },
      );
      store.update("liveAssist", { followup: result });
      detail(`${result.handoff.suggested_questions?.length ?? 0} questions for the next call`);
    },

    /* ---------------------------------------------------------------- 5. SOW draft + WA review */
    async sow_draft(mode) {
      const templateId = requireTemplate();
      const opp = selected();
      const name = requireCustomer(opp);
      if (!opp && !S().inputs.projectDetails.trim()) throw new UserError("Add project details in the SOW tab, or run Intake first.");
      const upstream = upstreamNow();
      store.update("sow", { upstream, progress: null });
      const draft = await agents.sow.generate(
        { customerName: name, templateId, opportunityId: opp && opportunityId(opp), projectDetails: S().inputs.projectDetails.trim() || undefined, upstream },
        { signal: signal(), onUpdate: (job) => { store.update("sow", { progress: job }); detail(`Drafting: ${job.stage ?? job.status} ${job.progress ?? 0}%`); } },
      );
      store.update("sow", { draft });
      detail("Draft ready · running Well-Architected review");
      const review = await agents.sow.waReview({ markdown: draft.markdown, upstream, templateId }, { signal: signal() });
      store.update("sow", { waDraft: review });
      const s = reviewSummary(review);
      detail(`Draft ready · review: ${s.high} high, ${s.medium} medium, ${s.low} low`);
      if (s.high > 0 && S().inputs.pauseOnHigh && mode !== "single") {
        const choice = await gate({ kind: "review", title: `${s.high} high Well-Architected finding(s) on the draft`, message: "Review the findings on the SOW tab. Continue to build the architecture on this draft, or stop to fix it." });
        if (choice === "stop") throw new AbortedError();
      }
    },

    /* ---------------------------------------------------------------- 6. Architecture */
    async architecture() {
      const { inputs, sow } = S();
      const srsText = inputs.architectureText.trim() || sow.draft?.markdown || "";
      if (srsText.length < 100) throw new UserError("Architecture needs a SOW draft or at least a paragraph of requirements. Run the SOW agent or paste text in the Architecture tab.");
      const result = await agents.architecture.run(
        { title: customer(selected()) || "Pipeline design", srsText },
        { signal: signal(), onUpdate: (r) => { store.update("architecture", { progress: r }); detail(`${r.stage ?? r.status} ${r.progress ?? 0}%`); } },
      );
      store.update("architecture", { result });
      detail(`${result.design.components?.length ?? 0} components${result.status === "partial" ? " · partial (check the diagram/usage)" : ""}`);
    },

    /* ---------------------------------------------------------------- 7. Cost */
    async cost() {
      const { inputs, architecture } = S();
      let requirements = inputs.costText.trim();
      if (!requirements) {
        if (!architecture.result) throw new UserError("Cost needs an architecture. Run the Architecture agent or paste requirements in the Cost tab.");
        try { requirements = costRequirements(architecture.result.design, architecture.result.usage); } catch (e) { throw new UserError(e.message); }
      }
      if (requirements.length < LIMITS.costMinChars) throw new UserError(`Describe what to price in at least ${LIMITS.costMinChars} characters.`);
      const result = await agents.cost.run(
        { title: customer(selected()) || "Pipeline estimate", requirements: requirements.slice(0, LIMITS.costMaxChars) },
        { signal: signal(), onUpdate: (r) => { store.update("cost", { progress: r }); detail(`${r.stage ?? r.status}`); } },
      );
      store.update("cost", { result });
      detail(`${result.services_count ?? 0} services priced`);
    },

    /* ---------------------------------------------------------------- 8. Combined SOW */
    async sow_combined() {
      const { sow, architecture, cost } = S();
      if (!sow.draft) throw new UserError("Run the SOW draft first.");
      if (!architecture.result || !cost.result) throw new UserError("The combined SOW needs both the architecture and the cost estimate.");
      const templateId = requireTemplate();
      const opp = selected();
      const upstream = upstreamNow({ withBuild: true });
      store.update("sow", { upstream });
      const generated = await agents.sow.generate(
        { customerName: requireCustomer(opp), templateId, opportunityId: opp && opportunityId(opp), projectDetails: S().inputs.projectDetails.trim() || undefined, upstream },
        { signal: signal(), onUpdate: (job) => { store.update("sow", { progress: job }); detail(`Combining: ${job.stage ?? job.status} ${job.progress ?? 0}%`); } },
      );
      // The calculator link is the single source of truth for pricing; make sure it is in the Pricing section.
      const { markdown, how } = applyCostLink(generated.markdown, cost.result.calculator_url);
      if (how !== "unchanged") log(`Cost estimate link written into the SOW Pricing section (${how})`);
      store.update("sow", { combined: { ...generated, markdown, linkHow: how } });
      detail("Combined SOW ready");
    },

    /* ---------------------------------------------------------------- 9. Final review + delivery */
    async final_review() {
      const { sow, architecture } = S();
      if (!sow.combined) throw new UserError("Run the combined SOW first.");
      const upstream = sow.upstream;
      const markdown = sow.combined.markdown;
      const waFinal = await agents.sow.waReview({ markdown, upstream, templateId: S().inputs.templateId }, { signal: signal() });
      store.update("sow", { waFinal });
      detail("Well-Architected review done · running rule check");
      const check = await agents.sow.check({ markdown, upstream }, { signal: signal(), onUpdate: (job) => detail(`Rule check: ${job.stage ?? job.status}`) });
      store.update("sow", { check });
      try {
        const docx = await agents.sow.exportDocx({ customerName: requireCustomer(selected()), markdown, opportunityId: selected() && opportunityId(selected()), diagramBase64: architecture.result?.diagramBase64 }, { signal: signal() });
        store.update("sow", { docx });
      } catch (err) {
        if (err.name === "AbortedError") throw err;
        log(`Word export unavailable: ${err.message}`, "warn");
      }
      const s = reviewSummary(waFinal);
      const failed = (check.rules ?? []).filter((r) => r.status === "fail").length;
      detail(`Final SOW delivered · ${s.high} high finding(s), ${failed} failed rule(s)`);
    },
  };
}
