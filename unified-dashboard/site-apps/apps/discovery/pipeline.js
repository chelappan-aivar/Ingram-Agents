// The Fetch panel: one expandable card per opportunity, Intake output on the left, Discovery output on the right.
// Data: GET /pipeline/overview -> rows [{intake, discovery}].
(() => {
  const { el, fill, usd, day, safeLink } = window.DiscoveryDom;
  const DECISION = { decided: "Decided", needs_info: "Needs info", to_confirm: "To confirm", not_covered: "Not covered" };
  const STATE = { starting: "Discovery starting", running: "Discovery working", awaiting_clarification: "Discovery needs input",
    succeeded: "Discovery report ready", failed: "Discovery failed" };

  const list = (items) => (items && items.length ? el("ul", {}, items.map((t) => el("li", {}, t))) : null);
  const block = (title, ...kids) => el("div", {}, el("h4", {}, title), kids);
  const muted = (text) => el("p", { class: "muted" }, text);

  function intakeColumn(o) {
    const d = o.decision || {};
    return el("div", {},
      el("h3", {}, "Intake Agent"),
      block("Funding decision",
        el("p", {}, el("strong", {}, d.program || DECISION[d.status] || "Not decided"),
          d.status && d.program ? ` (${(DECISION[d.status] || d.status).toLowerCase()})` : ""),
        d.total_usd ? el("p", {}, `Estimated funding ${usd(d.total_usd)}`) : null,
        d.rationale ? el("p", { class: "muted" }, d.rationale) : null),
      o.summary ? block("Summary", el("p", {}, o.summary)) : null,
      block("Opportunity",
        el("p", {}, `Customer: ${o.customer || "not available"}`),
        el("p", {}, `Seller: ${o.seller || "not available"}`),
        o.estimated_value_usd ? el("p", {}, `Size: ${usd(o.estimated_value_usd)}`) : null,
        safeLink(o.crm_url, "Open the CRM record ↗", "intake-link"),
        el("p", { class: "muted" }, `${o.call_count || 0} Live Assist call${o.call_count === 1 ? "" : "s"}` +
          (o.checked_at ? ` · funding checked ${day(o.checked_at)}` : ""))));
  }

  function discoveryColumn(o, d, hooks) {
    const head = el("h3", {}, "Discovery Agent");
    if (!d) {
      const run = el("button", { type: "button", class: "btn sm primary" }, "Run Discovery");
      run.addEventListener("click", () => hooks.onRun(o, run));
      return el("div", {}, head, muted(o.call_count ? "No Discovery run for this opportunity yet."
        : "No Discovery run yet. It will read the Intake record only until a Live Assist call has ended."), run);
    }
    const s = d.summary;
    const open = el("button", { type: "button", class: "btn sm" }, "Open the full report");
    open.addEventListener("click", () => hooks.onOpen(d.engagement, d.run_id));
    if (!s) return el("div", {}, head, muted(`${STATE[d.state] || d.state} · started ${day(d.started_at)}`), open);
    return el("div", {}, head,
      block("Executive summary", s.executive_summary.length ? s.executive_summary.map((p) => el("p", {}, p))
        : muted("No narrative was written for this run.")),
      el("p", { class: "muted" }, `${s.use_case_count} use cases · ${s.hidden_opportunities.length} hidden opportunities · ` +
        `${s.open_question_count} open questions · generated ${day(s.generated_at)}`),
      s.use_cases.length ? block("Use cases", list(s.use_cases.slice(0, 5).map((u) =>
        `${u.title} (${String(u.category || "").toLowerCase()}, ${u.confidence} confidence)`))) : null,
      s.hidden_opportunities.length ? block("Hidden opportunities", list(s.hidden_opportunities.slice(0, 5).map((h) => h.title))) : null,
      s.pain_points.length ? block("Pain points", list(s.pain_points.slice(0, 5))) : null,
      s.open_questions.length ? block("Open questions", list(s.open_questions.slice(0, 5))) : null,
      open);
  }

  function card({ intake: o, discovery: d }, hooks) {
    const decision = o.decision || {};
    return el("li", {}, el("details", { class: `output${o.intake_opportunity_id === hooks.focus ? " focus" : ""}`, open: o.intake_opportunity_id === hooks.focus },
      el("summary", {},
        el("span", { class: "card-title" }, o.name || "Opportunity"),
        el("span", { class: "pill" }, decision.program || DECISION[decision.status] || "Not checked"),
        el("span", { class: "muted" }, o.customer || ""),
        el("span", { class: `pill s-${d ? d.state : "none"}` }, d ? STATE[d.state] || d.state : "No Discovery run")),
      el("div", { class: "output-body" }, intakeColumn(o), discoveryColumn(o, d, hooks))));
  }

  function render(node, rows, hooks) {
    const sorted = rows.slice().sort((a, b) => (b.intake.intake_opportunity_id === hooks.focus) - (a.intake.intake_opportunity_id === hooks.focus));
    fill(node, sorted.length ? sorted.map((r) => card(r, hooks)) : el("li", { class: "empty muted" }, "No funding-checked opportunities yet."));
  }

  window.DiscoveryPipeline = { render };
})();
