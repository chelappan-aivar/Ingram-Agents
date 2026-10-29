// Funding check page: intake form -> Dynamics 365 opportunity -> agent fetches it and applies the
// AWS Funding Programs Decision Framework. Shares the browser session (and Microsoft connection) with index.html.
(async () => {
  const cfg = window.INTAKE_CONFIG || {};
  const FORM = window.OPPORTUNITY_FORM || [];
  const POLL_MS = 2500;
  const DONE = new Set(["COMPLETED", "FAILED"]);
  const $ = (id) => document.getElementById(id);
  const view = $("view");

  // ---------------------------------------------------------------- DOM helper (no innerHTML: all data is text)
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
  function svgIcon(paths) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2", "aria-hidden": "true" })) svg.setAttribute(k, v);
    for (const d of paths) {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d);
      svg.append(p);
    }
    return svg;
  }
  const usd = (n) => (n === null || n === undefined ? "–" : `${Math.round(n).toLocaleString("en-US")} USD`);
  const when = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");

  // ---------------------------------------------------------------- theme (same storage key as index.html)
  const THEMES = [
    ["light", "Light theme", ["M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"]],
    ["dark", "Dark theme", ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"]],
    ["system", "System theme", ["M3 4h18v12H3zM8 20h8M12 16v4"]],
  ];
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(choice) {
    localStorage.setItem("intakeTheme", choice);
    document.documentElement.dataset.theme = choice === "system" ? (systemDark.matches ? "dark" : "light") : choice;
    document.querySelectorAll("[data-theme-choice]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeChoice === choice)));
  }
  document.querySelectorAll("[data-theme-toggle]").forEach((group) => {
    for (const [choice, label, paths] of THEMES) {
      const btn = el("button", { type: "button", "data-theme-choice": choice, "aria-label": label, title: label });
      btn.append(svgIcon(paths));
      btn.addEventListener("click", () => applyTheme(choice));
      group.append(btn);
    }
  });
  systemDark.addEventListener("change", () => { if ((localStorage.getItem("intakeTheme") || "dark") === "system") applyTheme("system"); });
  applyTheme(localStorage.getItem("intakeTheme") || "dark");

  // ---------------------------------------------------------------- session (shared with index.html)
  const SESSION_KEY = "intakeSession";
  let session = (() => {
    try {
      const s = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
      return s && s.expires_at > Date.now() + 60000 ? s : null;
    } catch { return null; }
  })();
  async function ensureSession() {
    if (session) return;
    const resp = await fetch(`${cfg.apiUrl}/session`, { method: "POST" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    session = { token: data.session, expires_at: Date.now() + data.expires_in * 1000 };
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  }
  class ApiError extends Error {
    constructor(message, status, data) { super(message); this.status = status; this.data = data; }
  }
  async function api(method, path, body, retry = true) {
    await ensureSession();
    const resp = await fetch(`${cfg.apiUrl}${path}`, {
      method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.token}` },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 401 && retry) {
      session = null;
      localStorage.removeItem(SESSION_KEY);
      return api(method, path, body, false);
    }
    if ((resp.status === 403 || resp.status === 409) && "microsoft" in data) setUser(null);
    if (!resp.ok) throw new ApiError(data.error || data.message || `HTTP ${resp.status}`, resp.status, data);
    return data;
  }

  // ---------------------------------------------------------------- Microsoft sign-in (device code)
  let user = null;
  let loginTimer = null;
  let loginId = null;
  function setUser(u) {
    user = u;
    $("gate").hidden = !!u;
    $("workspace").hidden = !u;
    $("user-chip").hidden = !u;
    $("who").textContent = u ? u.email : "";
    const name = u ? (u.name || u.email) : "";
    $("avatar").textContent = name.split(/[\s.@]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
  }
  function gateNote(message, isError = false) {
    $("gate-note").textContent = message || "";
    $("gate-note").className = `status-line ${isError ? "error" : "muted"}`;
  }
  function resetConnect(message, isError = false) {
    clearTimeout(loginTimer);
    loginId = null;
    $("device").hidden = true;
    $("connect").hidden = false;
    $("connect").disabled = false;
    gateNote(message, isError);
  }
  async function pollLogin(interval) {
    if (!loginId) return;
    try {
      const res = await api("POST", "/auth/device/poll", { login_id: loginId });
      if (res.status === "done") { resetConnect(); setUser(res.user); start(); return; }
      const next = res.slow_down ? interval + 5 : interval;
      loginTimer = setTimeout(() => pollLogin(next), next * 1000);
    } catch (err) { resetConnect(err.message, true); }
  }
  $("connect").addEventListener("click", async () => {
    $("connect").disabled = true;
    gateNote("Getting a sign-in code…");
    try {
      const res = await api("POST", "/auth/device/start");
      loginId = res.login_id;
      $("device-code").textContent = res.user_code;
      $("device-link").href = res.verification_uri;
      $("connect").hidden = true;
      $("device").hidden = false;
      gateNote("");
      loginTimer = setTimeout(() => pollLogin(res.interval), res.interval * 1000);
    } catch (err) { resetConnect(`Could not start: ${err.message}`, true); }
  });
  $("copy-code").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("device-code").textContent); $("copy-code").textContent = "Copied"; } catch { /* copy by hand */ }
    setTimeout(() => { $("copy-code").textContent = "Copy"; }, 1500);
  });
  $("device-cancel").addEventListener("click", () => resetConnect());

  // ---------------------------------------------------------------- mode (Dynamics connected or not)
  let mode = "demo";
  function setMode(m, host) {
    mode = m;
    const pill = $("mode-pill");
    pill.hidden = false;
    pill.textContent = m === "dynamics" ? (host ? `Dynamics 365 · ${host}` : "Dynamics 365") : "Dynamics 365 not connected";
    pill.className = `pill mode-pill ${m}`;
    pill.title = m === "dynamics" ? "Opportunities are created in and fetched from Microsoft Dynamics 365"
      : "DYNAMICS_URL is not configured";
    $("list-tabs").hidden = m !== "dynamics";
  }

  // ---------------------------------------------------------------- list: mine / in Dynamics
  let tab = "mine";
  let cursor = null;
  let items = [];
  let selected = null;   // opportunity id shown on the right
  const STATUS_TEXT = { decided: "Funding determined" };   // every opportunity maps to a programme

  function listNote(message, isError = false) {
    $("list-note").textContent = message || "";
    $("list-note").className = `status-line ${isError ? "error" : "muted"}`;
  }
  function decisionPill(latest) {
    if (latest?.status !== "decided") return el("span", { class: "pill dec-none" }, "Pending assessment");
    return el("span", { class: "pill dec-decided" }, latest.program || STATUS_TEXT.decided);
  }
  function renderList() {
    $("opp-list").replaceChildren(...(items.length ? items.map((o) => {
      const btn = el("button", { type: "button", class: `history-item${o.id === selected ? " current" : ""}`,
        "aria-current": o.id === selected ? "true" : null },
        el("span", { class: "history-main" },
          el("span", { class: "history-when" }, o.name || "Opportunity"),
          el("span", { class: "history-src" }, o.customer ? el("span", { class: "muted" }, o.customer) : null,
            el("span", { class: "muted" }, when(o.created_at || o.created_on)))),
        tab === "mine" ? decisionPill(o.latest) : el("span", { class: "muted small-r" }, o.estimated_value ? usd(o.estimated_value) : ""));
      btn.addEventListener("click", () => showOpportunity(o));
      return el("li", {}, btn);
    }) : [el("li", { class: "muted history-empty" }, tab === "mine"
      ? "No opportunities yet. Create one with the form." : "No opportunities found in Dynamics.")]));
    $("more").hidden = !(tab === "mine" && cursor);
  }
  async function loadList(more = false) {
    listNote("");
    try {
      if (tab === "mine") {
        const res = await api("GET", `/funding/opportunities${more && cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`);
        items = more ? [...items, ...res.opportunities] : res.opportunities;
        cursor = res.next_cursor;
      } else {
        const q = $("dyn-q").value.trim();
        const res = await api("GET", `/funding/dynamics${q ? `?search=${encodeURIComponent(q)}` : ""}`);
        items = res.opportunities;
        cursor = null;
      }
      renderList();
    } catch (err) { listNote(`Could not load: ${err.message}`, true); }
  }
  document.querySelectorAll("#list-tabs button").forEach((b) => b.addEventListener("click", () => {
    tab = b.dataset.tab;
    document.querySelectorAll("#list-tabs button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    $("dyn-search").hidden = tab !== "dynamics";
    items = [];
    renderList();
    loadList();
  }));
  let searchTimer = null;
  $("dyn-q").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadList(), 400); });
  $("refresh").addEventListener("click", () => loadList());
  $("more").addEventListener("click", () => loadList(true));
  $("new-opp").addEventListener("click", () => showForm());
  $("by-id-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const id = $("by-id").value.trim();
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) { listNote("That is not a Dynamics opportunity ID (GUID).", true); return; }
    runCheck(id, { id, name: "Dynamics opportunity" });
  });

  function setBadge(text, kind = "") {
    $("badge").textContent = text;
    $("badge").className = `badge ${kind}`;
  }

  // ---------------------------------------------------------------- the opportunity form (ui/form.js)
  const SAMPLES = [
    { name: "Startup migration · 15k MRR · VMs", answers: {
      request_type: "Cloud Services Opportunity", im_country: "India", company_name: "Aivar Innovations",
      partner_contact_name: "Priya Raman", partner_contact_email: "priya@example.com", ec_name: "Tailwind Robotics",
      ec_country: "India", ec_industry: "Manufacturing", cs_vendor: "AWS", aws_opportunity_type: "Migration",
      ace_registered: "No", cs_opportunity_title: "Tailwind Robotics VMware exit to AWS",
      cs_opportunity_details: "Seed-funded robotics startup (Series A in 2026) running 35 VMs on VMware in a Bengaluru colo. Wants to rehost on EC2 in ap-south-1 before the colo contract ends in March.",
      estimated_arr: "180,000", cs_timeline: "1-3 months", delivery_timeline: "1-3 months", budget_allocated: "Yes",
      fund_segment: "Startup", fund_workload: "Migration", fund_migration_source: "On-premises", fund_vms: "Yes", fund_mrr_usd: "15000" } },
    { name: "Enterprise Gen AI · details in text only", answers: {
      request_type: "Data & AI Opportunity", company_name: "Contoso Partners", ec_name: "Northwind Health",
      ec_industry: "Healthcare", ec_country: "United Kingdom", dai_vendor: "AWS", dai_stage: "Qualification",
      dai_title: "Clinical notes summarisation on Amazon Bedrock",
      dai_details: "Northwind Health is a 12,000-employee hospital group. They want a proof of concept for summarising clinical notes with Amazon Bedrock. Expected AWS spend is about 25,000 USD per month once live.",
      dai_project_kind: "Project Delivery", dai_budget: "Yes", fund_segment: "Not sure", fund_workload: "Not sure" } },
    { name: "SAP migration (no rules yet)", answers: {
      request_type: "Cloud Services Opportunity", company_name: "Fabrikam Reseller", ec_name: "Contoso Retail",
      cs_vendor: "AWS", aws_opportunity_type: "SAP migration", cs_opportunity_title: "SAP ECC to AWS Mumbai",
      cs_opportunity_details: "Move SAP ECC (45 servers, 14 TB) from on-premises to AWS ap-south-1 by Q2 2027.",
      estimated_arr: "600000", fund_segment: "Non-startup (enterprise and others)", fund_workload: "SAP migration" } },
  ];

  const visible = (rule, answers) => !rule || rule.in.includes(answers[rule.field]);

  function inputFor(f, value) {
    const id = `q-${f.id}`;
    const common = { id, name: f.id, "aria-describedby": f.help ? `${id}-help` : null, required: f.required || null };
    if (f.type === "select") {
      return el("select", common, el("option", { value: "" }, "Select…"),
        f.options.map((o) => el("option", { value: o, selected: o === value || null }, o)));
    }
    if (f.type === "radio") {
      return el("div", { class: "radio-row", role: "radiogroup", "aria-labelledby": `${id}-label` },
        f.options.map((o, n) => el("label", { class: "radio" },
          el("input", { type: "radio", name: f.id, value: o, id: n === 0 ? id : null, checked: o === value || null }), o)));
    }
    if (f.type === "textarea") return el("textarea", { ...common, rows: "3", maxlength: "8000" }, value || "");
    const type = { number: "text", tel: "tel", email: "email", date: "date" }[f.type] || "text";
    return el("input", { ...common, type, value: value ?? "", maxlength: "300",
      inputmode: f.type === "number" ? "decimal" : null });
  }

  function readForm(form) {
    const answers = {};
    for (const s of FORM) for (const f of s.fields) {
      if (f.type === "radio") {
        const c = form.querySelector(`input[name="${f.id}"]:checked`);
        if (c) answers[f.id] = c.value;
      } else {
        const node = form.elements.namedItem(f.id);
        if (node && node.value.trim()) answers[f.id] = node.value.trim();
      }
    }
    return answers;
  }

  function applyVisibility(form) {
    const answers = readForm(form);
    for (const s of FORM) {
      const sec = form.querySelector(`[data-section="${s.id}"]`);
      const on = visible(s.show_when, answers);
      sec.hidden = !on;
      for (const f of s.fields) {
        const row = sec.querySelector(`[data-field="${f.id}"]`);
        row.hidden = !(on && visible(f.show_when, answers));
      }
    }
    // Progress: visible questions answered
    const shown = [...form.querySelectorAll("[data-field]")].filter((r) => !r.hidden && !r.closest("[hidden]"));
    const done = shown.filter((r) => answers[r.dataset.field] !== undefined).length;
    $("form-progress").textContent = `${done} of ${shown.length} questions answered`;
  }

  function showForm(prefill = {}) {
    clearTimeout(pollTimer);
    selected = null;
    renderList();
    setBadge("New");
    const form = el("form", { class: "opp-form", novalidate: true });
    const startedAt = new Date().toISOString();   // Microsoft Forms "Start time"
    const sections = FORM.map((s) => el("fieldset", { class: `form-section${s.added ? " added" : ""}`, "data-section": s.id },
      el("legend", {}, s.title, s.added ? el("span", { class: "pill added-pill" }, "Added for funding") : null),
      s.description ? el("p", { class: "muted small" }, s.description) : null,
      s.fields.map((f) => el("div", { class: `form-row${f.type === "textarea" ? " wide" : ""}`, "data-field": f.id },
        el("label", { for: f.type === "radio" ? null : `q-${f.id}`, id: `q-${f.id}-label`, class: "q-label" },
          f.label, f.required ? el("span", { class: "req", "aria-hidden": "true" }, " *") : null),
        inputFor(f, prefill[f.id]),
        f.help ? el("span", { class: "muted q-help", id: `q-${f.id}-help` }, f.help) : null,
        f.column ? el("code", { class: "col-name", title: "Dynamics 365 column on the opportunity" }, f.column) : null,
        el("span", { class: "q-error error", id: `q-${f.id}-error` })))));
    const sampleBar = el("div", { class: "sample-bar" }, el("span", { class: "muted small" }, "Fill with a sample:"),
      SAMPLES.map((s) => {
        const b = el("button", { type: "button", class: "btn sm ghost" }, s.name);
        b.addEventListener("click", () => showForm(s.answers));
        return b;
      }));
    const runCheckBox = el("label", { class: "check" }, el("input", { type: "checkbox", id: "check-now", checked: true }),
      "Run the funding check as soon as it is saved");
    const submit = el("button", { type: "submit", class: "btn primary block" },
      mode === "dynamics" ? "Create in Dynamics 365" : "Create opportunity");
    const note = el("p", { class: "status-line muted", role: "status", "aria-live": "polite" });
    form.append(sampleBar, ...sections, el("div", { class: "run-bar" }, runCheckBox, submit, note));
    form.addEventListener("input", () => applyVisibility(form));
    form.addEventListener("change", () => applyVisibility(form));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      form.querySelectorAll(".q-error").forEach((n) => { n.textContent = ""; });
      form.querySelectorAll(".invalid").forEach((n) => n.classList.remove("invalid"));
      const answers = readForm(form);
      const missing = FORM.flatMap((s) => s.fields).filter((f) => f.required && !answers[f.id]);
      if (missing.length) { showErrors(form, Object.fromEntries(missing.map((f) => [f.id, "required"]))); note.textContent = "Fill in the required questions."; note.className = "status-line error"; return; }
      submit.disabled = true;
      note.className = "status-line muted";
      note.textContent = mode === "dynamics" ? "Creating the opportunity in Dynamics 365…" : "Creating the opportunity…";
      try {
        const res = await api("POST", "/funding/opportunities", { answers, check: $("check-now").checked, started_at: startedAt });
        loadList();
        if (res.check) { selected = res.opportunity.id; watchCheck(res.check.check_id, res.opportunity); }
        else showOpportunity(res.opportunity, res.check_error);
      } catch (err) {
        submit.disabled = false;
        note.className = "status-line error";
        note.textContent = err.message;
        if (err.data?.fields) showErrors(form, err.data.fields);
      }
    });
    view.replaceChildren(el("p", { class: "card-sub" }, "All questions from the intake form. Sections appear for the request type you pick. ",
      el("b", {}, "AWS funding"), " adds the inputs the decision framework needs."),
      el("p", { class: "muted small", id: "form-progress" }), form);
    applyVisibility(form);
    form.querySelector("select, input")?.focus({ preventScroll: true });
  }

  function showErrors(form, errors) {
    let first = null;
    for (const [fid, msg] of Object.entries(errors)) {
      const row = form.querySelector(`[data-field="${fid}"]`);
      if (!row) continue;
      row.classList.add("invalid");
      row.querySelector(".q-error").textContent = msg;
      first = first || row;
    }
    first?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  // ---------------------------------------------------------------- opportunity -> check
  let pollTimer = null;

  function showOpportunity(o, warning) {
    clearTimeout(pollTimer);
    selected = o.id;
    renderList();
    if (o.latest?.check_id) { watchCheck(o.latest.check_id, o); return; }
    setBadge("Not checked");
    const btn = el("button", { type: "button", class: "btn primary block" }, "Run funding check");
    btn.addEventListener("click", () => runCheck(o.id, o));
    view.replaceChildren(oppHeader(o), warning ? el("p", { class: "error" }, warning) : null,
      el("p", { class: "card-sub" }, "The agent fetches this opportunity from ",
        "Dynamics 365",
        ", reads the form answers and notes, and applies the decision framework."), btn);
  }

  function oppHeader(o, result) {
    const r = result?.opportunity || o;
    return el("div", { class: "opp-header" },
      el("div", {}, el("h3", {}, r.customer || o.customer || "", r.customer || o.customer ? " · " : "", r.name || o.name || "Opportunity"),
        el("div", { class: "muted small-r" },
          "Dynamics 365", " · ", el("code", {}, r.id || o.id))),
      r.url || o.url ? el("a", { class: "btn sm", href: r.url || o.url, target: "_blank", rel: "noopener noreferrer" }, "Open in Dynamics ↗") : null);
  }

  async function runCheck(id, o) {
    clearTimeout(pollTimer);
    selected = id;
    renderList();
    setBadge("Starting", "running");
    view.replaceChildren(oppHeader(o), progressView([]));
    try {
      const res = await api("POST", "/funding/checks", { opportunity_id: id });
      loadList();
      watchCheck(res.check_id, o);
    } catch (err) {
      setBadge("Failed", "failed");
      view.replaceChildren(oppHeader(o), el("p", { class: "error" }, err.message));
    }
  }

  function progressView(logs) {
    const steps = [["fetch", "Fetch from Dynamics"], ["facts", "Classify facts"], ["decide", "Apply framework"], ["record", "Record decision"]];
    const seen = new Set(logs.map((l) => l.step));
    const at = steps.findIndex(([s]) => !seen.has(s));
    return el("div", {},
      el("ol", { class: "phases" }, steps.map(([s, label], n) => el("li", { class: seen.has(s) ? "done" : n === at ? "active" : "" }, label))),
      el("div", { class: "empty" }, svgIcon(["M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"]),
        el("div", {}, "The funding agent is working (usually 10–20 seconds)…")),
      logBox(logs));
  }

  function logBox(logs) {
    if (!logs.length) return null;
    return el("details", { class: "log" }, el("summary", {}, "Agent log"),
      el("div", { class: "logs" }, logs.map((l) => `${l.at.slice(11, 19)}  ${l.level.padEnd(5)} ${l.step}: ${l.message}`).join("\n")));
  }

  async function watchCheck(checkId, o) {
    clearTimeout(pollTimer);
    try {
      const c = await api("GET", `/funding/checks/${encodeURIComponent(checkId)}`);
      if (selected && selected !== c.opportunity_id) return;   // the user moved on
      if (!DONE.has(c.status)) {
        setBadge("Running", "running");
        view.replaceChildren(oppHeader(o), progressView(c.logs || []));
        pollTimer = setTimeout(() => watchCheck(checkId, o), POLL_MS);
        return;
      }
      if (c.status === "FAILED") {
        setBadge("Failed", "failed");
        const retry = el("button", { type: "button", class: "btn" }, "Try again");
        retry.addEventListener("click", () => runCheck(c.opportunity_id, o));
        view.replaceChildren(oppHeader(o), el("p", { class: "error" }, c.error || "The check failed."), retry, logBox(c.logs || []));
        return;
      }
      renderResult(c, o);
      loadList();
    } catch (err) {
      setBadge("Error", "failed");
      view.replaceChildren(oppHeader(o), el("p", { class: "error" }, `Could not read the check: ${err.message}`));
    }
  }

  // ---------------------------------------------------------------- the decision
  const STATUS_LONG = { decided: "Funding determined" };
  const SOURCE_TEXT = { form: "Form", agent: "Agent (from text)" };

  function renderResult(c, o) {
    const r = c.result;
    const d = r.decision;
    setBadge(STATUS_LONG.decided, "done");
    const again = el("button", { type: "button", class: "btn sm" }, "Run again");
    again.addEventListener("click", () => runCheck(c.opportunity_id, o));
    const copy = el("button", { type: "button", class: "btn sm ghost" }, "Copy questions");
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText((d.assumptions || []).map((m, i) => `${i + 1}. ${m.question}`).join("\n")); copy.textContent = "Copied"; } catch { /* blocked */ }
    });

    view.replaceChildren(
      oppHeader(o, r),
      el("div", { class: `decision dec-${d.status}` },
        el("div", { class: "decision-head" },
          el("div", {},
            el("div", { class: "decision-label" }, STATUS_LONG.decided),
            el("div", { class: "decision-program" }, d.program || d.funding)),
          d.total_usd !== null && d.total_usd !== undefined
            ? el("div", { class: "decision-amount" }, el("span", { class: "muted" }, "Estimated funding"), el("b", {}, usd(d.total_usd)))
            : null),
        d.program ? el("div", { class: "decision-funding" }, d.funding) : null,
        el("p", { class: "decision-why" }, d.rationale),
        d.components?.length ? el("table", { class: "components" },
          el("thead", {}, el("tr", {}, el("th", {}, "Component"), el("th", {}, "Basis"), el("th", {}, "Amount"))),
          el("tbody", {}, d.components.map((x) => el("tr", {}, el("td", {}, x.name), el("td", {}, x.basis), el("td", {}, usd(x.amount_usd)))))) : null),

      el("div", { class: "section-label" }, "Decision path"),
      el("ol", { class: "path" }, d.path.map((p, n) => el("li", { class: p.known ? "known" : "unknown" },
        el("span", { class: "path-n" }, String(n + 1)), el("div", {}, el("b", {}, p.step), el("div", { class: "muted" }, p.value))))),

      d.alternatives?.length ? [el("div", { class: "section-label" }, "Depends on"),
        el("div", { class: "alts" }, d.alternatives.map((a) => el("div", { class: "alt" },
          el("div", { class: "muted small-r" }, `If ${a.when.toLowerCase()}`),
          el("b", {}, a.program), el("div", { class: "muted" }, a.funding),
          a.components?.length ? el("div", { class: "small-r" }, a.components.map((x) => `${x.name}: ${usd(x.amount_usd)}`).join(" · ")) : null)))] : null,

      d.assumptions?.length ? [el("div", { class: "section-label" }, `Best-fit assumptions, please confirm (${d.assumptions.length})`),
        el("ol", { class: "gaps" }, d.assumptions.map((a) => el("li", {}, el("div", { class: "gap-q" },
          el("span", {}, `${a.question} `), el("b", {}, `Assumed: ${a.assumed}`))))),
        el("div", { class: "actions" }, copy)] : null,

      [...(d.notes || []), ...(r.conflicts || [])].length ? [el("div", { class: "section-label" }, "Notes"),
        el("ul", { class: "notes" }, [...d.notes, ...r.conflicts].map((n) => el("li", {}, n)))] : null,

      el("div", { class: "section-label" }, "Facts used"),
      el("div", { class: "field-table" }, r.facts.map((f) => el("div", { class: `field-row${f.used ? "" : " missing"}` },
        el("div", { class: "field-label" }, f.label),
        el("div", {},
          el("div", { class: "field-value" }, f.display,
            f.source ? el("span", { class: `pill fact-src ${f.source}` }, SOURCE_TEXT[f.source]) : null),
          f.evidence ? el("blockquote", {}, f.source === "form" ? `${f.field}: ${f.evidence}` : `“${f.evidence}”`,
            f.column ? el("code", { class: "col-name" }, f.column) : null,
            f.source === "agent" ? el("span", { class: f.verified ? "src-ok" : "src-warn" },
              f.verified ? " ✓ found in the opportunity" : ` ⚠ quote not found, not used${f.suggested ? ` (suggested: ${f.suggested})` : ""}`) : null) : null)))),

      r.summary ? [el("div", { class: "section-label" }, "Agent summary"), el("p", {}, r.summary)] : null,
      r.model_error ? el("p", { class: "hint" }, `The opportunity text could not be processed (${r.model_error}); the decision uses the form answers only.`) : null,
      el("div", { class: "actions" }, again,
        el("span", { class: "muted small-r" }, `Checked ${when(r.checked_at)}`,
          r.written_back ? (r.opportunity.source === "dynamics" ? " · saved as a note in Dynamics" : " · saved with the opportunity") : "")),
      logBox(c.logs || []));
  }

  // ---------------------------------------------------------------- start
  setUser(null);
  $("gate").hidden = true;
  if (!cfg.apiUrl) { gateNote("ui/config.js has no apiUrl yet. Run scripts/deploy.sh first.", true); $("gate").hidden = false; return; }

  async function start() {
    try {
      const c = await api("GET", "/funding/config");
      setMode(c.mode, c.flavour === "mock" ? "" : c.dynamics_host);
    } catch (err) { listNote(`Could not reach the API: ${err.message}`, true); }
    loadList();
    showForm();
  }

  try {
    const s = await api("GET", "/session");
    setUser(s.microsoft);
    if (s.microsoft) start();
  } catch (err) {
    $("gate").hidden = false;
    gateNote(`Could not reach the API: ${err.message}`, true);
  }
})();
