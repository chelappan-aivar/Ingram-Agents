// Single in-memory store. Nothing is persisted: a page reload or "Exit pipeline" returns every agent to empty.
const fresh = () => ({
  inputs: { notesText: "", files: [], outlookDays: null, dynamicsLimit: null, laKey: "", templateId: "aiml/ai-ml", pause: false },
  confirmed: false, reviewed: false, laMatch: null, ms: null,
  opps: [], intakeRun: null, selected: null,
  la1: null, discovery: null, la2: null,
  sow: null,        // {markdown, check, session, customer}
  arch: null, cost: null, sow1: null,
  steps: {},        // id -> {status: pending|running|done|skipped|failed|blocked, detail}
  log: [], mode: "idle",   // idle | running | select (pick the opportunity) | gate (approve the SOW draft) | finished
  agentStatus: {},  // standalone agent -> {state: running|done|failed, text}
});

let state = fresh();
const listeners = new Set();
export const get = () => state;
export const subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
export const emit = () => listeners.forEach((fn) => fn(state));
export function update(fn) { fn(state); emit(); }
export function log(msg, level = "info", stage = "Pipeline") { state.log.push({ at: new Date(), msg, level, stage }); emit(); }
export function setStep(id, status, detail = "") { state.steps[id] = { status, detail }; emit(); }

// Exit pipeline: everything back to empty.
export function resetAll() { state = fresh(); emit(); }
// Clear opportunities: Intake, Live Assist, Discovery and SOW (and the SOW-derived Architecture/Cost) go empty; inputs stay so the chain can re-run.
export function clearChain() {
  const { inputs, ms } = state;
  state = fresh();
  state.inputs = inputs; state.ms = ms;
  emit();
}
