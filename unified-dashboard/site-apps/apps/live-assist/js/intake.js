// The "Intake Agent opportunities" card: funding-checked opportunities from the Intake Agent, their decision,
// and the call history map (every Live Assist call held for an opportunity). Picking one links the next call to it.
// Everything shown comes from the API and is set with textContent only (el()).

import { api } from "./api.js";
import { $, el, toast } from "./dom.js";

const STATUS = { decided: "Decided", needs_info: "Needs information", to_confirm: "To confirm", not_covered: "Not covered" };
let opportunities = [];
let selected = null;      // intake_opportunity_id shown in the detail
let forCall = null;       // {intake_opportunity_id, name, account_id, opportunity_id}: the next call is held for it
let discoveryUi = null;   // the Discovery agent's web UI, to open a run or start one for an opportunity

const usd = (n) => (n === null || n === undefined ? "" : `${Math.round(n).toLocaleString("en-US")} USD`);
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");

function decisionPill(d) {
  return el("span", d.program || STATUS[d.status] || d.status, `pill dec-${d.status}`);
}

function renderList() {
  const list = $("intake-list");
  list.replaceChildren(...opportunities.map((o) => {
    const button = el("button", null, `intake-item${o.intake_opportunity_id === selected ? " current" : ""}`, { type: "button" });
    const main = el("span", null, "intake-main");
    main.append(el("b", o.name), el("small", [o.customer, o.decision.total_usd ? usd(o.decision.total_usd) : ""].filter(Boolean).join(" · ")));
    const side = el("span", null, "intake-side");
    side.append(decisionPill(o.decision),
      el("small", o.call_count ? `${o.call_count} call${o.call_count === 1 ? "" : "s"}` : "No calls yet"));
    button.append(main, side);
    button.addEventListener("click", () => openDetail(o.intake_opportunity_id));
    const li = el("li");
    li.append(button);
    return li;
  }));
  $("intake-empty").hidden = opportunities.length > 0;
}

async function openDetail(id) {
  selected = id;
  renderList();
  const box = $("intake-detail");
  box.hidden = false;
  box.replaceChildren(el("p", "Loading…", "muted"));
  try {
    box.replaceChildren(...detailView(await api.intakeOpportunity(id)));
    loadDiscovery(id);
  } catch (error) {
    box.replaceChildren(el("p", error.message, "error"));
  }
}

function detailView(o) {
  const d = o.decision;
  const head = el("div", null, "intake-decision");
  const title = el("div");
  title.append(el("span", STATUS[d.status] || d.status, "muted"), el("b", d.program || d.funding || ""));
  head.append(title);
  if (d.total_usd) head.append(el("b", usd(d.total_usd), "intake-amount"));
  const nodes = [el("h3", o.name), el("p", [o.customer, `checked ${when(o.checked_at)}`].filter(Boolean).join(" · "), "muted"), head];
  if (d.program && d.funding) nodes.push(el("p", d.funding, "intake-funding"));
  if (d.rationale) nodes.push(el("p", d.rationale));
  if ((d.path || []).length) nodes.push(el("p", d.path.map((p) => `${p.step}: ${p.value}`).join("  →  "), "muted"));
  if ((d.missing || []).length) {
    nodes.push(el("h4", "Ask in the call"));
    const ul = el("ul");
    for (const q of d.missing) ul.append(el("li", q));
    nodes.push(ul);
  }

  const use = el("button", forCall?.intake_opportunity_id === o.intake_opportunity_id ? "Selected for the next call" : "Use for the next call",
    "btn btn-primary btn-block", { type: "button" });
  use.addEventListener("click", async () => {
    use.disabled = true;
    try {
      const link = await api.intakeLink(o.intake_opportunity_id);
      forCall = { ...link, name: o.name };
      showForCall();
      toast("The next call is linked to this opportunity");
      openDetail(o.intake_opportunity_id);
    } catch (error) {
      toast(error.message);
      use.disabled = false;
    }
  });
  nodes.push(use);

  nodes.push(el("h4", "Discovery agent"), el("div", null, "intake-discovery"));
  nodes[nodes.length - 1].id = "intake-discovery";
  nodes.push(el("h4", `Call history (${o.calls.length})`));
  if (!o.calls.length) nodes.push(el("p", "No Live Assist calls for this opportunity yet.", "muted"));
  else {
    const ol = el("ol", null, "intake-calls");
    for (const c of o.calls) {
      const li = el("li");
      const line = el("span");
      line.append(el("b", c.title || "Call"), el("small", `${when(c.created_at)} · ${c.status}${c.turn_count ? ` · ${c.turn_count} lines` : ""}`));
      li.append(line);
      if (c.decision) li.append(el("small", `Funding at the time: ${c.decision.program || STATUS[c.decision.status] || ""}`, "muted"));
      ol.append(li);
    }
    nodes.push(ol);
  }
  return nodes;
}

async function loadDiscovery(id) {
  const box = $("intake-discovery");
  if (!box) return;
  box.replaceChildren(el("p", "Loading…", "muted"));
  let out;
  try { out = await api.intakeDiscovery(id); } catch (error) { box.replaceChildren(el("p", error.message, "muted")); return; }
  if (selected !== id) return;
  const nodes = [];
  const run = out.runs[0];
  if (!run) nodes.push(el("p", "No Discovery report yet. Start one from the Discovery agent: it reads this opportunity's Intake record and its ended calls.", "muted"));
  else {
    const label = { starting: "Starting", running: `Working · step ${run.steps_done || 0} of 7`, awaiting_clarification: "Needs your answers",
      succeeded: "Report ready", failed: "Failed" }[run.state] || run.state;
    nodes.push(el("p", `${label} · ${when(run.started_at)} · ${run.call_ids.length} call${run.call_ids.length === 1 ? "" : "s"} read${run.stale ? " · status may be out of date" : ""}`, "muted"));
    const s = run.summary;
    if (s) {
      nodes.push(el("p", `${s.use_case_count} use cases · ${s.hidden_opportunities.length} hidden opportunities · ${s.open_question_count} open questions`));
      const ul = el("ul");
      for (const u of s.use_cases.slice(0, 4)) ul.append(el("li", `${u.title} (${String(u.category || "").toLowerCase()}, ${u.confidence} confidence)`));
      nodes.push(ul);
      if (s.open_questions.length) {
        const agenda = el("button", "Use open questions for the next call", "btn btn-block", { type: "button" });
        agenda.addEventListener("click", () => useAgenda(s.open_questions));
        nodes.push(agenda);
      }
    }
    if (run.url) nodes.push(el("a", "Open in the Discovery agent ↗", "intake-link", { href: run.url, target: "_blank", rel: "noopener noreferrer" }));
  }
  if (discoveryUi) {
    nodes.push(el("a", run ? "Run Discovery again ↗" : "Start Discovery for this opportunity ↗", "intake-link",
      { href: `${discoveryUi}/#opportunity=${encodeURIComponent(id)}`, target: "_blank", rel: "noopener noreferrer" }));
  }
  box.replaceChildren(...nodes);
}

/** The Discovery report's open questions become the next meeting's agenda (title/description drive pre-questions). */
function useAgenda(questions) {
  const o = opportunities.find((x) => x.intake_opportunity_id === selected);
  const meeting = document.querySelector('.source[data-source="meeting"] .source-head');
  meeting?.click();
  if (o && !$("meeting-title").value.trim()) $("meeting-title").value = `${o.name}: follow-up`.slice(0, 120);
  $("meeting-description").value = ["Open questions from the Discovery report:", ...questions.map((q) => `- ${q}`)].join("\n").slice(0, 2000);
  $("meeting-description").scrollIntoView({ behavior: "smooth", block: "center" });
  toast("Open questions added as the meeting agenda");
}

function showForCall() {
  const node = $("intake-for-call");
  node.hidden = !forCall;
  if (!forCall) return;
  node.replaceChildren(el("span", `Next call is for: ${forCall.name}`));
  const clear = el("button", "×", null, { type: "button", "aria-label": "Do not link the next call" });
  clear.addEventListener("click", () => { forCall = null; showForCall(); if (selected) openDetail(selected); });
  node.append(clear);
}

/** Fields for POST /calls when an Intake opportunity is selected for the next call. */
export function intakeCallFields() {
  return forCall ? { account_id: forCall.account_id, opportunity_id: forCall.opportunity_id } : {};
}

/** After a call starts: add it to the selected opportunity's call history. */
export async function recordIntakeCall(call) {
  if (!forCall || call.opportunity_id !== forCall.opportunity_id) return;
  const id = forCall.intake_opportunity_id;
  try {
    await api.intakeRecordCall(id, call.session_id);
    await loadIntake();
    if (selected === id) openDetail(id);
  } catch (error) {
    toast(`The call started, but was not added to the opportunity's history: ${error.message}`);
  }
}

export async function loadIntake() {
  const card = $("intake-card");
  try {
    const out = await api.intakeOpportunities();
    card.hidden = !out.enabled;
    opportunities = out.opportunities;
    discoveryUi = out.discovery_ui_url || null;
    renderList();
  } catch (error) {
    card.hidden = false;
    $("intake-empty").hidden = false;
    $("intake-empty").textContent = `Could not load the Intake opportunities: ${error.message}`;
  }
}

export function initIntake() {
  $("intake-refresh").addEventListener("click", () => loadIntake());
  showForCall();
}
