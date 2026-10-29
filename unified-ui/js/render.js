// Renderers. Every agent value is inserted as a text node (never innerHTML); markdown goes through the safe renderer.
import { el, fill } from "./util.js";
import { customerOf, tocOf } from "./context.js";

const empty = (text) => el("div", { class: "empty" }, text);
const md = (text, opts) => window.renderMarkdown(text || "", opts);
const chip = (t, cls = "") => el("span", { class: `chip ${cls}` }, t);

export function renderOpps(root, opps, selected, onSelect) {
  if (!opps.length) return fill(root, empty("No opportunities yet. Run the Intake agent."));
  const rows = opps.map((o) => el("tr", { class: o === selected ? "sel" : "", tabindex: "0", role: "button", "aria-pressed": o === selected ? "true" : "false",
    onclick: () => onSelect(o), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(o); } } },
    el("td", {}, customerOf(o)), el("td", {}, o.opportunity_title || o.summary || ""),
    el("td", {}, chip(o.urgency || "unknown", o.urgency === "high" ? "high" : "")),
    el("td", {}, o.confidence ?? ""), el("td", {}, `${o.verified_count ?? 0}/${o.field_count ?? 0}`),
    el("td", {}, (o.gaps || []).length), el("td", {}, ["MAP", "MPE"].map((p) => o.programme_fit?.[p] && o.programme_fit[p].fit !== "not_applicable" ? chip(`${p}: ${o.programme_fit[p].fit}`, o.programme_fit[p].fit) : null)),
    el("td", {}, (o.source_types || []).join(", "))));
  const detail = selected && el("div", { class: "detail" },
    el("h3", {}, `${customerOf(selected)}: facts`),
    el("div", {}, (selected.fields || []).filter((f) => String(f.value ?? "").trim()).map((f) => el("div", {}, el("b", {}, `${f.label || f.field}: `), String(f.value)))),
    (selected.gaps || []).length ? [el("h3", {}, "Gaps"), ...selected.gaps.map((g) => el("div", {}, el("b", {}, `${g.label || g.field}: `), g.question || g.why))] : null,
    (selected.conflicts || []).length ? [el("h3", {}, "Conflicts"), ...selected.conflicts.map((c) => el("div", {}, el("b", {}, `${c.label || c.field}: `), c.detail))] : null);
  fill(root, el("div", { class: "tablewrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Customer", "Opportunity", "Urgency", "Confidence", "Facts verified", "Gaps", "Funding fit", "Source"].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows))), detail);
}

export function renderQuestions(root, la, label) {
  if (!la) return fill(root, empty("No questions yet."));
  fill(root, el("div", { class: "hint" }, label), el("ol", {}, la.questions.map((q) => el("li", {}, el("b", {}, q.question), q.why ? el("div", { class: "hint" }, `${q.topic_label ? q.topic_label + ": " : ""}${q.why}`) : null))));
}

export function renderDiscovery(root, d) {
  if (!d) return fill(root, empty("No discovery report yet."));
  const r = d.report || {};
  const list = (title, items) => items.length ? [el("h3", {}, title), el("ul", {}, items.map((t) => el("li", {}, t)))] : null;
  fill(root, el("div", { class: "detail" },
    list("Executive summary", d.narrative?.executive_summary || []),
    list("What we heard", (r.what_we_heard?.pain_points || []).map((p) => p.text)),
    list("Use cases", (r.use_cases || []).map((u) => `${u.title}${u.confidence ? ` (${u.confidence})` : ""}: ${u.problem || ""}`)),
    list("Hidden opportunities", (r.top_hidden_opportunities || []).map((h) => `${h.title}: ${h.why_it_matters || ""}`)),
    list("Open questions", (r.gaps_and_open_questions || []).map((g) => g.question))));
}

export function renderSow(root, checksRoot, sow, { diagramB64 = null, calcUrl = "" } = {}) {
  if (!sow?.markdown) { fill(root, empty("No SOW yet.")); return fill(checksRoot); }
  const resolveImage = (t) => (diagramB64 && /^arch:1$/.test(t) ? `data:image/png;base64,${diagramB64}` : null);
  const toc = tocOf(sow.markdown);
  fill(root,
    toc.length ? el("nav", { class: "toc", "aria-label": "Table of contents" }, el("h3", {}, "Table of contents"),
      el("ol", {}, toc.map((h) => el("li", { class: `l${h.level}` }, h.text)))) : null,
    calcUrl ? el("p", { class: "calc" }, el("b", {}, "AWS Pricing Calculator estimate: "), el("a", { href: calcUrl, target: "_blank", rel: "noopener noreferrer" }, calcUrl)) : null,
    md(sow.markdown, { lineBreaks: true, resolveImage }));
  const c = sow.check;
  fill(checksRoot, c ? el("div", { class: "detail" }, el("b", {}, `Checks: ${c.summary?.passed ?? "?"}/${c.summary?.total ?? "?"} passed`),
    (c.issues || []).slice(0, 12).map((i) => el("div", { class: `finding ${i.severity || ""}` }, `${i.severity ? i.severity.toUpperCase() + ": " : ""}${i.label || i.message || i.detail || ""}`))) : null);
}

export function renderArch(root, arch) {
  if (!arch) return fill(root, empty("No architecture yet."));
  fill(root, arch.diagramB64 ? el("img", { class: "diagram", alt: "Architecture diagram", src: `data:image/png;base64,${arch.diagramB64}` }) : null,
    arch.adr ? el("div", { class: "md" }, md(arch.adr)) : null,
    !arch.adr && !arch.diagramB64 ? empty("The run finished but returned no files.") : null);
}

export function renderCost(root, cost) {
  if (!cost) return fill(root, empty("No estimate yet."));
  const envs = Object.entries(cost.environments || {});
  fill(root,
    cost.calculatorUrl ? el("p", {}, el("a", { href: cost.calculatorUrl, target: "_blank", rel: "noopener noreferrer" }, "Open the estimate in the AWS Pricing Calculator")) : el("p", { class: "hint" }, "No calculator link was returned."),
    envs.map(([name, env]) => [el("h3", {}, name), el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Service", "Description"].map((h) => el("th", {}, h)))),
      el("tbody", {}, (env.services || []).map((s) => el("tr", {}, el("td", {}, s.name || s.service || ""), el("td", {}, s.description || "")))))) ]),
    (cost.unsupported || []).length ? el("p", { class: "hint" }, `Not priced: ${cost.unsupported.map((u) => u.name || u).join(", ")}`) : null);
}

const ICON = { pending: "·", running: "…", done: "✓", failed: "!", skipped: "–", blocked: "⏸" };
export function renderSteps(root, steps, defs) {
  fill(root, defs.map((d, i) => {
    const s = steps[d.id] || { status: "pending", detail: "" };
    return el("li", { class: `s-${s.status}` }, el("span", { class: "dot", "aria-hidden": "true" }, ICON[s.status] ?? i + 1),
      el("div", {}, el("b", {}, `${i + 1}. ${d.label}`), el("small", {}, d.note), s.detail ? el("small", { class: "d" }, `${s.status}: ${s.detail}`) : el("small", { class: "d" }, s.status)));
  }));
}

const LEVEL = { info: "INFO", warn: "WARNING", error: "ERROR" };
export function renderLog(root, log) {
  fill(root, log.map((l) => el("div", { class: `row-log ${l.level}` },
    el("time", { datetime: l.at.toISOString() }, l.at.toLocaleTimeString([], { hour12: false })),
    el("span", { class: "lvl" }, LEVEL[l.level] || "INFO"), el("span", { class: "stg" }, l.stage || "Pipeline"), el("span", { class: "msg" }, l.msg))));
  root.scrollTop = root.scrollHeight;
}
export const logText = (log) => log.map((l) => `${l.at.toISOString()}  ${(LEVEL[l.level] || "INFO").padEnd(7)}  ${(l.stage || "Pipeline").padEnd(22)}  ${l.msg}`).join("\n");
