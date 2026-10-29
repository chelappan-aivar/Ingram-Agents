// The discovery report on the page: executive summary, earlier-agent context, hidden opportunities, use cases,
// pain points, open questions. Every point lists the documents it comes from and the quotes behind it.
// Data: GET /runs/{id}/report -> {report, narrative, context}.
(() => {
  const { el, fill, usd } = window.DiscoveryDom;
  const HYPOTHESIS = "Hypothesis, not confirmed";

  function labelsOf(report) {
    return Object.fromEntries(((report.meta || {}).sources || []).map((s) => [s.id, s.label || s.id]));
  }

  /** "Sources: Oct 20, Oct 22" with the verbatim quotes behind the point one click away. */
  function sources(evidence, labels) {
    const list = evidence || [];
    if (!list.length) return null;
    const names = [...new Set(list.map((e) => labels[e.source] || e.source))];
    return el("details", { class: "sources" },
      el("summary", {}, `Sources: ${names.join(", ")}`),
      el("ul", { class: "plain" }, list.map((e) => el("li", {},
        el("q", {}, e.quote),
        el("span", { class: "muted small" }, ` ${e.speaker ? `${e.speaker}, ` : ""}${labels[e.source] || e.source}`)))));
  }

  const section = (title, ...kids) => el("section", { class: "report-section" }, el("h3", {}, title), kids);
  const pill = (text, cls = "") => el("span", { class: `pill ${cls}` }, text);
  const empty = (text) => el("p", { class: "muted" }, text);

  function executive(narrative) {
    const paras = (narrative && narrative.executive_summary) || [];
    return section("Executive summary", paras.length ? paras.map((p) => el("p", {}, p))
      : empty("No narrative was written for this run."));
  }

  function earlier(context) {
    const h = (context && context.header) || {};
    const rows = [["AWS program selected", h.aws_program ? `${h.aws_program}${h.funding_status ? `, ${h.funding_status}` : ""}` : ""],
      ["Estimated AWS funding", h.estimated_funding_usd ? usd(h.estimated_funding_usd) : ""],
      ["Why this program", h.funding_rationale || ""],
      ["Live Assist calls", (context && context.calls && context.calls.length) ? String(context.calls.length) : ""]]
      .filter(([, v]) => v);
    if (!rows.length) return null;
    return section("From earlier agents",
      el("dl", { class: "facts" }, rows.map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
      el("p", { class: "muted small" }, "Source: Intake Agent record and Live Assist calls."));
  }

  function hidden(report, narrative, labels) {
    const byId = Object.fromEntries((report.use_cases || []).map((u) => [u.id, u]));
    const items = report.top_hidden_opportunities || [];
    return section("Hidden opportunities", items.length ? el("ol", { class: "report-list" }, items.map((t) => {
      const uc = byId[t.use_case_id] || {};
      const why = ((narrative || {}).opportunity_narratives || {})[t.use_case_id] || t.why_it_matters;
      return el("li", {}, el("strong", {}, t.title), " ", pill(HYPOTHESIS, "t-gap"), el("p", {}, why), sources(uc.evidence, labels));
    })) : empty("No hidden opportunities with enough evidence."));
  }

  function useCases(report, narrative, labels) {
    const summaries = (narrative || {}).use_case_summaries || {};
    const ucs = (report.use_cases || []).slice().sort((a, b) => a.rank - b.rank);
    return section("Use cases", ucs.length ? el("ol", { class: "report-list" }, ucs.map((u) => {
      const inferred = u.category !== "EXPLICIT";
      return el("li", {},
        el("strong", {}, u.title), " ", pill(String(u.category).toLowerCase()), pill(`${u.confidence} confidence`),
        inferred ? pill(HYPOTHESIS, "t-gap") : null,
        summaries[u.id] ? el("p", {}, summaries[u.id]) : null,
        el("p", {}, el("b", {}, "Problem: "), u.problem),
        u.customer_value ? el("p", {}, el("b", {}, "Value for the customer: "), u.customer_value) : null,
        u.company_value ? el("p", {}, el("b", {}, "Value for us: "), u.company_value) : null,
        inferred && u.why_hidden ? el("p", {}, el("b", {}, "Why it is hidden: "), u.why_hidden) : null,
        u.validation_question ? el("p", {}, el("b", {}, "Ask on the next call: "), u.validation_question) : null,
        sources(u.evidence, labels));
    })) : empty("No use cases found."));
  }

  function painPoints(report, labels) {
    const items = ((report.what_we_heard || {}).pain_points) || [];
    return section("Pain points", items.length ? el("ul", { class: "report-list" }, items.map((p) => el("li", {},
      p.text, " ", p.tag === "stated" ? null : pill(HYPOTHESIS, "t-gap"), sources(p.evidence, labels))))
      : empty("No pain points found in the sources."));
  }

  function openQuestions(report, labels) {
    const open = (report.gaps_and_open_questions || []).filter((q) => !["resolved", "needs_confirmation"].includes(q.status));
    return section("Open questions", open.length ? el("ul", { class: "report-list" }, open.map((q) => el("li", {},
      el("strong", {}, q.question), el("p", { class: "muted" }, q.reason), sources(q.evidence, labels))))
      : empty("None. All questions were addressed during review."));
  }

  function render(node, data) {
    if (!data) { fill(node); return; }
    const { report, narrative, context } = data;
    const labels = labelsOf(report);
    fill(node, executive(narrative), earlier(context), hidden(report, narrative, labels),
      useCases(report, narrative, labels), painPoints(report, labels), openQuestions(report, labels));
  }

  window.DiscoveryReport = { render };
})();
