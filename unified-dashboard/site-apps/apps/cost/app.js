// Ingram Cost estimate agent console: free-text infrastructure in -> saved AWS Pricing Calculator estimate out.
// All agent output is rendered as text nodes (no innerHTML). The calculator link is shown only for calculator.aws.
(() => {
  const cfg = window.COST_CONFIG || {};
  const POLL_MS = 5000;
  const POLL_CAP_MS = 20 * 60 * 1000;   // stop polling one run after 20 minutes
  const MIN_TEXT = 30;
  const MAX_TEXT = 20000;
  const EXPECTED_S = 240;               // typical estimate time, only used to move the progress bar
  const CALC_PREFIX = "https://calculator.aws/";
  const ACTIVE = new Set(["queued", "running"]);
  const STATUS = { queued: "Queued", running: "Working", completed: "Ready", failed: "Failed" };
  const EXAMPLES = {
    web: ["Web app (us-east-1)",
      "Production in us-east-1, running 24x7:\n- 2 x Amazon EC2 t3.medium, Linux, behind an Application Load Balancer\n- Amazon RDS for PostgreSQL db.t3.medium, Multi-AZ, 100 GB gp3\n- Amazon S3 Standard: 500 GB, 1 million GET requests a month\n- Amazon CloudFront: 1 TB data transfer out a month"],
    serverless: ["Serverless API (eu-west-1)",
      "Serverless API in eu-west-1:\n- Amazon API Gateway HTTP API: 20 million requests a month\n- AWS Lambda: 20 million invocations, 512 MB, 200 ms average, arm64\n- Amazon DynamoDB on-demand: 50 GB storage, 30 million writes and 100 million reads a month\n- Amazon SQS standard: 10 million requests a month"],
    envs: ["Dev and Prod (ap-south-1)",
      "Two environments in ap-south-1.\nDevelopment: 1 x EC2 t3.small Linux (business hours, 200 hours a month), RDS for MySQL db.t3.micro Single-AZ with 20 GB, 50 GB S3 Standard.\nProduction: 3 x EC2 m6i.large Linux 24x7 behind an Application Load Balancer, RDS for MySQL db.m6i.large Multi-AZ with 200 GB, 1 TB S3 Standard, AWS Backup for 300 GB."],
  };

  const $ = (id) => document.getElementById(id);
  const fill = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false && k !== ""));
  let current = null;          // {run, result, log}
  let pollTimer = null;
  let pollStarted = 0;
  let tickTimer = null;

  // ---------------------------------------------------------------- DOM helpers
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
    for (const [k, v] of [["viewBox", "0 0 24 24"], ["fill", "none"], ["stroke", "currentColor"], ["stroke-width", "2"], ["aria-hidden", "true"]]) svg.setAttribute(k, v);
    for (const d of paths) {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d);
      svg.append(p);
    }
    return svg;
  }
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");
  const fmtNum = (n) => (typeof n === "number" ? n.toLocaleString() : String(n ?? "–"));
  const fmtDur = (s) => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`);
  const statusPill = (s) => el("span", { class: `pill s-${s}` }, STATUS[s] || s);
  const safeCalcUrl = (u) => (typeof u === "string" && u.startsWith(CALC_PREFIX) ? u : null);
  const humanKey = (k) => k.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ");

  // ---------------------------------------------------------------- theme
  const THEMES = [
    ["light", "Light theme", ["M12 4V2M12 22v-2M4 12H2M22 12h-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"]],
    ["dark", "Dark theme", ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"]],
    ["system", "System theme", ["M3 4h18v12H3zM8 20h8M12 16v4"]],
  ];
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(choice) {
    localStorage.setItem("costTheme", choice);
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
  systemDark.addEventListener("change", () => { if (localStorage.getItem("costTheme") === "system") applyTheme("system"); });
  applyTheme(localStorage.getItem("costTheme") || "dark");

  // ---------------------------------------------------------------- API (open: no sign-in)
  async function api(method, path, body) {
    if (!cfg.apiUrl) throw new Error("The API URL is not configured (config.js).");
    const resp = await fetch(`${cfg.apiUrl}${path}`, {
      method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || data.message || `HTTP ${resp.status}`);
    return data;
  }
  function showQuota(res) {
    if (res && typeof res.runs_today === "number") {
      $("quota").textContent = `${res.runs_today} of ${res.runs_per_day} estimates used today · up to ${res.runs_per_ip_hour} an hour per user`;
    }
  }
  const refreshQuota = () => api("GET", "/session").then(showQuota).catch(() => {});

  // ---------------------------------------------------------------- new estimate form
  function formStatus(text, isError = false) {
    $("form-status").textContent = text;
    $("form-status").className = `status-line ${isError ? "error" : "muted"}`;
  }
  function refreshForm() {
    const n = $("text").value.length;
    $("text-count").textContent = `${n.toLocaleString()} / ${MAX_TEXT.toLocaleString()}`;
    const len = $("text").value.trim().length;
    $("start").disabled = len < MIN_TEXT || len > MAX_TEXT;
    $("clear").disabled = !(n || $("title").value);
  }
  $("text").addEventListener("input", refreshForm);
  $("title").addEventListener("input", refreshForm);
  $("clear").addEventListener("click", () => { $("text").value = ""; $("title").value = ""; formStatus(""); refreshForm(); $("text").focus(); });
  document.querySelectorAll("[data-example]").forEach((chip) => chip.addEventListener("click", () => {
    const [title, text] = EXAMPLES[chip.dataset.example];
    $("text").value = text;
    if (!$("title").value) $("title").value = title;
    formStatus("Example loaded. Edit it or start the estimate.");
    refreshForm();
    $("text").focus();
  }));

  $("run-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("start").disabled = true;
    formStatus("Starting…");
    try {
      const res = await api("POST", "/runs", { title: $("title").value.trim(), requirements: $("text").value });
      formStatus("Started. The estimate appears on the right; you can leave this page and come back.");
      $("text").value = "";
      $("title").value = "";
      refreshForm();
      await openRun(res.run_id, true);
      loadHistory();
      refreshQuota();
    } catch (err) {
      formStatus(`Not started: ${err.message}`, true);
      refreshForm();
    }
  });

  // ---------------------------------------------------------------- history
  async function loadHistory() {
    const list = $("history");
    try {
      const { runs } = await api("GET", "/runs");
      fill(list, ...(runs.length ? runs.map(historyItem) : [el("li", { class: "empty" }, "No estimates yet.")]));
    } catch (err) {
      fill(list, el("li", { class: "error" }, `Could not load the history: ${err.message}`));
    }
  }
  function historyItem(r) {
    const btn = el("button", { type: "button", class: "card-item", "data-run": r.run_id, "aria-current": current && current.run.run_id === r.run_id ? "true" : "false" },
      el("div", { class: "card-main" },
        el("span", { class: "card-title" }, r.title || r.estimate_name || "Untitled estimate"),
        el("span", { class: "card-meta" }, statusPill(r.status), el("span", { class: "muted" }, fmtTime(r.created_at)),
          typeof r.services_count === "number" && r.status === "completed" ? el("span", { class: "muted" }, `${r.services_count} services`) : null)));
    btn.addEventListener("click", () => openRun(r.run_id, true));
    return el("li", {}, btn);
  }
  $("history-refresh").addEventListener("click", loadHistory);

  // ---------------------------------------------------------------- one run
  async function openRun(runId, fresh = false) {
    clearTimeout(pollTimer);
    if (fresh || !current || current.run.run_id !== runId) pollStarted = Date.now();
    try {
      const data = await api("GET", `/runs/${encodeURIComponent(runId)}`);
      const wasActive = current && current.run.run_id === runId && ACTIVE.has(current.run.status);
      current = data;
      sessionStorage.setItem("costRunId", runId);
      renderRun();
      if (ACTIVE.has(data.run.status)) {
        if (Date.now() - pollStarted < POLL_CAP_MS) pollTimer = setTimeout(() => openRun(runId), POLL_MS);
        else { $("running-text").textContent = "Still working after 20 minutes. Refresh the history later to check."; }
      } else if (wasActive) loadHistory();
    } catch (err) {
      if (String(err.message).includes("not found")) { sessionStorage.removeItem("costRunId"); return; }
      if (Date.now() - pollStarted < POLL_CAP_MS) pollTimer = setTimeout(() => openRun(runId), POLL_MS * 2);
    }
    document.querySelectorAll("#history .card-item").forEach((b) => b.setAttribute("aria-current", String(b.dataset.run === runId)));
  }

  function tick() {
    clearInterval(tickTimer);
    const update = () => {
      if (!current || !ACTIVE.has(current.run.status)) { clearInterval(tickTimer); return; }
      const since = Date.parse(current.run.started_at || current.run.created_at);
      const s = Math.max(0, (Date.now() - since) / 1000);
      const pct = Math.min(92, Math.max(3, Math.round((s / EXPECTED_S) * 90)));
      $("progress-bar").style.width = `${pct}%`;
      $("progress").setAttribute("aria-valuenow", String(pct));
      $("elapsed").textContent = `${fmtDur(s)} elapsed`;
    };
    update();
    tickTimer = setInterval(update, 1000);
  }

  function renderRun() {
    const { run, result, log } = current;
    $("result-empty").hidden = true;
    $("result").hidden = false;
    $("run-title").textContent = run.title || run.estimate_name || "Untitled estimate";
    $("run-sub").textContent = [`Started ${fmtTime(run.created_at)}`, run.input_chars ? `${fmtNum(run.input_chars)} characters` : ""]
      .filter(Boolean).join(" · ");
    fill($("status-pill"), statusPill(run.status));

    const active = ACTIVE.has(run.status);
    $("running").hidden = !active;
    if (active) {
      $("running-text").textContent = run.status === "queued" ? "Queued…" : "Building the estimate in the AWS Pricing Calculator…";
      tick();
    }
    $("run-error").hidden = run.status !== "failed";
    $("run-error").textContent = run.status === "failed" ? `The estimate failed${run.stage ? ` (${run.stage})` : ""}: ${run.error || "unknown error"}` : "";

    const url = safeCalcUrl(run.calculator_url);
    $("link-row").hidden = !url;
    if (url) {
      $("calc-link").setAttribute("href", url);
      $("calc-url").textContent = url;
    }
    renderStats(run);
    renderResult(result);
    renderLog(log);
  }

  $("copy-link").addEventListener("click", async () => {
    const url = current && safeCalcUrl(current.run.calculator_url);
    if (!url) return;
    try { await navigator.clipboard.writeText(url); $("copy-status").textContent = "Link copied."; }
    catch { $("copy-status").textContent = "Copy failed: select the link below and copy it."; }
    setTimeout(() => { $("copy-status").textContent = ""; }, 4000);
  });

  function stat(value, label) { return el("div", { class: "stat" }, el("b", {}, value), el("span", {}, label)); }
  function renderStats(run) {
    if (run.status !== "completed") { fill($("stats")); return; }
    fill($("stats"),
      stat(fmtNum(run.services_count), "Services priced"),
      typeof run.requested_count === "number" && run.requested_count ? stat(fmtNum(run.requested_count), "Services requested") : null,
      typeof run.missing_count === "number" ? stat(fmtNum(run.missing_count), "Missing after check") : null,
      run.seconds ? stat(fmtDur(Number(run.seconds)), "Agent time") : null);
  }

  function configChips(config) {
    const entries = Object.entries(config || {});
    if (!entries.length) return el("span", { class: "muted" }, "–");
    return el("div", { class: "kv" }, entries.map(([k, v]) =>
      el("span", {}, el("b", {}, humanKey(k)), typeof v === "object" && v !== null ? JSON.stringify(v) : String(v))));
  }

  function renderResult(result) {
    const services = $("services");
    if (!result) {
      fill($("out-note")); fill($("checklist-box"));
      fill(services, current.run.status === "completed" ? el("p", { class: "muted" }, "No stored estimate details.") : null);
      $("raw").textContent = "";
      $("raw-panel").hidden = true;
      return;
    }
    const estimate = result.estimate || {};
    fill($("out-note"), result.truncated ? el("p", { class: "note" }, result.note || "The stored estimate is incomplete.") : null);

    // requested-services checklist (from the agent's validation step)
    const checklist = Array.isArray(result.checklist) ? result.checklist : [];
    const missing = new Set(((result.coverage || {}).missing || []).map(String));
    const label = (c) => (c.environment ? `${c.name} (${c.environment})` : c.name);
    fill($("checklist-box"), checklist.length ? el("details", { class: "section", open: missing.size > 0 },
      el("summary", {}, `Requested services checklist (${checklist.length}${missing.size ? `, ${missing.size} not found in the saved estimate` : ""})`),
      el("ul", { class: "checklist", "aria-label": "Requested services" }, checklist.map((c) => {
        const miss = missing.has(label(c));
        return el("li", { class: miss ? "miss" : "" }, miss ? `${label(c)} – missing` : label(c));
      }))) : null);

    // services grouped by environment
    const parts = [];
    const envs = estimate.environments && typeof estimate.environments === "object" ? estimate.environments : {};
    for (const [envName, env] of Object.entries(envs)) {
      const list = Array.isArray(env && env.services) ? env.services : [];
      parts.push(el("h3", { class: "env-head" }, envName, el("span", { class: "count" }, list.length)));
      parts.push(el("div", { class: "table-wrap" }, el("table", { class: "usage" },
        el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Service"), el("th", { scope: "col" }, "Description"),
          el("th", { scope: "col" }, "Region"), el("th", { scope: "col" }, "Key configuration"))),
        el("tbody", {}, list.map((s) => {
          const conf = { ...(s.config || {}) };
          const region = conf.location || estimate.region || "";
          delete conf.location;
          return el("tr", {}, el("td", { class: "svc" }, s.name || s.service_code || "–"), el("td", { class: "desc" }, s.description || ""),
            el("td", { class: "region" }, region), el("td", {}, configChips(conf)));
        })))));
    }
    const meta = [estimate.estimate_name, estimate.region ? `region ${estimate.region}` : "", estimate.currency || ""].filter(Boolean).join(" · ");
    fill(services, meta ? el("p", { class: "out-note" }, meta) : null, ...parts);
    $("raw").textContent = JSON.stringify(estimate, null, 2);
    $("raw-panel").hidden = false;
  }

  function renderLog(log) {
    fill($("log"), ...(log || []).slice().reverse().map((l) =>
      el("li", { class: `audit-item lvl-${l.level}` },
        el("div", { class: "audit-head" }, el("span", { class: "stage" }, l.stage), el("span", {}, l.message),
          el("span", { class: "muted" }, new Date(l.at).toLocaleTimeString())))));
  }

  // ---------------------------------------------------------------- start
  refreshForm();
  loadHistory();
  refreshQuota();
  const lastRun = sessionStorage.getItem("costRunId");
  if (lastRun) openRun(lastRun, true);
})();
