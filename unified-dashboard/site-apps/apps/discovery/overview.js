// Engagement overview: the header strip and "what has been done" above the report; stakeholders and the call
// timeline below it. Data: GET /runs/{id}/overview (Live Assist context + the finished report).
(() => {
  const { el, fill, usd, day, safeLink } = window.DiscoveryDom;
  const SIDES = [["ingram", "Ingram Micro"], ["partner", "Partner"], ["customer", "Customer"], ["unplaced", "Mentioned, side unknown"]];
  const FUNDING = { decided: "decided", needs_info: "needs more information", to_confirm: "to be confirmed", not_covered: "no program covers it" };

  const tile = (label, value, note) => el("div", { class: "tile" },
    el("span", { class: "tile-label" }, label),
    value || el("span", { class: "muted" }, "Not available"),
    note ? el("span", { class: "tile-note muted" }, note) : null);

  function header(h) {
    const program = h.aws_program ? `${h.aws_program}${h.funding_status ? ` (${FUNDING[h.funding_status] || h.funding_status})` : ""}` : "";
    return el("div", { class: "tiles" },
      tile("Customer", h.customer ? el("strong", {}, h.customer) : null, h.opportunity),
      tile("Seller", h.seller ? el("strong", {}, h.seller) : null),
      tile("Opportunity size", h.opportunity_size_usd ? el("strong", {}, usd(h.opportunity_size_usd)) : null, h.opportunity_size_basis),
      tile("CRM", safeLink(h.crm_url, "Open the CRM record ↗", "intake-link")),
      tile("AWS program", program ? el("strong", {}, program) : null,
        h.estimated_funding_usd ? `Estimated funding ${usd(h.estimated_funding_usd)}` : ""));
  }

  function summary(lines) {
    if (!lines.length) return null;
    return el("div", { class: "overview-block" }, el("h4", {}, "What has been done"),
      el("ul", { class: "plain" }, lines.map((l) => el("li", {}, l))));
  }

  function people(stakeholders) {
    const columns = SIDES.filter(([side]) => (stakeholders[side] || []).length).map(([side, label]) =>
      el("div", { class: "people-col" }, el("h4", {}, label),
        el("ul", { class: "plain" }, stakeholders[side].map((p) => el("li", {},
          el("strong", {}, p.name), p.role ? el("span", { class: "muted" }, ` · ${p.role}`) : null,
          p.sources && p.sources.length ? el("span", { class: "muted small" }, ` · from ${p.sources.join(", ")}`) : null)))));
    return el("div", { class: "overview-block" }, el("h3", {}, "Stakeholders"),
      columns.length ? el("div", { class: "people" }, columns)
        : el("p", { class: "muted" }, "No stakeholders yet. Names appear here from the calls and the report."));
  }

  function timeline(items) {
    return el("div", { class: "overview-block" }, el("h3", {}, "Call timeline"),
      items.length ? el("ol", { class: "timeline" }, items.map((c) => el("li", {},
        el("span", { class: "timeline-date" }, c.date ? day(c.date) : "Date not recorded"),
        el("div", {}, el("strong", {}, c.title), c.summary ? el("p", { class: "muted" }, c.summary) : null))))
        : el("p", { class: "muted" }, "No calls recorded yet."));
  }

  function render(headEl, detailEl, data) {
    const hasHeader = data && Object.keys(data.header || {}).length;
    fill(headEl, hasHeader ? header(data.header) : el("p", { class: "muted" },
      "Customer, seller, opportunity size, CRM link and AWS program appear here when the run is started from a Live Assist opportunity."),
    data && summary(data.summary || []));
    headEl.hidden = !data;
    fill(detailEl, data && people(data.stakeholders || {}), data && timeline(data.timeline || []));
    detailEl.hidden = !data;
  }

  window.DiscoveryOverview = { render };
})();
