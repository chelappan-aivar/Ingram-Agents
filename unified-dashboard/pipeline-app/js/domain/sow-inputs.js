/**
 * Builds the SOW agent's `upstream` payload (UpstreamInputs v1, additionalProperties:false everywhere)
 * and the free-text brief the Cost agent prices. Ports of the pipeline-orchestrator adapters, kept
 * in the browser so each agent stays independently callable.
 */
import { LIMITS } from "../config.js";
import { clean, isEmail, splitList, truncate, unique } from "./text.js";
import { customerOf, fieldMap, opportunityId } from "./opportunities.js";

const MAX_ITEMS = 40;
const asText = (item) => clean(typeof item === "string" ? item : item?.text ?? item?.title ?? item?.question);

/* ------------------------------------------------------------------ upstream blocks */

export function metaBlock({ opportunity, templateId, agents }) {
  return {
    schema_version: "1.0",
    opportunity_id: opportunity ? opportunityId(opportunity) : "standalone",
    template_id: templateId,
    source_agents: agents,
    generated_by: "pipeline-runs-ui",
  };
}

export function intakeBlock(opportunity, fallbackCustomer = "") {
  const f = fieldMap(opportunity ?? {});
  const block = { customer_name: customerOf(opportunity ?? {}) };
  if (block.customer_name === "Unknown customer") block.customer_name = clean(fallbackCustomer) || "Customer";
  if (f.industry) block.industry = f.industry;
  if (f.contact_name) {
    block.contacts = [{ name: f.contact_name, ...(f.contact_role && { role: f.contact_role }), ...(isEmail(f.contact_email) && { email: f.contact_email }) }];
  }
  if (f.opportunity_type) block.engagement_type = f.opportunity_type;
  const problem = f.business_problem || opportunity?.summary;
  if (problem) block.problem_statement = clean(problem);
  const goals = splitList(f.desired_outcomes || f.success_criteria);
  if (goals.length) block.goals = goals.slice(0, MAX_ITEMS);
  if (f.timeline) block.timeline = f.timeline;
  if (f.budget) block.budget_range = f.budget;
  const constraints = unique([f.compliance, f.security_requirements, f.availability_dr, f.connectivity, f.out_of_scope && `Out of scope: ${f.out_of_scope}`].flatMap(splitList));
  if (constraints.length) block.constraints = constraints.slice(0, MAX_ITEMS);
  return block;
}

/**
 * @param handoff    Live Assist handoff from the first call (intake briefing + any real call)
 * @param followup   handoff from the second call, run on the Discovery report; its open questions become SOW open items
 */
export function liveAssistBlock(handoff, transcriptText = "", followup = null) {
  if (!handoff && !followup) return null;
  const first = handoff ?? {};
  const m = first.minutes?.minutes ?? {};
  const block = {
    source: "live_assist",
    findings: unique([...(first.captured ?? []).map((c) => `${c.label}: ${clean(c.value)}`), ...(m.key_points ?? []).map(asText)]).slice(0, MAX_ITEMS),
    requirements: unique([...(m.decisions ?? []).map(asText), ...(m.action_items ?? []).map(asText)]).slice(0, MAX_ITEMS),
    stakeholders: (m.attendees ?? []).map((a) => ({ name: clean(a?.name ?? a), ...(a?.role && { role: clean(a.role) }) })).filter((s) => s.name).slice(0, 20),
    transcript_refs: clean(transcriptText) ? [{ title: "Call transcript", text: String(transcriptText).slice(0, 20_000) }] : [],
  };
  const openQuestions = unique([...(followup?.suggested_questions ?? []), ...(first.suggested_questions ?? [])]).slice(0, 15);
  const gaps = unique([...(followup?.open_gaps ?? []), ...(first.open_gaps ?? [])].map((g) => g.label));
  const notes = [
    first.summary || first.problem_summary,
    gaps.length && `Open gaps after discovery: ${gaps.join("; ")}`,
    openQuestions.length && `Questions still to ask the customer: ${openQuestions.join(" | ")}`,
  ].filter(Boolean).join("\n");
  if (notes) block.notes = notes;
  return block;
}

export function discoveryBlock(report, narrative) {
  if (!report) return null;
  const heard = report.what_we_heard ?? {};
  return {
    source: "discovery",
    findings: unique([
      ...(heard.pain_points ?? []).map(asText),
      ...(heard.current_state ?? []).map(asText),
      ...(report.use_cases ?? []).map((u) => `${u.title} (${u.category}, ${u.confidence} confidence): ${clean(u.problem)}`),
      ...(report.top_hidden_opportunities ?? []).map((h) => `Hidden opportunity: ${h.title}. ${clean(h.why_it_matters)}`),
    ]).slice(0, MAX_ITEMS),
    requirements: unique([...(heard.goals ?? []).map(asText), ...(heard.constraints ?? []).map(asText)]).slice(0, MAX_ITEMS),
    stakeholders: (heard.stakeholders ?? []).map((s) => ({ name: clean(s.name), ...(s.role && { role: clean(s.role) }) })).filter((s) => s.name).slice(0, 20),
    transcript_refs: [],
    notes: truncate((narrative?.executive_summary ?? []).join("\n\n"), 4000) || null,
  };
}

/* ------------------------------------------------------------------ architecture / cost */

const components = (design) => (Array.isArray(design?.components) ? design.components.filter((c) => c && typeof c === "object") : []);
const zones = (design) => (Array.isArray(design?.availability_zones) ? design.availability_zones.map(clean).filter(Boolean) : design?.availability_zone ? [clean(design.availability_zone)] : []);

export function architectureBlock(design, diagramPngBase64) {
  const services = [];
  const seen = new Set();
  for (const c of components(design)) {
    if (["actor", "external"].includes(clean(c.category).toLowerCase())) continue;
    const name = clean(c.service) || clean(c.name);
    if (!name || seen.has(name.toLowerCase())) continue;
    seen.add(name.toLowerCase());
    services.push({ name, purpose: clean(c.purpose) || clean(c.role) });
  }
  const assumptions = (design?.assumptions ?? []).map(asText).filter(Boolean);
  const z = zones(design);
  if (z.length) assumptions.unshift(`Deployed across ${z.length} Availability Zone${z.length > 1 ? "s" : ""}: ${z.join(", ")}.`);
  const nfrs = (design?.requirements ?? []).filter((r) => ["non_functional", "constraint"].includes(clean(r?.kind))).map(asText).filter(Boolean);
  const block = { services: services.slice(0, 60), assumptions: assumptions.slice(0, 40), nfrs: nfrs.slice(0, 40) };
  // Base64 is ~4/3 of the binary size; skip an image that would push the request past the SOW API body cap.
  if (diagramPngBase64 && diagramPngBase64.length * 0.75 <= LIMITS.maxDiagramBytes) {
    block.diagram_images = [{ filename: "architecture.png", content_b64: diagramPngBase64 }];
  }
  return block;
}

const number = (v) => {
  if (v == null || typeof v === "boolean") return null;
  const n = typeof v === "number" ? v : Number.parseFloat(String(v).replace(/[^0-9.-]/g, ""));
  return Number.isFinite(n) ? n : null;
};

export function costBlock(cost) {
  const estimate = cost?.estimate && typeof cost.estimate === "object" ? cost.estimate : {};
  const items = [];
  for (const env of Object.values(estimate.environments ?? {})) {
    for (const s of env?.services ?? []) {
      const name = clean(s?.service ?? s?.name);
      if (!name) continue;
      const monthly = number(s.monthly_cost ?? s.monthly);
      items.push({ service: name, ...(monthly != null && { monthly }) });
    }
  }
  if (!items.length) {
    for (const s of estimate.line_items ?? []) {
      if (clean(s?.service)) items.push({ service: clean(s.service), ...(number(s.monthly) != null && { monthly: number(s.monthly) }) });
    }
  }
  const block = {
    currency: clean(estimate.currency) || "USD",
    source_link: clean(cost?.calculator_url ?? estimate.calculator_url) || null,
    line_items: items.slice(0, 60),
    assumptions: (estimate.assumptions ?? []).map(asText).filter(Boolean).slice(0, 40),
  };
  if (clean(estimate.region)) block.region = clean(estimate.region);
  const monthly = number(estimate.monthly_total ?? estimate.total_monthly_cost);
  const annual = number(estimate.annual_total ?? estimate.total_annual_cost);
  if (monthly != null) block.monthly_total = monthly;
  if (annual != null) block.annual_total = annual;
  else if (monthly != null) block.annual_total = Math.round(monthly * 12 * 100) / 100;
  return block;
}

/** Drops blocks that are empty so the SOW agent never sees an empty `architecture`/`cost` object. */
export function buildUpstream({ meta, intake, liveAssist, discovery, architecture, cost }) {
  const upstream = { meta, intake };
  if (liveAssist) upstream.live_assist = liveAssist;
  if (discovery) upstream.discovery = discovery;
  if (architecture && (architecture.services?.length || architecture.assumptions?.length || architecture.nfrs?.length)) upstream.architecture = architecture;
  if (cost && (cost.source_link || cost.line_items?.length || cost.monthly_total != null)) upstream.cost = cost;
  return upstream;
}

/** The prose the Cost agent prices, rendered from usage.json (quantities drive price) or the component list. */
export function costRequirements(design, usage) {
  const parts = [`Price the following AWS solution: ${clean(design?.title) || "AWS solution"}.`, "", `Region: ${clean(design?.region) || "us-east-1"}.`];
  const z = zones(design);
  if (z.length) {
    parts.push(
      `Deployment: ${z.length} Availability Zone${z.length > 1 ? "s" : ""} (${z.join(", ")}). ` +
        (z.length > 1
          ? "Price per-zone resources once per zone, include the Multi-AZ standby where a managed service has one, and include cross-AZ data transfer."
          : "Single zone: no standby instances and no cross-AZ transfer."),
    );
  }
  parts.push("");
  const lines = [];
  for (const entries of Object.values(usage?.services ?? {})) {
    if (!Array.isArray(entries)) continue;
    for (const e of entries) {
      if (!e || !clean(e.service)) continue;
      const figures = Object.entries(e)
        .filter(([k, v]) => !["service", "explanation", "assumed_fields"].includes(k) && v !== null && v !== "")
        .map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(", ") || "none" : typeof v === "boolean" ? (v ? "yes" : "no") : v}`)
        .join(", ");
      lines.push(figures ? `- ${clean(e.service)}: ${figures}` : `- ${clean(e.service)}`);
    }
  }
  if (lines.length) {
    parts.push("Monthly usage per service, already estimated. Use these figures as given:", ...lines);
  } else {
    const comps = components(design);
    if (!comps.length) throw new Error("The architecture has neither usage figures nor components to price.");
    parts.push("Services in the design, with their configuration:");
    for (const c of comps.slice(0, 60)) {
      if (!clean(c.service)) continue;
      const qty = Number(c.quantity) > 1 ? `, quantity ${c.quantity}` : "";
      parts.push(`- ${clean(c.service)}${clean(c.sku_summary) ? ` (${clean(c.sku_summary)})` : ""}${qty}`);
    }
  }
  const text = parts.join("\n").trim();
  if (text.length < LIMITS.costMinChars) throw new Error("The architecture is too thin to build a cost estimate from.");
  return text.slice(0, LIMITS.costMaxChars);
}

/* ------------------------------------------------------------------ cost link → SOW */

const PLACEHOLDER = /\[\s*cost calculator link\s*\]/i;
const CALCULATOR_URL = /https?:\/\/calculator\.aws\/[^\s)>\]]+/i;
const LINK_LINE = /^(\s*(?:AWS\s+)?Pricing\s+Calculator\s+estimate\s*:\s*).*$/im;
const PRICING_HEADING = /^(#{1,6})\s+(?:.*\b)?(pricing|commercials|fees|investment)\b.*$/im;

/** Writes the real calculator link into the SOW Pricing section. Returns { markdown, how }. */
export function applyCostLink(markdown, url) {
  const link = clean(url);
  if (!link || !markdown) return { markdown, how: "unchanged" };
  if (markdown.includes(link) && !PLACEHOLDER.test(markdown)) return { markdown, how: "unchanged" };
  if (PLACEHOLDER.test(markdown)) return { markdown: markdown.replace(new RegExp(PLACEHOLDER, "gi"), link), how: "placeholder" };

  const heading = PRICING_HEADING.exec(markdown);
  const start = heading ? heading.index + heading[0].length : 0;
  const next = /^#{1,6}\s/m.exec(markdown.slice(start));
  const end = next ? start + next.index : markdown.length;
  let section = markdown.slice(start, end);

  if (LINK_LINE.test(section)) {
    section = section.replace(LINK_LINE, (_, lead) => `${lead}${link}`);
    return { markdown: markdown.slice(0, start) + section + markdown.slice(end), how: "replaced" };
  }
  if (CALCULATOR_URL.test(section)) {
    section = section.replace(CALCULATOR_URL, link);
    return { markdown: markdown.slice(0, start) + section + markdown.slice(end), how: "replaced" };
  }
  const line = `\n\nRefer to the cost estimate below, which is the authoritative source for the AWS run-rate:\n\nAWS Pricing Calculator estimate: ${link}\n`;
  if (heading) return { markdown: markdown.slice(0, end).replace(/\n+$/, "") + line + markdown.slice(end), how: "inserted" };
  return { markdown: `${markdown.replace(/\n+$/, "")}\n\n# Pricing${line}`, how: "appended" };
}

/** Counts findings by severity from a WA review so the UI can show a one-line verdict. */
export function reviewSummary(review) {
  const findings = review?.findings ?? [];
  const count = (sev) => findings.filter((f) => f.severity === sev).length;
  return { total: review?.summary?.total ?? findings.length, high: review?.summary?.high ?? count("high"), medium: review?.summary?.medium ?? count("medium"), low: review?.summary?.low ?? count("low") };
}
