/** Live Assist and Discovery panels: what each agent returned, with nothing shown until it has run. */
import { handoffDigest } from "../domain/context.js";
import { badge, empty, h, list, section, table } from "./dom.js";

const CONFIDENCE_TONE = { high: "success", medium: "warning", low: "neutral" };

function callSection(title, call) {
  if (!call) return null;
  const d = handoffDigest(call.handoff);
  const h0 = call.handoff;
  return section(
    title,
    call.partial ? h("p", { class: "notice warn" }, "The call did not finish in time. Showing what was captured so far.") : null,
    h("div", { class: "stats" }, stat("Fields captured", d.captured), stat("Open gaps", d.gaps), stat("Questions to ask", d.questions.length), stat("Turns", call.view?.turns?.length ?? 0)),
    d.summary ? h("p", {}, d.summary) : null,
    h0.captured?.length ? [h("h4", {}, "Captured against the checklist"), table([{ label: "Topic", cell: (c) => c.label }, { label: "Value", cell: (c) => c.value }], h0.captured)] : null,
    h0.open_gaps?.length ? [h("h4", {}, "Still open"), list(h0.open_gaps.map((g) => g.label + (g.required ? " (required)" : "")))] : null,
    d.questions.length ? [h("h4", {}, "Suggested questions for the next conversation"), list(d.questions, "bullets questions")] : null,
  );
}

const stat = (label, value) => h("div", { class: "stat" }, h("div", { class: "stat-value" }, String(value)), h("div", { class: "stat-label" }, label));

export function renderLiveAssist(state) {
  const { brief, followup } = state.liveAssist;
  if (!brief && !followup) return empty("Live Assist has not run. It takes the Intake opportunity (and an optional call transcript), captures what is known, and lists what is still missing.");
  return h("div", {}, callSection("Briefing from Intake", brief), callSection("Follow-up on the Discovery report", followup));
}

export function renderDiscovery(state) {
  const { result, progress } = state.discovery;
  if (!result) {
    return progress
      ? h("div", { class: "block" }, h("p", {}, `Discovery is ${progress.state}. This usually takes 10 to 30 minutes.`), list((progress.completed_steps ?? []).map((s) => `Finished: ${s.replaceAll("_", " ")}`)))
      : empty("Discovery has not run. It reads the Intake record and the Live Assist output and returns use cases, hidden needs and open questions.");
  }
  const { report, narrative } = result;
  const useCases = report.use_cases ?? [];
  return h("div", {},
    h("p", { class: "muted" }, `Engagement ${result.engagement}`),
    narrative?.executive_summary?.length ? section("Executive summary", narrative.executive_summary.map((p) => h("p", {}, p))) : null,
    useCases.length ? section("Use cases", table([
      { label: "#", class: "num", cell: (u) => String(u.rank ?? "") },
      { label: "Category", cell: (u) => badge(u.category, u.category === "EXPLICIT" ? "primary" : "warning") },
      { label: "Use case", cell: (u) => [h("strong", {}, u.title), h("div", { class: "muted" }, u.problem)] },
      { label: "Confidence", cell: (u) => badge(u.confidence, CONFIDENCE_TONE[u.confidence] ?? "neutral") },
      { label: "To validate", cell: (u) => u.validation_question ?? "" },
    ], useCases)) : null,
    report.top_hidden_opportunities?.length ? section("Hidden opportunities", list(report.top_hidden_opportunities.map((o) => `${o.title}: ${o.why_it_matters}`))) : null,
    report.gaps_and_open_questions?.length ? section("Open questions", list(report.gaps_and_open_questions.filter((q) => q.status !== "resolved").map((q) => `${q.question}${q.reason ? ` — ${q.reason}` : ""}`))) : null);
}
