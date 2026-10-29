import { AGENT_TABS } from "../pipeline/state.js";
import { button, h, statusBadge } from "./dom.js";
import { renderOpportunities } from "./opportunities.js";
import { renderArchitecture, renderCost, renderSow } from "./panels-build.js";
import { renderDiscovery, renderLiveAssist } from "./panels-context.js";

const DETAIL = {
  intake: { slug: "intake-agent", blurb: "Finds opportunities in seller notes and CRM records and structures each one.", actions: [["intake", "Run Intake"]], showOpps: true, view: null },
  liveAssist: { slug: "live-assist-agent", blurb: "Captures what is known from the Intake record (and any call), and lists what is still missing.", actions: [["live_assist", "Run Live Assist"], ["live_assist_followup", "Run follow-up on Discovery"]], showOpps: true, view: renderLiveAssist },
  discovery: { slug: "discovery-agent", blurb: "Reads the Intake and Live Assist output and finds use cases, hidden needs and open questions.", actions: [["discovery", "Run Discovery"]], showOpps: true, view: renderDiscovery },
  sow: { slug: "catalyst-sow-generator-agent", blurb: "Drafts the SOW, reviews it against the Well-Architected Framework, and rebuilds it with the architecture and cost.", actions: [["sow_draft", "Draft SOW + review"], ["sow_combined", "Build combined SOW"], ["final_review", "Final review"]], showOpps: true, view: renderSow },
  architecture: { slug: "architecture-agent", blurb: "Turns the SOW into a design, decision record and diagram.", actions: [["architecture", "Run Architecture"]], showOpps: false, view: renderArchitecture },
  cost: { slug: "cost-calculator-agent", blurb: "Prices the architecture in the AWS Pricing Calculator.", actions: [["cost", "Run Cost estimate"]], showOpps: false, view: renderCost },
};

export function tabStatus(state, tab) {
  const statuses = tab.stages.map((k) => state.run.stages[k].status);
  for (const s of ["running", "failed", "stopped"]) if (statuses.includes(s)) return s;
  if (state.run.gate && tab.stages.includes(state.run.gate.stageKey)) return "paused";
  return statuses.includes("done") ? "done" : "pending";
}

export function renderTabBar(state, active, onSelect) {
  return h("div", { class: "tabs", role: "tablist", "aria-label": "Agents" },
    AGENT_TABS.map((tab) => h("button", { type: "button", role: "tab", id: `tab-${tab.id}`, "aria-selected": String(tab.id === active), "aria-controls": "tab-panel", class: "tab", onClick: () => onSelect(tab.id) }, tab.label, " ", statusBadge(tabStatus(state, tab)))));
}

export function renderTabPanel(state, active, actions) {
  const tab = AGENT_TABS.find((t) => t.id === active);
  const d = DETAIL[active];
  const busy = ["running", "paused"].includes(state.run.status);
  const stageNotes = tab.stages.map((k) => state.run.stages[k]).filter((s) => s.detail || s.error);
  const selectable = !busy || state.run.gate?.kind === "select";

  return h("div", { id: "tab-panel", role: "tabpanel", "aria-labelledby": `tab-${active}`, class: "card panel" },
    h("div", { class: "toolbar" },
      h("div", {}, h("h2", { class: "card-title" }, tab.label), h("p", { class: "muted" }, d.blurb)),
      h("div", { class: "toolbar-actions" }, d.actions.map(([key, label], i) => button(label, { variant: i === 0 ? "primary" : "secondary", disabled: busy, onClick: () => actions.runStage(key) })))),
    stageNotes.map((s) => h("p", { class: s.error ? "notice error" : "notice" }, s.error || s.detail)),
    d.showOpps ? renderOpportunities(state, actions, { selectable }) : null,
    d.view ? d.view(state) : null);
}
