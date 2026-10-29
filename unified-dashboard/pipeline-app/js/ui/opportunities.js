import { toRow, fieldMap } from "../domain/opportunities.js";
import { rankOpportunities } from "../domain/opportunities.js";
import { badge, button, empty, h, list, table } from "./dom.js";

const URGENCY_TONE = { high: "danger", medium: "warning", low: "neutral", unknown: "neutral" };
const expanded = new Set();

/**
 * The shared Opportunities table, shown on the Intake, Live Assist, Discovery and SOW tabs.
 * Selecting a row chooses the opportunity the pipeline works on. Clear discards every agent
 * output; "Clear and re-run" repeats Intake → Live Assist → Discovery → SOW on the same inputs.
 */
export function renderOpportunities(state, actions, { selectable }) {
  const { opportunities, selectedKey } = state.intake;
  const busy = ["running", "paused"].includes(state.run.status);
  const ranked = rankOpportunities(opportunities);
  const gated = state.run.gate?.kind === "select";

  const toolbar = h("div", { class: "toolbar" },
    h("div", {}, h("h3", { class: "block-title" }, "Opportunities"), h("p", { class: "muted" }, ranked.length ? `${ranked.length} found by Intake. The highlighted row is the one the other agents work on.` : "None yet.")),
    h("div", { class: "toolbar-actions" },
      button("Clear opportunities", { variant: "secondary", disabled: busy || (!ranked.length && state.run.status === "idle"), onClick: actions.clear, title: "Discard Intake, Live Assist, Discovery and SOW results" }),
      button("Clear and re-run 4 agents", { variant: "primary", disabled: busy, onClick: actions.rerunFour, title: "Run Intake → Live Assist → Discovery → SOW again, each on the previous agent's output" })));

  if (!ranked.length) return h("section", { class: "block" }, toolbar, empty("No opportunities yet. Add sources and run the Intake agent, or start the pipeline."));

  const rows = ranked.map(toRow);
  const byKey = new Map(ranked.map((o) => [o.thread_key, o]));
  const columns = [
    selectable ? { label: "", class: "col-select", cell: (r) => h("input", { type: "radio", name: "opportunity", checked: r.key === selectedKey, "aria-label": `Select ${r.customer}`, disabled: busy && !gated, onChange: () => actions.select(r.key) }) } : null,
    { label: "Customer", cell: (r) => h("strong", {}, r.customer) },
    { label: "Opportunity", cell: (r) => r.title },
    { label: "Type", cell: (r) => r.type || "—" },
    { label: "Urgency", cell: (r) => badge(r.urgency, URGENCY_TONE[r.urgency]) },
    { label: "Sources", cell: (r) => [r.sources || "—", r.matched ? badge("matched", "success") : null] },
    { label: "Fields", class: "num", cell: (r) => `${r.verified}/${r.fields}` },
    { label: "Gaps", class: "num", cell: (r) => String(r.gaps) },
    { label: "Programme fit", cell: (r) => r.fit || "—" },
    { label: "", cell: (r) => h("button", { type: "button", class: "link", "aria-expanded": expanded.has(r.key), onClick: (e) => { e.stopPropagation(); expanded.has(r.key) ? expanded.delete(r.key) : expanded.add(r.key); actions.refresh(); } }, expanded.has(r.key) ? "Hide" : "Details") },
  ].filter(Boolean);

  const wrap = h("section", { class: "block" }, toolbar, table(columns, rows, { rowClass: (r) => (r.key === selectedKey ? "row-selected" : ""), caption: "Opportunities found by the Intake agent" }));
  for (const r of rows.filter((x) => expanded.has(x.key))) wrap.append(detail(byKey.get(r.key)));
  return wrap;
}

function detail(o) {
  const fields = Object.entries(fieldMap(o)).slice(0, 14);
  return h("div", { class: "detail" },
    h("h4", {}, `${o.opportunity_title || "Opportunity"} — details`),
    o.summary ? h("p", {}, o.summary) : null,
    fields.length ? h("dl", { class: "kv" }, fields.map(([k, v]) => [h("dt", {}, k.replaceAll("_", " ")), h("dd", {}, v)])) : null,
    o.gaps?.length ? [h("h5", {}, "Information still missing"), list(o.gaps.map((g) => `${g.label || g.field}: ${g.question || g.why}`))] : null,
    o.conflicts?.length ? [h("h5", {}, "Conflicts between sources"), list(o.conflicts.map((c) => `${c.label || c.field}: ${c.detail}`))] : null);
}
