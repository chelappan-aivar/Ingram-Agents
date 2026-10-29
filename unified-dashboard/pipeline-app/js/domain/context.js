/**
 * Turns one agent's output into the input the next agent understands.
 *
 *   Intake opportunity ──► Live Assist briefing transcript
 *   Intake + Live Assist ─► Discovery documents
 *   Discovery report ─────► Live Assist follow-up transcript
 */
import { LIMITS, LIVE_ASSIST } from "../config.js";
import { chunk, clean, truncate } from "./text.js";
import { customerOf, fieldMap, titleOf } from "./opportunities.js";

const GROUP_ORDER = ["Customer", "Contacts", "Opportunity", "Current environment", "Target and constraints", "Commercial", "Source and partner"];

/* ------------------------------------------------------------------ Intake → Live Assist */

/**
 * A transcript Live Assist can replay. The seller "speaks" the intake facts so Live Assist can
 * capture them against its checklist and work out what is still missing. A real call transcript,
 * when the user supplies one, follows the briefing and is the only part treated as customer speech.
 */
export function briefingTranscript(opportunity, callTranscript = "") {
  const lines = [];
  const say = (text, speaker = LIVE_ASSIST.seller) => {
    for (const part of chunk(text, LIMITS.maxLiveAssistTurnChars)) lines.push(`${speaker}: ${part}`);
  };

  if (opportunity) {
    const f = fieldMap(opportunity);
    say(`Briefing from the intake record for ${customerOf(opportunity)}: ${titleOf(opportunity)}.`);
    if (opportunity.summary) say(`Summary: ${opportunity.summary}`);
    const byGroup = new Map();
    for (const field of opportunity.fields ?? []) {
      if (!clean(field.value) || field.field === "customer_name") continue;
      const group = field.group || "Other";
      if (!byGroup.has(group)) byGroup.set(group, []);
      byGroup.get(group).push(`${field.label || field.field}: ${clean(field.value)}`);
    }
    const rank = (g) => (GROUP_ORDER.includes(g) ? GROUP_ORDER.indexOf(g) : GROUP_ORDER.length);
    const groups = [...byGroup.keys()].sort((a, b) => rank(a) - rank(b));
    for (const group of groups) say(`${group}. ${byGroup.get(group).join(". ")}.`);
    for (const c of opportunity.conflicts ?? []) say(`Conflicting information on ${c.label || c.field}: ${c.detail}`);
    if (!Object.keys(f).length && !opportunity.summary) say("The intake record has no extracted fields yet.");
  }

  const extra = String(callTranscript ?? "").trim();
  const text = extra ? `${lines.join("\n")}${lines.length ? "\n" : ""}${extra}` : lines.join("\n");
  return text.length > LIMITS.maxLiveAssistChars ? text.slice(0, LIMITS.maxLiveAssistChars) : text;
}

/* ------------------------------------------------------------------ Discovery report → Live Assist */

const asText = (item) => clean(typeof item === "string" ? item : item?.text ?? item?.question ?? item?.title);

export function followUpTranscript(report, narrative) {
  const lines = [];
  const say = (text) => {
    for (const part of chunk(text, LIMITS.maxLiveAssistTurnChars)) lines.push(`${LIVE_ASSIST.seller}: ${part}`);
  };
  for (const paragraph of narrative?.executive_summary ?? []) say(`Discovery summary: ${paragraph}`);
  const heard = report?.what_we_heard ?? {};
  for (const [label, key] of [["Goal", "goals"], ["Pain point", "pain_points"], ["Constraint", "constraints"]]) {
    for (const item of heard[key] ?? []) say(`${label}: ${asText(item)}`);
  }
  for (const uc of report?.use_cases ?? []) {
    say(`Use case ${uc.rank ?? ""} (${uc.category}, ${uc.confidence} confidence): ${uc.title}. ${uc.problem ?? ""} To validate: ${uc.validation_question ?? ""}`);
  }
  for (const h of report?.top_hidden_opportunities ?? []) say(`Hidden opportunity: ${h.title}. ${h.why_it_matters ?? ""}`);
  for (const q of report?.gaps_and_open_questions ?? []) {
    if (q.status !== "resolved") say(`Still open: ${q.question} ${q.reason ?? ""}`);
  }
  const text = lines.join("\n");
  return text.length > LIMITS.maxLiveAssistChars ? text.slice(0, LIMITS.maxLiveAssistChars) : text;
}

/* ------------------------------------------------------------------ Documents for Discovery */

export function intakeRecordMarkdown(opportunity) {
  const out = [`# Intake record: ${customerOf(opportunity)}`, "", `Opportunity: ${titleOf(opportunity)}`];
  if (opportunity.summary) out.push("", clean(opportunity.summary));
  out.push("", `Urgency: ${opportunity.urgency || "unknown"}  `, `Found in: ${(opportunity.source_types ?? []).join(", ") || "n/a"}`);
  const groups = new Map();
  for (const f of opportunity.fields ?? []) {
    if (!clean(f.value)) continue;
    const g = f.group || "Other";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(f);
  }
  for (const [group, fields] of groups) {
    out.push("", `## ${group}`);
    for (const f of fields) out.push(`- ${f.label || f.field}: ${clean(f.value)}${f.verified ? "" : " (unverified)"}`);
  }
  if (opportunity.conflicts?.length) {
    out.push("", "## Conflicts between sources");
    for (const c of opportunity.conflicts) out.push(`- ${c.label || c.field}: ${clean(c.detail)}`);
  }
  if (opportunity.gaps?.length) {
    out.push("", "## Information still missing");
    for (const g of opportunity.gaps) out.push(`- [${g.priority || "medium"}] ${g.label || g.field}: ${clean(g.question || g.why)}`);
  }
  return out.join("\n");
}

export function liveAssistMinutesMarkdown(handoff, title = "Live Assist handoff") {
  if (!handoff) return "";
  const out = [`# ${title}`];
  const m = handoff.minutes?.minutes ?? {};
  if (handoff.problem_summary) out.push("", "## Problem summary", clean(handoff.problem_summary));
  if (handoff.summary || m.summary) out.push("", "## Summary", clean(handoff.summary || m.summary));
  const list = (heading, items) => {
    const rows = (items ?? []).map(asText).filter(Boolean);
    if (rows.length) out.push("", `## ${heading}`, ...rows.map((r) => `- ${r}`));
  };
  list("Key points", m.key_points);
  list("Decisions", m.decisions);
  list("Action items", m.action_items);
  list("Risks", m.risks);
  list("Next steps", m.next_steps);
  if (handoff.captured?.length) {
    out.push("", "## Captured against the checklist");
    for (const c of handoff.captured) out.push(`- ${c.label}: ${clean(c.value)}`);
  }
  if (handoff.open_gaps?.length) {
    out.push("", "## Open gaps");
    for (const g of handoff.open_gaps) out.push(`- ${g.label}${g.required ? " (required)" : ""}`);
  }
  list("Questions to ask next", handoff.suggested_questions);
  list("Open questions from the call", m.open_questions);
  return out.join("\n");
}

/** The documents Discovery reads, named so they sort oldest to newest and avoid its reserved prefixes. */
export function discoveryDocuments({ opportunity, handoff, callTranscript, extraText }) {
  const docs = [];
  if (opportunity) docs.push({ name: "00_intake_record.md", content: intakeRecordMarkdown(opportunity) });
  const minutes = liveAssistMinutesMarkdown(handoff, "Live Assist: captured context and open gaps");
  if (minutes) docs.push({ name: "01_live_assist_handoff.md", content: minutes });
  if (clean(callTranscript)) docs.push({ name: "02_call_transcript.txt", content: String(callTranscript).trim() });
  if (clean(extraText)) docs.push({ name: "03_additional_notes.txt", content: String(extraText).trim() });
  return docs.filter((d) => d.content.trim());
}

/** Short, display-ready facts pulled from a Live Assist handoff. */
export function handoffDigest(handoff) {
  if (!handoff) return null;
  return {
    captured: (handoff.captured ?? []).length,
    gaps: (handoff.open_gaps ?? []).length,
    questions: handoff.suggested_questions ?? [],
    summary: truncate(handoff.summary || handoff.problem_summary, 600),
  };
}
