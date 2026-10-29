/** What a person still has to resolve before the final SOW goes out, collected from every agent. */
import { chooseOpportunity, customerOf, opportunityId } from "./opportunities.js";
import { reviewSummary } from "./sow-inputs.js";
import { clean, unique } from "./text.js";

const findingLine = (f) => clean([f.title ?? f.name ?? f.id, f.detail ?? f.description ?? f.message].filter(Boolean).join(": "));

export function openItems(state) {
  const items = [];
  const add = (source, severity, text) => { if (clean(text)) items.push({ source, severity, text: clean(text) }); };

  for (const q of unique([...(state.liveAssist.followup?.handoff?.suggested_questions ?? []), ...(state.liveAssist.brief?.handoff?.suggested_questions ?? [])]).slice(0, 10)) add("Live Assist", "question", q);
  for (const q of (state.discovery.result?.report?.gaps_and_open_questions ?? []).filter((x) => x.status !== "resolved").slice(0, 10)) add("Discovery", "question", q.question);
  for (const f of (state.sow.waFinal?.findings ?? []).filter((x) => ["high", "medium"].includes(x.severity))) add("Well-Architected review", f.severity, findingLine(f));
  for (const r of (state.sow.check?.rules ?? []).filter((x) => x.status === "fail")) add("SOW rule check", r.severity === "required" ? "high" : "medium", r.name ?? r.id);
  for (const w of state.architecture.result?.design?.open_questions ?? []) add("Architecture", "question", typeof w === "string" ? w : w?.text);
  return items;
}

/** A single JSON document of every output, for the user to keep before exiting (nothing is stored server-side by this page). */
export function buildBundle(state) {
  const opp = chooseOpportunity(state.intake.opportunities, state.intake.selectedKey);
  return {
    generated_at: new Date().toISOString(),
    opportunity: opp ? { id: opportunityId(opp), customer: customerOf(opp), record: opp } : null,
    intake_counts: state.intake.run?.counts ?? null,
    live_assist: { briefing: state.liveAssist.brief?.handoff ?? null, follow_up: state.liveAssist.followup?.handoff ?? null },
    discovery: state.discovery.result ? { engagement: state.discovery.result.engagement, report: state.discovery.result.report, narrative: state.discovery.result.narrative } : null,
    sow: {
      draft: state.sow.draft?.markdown ?? null,
      draft_review: state.sow.waDraft ?? null,
      combined: state.sow.combined?.markdown ?? null,
      final_review: state.sow.waFinal ?? null,
      rule_check: state.sow.check ?? null,
      word_download_url: state.sow.docx?.download_url ?? null,
    },
    architecture: state.architecture.result ? { design: state.architecture.result.design, usage: state.architecture.result.usage, adr: state.architecture.result.adr } : null,
    cost: state.cost.result ?? null,
    open_items: openItems(state),
  };
}

export const verdict = (state) => {
  const s = reviewSummary(state.sow.waFinal);
  const failed = (state.sow.check?.rules ?? []).filter((r) => r.status === "fail" && r.severity === "required").length;
  return s.high || failed ? "needs-review" : "ready";
};
