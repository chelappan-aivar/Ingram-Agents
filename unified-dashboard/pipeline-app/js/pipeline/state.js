/** Pipeline definition and the initial (empty) state. Nothing here is persisted. */

export const STAGES = Object.freeze([
  { key: "intake", agent: "intake", label: "Intake", blurb: "Find and structure the opportunity" },
  { key: "live_assist", agent: "liveAssist", label: "Live Assist", blurb: "Capture context, list open gaps" },
  { key: "discovery", agent: "discovery", label: "Discovery", blurb: "Use cases, hidden needs, open questions" },
  { key: "live_assist_followup", agent: "liveAssist", label: "Live Assist follow-up", blurb: "Turn the Discovery report into next questions" },
  { key: "sow_draft", agent: "sow", label: "SOW draft + Well-Architected review", blurb: "Draft from Intake, Live Assist and Discovery" },
  { key: "architecture", agent: "architecture", label: "Architecture", blurb: "Design, ADR and diagram from the SOW" },
  { key: "cost", agent: "cost", label: "Cost estimate", blurb: "Price the architecture" },
  { key: "sow_combined", agent: "sow", label: "Combined SOW", blurb: "Regenerate with architecture and cost" },
  { key: "final_review", agent: "sow", label: "Final review + delivery", blurb: "Well-Architected review, rule check, export" },
]);

export const STAGE_KEYS = STAGES.map((s) => s.key);

/** The four-agent loop the Opportunities "Clear and re-run" action repeats. */
export const FOUR_AGENT_KEYS = Object.freeze(["intake", "live_assist", "discovery", "live_assist_followup", "sow_draft"]);

export const AGENT_TABS = Object.freeze([
  { id: "intake", label: "Intake", stages: ["intake"] },
  { id: "liveAssist", label: "Live Assist", stages: ["live_assist", "live_assist_followup"] },
  { id: "discovery", label: "Discovery", stages: ["discovery"] },
  { id: "sow", label: "SOW Generator", stages: ["sow_draft", "sow_combined", "final_review"] },
  { id: "architecture", label: "Architecture", stages: ["architecture"] },
  { id: "cost", label: "Cost Estimate", stages: ["cost"] },
]);

const blankStage = () => ({ status: "pending", startedAt: null, endedAt: null, detail: "", error: "" });
export const blankStages = () => Object.fromEntries(STAGE_KEYS.map((k) => [k, blankStage()]));

export const freshOutputs = Object.freeze({
  intake: () => ({ runId: null, run: null, opportunities: [], selectedKey: null }),
  liveAssist: () => ({ brief: null, followup: null, live: null }),
  discovery: () => ({ result: null, progress: null }),
  sow: () => ({ draft: null, waDraft: null, combined: null, waFinal: null, check: null, docx: null, upstream: null, progress: null }),
  architecture: () => ({ result: null, progress: null }),
  cost: () => ({ result: null, progress: null }),
});

/** Which output slices a stage owns, so re-running a stage can discard exactly its own results. */
export const STAGE_OUTPUTS = Object.freeze({
  intake: [["intake"]],
  live_assist: [["liveAssist", "brief"]],
  discovery: [["discovery"]],
  live_assist_followup: [["liveAssist", "followup"]],
  sow_draft: [["sow", "draft"], ["sow", "waDraft"], ["sow", "upstream"]],
  architecture: [["architecture"]],
  cost: [["cost"]],
  sow_combined: [["sow", "combined"]],
  final_review: [["sow", "waFinal"], ["sow", "check"], ["sow", "docx"]],
});

export function initialState() {
  return {
    templates: [],
    inputs: {
      notesText: "", crmFiles: [], notesFiles: [], callTranscript: "",
      customerName: "", templateId: "", projectDetails: "",
      discoveryText: "", architectureText: "", costText: "",
      autoSelect: true, pauseOnHigh: true,
      useOutlook: false, outlookDays: 7, useDynamics: false, dynamicsLimit: 25,
    },
    outlook: { user: null, code: null, uri: null, message: "", busy: false },
    run: { status: "idle", mode: null, startedAt: null, finishedAt: null, stages: blankStages(), gate: null, log: [], error: null },
    ...Object.fromEntries(Object.entries(freshOutputs).map(([k, make]) => [k, make()])),
  };
}
