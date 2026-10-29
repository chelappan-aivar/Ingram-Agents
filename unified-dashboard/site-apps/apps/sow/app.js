/* Ingram Pipeline console: start a run (or load a sample), watch the six stages, act on a stop.
 *
 * The stage order comes from GET /session, so the page cannot drift from the order the backend enforces.
 * No innerHTML anywhere: every value from the API is set as text, and a link is only made clickable when it
 * points at the AWS Pricing Calculator.
 */
(() => {
  "use strict";

  const API = String((window.PIPELINE_CONFIG || {}).apiUrl || "").replace(/\/$/, "");
  const POLL_MS = 4000;
  const SETTLED = ["completed", "failed", "blocked"];
  const templates = Array.isArray(window.PIPELINE_TEMPLATES) ? window.PIPELINE_TEMPLATES : [];
  const samples = Array.isArray(window.PIPELINE_SAMPLES) ? window.PIPELINE_SAMPLES : [];
  const PILLAR_LABELS = {
    operational_excellence: "Operational Excellence", security: "Security", reliability: "Reliability",
    performance_efficiency: "Performance Efficiency", cost_optimization: "Cost Optimization", sustainability: "Sustainability",
  };

  const state = {
    runId: null, timer: null, stages: [], pipeline: null, sowSig: "", busy: false,
    // The architecture tab: the PNG (null = not loaded, "" = unavailable), the design document (undefined = not
    // loaded, null = unavailable), the version they were fetched for, and a signature so the view is only rebuilt
    // when something changed (a rebuild would collapse the ADR the reader just opened).
    diagram: null, archDoc: undefined, archFor: null, archRev: 0, archSig: "",
    reviseAutoOpened: false, reviseWorking: false,
  };
  const REVISE_TARGETS = {
    sow: { label: "SOW", placeholder: "e.g. Add a section on staff training and move go-live to week 12.",
      hint: "Edits the SOW text. The review, architecture, cost estimate and final review then run again." },
    architecture: { label: "Architecture design", placeholder: "e.g. Put AWS WAF in front of the API and spread the database across three Availability Zones.",
      hint: "Revises the design, re-estimates usage and redraws the diagram. The cost estimate and final review then run again." },
    cost: { label: "Cost estimate", placeholder: "e.g. Price compute with a 1-year Savings Plan and assume traffic doubles in year two.",
      hint: "Prices the current architecture again with your adjustment. To add or remove a service, change the architecture instead." },
  };
  const TAB_TARGET = { sow: "sow", arch: "architecture", cost: "cost" };

  // ---------------------------------------------------------------- helpers
  const $ = (id) => document.getElementById(id);
  const fill = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false && k !== ""));
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === false || v === null || v === undefined) continue;
      if (k === "class") node.className = v; else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat(Infinity)) {
      if (child !== null && child !== undefined && child !== "" && child !== false) {
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
      }
    }
    return node;
  }
  const templateLabel = (id) => (templates.find((t) => t.id === id) || {}).label || id || "";
  const fmtTime = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "" : d.toLocaleString(); };
  const isCalculatorUrl = (url) => /^https:\/\/calculator\.aws\//i.test(String(url || ""));

  // ---------------------------------------------------------------- theme (same toggle as the other Ingram consoles)
  const THEMES = [
    ["light", "Light theme", "M12 4V2M12 22v-2M4 12H2M22 12h-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"],
    ["dark", "Dark theme", "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"],
    ["system", "System theme", "M3 4h18v12H3zM8 20h8M12 16v4"],
  ];
  const systemDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : { matches: true, addEventListener() {} };
  function applyTheme(choice) {
    try { localStorage.setItem("pipelineTheme", choice); } catch { /* private mode */ }
    document.documentElement.dataset.theme = choice === "system" ? (systemDark.matches ? "dark" : "light") : choice;
    document.querySelectorAll("[data-theme-choice]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeChoice === choice)));
  }
  document.querySelectorAll("[data-theme-toggle]").forEach((group) => {
    for (const [choice, label, path] of THEMES) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("fill", "none"); svg.setAttribute("stroke", "currentColor");
      svg.setAttribute("stroke-width", "2"); svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round");
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", path); svg.append(p);
      const btn = el("button", { type: "button", "data-theme-choice": choice, "aria-label": label, title: label }, svg);
      btn.addEventListener("click", () => applyTheme(choice));
      group.append(btn);
    }
  });
  systemDark.addEventListener("change", () => { if (localStorage.getItem("pipelineTheme") === "system") applyTheme("system"); });
  applyTheme((() => { try { return localStorage.getItem("pipelineTheme") || "dark"; } catch { return "dark"; } })());

  // ---------------------------------------------------------------- API
  async function api(path, options = {}) {
    if (!API) throw new Error("The API address is not configured (config.js).");
    let response;
    try {
      response = await fetch(`${API}${path}`, { headers: { "content-type": "application/json" }, ...options });
    } catch {
      throw new Error("Could not reach the API. Check your connection, or that this site's address is allowed by the API's CORS settings.");
    }
    let body = {};
    try { body = await response.json(); } catch { /* an empty body is fine */ }
    if (!response.ok) throw new Error(body.error || `The request failed (HTTP ${response.status}).`);
    return body;
  }
  const staticText = async (path) => {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`${path.split("/").pop()}: HTTP ${response.status}`);
    return response.text();
  };

  async function loadSession() {
    try {
      const s = await api("/session");
      state.stages = s.stages || [];
      $("quota").textContent = `${s.runs_today} of ${s.runs_per_day} runs used today`;
    } catch (err) {
      $("quota").textContent = err.message;
      $("quota").classList.add("error");
    }
  }

  // ---------------------------------------------------------------- the form
  function fillTemplates() {
    const select = $("template");
    fill(select, templates.length
      ? templates.map((t) => el("option", { value: t.id }, t.label))
      : el("option", { value: "" }, "No templates available"));
    if (templates.length) select.value = templates.find((t) => t.id === "aiml/ai-ml") ? "aiml/ai-ml" : templates[0].id;
  }

  function formStatus(message, isError = false) {
    const node = $("form-status");
    node.textContent = message || "";
    node.className = `status-line ${isError ? "error" : "muted"}`;
  }

  /** The request body, or an error message. Builds a minimal handoff when no intake JSON is given. */
  function buildRequest() {
    const customer = $("customer").value.trim();
    const opportunity = $("opportunity").value.trim();
    const templateId = $("template").value;
    if (!templateId) return { error: "Choose a template." };

    let upstream;
    const raw = $("upstream").value.trim();
    if (raw) {
      try { upstream = JSON.parse(raw); } catch (err) { return { error: `Intake data is not valid JSON: ${err.message}` }; }
      if (!upstream || typeof upstream !== "object" || Array.isArray(upstream)) return { error: "Intake data must be a JSON object." };
      upstream.meta = { schema_version: "1.0", ...(upstream.meta || {}), template_id: templateId };
      upstream.intake = { ...(upstream.intake || {}) };
      if (customer) upstream.intake.customer_name = customer;
      if (opportunity) upstream.meta.opportunity_id = opportunity;
      upstream.meta.opportunity_id = upstream.meta.opportunity_id || "OPP-PIPELINE";
    } else {
      if (!customer) return { error: "Enter the customer name." };
      upstream = {
        meta: { schema_version: "1.0", opportunity_id: opportunity || "OPP-PIPELINE", template_id: templateId },
        intake: { customer_name: customer },
      };
    }
    if (!String((upstream.intake || {}).customer_name || "").trim()) return { error: "Enter the customer name." };

    delete upstream.architecture; // produced by this pipeline, never an input to it
    delete upstream.cost;
    const body = { template_id: templateId, upstream, stop_on_blocked: $("stop-on-blocked").checked };
    const details = $("details").value.trim();
    const sow = $("sow").value.trim();
    if (details) body.project_details = details;
    if (sow) body.sow_markdown = sow;
    body.title = upstream.intake.customer_name;
    return { body };
  }

  $("run-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.busy) return;
    const { body, error } = buildRequest();
    if (error) { formStatus(error, true); return; }
    state.busy = true; $("start").disabled = true; $("start").classList.add("loading");
    formStatus("Starting the pipeline…");
    try {
      const { run_id: runId } = await api("/runs", { method: "POST", body: JSON.stringify(body) });
      formStatus(body.sow_markdown ? "Started from your SOW: it is reviewed, then the architecture and cost follow." : "Started. The SOW is drafted first.");
      watch(runId);
      loadHistory();
    } catch (err) { formStatus(err.message, true); }
    state.busy = false; $("start").disabled = false; $("start").classList.remove("loading");
  });

  // ---------------------------------------------------------------- samples ("Use sample")
  function renderSamples() {
    if (!samples.length) { fill($("samples"), el("li", { class: "empty" }, "No samples are available (samples.js).")); return; }
    fill($("samples"), samples.map((s) => {
      const use = el("button", { type: "button", class: "btn sm primary", title: "Fill the form with this sample; the SOW is drafted from scratch" }, "Use sample");
      use.addEventListener("click", () => useSample(s, false, use));
      const withSow = s.example_md
        ? el("button", { type: "button", class: "btn sm", title: "Also load the finished SOW, which skips the drafting stage" }, "Use with example SOW") : null;
      if (withSow) withSow.addEventListener("click", () => useSample(s, true, withSow));
      const view = s.example_md ? el("button", { type: "button", class: "btn sm ghost", title: "Read the example SOW" }, "View example") : null;
      if (view) view.addEventListener("click", () => viewExample(s));
      return el("li", { class: "sample" },
        el("div", { class: "sample-head" }, el("b", {}, s.title || s.id), el("span", { class: "muted small" }, templateLabel(s.template_id))),
        s.summary ? el("p", { class: "muted small" }, s.summary) : null,
        el("div", { class: "actions" }, use, withSow, view));
    }));
  }

  async function useSample(s, withSow, button) {
    if (state.busy) return;
    button.disabled = true; button.classList.add("loading");
    formStatus(`Loading the ${s.customer_name} sample…`);
    try {
      const [details, upstream, sow] = await Promise.all([
        s.details ? staticText(s.details) : "",
        s.upstream ? staticText(s.upstream) : "",
        withSow && s.example_md ? staticText(s.example_md) : "",
      ]);
      $("run-form").reset();
      $("customer").value = s.customer_name || "";
      $("opportunity").value = s.opportunity_id || "";
      if (templates.some((t) => t.id === s.template_id)) $("template").value = s.template_id;
      $("details").value = details.trim();
      $("upstream").value = upstream.trim();
      $("upstream-panel").open = !!upstream;
      $("sow").value = sow.trim();
      $("sow-panel").open = !!sow;
      $("stop-on-blocked").checked = true;
      formStatus(`${s.customer_name} sample loaded${sow ? " with its example SOW (drafting is skipped)" : ""}. Review the form, then Start the pipeline.`);
      $("new-title").scrollIntoView({ behavior: "smooth", block: "start" });
      $("customer").focus({ preventScroll: true });
    } catch (err) { formStatus(`Could not load the sample: ${err.message}`, true); }
    button.disabled = false; button.classList.remove("loading");
  }

  async function viewExample(s) {
    $("viewer-title").textContent = `Example SOW: ${s.customer_name}`;
    fill($("viewer-body"), el("div", { class: "loading" }, "Loading the example…"));
    showViewer(true);
    try {
      const md = (await staticText(s.example_md)).replace(/<\/?(sample|requirements)>/gi, "");
      fill($("viewer-body"), window.renderMarkdown(md, { lineBreaks: true, resolveImage: () => null }));
    } catch (err) { fill($("viewer-body"), el("p", { class: "error" }, `Could not load the example: ${err.message}`)); }
  }
  function showViewer(on) {
    $("viewer").hidden = !on;
    $("run").hidden = on || !state.runId;
    $("empty").hidden = on || !!state.runId;
  }
  $("viewer-close").addEventListener("click", () => showViewer(false));

  // ---------------------------------------------------------------- watching a run
  function watch(runId) {
    if (state.runId !== runId) { state.reviseAutoOpened = false; $("revise-instruction").value = ""; $("revise-status").textContent = ""; }
    state.runId = runId; state.sowSig = ""; state.diagram = null; state.archDoc = undefined; state.archFor = null; state.archSig = "";
    if (state.timer) clearInterval(state.timer);
    showViewer(false);
    $("run").hidden = false; $("empty").hidden = true;
    poll();
    state.timer = setInterval(poll, POLL_MS);
  }

  async function poll() {
    if (!state.runId) return;
    const runId = state.runId;
    try {
      const { run, state: pipeline, log } = await api(`/runs/${runId}`);
      if (runId !== state.runId) return; // the user opened another run meanwhile
      state.pipeline = pipeline;
      render(run, pipeline, log || []);
      if (SETTLED.includes(run.status) && state.timer) { clearInterval(state.timer); state.timer = null; loadHistory(); loadSession(); }
    } catch (err) {
      $("run-error").textContent = err.message;
    }
  }

  const statusOf = (pipeline, stage) => ((pipeline?.stages || []).find((s) => s.stage === stage) || {}).status || "pending";

  function render(run, pipeline, log) {
    $("run-title").textContent = run.title || run.run_id;
    $("run-sub").textContent = [templateLabel(run.template_id), `started ${fmtTime(run.created_at)}`, `${run.progress || 0}% complete`].filter(Boolean).join(" · ");
    const pill = $("run-pill");
    pill.textContent = run.status;
    pill.className = `pill s-${run.status}`;
    $("bar").style.width = `${Math.max(0, Math.min(100, Number(run.progress) || 0))}%`;
    $("bar").parentElement.classList.toggle("active", run.status === "queued" || run.status === "running");
    $("run-error").textContent = run.status === "failed" && run.error ? run.error : "";

    renderStages(pipeline);
    renderBlocked(pipeline);
    renderRevise(run, pipeline);
    renderOutputs(run, pipeline);
    renderLog(log);

    const sig = [pipeline?.has_sow, statusOf(pipeline, "wa_review"), statusOf(pipeline, "architecture"), statusOf(pipeline, "cost"),
      (pipeline?.revisions || []).length, pipeline?.revision ? "pending" : ""].join("|");
    if (sig !== state.sowSig) { state.sowSig = sig; refreshSow(); }
  }

  function renderStages(pipeline) {
    const live = new Map((pipeline?.stages || []).map((s) => [s.stage, s]));
    const meta = state.stages.length ? state.stages : (pipeline?.stages || []);
    fill($("stages"), meta.map((m) => {
      const s = live.get(m.stage) || {};
      const status = s.status || "pending";
      const note = s.detail || s.error || "";
      return el("li", { class: `stage ${status}` },
        el("span", {}, el("span", { class: "name" }, m.label || s.label || m.stage), el("span", { class: "agent" }, m.agent || s.agent || "")),
        el("span", { class: `pill s-${status}` }, s.seconds ? `${status} · ${s.seconds}s` : status),
        note ? el("span", { class: "note" }, note) : null);
    }));
  }

  function renderBlocked(pipeline) {
    const blocked = (pipeline?.stages || []).find((s) => s.status === "blocked");
    const isSa = !!blocked && blocked.stage === "sa_review";       // the Solutions Architect checkpoint, not a findings stop
    $("blocked").hidden = !blocked || isSa;
    $("sa-review").hidden = !isSa;
    if (blocked && !isSa) $("blocked-detail").textContent = `${blocked.label}: ${blocked.detail}`;
    if (isSa) renderSaReview(blocked, pipeline);
    if (blocked && !state.reviseAutoOpened) {       // the usual action here is a SOW edit, so put that in front of the reader once
      state.reviseAutoOpened = true;
      $("revise-panel").open = true;
      setReviseTarget("sow");
    }
  }

  // ---------------------------------------------------------------- the Solutions Architect checkpoint
  function saStatus(message, isError = false) {
    const node = $("sa-status");
    node.textContent = message || "";
    node.className = `small ${isError ? "error" : "muted"}`;
  }

  function renderSaReview(stage, pipeline) {
    $("sa-detail").textContent = stage.detail || "The draft is ready for the Solutions Architect to review.";
    const comments = (pipeline?.sa_comments) || stage.comments || [];
    fill($("sa-comments"), comments.map((c) => el("li", { class: "audit-item" },
      el("div", { class: "audit-head" }, el("span", { class: "stage" }, c.by || "Architect"),
        el("span", { class: "muted" }, fmtTime(c.at))),
      el("div", {}, c.text))));
  }

  async function submitSaReview(signOff) {
    if (!state.runId || state.busy) return;
    const comment = $("sa-comment").value.trim();
    if (!signOff && comment.length < 1) { saStatus("Write a comment first, or approve to continue.", true); return; }
    state.busy = true; $("sa-approve").disabled = true; $("sa-comment-add").disabled = true;
    saStatus(signOff ? "Approving…" : "Saving comment…");
    try {
      await api(`/runs/${state.runId}/sa-review`, { method: "POST", body: JSON.stringify({ comment, sign_off: signOff }) });
      $("sa-comment").value = "";
      saStatus(signOff ? "Approved. Running the Well-Architected review…" : "Comment added.");
      watch(state.runId);
      loadHistory();
    } catch (err) { saStatus(err.message, true); }
    state.busy = false; $("sa-approve").disabled = false; $("sa-comment-add").disabled = false;
  }
  $("sa-comment-add").addEventListener("click", () => submitSaReview(false));
  $("sa-approve").addEventListener("click", () => submitSaReview(true));

  // ---------------------------------------------------------------- change with AI
  function setReviseTarget(target) {
    const info = REVISE_TARGETS[target];
    if (!info) return;
    $("revise-target").value = target;
    $("revise-instruction").placeholder = info.placeholder;
    $("revise-target-hint").textContent = info.hint;
  }
  $("revise-target").addEventListener("change", () => setReviseTarget($("revise-target").value));
  setReviseTarget("sow");

  /** Open the panel with a target and a ready-made instruction, e.g. from a review finding. */
  function prefillRevise(target, text) {
    setReviseTarget(target);
    $("revise-instruction").value = String(text || "").slice(0, 1900);
    $("revise-panel").open = true;
    $("revise-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("revise-instruction").focus({ preventScroll: true });
  }

  function reviseStatus(message, isError = false) {
    const node = $("revise-status");
    node.textContent = message || "";
    node.className = `small ${isError ? "error" : "muted"}`;
  }

  const REVISION_PILL = { applied: ["completed", "applied"], no_change: ["partial", "no change"], failed: ["failed", "not applied"] };

  function renderRevise(run, pipeline) {
    const active = !SETTLED.includes(run.status);
    const pending = pipeline?.revision;
    $("revise-send").disabled = state.busy || active;
    if (active) {
      state.reviseWorking = true;
      reviseStatus(pending ? `Applying your change to the ${(REVISE_TARGETS[pending.target] || {}).label || "run"}…` : "The run is still working. You can ask for a change when it finishes.");
    } else if (state.reviseWorking) {
      state.reviseWorking = false;
      reviseStatus("");
    }

    const done = (pipeline?.revisions || []).slice().reverse().map((r) => {
      const [cls, label] = REVISION_PILL[r.status] || ["pending", r.status];
      return el("li", { class: `audit-item ${r.status === "failed" ? "lvl-error" : ""}` },
        el("div", { class: "audit-head" },
          el("span", { class: "stage" }, (REVISE_TARGETS[r.target] || {}).label || r.target),
          el("span", { class: `pill s-${cls}` }, label),
          el("span", { class: "muted" }, fmtTime(r.finished_at))),
        el("div", {}, r.instruction),
        r.status === "failed" ? el("div", { class: "error" }, r.error) : el("div", { class: "muted" }, r.summary),
        (r.details || []).length ? el("ul", { class: "rev-details" }, r.details.map((d) => el("li", {}, d))) : null);
    });
    const waiting = pending
      ? el("li", { class: "audit-item" },
        el("div", { class: "audit-head" }, el("span", { class: "stage" }, (REVISE_TARGETS[pending.target] || {}).label || pending.target),
          el("span", { class: "pill s-running" }, "in progress")),
        el("div", {}, pending.instruction))
      : null;
    fill($("revisions"), waiting, done);
  }

  $("revise-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.runId || state.busy) return;
    const target = $("revise-target").value;
    const instruction = $("revise-instruction").value.trim();
    if (instruction.length < 5) { reviseStatus("Say what to change, in a sentence.", true); return; }
    state.busy = true; $("revise-send").disabled = true; $("revise-send").classList.add("loading");
    reviseStatus("Sending…");
    try {
      await api(`/runs/${state.runId}/revise`, { method: "POST", body: JSON.stringify({ target, instruction }) });
      $("revise-instruction").value = "";
      state.reviseWorking = true;
      reviseStatus("Queued. The change is applied first, then the stages after it run again.");
      watch(state.runId);
      loadHistory();
    } catch (err) { reviseStatus(err.message, true); }
    state.busy = false; $("revise-send").classList.remove("loading");
    $("revise-send").disabled = state.reviseWorking;
  });

  $("continue").addEventListener("click", async () => {
    const button = $("continue");
    button.disabled = true;
    try {
      await api(`/runs/${state.runId}/continue`, { method: "POST" });
      watch(state.runId);
    } catch (err) { $("blocked-detail").textContent = err.message; }
    button.disabled = false;
  });

  // ---------------------------------------------------------------- outputs
  function renderOutputs(run, pipeline) {
    const stage = (name) => (pipeline?.stages || []).find((s) => s.stage === name) || {};
    const costDone = stage("cost").status === "completed";
    $("downloads").hidden = !pipeline?.has_sow;
    $("export").disabled = !costDone;
    $("export").title = costDone
      ? "The final SOW with the architecture diagram and the real cost estimate link, as Word"
      : "Available once the cost estimate is complete, because the SOW gets its cost link from it";
    const link = $("calc-link");
    const url = stage("cost").calculator_url || run.calculator_url;
    link.hidden = !isCalculatorUrl(url);
    if (!link.hidden) link.setAttribute("href", url);

    renderReview(stage("wa_review"), stage("combined_review"));
    renderArch(stage("architecture"));
    renderCost(stage("cost"), pipeline?.scope, url);
  }

  function renderReview(wa, combined) {
    const findings = wa.findings || [];
    const failed = combined.failed_rules || [];
    const saFindings = combined.sa_findings || [];
    const count = $("review-count");
    count.hidden = !(findings.length + failed.length + saFindings.length);
    count.textContent = String(findings.length + failed.length + saFindings.length);

    const bySeverity = (sev) => findings.filter((f) => f.severity === sev).length;
    const pillars = (wa.summary?.pillars || []).map((p) => el("span", { class: `pill ${p.findings ? "has" : ""}` }, `${p.label || PILLAR_LABELS[p.pillar] || p.pillar}: ${p.findings}`));
    const blocks = [];
    if (!wa.status || wa.status === "pending") blocks.push(el("p", { class: "hint" }, "The Well-Architected review runs against the SOW as soon as it exists, before the diagram is drawn."));
    else if (!findings.length) blocks.push(el("p", { class: "out-note" }, "No Well-Architected findings against the SOW."));
    else blocks.push(el("p", { class: "out-note" }, `${bySeverity("high")} high, ${bySeverity("medium")} medium, ${bySeverity("low")} low.`));
    if (pillars.length && findings.length) blocks.push(el("div", { class: "pillars" }, pillars));
    for (const f of findings) {
      const fix = el("button", { type: "button", class: "btn sm ghost", title: "Ask the AI to change the SOW to resolve this finding" }, "Fix with AI");
      fix.addEventListener("click", () => prefillRevise("sow", `Resolve this Well-Architected finding: ${f.title}. ${f.suggestion || f.detail || ""}`.trim()));
      blocks.push(el("div", { class: `finding ${f.severity}` },
        el("div", { class: "finding-head" }, el("b", {}, f.title), el("span", { class: `pill sev-${f.severity}` }, f.severity),
          el("span", { class: "muted small" }, PILLAR_LABELS[f.pillar] || f.pillar), fix),
        f.detail ? el("p", {}, f.detail) : null,
        f.suggestion ? el("p", { class: "fix" }, el("b", {}, "Fix: "), f.suggestion) : null,
        f.evidence ? el("p", { class: "evidence" }, f.evidence) : null));
    }
    if (failed.length) {
      blocks.push(el("h4", {}, "Final SOW review"));
      for (const r of failed) blocks.push(el("div", { class: "finding high" },
        el("div", { class: "finding-head" }, el("b", {}, r.name || r.id), el("span", { class: "pill sev-high" }, r.severity || "required"))));
    }
    if (saFindings.length) {
      const SEV = { critical: "high", high: "high", medium: "medium", low: "low" };
      blocks.push(el("h4", {}, "SA document review"));
      blocks.push(el("p", { class: "out-note" }, "A checklist pass over the final SOW. Critical items stop the run until fixed or waved through; the rest are for you to confirm before it goes out."));
      for (const f of saFindings) blocks.push(el("div", { class: `finding ${SEV[f.severity] || "medium"}` },
        el("div", { class: "finding-head" }, el("b", {}, f.label),
          el("span", { class: `pill sev-${SEV[f.severity] || "medium"}` }, f.severity),
          f.dimension ? el("span", { class: "muted small" }, `dim ${f.dimension}`) : null),
        f.detail ? el("p", {}, f.detail) : null));
    }
    fill($("review-view"), blocks);
  }

  // The diagram and the design are fetched once per version of the architecture. A change to the architecture
  // leaves the stage "completed" the whole time, so the version is the number of changes made so far, and nothing
  // is fetched while a change is pending (it would be the old design).
  const archVersion = () => `${state.runId}:${(state.pipeline?.revisions || []).length}`;
  const archStage = () => (state.pipeline?.stages || []).find((s) => s.stage === "architecture") || {};

  async function refreshArch() {
    if (statusOf(state.pipeline, "architecture") !== "completed" || state.pipeline?.revision) return;
    const version = archVersion();
    if (state.archFor === version) return;
    state.archFor = version;
    const runId = state.runId;
    const [diagram, doc] = await Promise.allSettled([api(`/runs/${runId}/diagram`), api(`/runs/${runId}/architecture`)]);
    if (runId !== state.runId || version !== archVersion()) return;     // another run or another change came in; the next poll refetches
    state.diagram = diagram.status === "fulfilled" ? `data:image/png;base64,${diagram.value.png_base64}` : "";
    state.archDoc = doc.status === "fulfilled" ? doc.value : null;
    state.archRev += 1;
    renderArch(archStage());
    refreshSow();
  }

  const dataTable = (head, rows) => el("div", { class: "md" }, el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, head.map((h) => el("th", {}, h)))),
    el("tbody", {}, rows.map((row) => el("tr", {}, row.map((cell) => el("td", {}, cell))))))));
  const bullets = (items) => el("ul", { class: "plain-list" }, items.map((i) => el("li", {}, i)));
  const collapsible = (title, md) => md
    ? el("details", { class: "section" }, el("summary", {}, title), el("div", { class: "md-scroll" }, window.renderMarkdown(String(md), {})))
    : null;

  /** The design the architecture agent prepared, as readable sections under the diagram. */
  function renderDesign(doc) {
    if (doc === undefined) return null;
    if (doc === null) return el("p", { class: "hint" }, "The design details are not available for this run.");
    const zones = doc.zones || [];
    const where = [doc.region ? `Region ${doc.region}` : "", zones.length ? `${zones.length} Availability Zone${zones.length > 1 ? "s" : ""}: ${zones.join(", ")}` : ""].filter(Boolean).join(" · ");
    const components = doc.components || [];
    const flows = doc.flows || [];
    const decisions = doc.decisions || [];
    return el("div", { class: "design" },
      el("h4", {}, doc.title || "Architecture design"),
      where ? el("p", { class: "out-note" }, where) : null,
      doc.context ? el("p", {}, doc.context) : null,
      (doc.scope_warnings || []).length
        ? el("div", { class: "review-panel" }, el("b", {}, "Possibly outside the agreed scope"), bullets(doc.scope_warnings)) : null,
      components.length ? [el("h4", {}, `Components (${components.length})`),
        dataTable(["Component", "Service", "Role", "Configuration", "Qty"],
          components.map((c) => [c.name || c.id, c.service, c.purpose || c.role, c.sku_summary, c.quantity > 1 ? String(c.quantity) : ""]))] : null,
      flows.length ? [el("h4", {}, `Data flows (${flows.length})`),
        bullets(flows.map((f) => `${f.from} → ${f.to}${f.label ? `: ${f.label}` : ""}${f.protocol ? ` (${f.protocol})` : ""}`))] : null,
      decisions.length ? [el("h4", {}, `Key decisions (${decisions.length})`),
        el("ul", { class: "plain-list" }, decisions.map((d) => el("li", {}, el("b", {}, `${d.id ? `${d.id}: ` : ""}${d.title || ""}`), d.decision ? ` ${d.decision}` : "")))] : null,
      (doc.assumptions || []).length ? [el("h4", {}, "Assumptions"), bullets(doc.assumptions)] : null,
      (doc.security_operations || []).length ? [el("h4", {}, "Security and operations"), bullets(doc.security_operations)] : null,
      (doc.open_questions || []).length ? [el("h4", {}, "Open questions"), bullets(doc.open_questions)] : null,
      collapsible("Architecture Decision Record (ADR)", doc.adr_md),
      collapsible("Usage assumptions behind the cost estimate", doc.usage_assumptions_md));
  }

  function renderArch(stage) {
    const sig = [stage.status, stage.detail, stage.error, state.archFor, state.archRev, state.pipeline?.revision ? "pending" : ""].join("|");
    if (sig === state.archSig) return;
    state.archSig = sig;
    if (stage.status !== "completed") {
      fill($("arch-view"), el("p", { class: "hint" }, stage.status === "failed" ? stage.error : "The architecture is drawn from the SOW's agreed scope, after the Well-Architected review passes."));
      return;
    }
    refreshArch();
    const changing = !!state.pipeline?.revision;
    fill($("arch-view"),
      changing ? el("p", { class: "out-note" }, "A change is being applied. This view updates when it finishes.") : null,
      stage.detail ? el("p", { class: "out-note" }, stage.detail) : null,
      state.diagram
        ? el("div", { class: "diagram-frame" }, el("img", { src: state.diagram, alt: "Architecture diagram drawn from the SOW" }))
        : el("div", { class: "loading" }, state.diagram === "" ? "The diagram is not available for this run." : "Loading the diagram…"),
      renderDesign(state.archDoc));
  }

  function renderCost(stage, scope, url) {
    const inScope = scope?.in_scope || [];
    const outScope = scope?.out_of_scope || [];
    const costBlock = stage.status === "completed" && isCalculatorUrl(url)
      ? el("p", {}, "AWS Pricing Calculator estimate: ", el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, url))
      : el("p", { class: "hint" }, stage.status === "failed" ? stage.error : "The cost estimate prices what the architecture stage produced. The SOW refers to it instead of restating the figures.");
    fill($("cost-view"),
      el("h4", {}, "Cost estimate"), costBlock,
      el("h4", {}, "Scope read from the SOW"),
      !inScope.length && !outScope.length
        ? el("p", { class: "hint" }, "Available after the SOW exists.")
        : el("div", { class: "scope-cols" },
          el("div", {}, el("b", {}, `In scope (${inScope.length})`), el("ul", {}, inScope.map((i) => el("li", {}, i)))),
          el("div", {}, el("b", {}, `Out of scope (${outScope.length})`), el("ul", {}, outScope.map((i) => el("li", {}, i))))));
  }

  async function refreshSow() {
    const runId = state.runId;
    if (!state.pipeline?.has_sow) { fill($("sow-view"), el("p", { class: "hint" }, "The SOW appears here as soon as the first stage finishes.")); return; }
    try {
      const { markdown } = await api(`/runs/${runId}/sow`);
      if (runId !== state.runId) return;
      const resolveImage = (target) => (/^arch:/.test(target) ? state.diagram : null);
      fill($("sow-view"), window.renderMarkdown(String(markdown).replace(/<\/?(sample|requirements)>/gi, ""), { lineBreaks: true, resolveImage }));
    } catch (err) { fill($("sow-view"), el("p", { class: "error" }, err.message)); }
  }

  function renderLog(log) {
    fill($("log"), log.map((line) => el("li", { class: `audit-item lvl-${line.level || "info"}` },
      el("div", { class: "audit-head" }, el("span", { class: "stage" }, line.stage), el("span", { class: "muted" }, (line.at || "").slice(11, 19))),
      el("div", {}, line.message))));
    $("log").scrollTop = $("log").scrollHeight;
  }

  // ---------------------------------------------------------------- export, tabs, history
  $("export").addEventListener("click", async () => {
    const button = $("export");
    button.disabled = true;
    $("export-status").textContent = "Rendering the document…";
    try {
      const out = await api(`/runs/${state.runId}/export`, { method: "POST" });
      $("export-status").textContent = `${out.filename}${out.with_diagram ? " (with the architecture diagram)" : ""}`;
      window.location.assign(out.download_url);
    } catch (err) { $("export-status").textContent = err.message; }
    button.disabled = statusOf(state.pipeline, "cost") !== "completed";
  });

  document.querySelectorAll(".subtab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".subtab").forEach((t) => t.removeAttribute("aria-current"));
    tab.setAttribute("aria-current", "page");
    document.querySelectorAll(".tab-panel").forEach((p) => { p.hidden = p.id !== `tab-${tab.dataset.tab}`; });
    if (TAB_TARGET[tab.dataset.tab]) setReviseTarget(TAB_TARGET[tab.dataset.tab]);   // asking for a change defaults to what you are looking at
  }));

  async function loadHistory() {
    try {
      const { runs } = await api("/runs");
      fill($("history"), (runs || []).length
        ? runs.map((r) => {
          const item = el("button", { type: "button", class: "card-item", "aria-current": String(r.run_id === state.runId) },
            el("span", { class: "card-main" },
              el("span", { class: "card-title" }, r.title || r.run_id),
              el("span", { class: "card-meta" }, el("span", { class: `pill s-${r.status}` }, r.status),
                el("span", { class: "muted" }, `${r.stage || ""} · ${r.progress || 0}% · ${fmtTime(r.created_at)}`))));
          item.addEventListener("click", () => watch(r.run_id));
          return el("li", {}, item);
        })
        : el("li", { class: "empty" }, "No runs yet."));
    } catch { /* the history is not worth an error banner */ }
  }

  // ---------------------------------------------------------------- start
  fillTemplates();
  renderSamples();
  loadSession();
  loadHistory();
})();
