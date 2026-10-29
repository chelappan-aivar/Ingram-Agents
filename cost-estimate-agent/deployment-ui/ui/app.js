// Ingram Cost Calculator console for the deployed backend:
//   POST {api} {user_input, session_id}  -> 202 (the proxy Lambda starts the agent in the background)
//   GET  {api}?session_id=...            -> {status: processing | success | fail, data: agent output, error}
// The API has no list of runs, so the history is kept in this browser (localStorage).
// All agent output is rendered as text nodes (no innerHTML). The calculator link is shown only for calculator.aws.
(() => {
  const cfg = window.COST_CONFIG || {};
  const POLL_MS = 5000;
  const POLL_CAP_MS = 20 * 60 * 1000;   // stop polling one run after 20 minutes
  const MIN_TEXT = 30;
  const MAX_TEXT = 20000;
  const MAX_RUNS = 25;
  const EXPECTED_S = 90;                // typical estimate time, only used to move the progress bar
  const CALC_PREFIX = "https://calculator.aws/";
  const STORE_KEY = "costRuns.v1";
  const STATUS = { running: "Working", completed: "Ready", failed: "Failed", unknown: "Unknown", example: "Example" };
  const STATUS_CLASS = { running: "s-running", completed: "s-completed", failed: "s-failed", unknown: "s-partial", example: "s-partial" };

  const $ = (id) => document.getElementById(id);
  const fill = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false && k !== ""));
  let current = null;          // the run on screen: {id, title, created_at, status, input, response, error, log, example?}
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
  const nowIso = () => new Date().toISOString();
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");
  const fmtNum = (n) => (typeof n === "number" ? n.toLocaleString() : String(n ?? "–"));
  const fmtDur = (s) => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`);
  const fmtSize = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  const ext = (name) => (String(name).split(".").pop() || "").toLowerCase();
  const statusPill = (s) => el("span", { class: `pill ${STATUS_CLASS[s] || ""}` }, STATUS[s] || s);
  const safeCalcUrl = (u) => (typeof u === "string" && u.startsWith(CALC_PREFIX) ? u : null);
  const humanKey = (k) => k.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ");
  const fmtValue = (v) => (typeof v === "number" ? v.toLocaleString() : typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));

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
  async function api(method, query, body) {
    if (!cfg.apiUrl) throw new Error("The API URL is not configured (config.js).");
    const url = query ? `${cfg.apiUrl}?${new URLSearchParams(query)}` : cfg.apiUrl;
    const resp = await fetch(url, {
      method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error(data.error || data.message || `HTTP ${resp.status}`);
      err.status = resp.status;
      throw err;
    }
    return data;
  }
  const newRunId = () => {
    const b = crypto.getRandomValues(new Uint8Array(12));
    return `web-${Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("")}`;
  };

  // ---------------------------------------------------------------- local run store
  function loadRuns() {
    try {
      const list = JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
      return Array.isArray(list) ? list.filter((r) => r && typeof r.id === "string") : [];
    } catch { return []; }
  }
  let runs = loadRuns();
  function saveRuns() {
    let list = runs.slice(0, MAX_RUNS);
    for (;;) {
      try { localStorage.setItem(STORE_KEY, JSON.stringify(list)); return; }
      catch (err) {
        if (list.length <= 1) { console.warn("could not save the history", err); return; }
        list = list.slice(0, -1);
        runs = runs.slice(0, list.length);
      }
    }
  }
  function log(run, stage, message, level = "info") {
    run.log = (run.log || []).concat({ at: nowIso(), stage, level, message }).slice(-40);
  }

  // The agent's answer: {success, calculator_url, estimate_id, data: {estimate_name, region, currency, environments,
  // unsupported_services?, free_tier?}} or {success: false, error, message}, or {response: "<text>"} when not JSON.
  function summary(response) {
    const r = response && typeof response === "object" ? response : {};
    const data = r.data && typeof r.data === "object" ? r.data : {};
    const envs = data.environments && typeof data.environments === "object" ? data.environments : {};
    const services = Object.values(envs).reduce((n, e) => n + (Array.isArray(e && e.services) ? e.services.length : 0), 0);
    const url = safeCalcUrl(r.calculator_url || data.calculator_url);
    const ok = r.success !== false && !!url;
    const error = ok ? null : String(r.message || r.error || r.response || "The agent did not return an estimate.");
    return { ok, url, data, envs, services, error, detail: r.success === false && r.error && r.message ? String(r.error) : null };
  }

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

  async function loadTextFile(file) {
    if (!["txt", "md", "json"].includes(ext(file.name))) { formStatus(`${file.name}: use a .txt, .md or .json file.`, true); return; }
    if (file.size > MAX_TEXT * 4) { formStatus(`${file.name} is ${fmtSize(file.size)}; the requirements can be up to 20,000 characters.`, true); return; }
    let text = await file.text();
    if (ext(file.name) === "json") { try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* keep as is */ } }
    if (text.length > MAX_TEXT) { formStatus(`${file.name} has ${text.length.toLocaleString()} characters; the limit is 20,000.`, true); return; }
    $("text").value = text;
    if (!$("title").value) $("title").value = file.name.replace(/\.[^.]+$/, "");
    formStatus(`Loaded ${file.name}. Review it, then start the estimate.`);
    refreshForm();
  }
  $("file").addEventListener("change", async () => { if ($("file").files[0]) await loadTextFile($("file").files[0]); $("file").value = ""; });
  const drop = $("file-drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) loadTextFile(e.dataTransfer.files[0]); });

  $("run-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = $("text").value.trim();
    $("start").disabled = true;
    formStatus("Starting…");
    const run = { id: newRunId(), title: $("title").value.trim(), created_at: nowIso(), status: "running", input, log: [] };
    try {
      await api("POST", null, { user_input: input, session_id: run.id });
      log(run, "submitted", `Sent ${input.length.toLocaleString()} characters to the agent`);
      runs.unshift(run);
      saveRuns();
      formStatus("Started. The estimate appears on the right; you can leave this page and come back.");
      $("text").value = "";
      $("title").value = "";
      refreshForm();
      renderHistory();
      openRun(run.id);
      schedule(run);
    } catch (err) {
      formStatus(`Not started: ${err.message}`, true);
      refreshForm();
    }
  });

  // ---------------------------------------------------------------- samples (static copies, see scripts/sync_samples.py)
  const samples = Array.isArray(window.COST_SAMPLES) ? window.COST_SAMPLES : [];
  async function fetchOk(path) {
    const resp = await fetch(path);
    if (!resp.ok) throw new Error(`${path.split("/").pop()}: HTTP ${resp.status}`);
    return resp;
  }
  function renderSamples() {
    if (!samples.length) { fill($("samples"), el("li", { class: "empty" }, "No samples are available (samples.js).")); return; }
    fill($("samples"), samples.map((s) => {
      const use = el("button", { type: "button", class: "btn sm primary", title: "Load this sample into the form" }, "Use sample");
      use.addEventListener("click", () => useSample(s, use));
      const view = s.example ? el("button", { type: "button", class: "btn sm", title: "Show the estimate the agent built from this sample" }, "View example") : null;
      if (view) view.addEventListener("click", () => viewExample(s, view));
      return el("li", { class: "sample" },
        el("div", { class: "sample-head" }, el("b", {}, s.title || s.file)),
        s.summary ? el("p", { class: "muted small" }, s.summary) : null,
        el("ul", { class: "file-list" }, el("li", {}, el("span", { class: `pill ft-${ext(s.file)}` }, ext(s.file).toUpperCase()),
          el("a", { class: "file-name", href: s.path, target: "_blank", rel: "noopener", title: `Open ${s.file}` }, s.file),
          el("span", { class: "muted" }, fmtSize(s.size)))),
        el("div", { class: "actions" }, use, view));
    }));
  }
  async function useSample(s, btn) {
    btn.disabled = true;
    try {
      let text = await fetchOk(s.path).then((r) => r.text());
      if (ext(s.file) === "json") { try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* keep as is */ } }
      $("text").value = text.trim();
      $("title").value = s.title || "";
      refreshForm();
      formStatus(`Sample loaded: ${s.file}. Edit it or start the estimate.`);
      $("new-title").scrollIntoView({ behavior: "smooth", block: "start" });
      $("text").focus({ preventScroll: true });
    } catch (err) { formStatus(`Could not load the sample: ${err.message}`, true); }
    btn.disabled = false;
  }
  async function viewExample(s, btn) {
    btn.disabled = true;
    try {
      const [ex, input] = await Promise.all([fetchOk(s.example).then((r) => r.json()), fetchOk(s.path).then((r) => r.text())]);
      const sum = summary(ex.data);
      current = { id: `example:${s.file}`, example: true, title: `Example: ${s.title || s.file}`, created_at: ex.created_at,
        status: "example", input, response: ex.data, seconds: ex.seconds, error: sum.ok ? null : sum.error, log: [] };
      sessionStorage.removeItem("costRunId");
      renderRun();
      markCurrent();
      $("result-card").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) { formStatus(`Could not load the example: ${err.message}`, true); }
    btn.disabled = false;
  }

  // ---------------------------------------------------------------- history
  function renderHistory() {
    fill($("history"), runs.length ? runs.map(historyItem) : [el("li", { class: "empty" }, "No estimates yet.")]);
    markCurrent();
  }
  function historyItem(r) {
    const btn = el("button", { type: "button", class: "card-item", "data-run": r.id },
      el("div", { class: "card-main" },
        el("span", { class: "card-title" }, r.title || r.estimate_name || "Untitled estimate"),
        el("span", { class: "card-meta" }, statusPill(r.status), el("span", { class: "muted" }, fmtTime(r.created_at)),
          r.status === "completed" && typeof r.services === "number" ? el("span", { class: "muted" }, `${r.services} services`) : null)));
    btn.addEventListener("click", () => openRun(r.id));
    const remove = el("button", { type: "button", class: "btn sm ghost", "aria-label": `Remove ${r.title || "this estimate"} from this browser` }, "Remove");
    remove.addEventListener("click", () => {
      runs = runs.filter((x) => x.id !== r.id);
      saveRuns();
      clearTimeout(timers.get(r.id));
      if (current && current.id === r.id) { current = null; $("result").hidden = true; $("result-empty").hidden = false; sessionStorage.removeItem("costRunId"); }
      renderHistory();
    });
    return el("li", { class: "inline-row" }, btn, remove);
  }
  function markCurrent() {
    document.querySelectorAll("#history .card-item").forEach((b) => b.setAttribute("aria-current", String(!!current && b.dataset.run === current.id)));
  }

  // ---------------------------------------------------------------- one run: poll until success / fail
  // The proxy Lambda writes UTC times without a zone ("2026-10-25T09:00:00.123456").
  const utc = (s) => (typeof s === "string" && s ? Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`) : NaN);
  const timers = new Map();      // run id -> poll timer (every running run is polled, not only the one on screen)
  function schedule(run, ms = POLL_MS) {
    clearTimeout(timers.get(run.id));
    timers.set(run.id, setTimeout(() => poll(run), ms));
  }

  function openRun(id) {
    const run = runs.find((r) => r.id === id);
    if (!run) { sessionStorage.removeItem("costRunId"); return; }
    current = run;
    sessionStorage.setItem("costRunId", id);
    renderRun();
    markCurrent();
    if (run.status === "running" || run.status === "unknown" || !run.response) schedule(run, 0);   // check now
  }
  async function poll(run) {
    timers.delete(run.id);
    try {
      const res = await api("GET", { session_id: run.id });
      if (res.status === "success" || res.status === "fail") {
        const done = run.status !== "completed" && run.status !== "failed";
        run.response = res.data || null;
        const secs = (utc(res.updated_at) - utc(res.created_at)) / 1000;
        run.seconds = Number.isFinite(secs) && secs >= 0 ? secs : null;
        const sum = summary(run.response);
        if (res.status === "success" && sum.ok) {
          run.status = "completed";
          run.services = sum.services;
          run.estimate_name = sum.data.estimate_name;
          run.error = null;
          if (done) log(run, "done", `Estimate saved: ${sum.services} service(s) in ${Object.keys(sum.envs).length} environment(s)`);
        } else {
          run.status = "failed";
          run.error = res.status === "fail" ? String(res.error || "The agent call failed.") : sum.error;
          if (done) log(run, "failed", run.error, "error");
        }
        saveRuns();
        renderHistory();
        if (current === run) renderRun();
        return;
      }
      if (run.status === "running" && Date.now() - Date.parse(run.created_at) > POLL_CAP_MS) {
        run.status = "unknown";
        run.error = "Still processing after 20 minutes. Open it from the history later to check again.";
        log(run, "timeout", run.error, "warn");
        saveRuns();
        renderHistory();
        if (current === run) renderRun();
        return;
      }
    } catch (err) {
      if (err.status === 404) {                    // the server has no record of this id
        if (run.status === "running" || run.status === "unknown") {
          run.status = "unknown";
          run.error = "The server has no record of this estimate.";
          saveRuns();
          renderHistory();
        }
        if (current === run) renderRun();
        return;
      }
    }
    if (run.status === "running") schedule(run);
  }

  function tick() {
    clearInterval(tickTimer);
    const update = () => {
      if (!current || current.status !== "running") { clearInterval(tickTimer); return; }
      const s = Math.max(0, (Date.now() - Date.parse(current.created_at)) / 1000);
      const pct = Math.min(92, Math.max(3, Math.round((s / EXPECTED_S) * 80)));
      $("progress-bar").style.width = `${pct}%`;
      $("progress").setAttribute("aria-valuenow", String(pct));
      $("elapsed").textContent = `${fmtDur(s)} elapsed`;
    };
    update();
    tickTimer = setInterval(update, 1000);
  }

  function renderRun() {
    const run = current;
    $("result-empty").hidden = true;
    $("result").hidden = false;
    const sum = summary(run.response);
    $("run-title").textContent = run.title || sum.data.estimate_name || "Untitled estimate";
    $("run-sub").textContent = [run.example ? "Saved example" : `Started ${fmtTime(run.created_at)}`,
      sum.data.region ? `region ${sum.data.region}` : "", sum.data.currency || "",
      run.input ? `${fmtNum(run.input.length)} characters` : ""].filter(Boolean).join(" · ");
    fill($("status-pill"), statusPill(run.status));
    $("example-note").hidden = !run.example;
    $("example-note").textContent = run.example ? "This is a saved example from the samples, not one of your estimates. Use the sample to run it yourself." : "";

    const active = run.status === "running";
    $("running").hidden = !active;
    if (active) { $("running-text").textContent = "Building the estimate in the AWS Pricing Calculator…"; tick(); }
    const showError = run.status === "failed" || run.status === "unknown";
    $("run-error").hidden = !showError;
    $("run-error").textContent = showError ? `${run.status === "failed" ? "The estimate failed" : "Status unknown"}: ${run.error || "unknown error"}${sum.detail ? ` (${sum.detail.slice(0, 300)})` : ""}` : "";

    $("link-row").hidden = !sum.url;
    if (sum.url) { $("calc-link").setAttribute("href", sum.url); $("calc-url").textContent = sum.url; }
    renderStats(run, sum);
    renderServices(sum);
    $("input").textContent = run.input || "";
    $("input-panel").hidden = !run.input;
    $("raw").textContent = run.response ? JSON.stringify(run.response, null, 2) : "";
    $("raw-panel").hidden = !run.response;
    renderLog(run.log);
    $("log-panel").hidden = !(run.log || []).length;
  }

  $("copy-link").addEventListener("click", async () => {
    const url = current && summary(current.response).url;
    if (!url) return;
    try { await navigator.clipboard.writeText(url); $("copy-status").textContent = "Link copied."; }
    catch { $("copy-status").textContent = "Copy failed: select the link below and copy it."; }
    setTimeout(() => { $("copy-status").textContent = ""; }, 4000);
  });

  function stat(value, label) { return el("div", { class: "stat" }, el("b", {}, value), el("span", {}, label)); }
  function renderStats(run, sum) {
    if (!sum.ok) { fill($("stats")); return; }
    const unsupported = Array.isArray(sum.data.unsupported_services) ? sum.data.unsupported_services.length : 0;
    fill($("stats"),
      stat(fmtNum(sum.services), "Services priced"),
      stat(fmtNum(Object.keys(sum.envs).length), Object.keys(sum.envs).length === 1 ? "Environment" : "Environments"),
      unsupported ? stat(fmtNum(unsupported), "Not in the calculator") : null,
      run.seconds ? stat(fmtDur(Number(run.seconds)), "Agent time") : null);
  }

  function configChips(config) {
    const entries = Object.entries(config || {});
    if (!entries.length) return el("span", { class: "muted" }, "–");
    return el("div", { class: "kv" }, entries.map(([k, v]) => el("span", {}, el("b", {}, humanKey(k)), fmtValue(v))));
  }

  function renderServices(sum) {
    const unsupported = Array.isArray(sum.data.unsupported_services) ? sum.data.unsupported_services : [];
    fill($("unsupported"), unsupported.length ? el("div", { class: "note" },
      el("b", {}, `Not priced in the calculator (${unsupported.length})`),
      el("ul", {}, unsupported.map((u) => el("li", {}, el("b", {}, `${u.name || "Service"}: `), u.reason || "")))) : null);
    const parts = [];
    for (const [envName, env] of Object.entries(sum.envs)) {
      const list = Array.isArray(env && env.services) ? env.services : [];
      parts.push(el("h3", { class: "env-head" }, envName, el("span", { class: "count" }, list.length)));
      parts.push(el("div", { class: "table-wrap" }, el("table", { class: "usage" },
        el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Service"), el("th", { scope: "col" }, "Description"),
          el("th", { scope: "col" }, "Region"), el("th", { scope: "col" }, "Key configuration"))),
        el("tbody", {}, list.map((s) => {
          const conf = { ...(s.config || {}) };
          const region = conf.location || sum.data.region || "";
          delete conf.location;
          return el("tr", {}, el("td", { class: "svc" }, s.name || s.service_code || "–"), el("td", { class: "desc" }, s.description || ""),
            el("td", { class: "region" }, region), el("td", {}, configChips(conf)));
        })))));
    }
    fill($("services"), parts);
  }

  function renderLog(entries) {
    fill($("log"), (entries || []).slice().reverse().map((l) =>
      el("li", { class: `audit-item lvl-${l.level}` },
        el("div", { class: "audit-head" }, el("span", { class: "stage" }, l.stage), el("span", {}, l.message),
          el("span", { class: "muted" }, new Date(l.at).toLocaleTimeString())))));
  }

  // ---------------------------------------------------------------- start
  renderSamples();
  refreshForm();
  renderHistory();
  for (const r of runs) if (r.status === "running") schedule(r, 500);
  const last = sessionStorage.getItem("costRunId");
  if (last) openRun(last);
})();
