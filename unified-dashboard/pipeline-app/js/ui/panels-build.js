/** SOW, Architecture and Cost panels. */
import { reviewSummary } from "../domain/sow-inputs.js";
import { badge, button, empty, h, list, section, table } from "./dom.js";
import { downloadBlob } from "../core/files.js";
import { renderMarkdown } from "./markdown.js";

const SEVERITY_TONE = { high: "danger", critical: "danger", medium: "warning", low: "neutral", required: "danger" };

const findingText = (f) => [f.title ?? f.name ?? f.id, f.detail ?? f.description ?? f.message, f.recommendation].filter(Boolean).join(" — ");

function reviewBlock(title, review) {
  if (!review) return null;
  const s = reviewSummary(review);
  const findings = review.findings ?? [];
  return section(
    title,
    h("p", {}, badge(`${s.high} high`, s.high ? "danger" : "success"), " ", badge(`${s.medium} medium`, s.medium ? "warning" : "neutral"), " ", badge(`${s.low} low`, "neutral")),
    findings.length ? table([{ label: "Severity", cell: (f) => badge(f.severity ?? "info", SEVERITY_TONE[f.severity] ?? "neutral") }, { label: "Finding", cell: findingText }], findings) : h("p", { class: "muted" }, "No findings."),
  );
}

function checkBlock(check) {
  if (!check) return null;
  const rules = check.rules ?? [];
  const failed = rules.filter((r) => r.status === "fail");
  return section(
    "SOW rule check",
    h("p", {}, badge(`${check.summary?.passed ?? rules.length - failed.length}/${check.summary?.total ?? rules.length} passed`, failed.length ? "warning" : "success")),
    failed.length ? table([{ label: "Severity", cell: (r) => badge(r.severity ?? "info", SEVERITY_TONE[r.severity] ?? "neutral") }, { label: "Rule", cell: (r) => r.name ?? r.id }], failed) : null,
    (check.issues ?? []).length ? list(check.issues.map((i) => (typeof i === "string" ? i : findingText(i)))) : null,
  );
}

const sowView = (title, markdown) => h("details", { class: "doc", open: false }, h("summary", {}, title), renderMarkdown(markdown));

export function renderSow(state) {
  const { draft, waDraft, combined, waFinal, check, docx } = state.sow;
  if (!draft) return empty("The SOW agent has not run. It drafts from Intake, Live Assist and Discovery, reviews the draft against the Well-Architected Framework, then is run again with the architecture and cost.");
  const final = combined ?? draft;
  return h("div", {},
    h("div", { class: "toolbar-actions" },
      button("Download Markdown", { onClick: () => downloadBlob("statement-of-work.md", final.markdown, "text/markdown") }),
      docx?.download_url ? h("a", { class: "btn btn-primary", href: docx.download_url, rel: "noopener noreferrer" }, `Download ${docx.filename || "Word document"}`) : null),
    h("p", { class: "muted" }, combined ? "Showing the combined SOW (architecture and cost included)." : "Showing the first draft."),
    sowView(combined ? "Combined SOW" : "SOW draft", final.markdown),
    combined ? sowView("First draft (before architecture and cost)", draft.markdown) : null,
    reviewBlock("Well-Architected review of the draft", waDraft),
    reviewBlock("Well-Architected review of the final SOW", waFinal),
    checkBlock(check));
}

export function renderArchitecture(state) {
  const r = state.architecture.result;
  if (!r) return empty("The Architecture agent has not run. It turns the SOW into a design, a decision record and a diagram.");
  const comps = (r.design.components ?? []).filter((c) => c && c.service);
  return h("div", {},
    h("p", { class: "muted" }, [r.design.title, r.design.region, r.status === "partial" ? "partial result" : null].filter(Boolean).join(" · ")),
    r.diagramBase64 ? section("Diagram", h("img", { class: "diagram", alt: `Architecture diagram for ${r.design.title ?? "the solution"}`, src: `data:image/png;base64,${r.diagramBase64}` })) : h("p", { class: "notice warn" }, "No diagram was produced."),
    comps.length ? section("Components", table([{ label: "Service", cell: (c) => c.service }, { label: "Name", cell: (c) => c.name ?? "" }, { label: "Role", cell: (c) => c.purpose ?? c.role ?? "" }], comps)) : null,
    r.adr ? h("details", { class: "doc" }, h("summary", {}, "Architecture decision record"), renderMarkdown(r.adr)) : null,
    r.usageAssumptions ? h("details", { class: "doc" }, h("summary", {}, "Usage assumptions"), renderMarkdown(r.usageAssumptions)) : null);
}

export function renderCost(state) {
  const r = state.cost.result;
  if (!r) return empty("The Cost agent has not run. It prices the architecture and returns an AWS Pricing Calculator estimate.");
  const rows = Object.values(r.estimate?.environments ?? {}).flatMap((e) => e?.services ?? []);
  return h("div", {},
    section("AWS Pricing Calculator estimate", h("p", {}, h("a", { href: r.calculator_url, target: "_blank", rel: "noopener noreferrer" }, r.estimate_name || r.calculator_url)), h("p", { class: "muted" }, `${r.services_count ?? rows.length} services priced`)),
    rows.length ? table([{ label: "Service", cell: (s) => s.service ?? s.name }, { label: "Monthly", class: "num", cell: (s) => String(s.monthly_cost ?? s.monthly ?? "") }], rows) : null,
    r.coverage ? h("details", { class: "doc" }, h("summary", {}, "Coverage"), h("pre", { class: "md-pre" }, JSON.stringify(r.coverage, null, 2))) : null);
}
