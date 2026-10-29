// Funding Desk: lists the opportunities in Microsoft Dynamics 365 and runs the AWS Funding Programs Decision
// Framework on them through the funding agent (one at a time or in batches). Own Amplify app; same API.
(async () => {
  const cfg = window.INTAKE_CONFIG || {};
  const FORM = window.OPPORTUNITY_FORM || [];
  const LABELS = Object.fromEntries(FORM.flatMap((s) => s.fields.map((f) => [f.id, f.label])));
  const SECTION = Object.fromEntries(FORM.flatMap((s) => s.fields.map((f) => [f.id, s.title])));
  const COLUMN = Object.fromEntries(FORM.flatMap((s) => s.fields.map((f) => [f.id, f.column])));
  const META = { _form_response_id: ["Id", "ingr_formresponseid"], _form_start_time: ["Start time", "ingr_formstarttime"],
    _form_completion_time: ["Completion time", "ingr_formcompletiontime"], _submitted_by: ["Email", "ingr_submitteremail"],
    _submitted_name: ["Name", "ingr_submittername"] };
  const POLL_MS = 3000;
  const MAX_BATCH = 25;
  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- helpers (no innerHTML: all data is text)
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === false || v === null || v === undefined) continue;
      if (k === "class") node.className = v; else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat(Infinity)) {
      if (c !== null && c !== undefined && c !== "" && c !== false) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  function svgIcon(paths) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2", "aria-hidden": "true" })) svg.setAttribute(k, v);
    for (const d of paths) { const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", d); svg.append(p); }
    return svg;
  }
  const usd = (n) => (n === null || n === undefined ? "–" : `${Math.round(n).toLocaleString("en-US")} USD`);
  const usdShort = (n) => (n === null || n === undefined ? "–" : n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `$${Math.round(n / 1e3)}k` : `$${Math.round(n)}`);
  const day = (iso) => (iso ? new Date(iso).toLocaleDateString([], { dateStyle: "medium" }) : "–");
  const when = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");

  // ---------------------------------------------------------------- theme
  const THEMES = [
    ["light", "Light theme", ["M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"]],
    ["dark", "Dark theme", ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"]],
    ["system", "System theme", ["M3 4h18v12H3zM8 20h8M12 16v4"]],
  ];
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(choice) {
    localStorage.setItem("deskTheme", choice);
    document.documentElement.dataset.theme = choice === "system" ? (systemDark.matches ? "dark" : "light") : choice;
    document.querySelectorAll("[data-theme-choice]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeChoice === choice)));
  }
  document.querySelectorAll("[data-theme-toggle]").forEach((g) => {
    for (const [choice, label, paths] of THEMES) {
      const b = el("button", { type: "button", "data-theme-choice": choice, "aria-label": label, title: label });
      b.append(svgIcon(paths));
      b.addEventListener("click", () => applyTheme(choice));
      g.append(b);
    }
  });
  systemDark.addEventListener("change", () => { if ((localStorage.getItem("deskTheme") || "dark") === "system") applyTheme("system"); });
  applyTheme(localStorage.getItem("deskTheme") || "dark");

  // ---------------------------------------------------------------- session (this app's own origin and storage)
  const SESSION_KEY = "deskSession";
  let session = (() => {
    try { const s = JSON.parse(localStorage.getItem(SESSION_KEY) || "null"); return s && s.expires_at > Date.now() + 60000 ? s : null; } catch { return null; }
  })();
  async function ensureSession() {
    if (session) return;
    const resp = await fetch(`${cfg.apiUrl}/session`, { method: "POST" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    session = { token: data.session, expires_at: Date.now() + data.expires_in * 1000 };
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  }
  async function api(method, path, body, retry = true) {
    await ensureSession();
    const resp = await fetch(`${cfg.apiUrl}${path}`, {
      method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.token}` },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 401 && retry) { session = null; localStorage.removeItem(SESSION_KEY); return api(method, path, body, false); }
    if ((resp.status === 403 || resp.status === 409) && "microsoft" in data) setUser(null);
    if (!resp.ok) throw new Error(data.error || data.message || `HTTP ${resp.status}`);
    return data;
  }

  // ---------------------------------------------------------------- Microsoft sign-in (device code)
  let loginTimer = null;
  let loginId = null;
  function setUser(u) {
    $("gate").hidden = !!u;
    $("workspace").hidden = !u;
    $("user-chip").hidden = !u;
    $("who").textContent = u ? u.email : "";
    const name = u ? (u.name || u.email) : "";
    $("avatar").textContent = name.split(/[\s.@]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
  }
  const gateNote = (m, err = false) => { $("gate-note").textContent = m || ""; $("gate-note").className = `status-line ${err ? "error" : "muted"}`; };
  function resetConnect(m, err = false) {
    clearTimeout(loginTimer); loginId = null;
    $("device").hidden = true; $("connect").hidden = false; $("connect").disabled = false;
    gateNote(m, err);
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
      $("connect").hidden = true; $("device").hidden = false; gateNote("");
      loginTimer = setTimeout(() => pollLogin(res.interval), res.interval * 1000);
    } catch (err) { resetConnect(`Could not start: ${err.message}`, true); }
  });
  $("copy-code").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("device-code").textContent); $("copy-code").textContent = "Copied"; } catch { /* copy by hand */ }
    setTimeout(() => { $("copy-code").textContent = "Copy"; }, 1500);
  });
  $("device-cancel").addEventListener("click", () => resetConnect());

  // ---------------------------------------------------------------- pipeline
  const STATUS = { decided: ["Funding determined", "dec-decided"] };   // every opportunity maps to a programme
  let mode = "demo";
  let fetched = false;   // nothing on screen until "Fetch from Dynamics" (and again after "Clear results")
  let flavourNow = "demo";
  let opps = [];
  let filter = "all";
  let selectedId = null;
  const picked = new Set();
  let pollTimer = null;
  // Decisions saved earlier (by anyone) stay hidden until "Check funding" is run on that opportunity in this session.
  const checkedNow = new Set();
  const hideOldDecision = (o) => { if (!checkedNow.has(o.id) && !o.running) { o.latest = null; o.last_error = null; } return o; };

  // Two outcomes only: a funding decision exists (decided), or it is still pending assessment.
  const bucket = (o) => (o.latest?.status === "decided" ? "decided" : "unchecked");
  const shown = () => (filter === "all" ? opps : opps.filter((o) => bucket(o) === filter));
  const listNote = (m, err = false) => { $("list-note").textContent = m || ""; $("list-note").className = `status-line ${err ? "error" : "muted"}`; };
  const bulkNote = (m, err = false) => { $("bulk-note").textContent = m || ""; $("bulk-note").className = `small-r ${err ? "error" : "muted"}`; };

  function setMode(m, n, flavour = m) {
    mode = m;
    flavourNow = flavour;
    const pillLabel = { dynamics: "Dynamics 365 · live", mock: "",
      demo: "Dynamics 365 not connected" }[flavour];
    $("mode-pill").hidden = !pillLabel;
    $("mode-pill").textContent = pillLabel;
    $("mode-pill").className = `pill mode-pill ${flavour === "mock" ? "demo" : m}`;
    $("source-line").textContent = { dynamics: `${n} newest from Dynamics 365`,
      mock: `${n} from Dynamics 365`,
      demo: `${n} opportunities` }[flavour];
    $("seed").hidden = m !== "demo";
  }

  function renderStats() {
    const count = (b) => opps.filter((o) => bucket(o) === b).length;
    $("s-total").textContent = opps.length;
    $("s-unchecked").textContent = count("unchecked");
    $("s-decided").textContent = count("decided");
    const total = opps.filter((o) => o.latest?.status === "decided" && o.latest.total_usd).reduce((n, o) => n + o.latest.total_usd, 0);
    $("s-funding").textContent = total ? usdShort(total) : "–";
    const byProgram = {};
    for (const o of opps) if (o.latest?.program) byProgram[o.latest.program] = (byProgram[o.latest.program] || 0) + 1;
    $("programs").replaceChildren(...Object.entries(byProgram).sort((a, b) => b[1] - a[1]).map(([p, n]) =>
      el("span", { class: "pill prog" }, `${p} · ${n}`)));
    document.querySelectorAll("#filter button").forEach((b) => {
      const n = b.dataset.f === "all" ? opps.length : count(b.dataset.f);
      b.textContent = `${{ all: "All", unchecked: "Pending assessment", decided: "Funding determined" }[b.dataset.f]} (${n})`;
    });
  }

  function decisionCell(o) {
    if (o.running) return el("span", { class: "dec-cell" },
      el("span", { class: "pill running-pill" }, el("span", { class: "spin", "aria-hidden": "true" }), "Checking…"),
      o.stage ? el("span", { class: "stage" }, o.stage) : null);
    if (o.last_error && !o.latest) return el("span", { class: "pill dec-failed", title: o.last_error.error }, "Assessment failed");
    if (o.latest?.status !== "decided") return el("span", { class: "pill dec-none" }, "Pending assessment");
    return el("span", { class: "dec-cell" }, el("span", { class: "pill dec-decided" }, o.latest.program || "Funding determined"));
  }

  function renderRows() {
    const list = shown();
    $("rows").replaceChildren(...(list.length ? list.map((o) => {
      const sel = el("input", { type: "checkbox", "aria-label": `Select ${o.name}`, checked: picked.has(o.id) || null, disabled: o.running || o.mailOnly || null });
      sel.addEventListener("click", (e) => e.stopPropagation());
      sel.addEventListener("change", () => { sel.checked ? picked.add(o.id) : picked.delete(o.id); updateBulk(); });
      const check = el("button", { type: "button", class: "btn sm", disabled: o.running || o.mailOnly || null, title: o.mailOnly ? "Email-only: not in Dynamics yet" : null }, o.latest ? "Re-check" : "Check");
      check.addEventListener("click", (e) => { e.stopPropagation(); runChecks([o.id]); });
      const tr = el("tr", { class: [o.id === selectedId ? "current" : "", o.reveal ? "reveal" : "", o.flash ? "flash" : ""].join(" ").trim(),
        tabindex: "0", "aria-selected": String(o.id === selectedId) },
        el("td", { class: "c-sel" }, sel),
        el("td", {}, el("div", { class: "o-name" }, o.name || "Opportunity"),
          el("div", { class: "muted small-r" }, o.customer || "No account",
            o.request_type ? ` · ${o.request_type.replace(" Opportunity", "")}` : "",
            o.workload && o.workload !== "Not sure" ? ` · ${o.workload}` : "",
            o.state && o.state !== "Open" ? ` · ${o.state}` : "")),
        el("td", {}, srcCell(o)),
        el("td", { class: "num" }, usdShort(o.estimated_value)),
        el("td", { class: "nowrap" }, day(o.close_date)),
        el("td", {}, decisionCell(o)),
        el("td", { class: "num" }, o.latest?.total_usd ? usdShort(o.latest.total_usd) : "–"),
        el("td", { class: "c-act" }, check));
      const open = () => openDetail(o.id);
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      return tr;
    }) : [el("tr", {}, el("td", { colspan: "8", class: "muted empty-row" },
      opps.length ? "No opportunities match this filter." : !fetched ? "Nothing fetched yet. Click Fetch opportunities to load the opportunities."
        : mode === "demo" ? "No opportunities yet. Load the sample opportunities to try the desk." : "No opportunities found in Dynamics."))]));
    for (const o of list) { o.reveal = false; o.flash = false; }   // animate once
    const selectable = list.filter((o) => !o.running && !o.mailOnly);
    $("sel-all").checked = selectable.length > 0 && selectable.every((o) => picked.has(o.id));
    $("sel-all").disabled = !selectable.length;
    updateBulk();
  }

  function updateBulk() {
    for (const id of [...picked]) if (!opps.some((o) => o.id === id && !o.running && !o.mailOnly)) picked.delete(id);
    $("check-selected").disabled = !picked.size;
    $("check-selected").textContent = picked.size ? `Check selected (${picked.size})` : "Check selected";
    $("check-selected").disabled = !picked.size || run.active;
    const checkable = opps.filter((o) => !o.running && !o.mailOnly).length;
    $("fetch").disabled = run.active;
    $("check-all").disabled = run.active || !checkable;
    if (!run.active) $("check-all").textContent = checkable ? `▶ Check funding (${checkable})` : "▶ Check funding";
    $("clear").disabled = run.active || (!fetched && $("live").hidden);
  }

  async function loadList(quiet = false) {
    if (!quiet) listNote("Fetching opportunities from Dynamics…");
    try {
      const q = $("q").value.trim();
      const res = await api("GET", `/funding/dynamics?state=${$("state").value}${q ? `&search=${encodeURIComponent(q)}` : ""}`);
      opps = res.opportunities.map(hideOldDecision);
      fetched = true;
      setMode(res.mode, opps.length, res.flavour || res.mode);
      applyMail();
      listNote("");
      renderStats();
      renderRows();
      if (selectedId && opps.some((o) => o.id === selectedId) && !quiet) openDetail(selectedId, false);
      schedulePoll();
    } catch (err) { listNote(`Could not load opportunities: ${err.message}`, true); }
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (run.active) return;   // the live run polls check status itself
    if (opps.some((o) => o.running)) pollTimer = setTimeout(async () => {
      const wasRunning = new Set(opps.filter((o) => o.running).map((o) => o.id));
      await loadList(true);
      if (selectedId && wasRunning.has(selectedId) && !opps.find((o) => o.id === selectedId)?.running) openDetail(selectedId, false);
    }, POLL_MS);
  }

  async function runChecks(ids) {
    ids = ids.slice(0, MAX_BATCH);
    bulkNote(`Starting ${ids.length} check${ids.length === 1 ? "" : "s"}…`);
    try {
      const res = await api("POST", "/funding/checks/batch", { opportunity_ids: ids });
      bulkNote(`${res.checks.length} check${res.checks.length === 1 ? "" : "s"} running${res.skipped_running ? ` · ${res.skipped_running} already running` : ""}. Results appear as each finishes.`);
      for (const id of ids) { checkedNow.add(id); picked.delete(id); const o = opps.find((x) => x.id === id); if (o) o.running = true; }
      renderRows();
      if (ids.includes(selectedId)) openDetail(selectedId, false);
      schedulePoll();
    } catch (err) { bulkNote(err.message, true); }
  }

  $("check-selected").addEventListener("click", () => runChecks([...picked]));
  $("sel-all").addEventListener("change", () => {
    for (const o of shown()) if (!o.running && !o.mailOnly) $("sel-all").checked ? picked.add(o.id) : picked.delete(o.id);
    renderRows();
  });
  document.querySelectorAll("#filter button").forEach((b) => b.addEventListener("click", () => {
    filter = b.dataset.f;
    document.querySelectorAll("#filter button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderRows();
  }));
  let searchTimer = null;
  $("q").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { if (fetched && !run.active) loadList(); }, 400); });
  $("state").addEventListener("change", () => { if (fetched && !run.active) loadList(); });
  $("seed").addEventListener("click", async () => {
    $("seed").disabled = true;
    bulkNote("Loading sample opportunities…");
    try { const r = await api("POST", "/funding/demo/seed"); bulkNote(`${r.seeded} sample opportunities loaded (unchecked).`); await loadList(); }
    catch (err) { bulkNote(err.message, true); }
    $("seed").disabled = false;
  });

  // ---------------------------------------------------------------- live run: fetch from Dynamics, then check all
  const run = { active: false, checks: new Map(), started: 0, clock: null };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const short = (o) => (o?.customer || o?.name || "Opportunity").slice(0, 22);
  const clockText = () => `${((Date.now() - run.started) / 1000).toFixed(1)} s`;

  function feed(who, message, kind = "") {
    const t = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    $("feed").prepend(el("li", { class: kind }, el("span", { class: "t" }, t), el("span", { class: "who" }, who), el("span", { class: "m" }, message)));
    while ($("feed").children.length > 300) $("feed").lastChild.remove();
  }
  function livePhase(p) {
    const order = ["fetch", "check", "done"];
    document.querySelectorAll("#live-phases li").forEach((li) => {
      const i = order.indexOf(li.dataset.p), at = order.indexOf(p);
      li.className = p === "done" || i < at ? "done" : i === at ? "active" : "";
    });
  }
  function liveCounts() {
    const all = [...run.checks.values()];
    const done = all.filter((c) => c.done).length;
    $("l-fetched").textContent = opps.length;
    $("l-running").textContent = all.length - done;
    $("l-done").textContent = done;
    const funding = all.filter((c) => c.decision?.status === "decided" && c.decision.total_usd).reduce((n, c) => n + c.decision.total_usd, 0);
    $("l-funding").textContent = funding ? usdShort(funding) : "–";
    const pct = all.length ? 10 + 90 * done / all.length : opps.length ? 10 : 3;
    $("live-bar").style.width = `${pct}%`;
    $("live-progress").setAttribute("aria-valuenow", String(Math.round(pct)));
    $("l-time").textContent = run.started ? clockText() : "";
  }
  function setBusy(action) {   // action: "fetch" | "check" | null
    run.active = !!action;
    $("fetch").classList.toggle("busy", action === "fetch");
    $("fetch").textContent = action === "fetch" ? "Fetching…" : "Fetch opportunities";
    $("mail-days").disabled = action === "fetch";
    $("check-all").classList.toggle("busy", action === "check");
    if (action === "check") $("check-all").textContent = "Agent checking…";
    updateBulk();
  }
  function startClock() {
    run.started = Date.now();
    clearInterval(run.clock);
    run.clock = setInterval(() => { $("l-time").textContent = clockText(); }, 200);
  }

  async function fetchOpps() {
    if (run.active) return;
    setBusy("fetch");
    run.checks = new Map();
    startClock();
    $("live").hidden = false;
    $("feed").replaceChildren();
    filter = "all";
    document.querySelectorAll("#filter button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.f === "all")));
    selectedId = null;
    detail.replaceChildren(emptyDetail());
    livePhase("fetch");
    const source = "Dynamics 365";
    feed("Intake Agent", `Fetching ${$("state").selectedOptions[0].text.toLowerCase()} opportunities from ${source} and your Outlook mailbox…`);
    try {
      const q = $("q").value.trim();
      const res = await api("GET", `/funding/dynamics?state=${$("state").value}${q ? `&search=${encodeURIComponent(q)}` : ""}`);
      setMode(res.mode, res.opportunities.length, res.flavour || res.mode);
      fetched = true;
      opps = [];
      renderRows();
      for (const o of res.opportunities) {   // rows appear as they arrive
        opps.push({ ...hideOldDecision(o), reveal: true });
        renderRows();
        liveCounts();
        await sleep(45);
      }
      const dynCount = opps.length;
      renderStats();
      const revenue = opps.reduce((n, o) => n + (o.estimated_value || 0), 0);
      const checked = opps.filter((o) => o.latest).length;
      feed("Dynamics 365", `${dynCount} opportunities fetched in ${clockText()}` +
        (revenue ? ` (${usdShort(revenue)} estimated revenue)` : "") + (checked ? ` · ${checked} already checked` : ""), "ok");
      mailOpps = [];
      try { await scanMail(); } catch (err) { feed("Outlook", `Mailbox not included: ${err.message}`, "err"); }
      livePhase("check");
      document.querySelector('#live-phases li[data-p="check"]').className = "";
      feed("Intake Agent", opps.length ? "Click Check funding to run the AWS funding framework on them." : "Nothing to check.");
    } catch (err) {
      feed("Intake Agent", err.message, "err");
      bulkNote(err.message, true);
    }
    clearInterval(run.clock);
    liveCounts();
    setBusy(null);
  }

  async function checkAll() {
    const todo = opps.filter((o) => !o.running && !o.mailOnly);
    if (run.active || !todo.length) return;
    setBusy("check");
    run.checks = new Map();
    for (const o of todo) checkedNow.add(o.id);
    startClock();
    $("live").hidden = false;
    livePhase("check");
    try {
      for (let i = 0; i < todo.length; i += MAX_BATCH) {   // 25 per agent invocation
        const part = todo.slice(i, i + MAX_BATCH);
        const b = await api("POST", "/funding/checks/batch", { opportunity_ids: part.map((o) => o.id) });
        for (const c of b.checks) {
          const o = opps.find((x) => x.id === c.opportunity_id);
          o.running = true; o.stage = "Queued"; o.latest = null; o.last_error = null;
          run.checks.set(c.check_id, { opp: o, seen: 0, done: false, decision: null });
        }
      }
      feed("Intake Agent", `Started ${run.checks.size} funding checks on AgentCore Runtime (4 at a time)`);
      renderRows();
      renderStats();
      liveCounts();
      while ([...run.checks.values()].some((c) => !c.done)) {   // follow every check live
        await sleep(900);
        const open = [...run.checks.entries()].filter(([, c]) => !c.done).map(([id]) => id);
        for (let i = 0; i < open.length; i += MAX_BATCH) {
          const st = await api("POST", "/funding/checks/status", { check_ids: open.slice(i, i + MAX_BATCH) });
          for (const x of st.checks) applyStatus(x);
        }
        renderRows();
        renderStats();
        liveCounts();
        if (Date.now() - run.started > 10 * 60 * 1000) { feed("Intake Agent", "Stopped following after 10 minutes; fetch again for the rest.", "warn"); break; }
      }
      finish();
    } catch (err) {
      feed("Intake Agent", err.message, "err");
      bulkNote(err.message, true);
      finish(false);
    }
  }

  function applyStatus(s) {
    const c = run.checks.get(s.check_id);
    if (!c || c.done) return;
    const o = c.opp;
    for (const l of s.logs.slice(c.seen)) {
      feed(short(o), l.message, l.level === "ERROR" ? "err" : l.level === "WARN" ? "warn" : l.step === "decide" ? "ok" : "");
    }
    c.seen = s.logs.length;
    o.stage = s.stage;
    if (s.status === "COMPLETED" || s.status === "FAILED") {
      c.done = true;
      o.running = false;
      o.flash = true;
      if (s.status === "COMPLETED" && s.decision) {
        c.decision = s.decision;
        o.latest = { ...s.decision, check_id: s.check_id, at: s.finished_at };
      } else {
        o.last_error = { error: s.error || "failed" };
      }
      if (o.id === selectedId) openDetail(o.id, false);
    }
  }

  async function finish(ok = true) {
    clearInterval(run.clock);
    livePhase(ok ? "done" : "check");
    const all = [...run.checks.values()];
    if (ok && all.length) {
      const n = (st) => all.filter((c) => c.decision?.status === st).length;
      const funding = all.filter((c) => c.decision?.status === "decided").reduce((t, c) => t + (c.decision.total_usd || 0), 0);
      feed("Intake Agent", `Done in ${clockText()}: ${n("decided")} funding determined, ` +
        `${all.length - n("decided")} pending assessment${funding ? ` · ${usdShort(funding)} estimated funding` : ""}`, "ok");
    }
    liveCounts();
    setBusy(null);
    await loadList(true);   // sync with the server (questions, shared latest decisions)
  }

  $("fetch").addEventListener("click", fetchOpps);
  $("check-all").addEventListener("click", checkAll);
  $("clear").addEventListener("click", async () => {
    const ids = opps.filter((o) => (o.latest || o.last_error) && !o.running).map((o) => o.id);
    if (ids.length && !window.confirm(`Clear the screen and the funding results of ${ids.length} opportunit${ids.length === 1 ? "y" : "ies"}? ` +
      "Fetch again to load them as Pending assessment. The decision notes already saved in Dynamics stay.")) return;
    $("clear").disabled = true;
    try {
      const r = ids.length ? await api("POST", "/funding/clear", { opportunity_ids: ids }) : { cleared: 0 };
      clearTimeout(pollTimer);
      opps = [];
      mailOpps = [];
      checkedNow.clear();
      fetched = false;
      picked.clear();
      selectedId = null;
      run.checks = new Map();
      $("live").hidden = true;
      $("feed").replaceChildren();
      detail.replaceChildren(emptyDetail());
      $("source-line").textContent = "";
      renderStats();
      renderRows();
      bulkNote(r.cleared ? `${r.cleared} result${r.cleared === 1 ? "" : "s"} cleared. Click Fetch opportunities to start again.`
        : "Cleared. Click Fetch opportunities to start again.");
    } catch (err) { bulkNote(err.message, true); }
    updateBulk();
  });

  // ---------------------------------------------------------------- Outlook mailbox: merged into the one list
  let mailOpps = [];
  const norm = (s) => (s || "").toLowerCase().replace(/\b(inc|ltd|llc|plc|gmbh|corp|corporation|company|co)\b\.?/g, "").replace(/[^a-z0-9]+/g, " ").trim();
  function sameCustomer(a, b) {
    a = norm(a); b = norm(b);
    return a.length >= 3 && b.length >= 3 && (a === b || a.includes(b) || b.includes(a));
  }
  // Dynamics rows stay as they are; a mailbox opportunity of the same customer is collated into that row, others get their own row.
  function applyMail() {
    opps = opps.filter((o) => !o.mailOnly);
    for (const o of opps) { o.src = ["Dynamics"]; o.mail = null; }
    for (const m of mailOpps) {
      // The agent gives every mailbox opportunity a Dynamics record; match on its id first, then on the customer name.
      const match = opps.find((o) => !o.mail && m.dynamics_id && o.id === m.dynamics_id)
        || opps.find((o) => !o.mail && sameCustomer(o.customer, m.customer_name));
      if (match) { match.src = ["Dynamics", "Mailbox"]; match.mail = m; continue; }
      opps.push({ id: `mail:${m.thread_key}`, mailOnly: true, src: ["Mailbox"], mail: m, latest: null,
        name: m.opportunity_title || m.source?.subject || "Opportunity from email", customer: m.customer_name || "Unknown customer",
        estimated_value: null, close_date: null, state: "Open" });
    }
  }
  const srcCell = (o) => el("span", { class: "src-cell" }, (o.src || ["Dynamics"]).map((s) =>
    el("span", { class: `pill src-${s.toLowerCase()}` }, s)));

  function mailView(m) {
    const s = m.source || {};
    const gaps = m.gaps || [];
    const link = (m.sources || [s]).map((x) => x && x.web_link).find(Boolean);
    return el("div", { class: "mail-item" },
      el("div", { class: "muted small-r" }, [s.subject, s.from, s.received_at ? when(s.received_at) : "", s.emails_in_thread > 1 ? `${s.emails_in_thread} emails` : ""].filter(Boolean).join(" · ")),
      m.summary ? el("p", {}, m.summary) : null,
      el("div", { class: "mail-meta" },
        el("span", { class: "pill prog" }, `${Math.round((m.confidence || 0) * 100)}% confidence`),
        m.urgency && m.urgency !== "unknown" ? el("span", { class: "pill" }, `${m.urgency} urgency`) : null,
        link ? el("a", { class: "btn sm", href: link, target: "_blank", rel: "noopener noreferrer" }, "Open email ↗") : null),
      gaps.length ? [el("div", { class: "section-label" }, `Missing information (${gaps.length})`),
        el("ul", { class: "mail-gaps small-r" }, gaps.map((g) => el("li", {}, g.question || g.field)))] : null);
  }

  async function scanMail() {   // POST /runs, then follow GET /runs/{id}; opportunities merge into the list as they appear
    const started = await api("POST", "/runs", { outlook: { days: Number($("mail-days").value) } });
    feed("Outlook", `Reading your mailbox (${$("mail-days").selectedOptions[0].text.replace("Mail: ", "").toLowerCase()})…`);
    const t0 = Date.now();
    let seen = 0;
    for (;;) {
      await sleep(4000);
      const r = await api("GET", `/runs/${encodeURIComponent(started.run_id)}`);
      mailOpps = r.opportunities || [];
      applyMail();
      renderRows();
      renderStats();
      liveCounts();
      for (const l of (r.logs || []).slice(seen)) feed("Outlook", l.message, l.level === "ERROR" ? "err" : l.level === "WARN" ? "warn" : "");
      seen = (r.logs || []).length;
      if (r.status === "FAILED") throw new Error(r.error || r.outlook_error || "The Outlook scan failed.");
      if (r.status !== "RUNNING" && r.status !== "QUEUED") {
        feed("Outlook", `${mailOpps.length} opportunit${mailOpps.length === 1 ? "y" : "ies"} found in ${r.counts?.emails ?? 0} emails`, "ok");
        return;
      }
      if (Date.now() - t0 > 30 * 60 * 1000) { feed("Outlook", "Still running; fetch again later for the rest.", "warn"); return; }
    }
  }

  function emptyDetail() {
    return el("div", { class: "empty" }, svgIcon(["M9 5h11M9 12h11M9 19h11M4 5h.01M4 12h.01M4 19h.01"]),
      el("div", {}, "Select an opportunity to see its Dynamics record and funding decision."));
  }

  // ---------------------------------------------------------------- detail: Dynamics record + decision
  const detail = $("detail");
  const SOURCE_TEXT = { form: "Form answer", agent: "Agent (from text)" };

  async function openDetail(id, loading = true) {
    selectedId = id;
    renderRows();
    const mailRow = opps.find((o) => o.id === id && o.mailOnly);
    if (mailRow) {   // email-only: nothing in Dynamics to fetch
      detail.replaceChildren(el("div", { class: "d-head" }, el("div", {}, el("h3", {}, mailRow.name),
        el("div", { class: "muted small-r" }, mailRow.customer, " · Mailbox only, not in Dynamics yet"))),
        el("div", { class: "section-label" }, "From your mailbox"), mailView(mailRow.mail));
      return;
    }
    if (loading) detail.replaceChildren(el("div", { class: "empty" }, el("div", {}, "Fetching the opportunity from Dynamics…")));
    try {
      let d = await api("GET", `/funding/dynamics/${encodeURIComponent(id)}`);
      if (selectedId !== id) return;
      if (!checkedNow.has(id) && !d.running) d = { ...d, latest: null, result: null, last_error: null };
      const row = opps.find((o) => o.id === id);
      if (row) { row.running = d.running; row.latest = d.latest; }
      renderDetail(d);
      if (window.matchMedia("(max-width: 1100px)").matches && loading) detail.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      detail.replaceChildren(el("p", { class: "error" }, `Could not fetch the opportunity: ${err.message}`));
    }
  }

  function renderDetail(d) {
    const o = d.opportunity;
    const check = el("button", { type: "button", class: "btn primary", disabled: d.running || null },
      d.running ? "Checking…" : d.latest ? "Re-check funding" : "Run funding check");
    check.addEventListener("click", () => runChecks([o.id]));
    const tabs = el("div", { class: "segmented tabs", role: "tablist" });
    const body = el("div", {});
    const mail = opps.find((x) => x.id === o.id)?.mail;
    const views = { decision: () => decisionView(d), record: () => recordView(o), mail: () => mailView(mail) };
    let current = d.result || d.running ? "decision" : "record";
    for (const [key, label] of [["decision", "Funding decision"], ["record", "Dynamics record"], ...(mail ? [["mail", "Mailbox"]] : [])]) {
      const b = el("button", { type: "button", role: "tab", "aria-pressed": String(key === current) }, label);
      b.addEventListener("click", () => {
        current = key;
        tabs.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        body.replaceChildren(views[key]());
      });
      tabs.append(b);
    }
    body.replaceChildren(views[current]());
    detail.replaceChildren(
      el("div", { class: "d-head" },
        el("div", {}, el("h3", {}, o.name || "Opportunity"),
          el("div", { class: "muted small-r" }, o.customer || "No account", " · ",
            "Dynamics 365")),
        o.url ? el("a", { class: "btn sm", href: o.url, target: "_blank", rel: "noopener noreferrer" }, "Open in Dynamics ↗") : null),
      el("div", { class: "d-facts" },
        el("div", {}, el("span", {}, "Est. revenue"), el("b", {}, usd(o.estimated_value))),
        el("div", {}, el("span", {}, "Close date"), el("b", {}, day(o.close_date))),
        el("div", {}, el("span", {}, "Status"), el("b", {}, o.state || "–"))),
      el("div", { class: "actions d-actions" }, check,
        d.latest ? el("span", { class: "muted small-r" }, `Last checked ${when(d.latest.at)}${d.latest.by ? ` by ${d.latest.by}` : ""}`) : null),
      tabs, body);
  }

  function recordView(o) {
    const answers = Object.entries(o.answers || {}).filter(([, v]) => v !== "" && v !== null);
    const meta = answers.filter(([k]) => META[k]);
    const form = answers.filter(([k]) => !META[k]);
    const row = (label, column, value) => el("div", { class: "field-row" },
      el("div", { class: "field-label" }, label, column ? el("code", { class: "col-name" }, column) : null),
      el("div", { class: "field-value pre" }, String(value)));
    let section = null;
    const rows = [];
    for (const [k, v] of form) {
      const s = SECTION[k] || "Other";
      if (s !== section) { rows.push(el("div", { class: "field-group" }, s)); section = s; }
      rows.push(row(LABELS[k] || k, o.answers_from === "columns" ? COLUMN[k] : null, v));
    }
    const fromText = { columns: "Dynamics columns (one ingr_ column per form question)", note: "the answers note (JSON)" }[o.answers_from];
    return el("div", {},
      el("div", { class: "section-label" }, "Standard opportunity columns"),
      el("div", { class: "field-table" },
        row("Topic", "name", o.name || "–"), row("Account", "customerid", o.customer || "–"),
        row("Est. revenue", "estimatedvalue", usd(o.estimated_value)), row("Est. close date", "estimatedclosedate", day(o.close_date)),
        row("Status", "statecode", o.state || "–"), o.description ? row("Description", "description", o.description) : null),
      el("div", { class: "section-label" }, `Intake form answers (${form.length})`),
      form.length ? [el("p", { class: "muted small-r" }, `Read from ${fromText}.`), el("div", { class: "field-table" }, rows)]
        : el("p", { class: "muted" }, "This opportunity was not created with the intake form. The agent reads its name, description and notes."),
      meta.length ? [el("div", { class: "section-label" }, "Form response"),
        el("div", { class: "field-table" }, meta.map(([k, v]) => row(META[k][0], o.answers_from === "columns" ? META[k][1] : null,
          k.endsWith("_time") ? when(v) : v)))] : null,
      (o.notes || []).length ? [el("div", { class: "section-label" }, `Notes (${o.notes.length})`),
        el("ul", { class: "d-notes" }, o.notes.map((n) => el("li", {}, el("b", {}, n.subject || "Note"), el("div", { class: "pre muted" }, n.text))))] : null);
  }

  function decisionView(d) {
    if (d.running) return el("div", { class: "empty" }, el("span", { class: "spin big", "aria-hidden": "true" }),
      el("div", {}, "The funding agent is fetching the opportunity from Dynamics and applying the framework (about 10–20 seconds)…"));
    if (!d.result) return el("div", {},
      d.last_error ? el("p", { class: "error" }, `The last check failed: ${d.last_error.error}`) : null,
      el("p", { class: "muted" }, "Pending assessment. Run the funding check to get the program and the funding amount."));
    const r = d.result;
    const x = r.decision;
    const [label] = STATUS[x.status] || [x.status];
    const copy = el("button", { type: "button", class: "btn sm ghost" }, "Copy questions");
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText((x.assumptions || []).map((m, i) => `${i + 1}. ${m.question}`).join("\n")); copy.textContent = "Copied"; } catch { /* blocked */ }
    });
    const notes = [...(x.notes || []), ...(r.conflicts || [])];
    return el("div", {},
      el("div", { class: `decision dec-${x.status}` },
        el("div", { class: "decision-head" },
          el("div", {}, el("div", { class: "decision-label" }, label), el("div", { class: "decision-program" }, x.program || x.funding)),
          x.total_usd !== null && x.total_usd !== undefined
            ? el("div", { class: "decision-amount" }, el("span", { class: "muted" }, "Estimated funding"), el("b", {}, usd(x.total_usd))) : null),
        x.program ? el("div", { class: "decision-funding" }, x.funding) : null,
        el("p", { class: "decision-why" }, x.rationale),
        x.components?.length ? el("table", { class: "components" },
          el("thead", {}, el("tr", {}, el("th", {}, "Component"), el("th", {}, "Basis"), el("th", {}, "Amount"))),
          el("tbody", {}, x.components.map((c) => el("tr", {}, el("td", {}, c.name), el("td", {}, c.basis), el("td", {}, usd(c.amount_usd)))))) : null),
      el("div", { class: "section-label" }, "Decision path"),
      el("ol", { class: "path" }, x.path.map((p, n) => el("li", { class: p.known ? "known" : "unknown" },
        el("span", { class: "path-n" }, String(n + 1)), el("div", {}, el("b", {}, p.step), el("div", { class: "muted" }, p.value))))),
      x.alternatives?.length ? [el("div", { class: "section-label" }, "Depends on"),
        el("div", { class: "alts" }, x.alternatives.map((a) => el("div", { class: "alt" },
          el("div", { class: "muted small-r" }, `If ${a.when.toLowerCase()}`), el("b", {}, a.program),
          el("div", { class: "muted" }, a.funding),
          a.components?.length ? el("div", { class: "small-r" }, a.components.map((c) => `${c.name}: ${usd(c.amount_usd)}`).join(" · ")) : null)))] : null,
      x.assumptions?.length ? [el("div", { class: "section-label" }, `Best-fit assumptions, please confirm (${x.assumptions.length})`),
        el("ol", { class: "gaps" }, x.assumptions.map((a) => el("li", {}, el("div", { class: "gap-q" },
          el("span", {}, `${a.question} `), el("b", {}, `Assumed: ${a.assumed}`))))),
        el("div", { class: "actions" }, copy)] : null,
      notes.length ? [el("div", { class: "section-label" }, "Notes"), el("ul", { class: "notes" }, notes.map((n) => el("li", {}, n)))] : null,
      el("div", { class: "section-label" }, "Facts used"),
      el("div", { class: "field-table" }, r.facts.map((f) => el("div", { class: `field-row${f.used ? "" : " missing"}` },
        el("div", { class: "field-label" }, f.label),
        el("div", {},
          el("div", { class: "field-value" }, f.display, f.source ? el("span", { class: `pill fact-src ${f.source}` }, SOURCE_TEXT[f.source]) : null),
          f.evidence ? el("blockquote", {}, f.source === "form" ? `${f.field}: ${f.evidence}` : `“${f.evidence}”`,
            f.column ? el("code", { class: "col-name" }, f.column) : null,
            f.inferred ? el("span", { class: "muted" }, " (inferred: servers to rehost count as VMs)") : null,
            f.source === "agent" ? el("span", { class: f.verified ? "src-ok" : "src-warn" },
              f.verified ? " ✓ found in the opportunity" : ` ⚠ quote not found, not used${f.suggested ? ` (suggested: ${f.suggested})` : ""}`) : null) : null)))),
      r.summary ? [el("div", { class: "section-label" }, "Agent summary"), el("p", {}, r.summary)] : null,
      r.model_error ? el("p", { class: "hint" }, `The opportunity text could not be processed (${r.model_error}); the decision uses the form answers only.`) : null,
      el("p", { class: "muted small-r" }, `Checked ${when(r.checked_at)}`,
        r.written_back ? (r.opportunity?.source === "dynamics" ? " · saved as a note on the Dynamics opportunity" : " · saved with the opportunity") : ""));
  }

  // ---------------------------------------------------------------- start
  setUser(null);
  $("gate").hidden = true;
  if (!cfg.apiUrl) { $("gate").hidden = false; gateNote("config.js has no apiUrl. Run scripts/deploy.sh.", true); return; }
  async function start() {
    try {
      const c = await api("GET", "/funding/config");
      setMode(c.mode, 0, c.flavour || c.mode);
      $("source-line").textContent = "";
    } catch (err) { listNote(`Could not reach the API: ${err.message}`, true); }
    renderStats();
    renderRows();
  }
  try {
    const s = await api("GET", "/session");
    setUser(s.microsoft);
    if (s.microsoft) start();
  } catch (err) { $("gate").hidden = false; gateNote(`Could not reach the API: ${err.message}`, true); }
})();
