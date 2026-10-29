// Ingram Intake web UI: no sign-in page. Three sources (Outlook via Microsoft, CRM files, seller notes)
// go into one run; the agent consolidates them into matched / unmatched opportunities.
(async () => {
  const cfg = window.INTAKE_CONFIG;
  const POLL_MS = 4000;
  const MAX_TEXT = 20000;
  const MAX_BYTES = 3 * 1024 * 1024;
  const DONE = new Set(["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"]);
  const PHASES = ["reading", "triage", "matching", "building"];
  const SRC = { email: "Outlook", crm: "CRM", notes: "Seller notes" };
  const SRC_LONG = { email: "Outlook", crm: "CRM records", notes: "seller notes" };

  const $ = (id) => document.getElementById(id);
  const statusEl = $("status");
  const oppsEl = $("opportunities");
  let pollTimer = null;
  let running = false;
  let days = 7;
  let microsoft = null;   // {name, email} when Outlook is connected
  const files = { crm: [], notes: [] };

  // ---------------------------------------------------------------- DOM helper (no innerHTML: all agent output is text)
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v; else node.setAttribute(k, v);
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
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("aria-hidden", "true");
    for (const d of paths) {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d);
      svg.append(p);
    }
    return svg;
  }

  // ---------------------------------------------------------------- theme: light / dark / system
  const THEMES = [
    ["light", "Light theme", ["M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"]],
    ["dark", "Dark theme", ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"]],
    ["system", "System theme", ["M3 4h18v12H3zM8 20h8M12 16v4"]],
  ];
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(choice) {
    localStorage.setItem("intakeTheme", choice);
    const effective = choice === "system" ? (systemDark.matches ? "dark" : "light") : choice;
    document.documentElement.dataset.theme = effective;
    document.querySelectorAll("[data-theme-choice]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.themeChoice === choice));
    });
  }
  document.querySelectorAll("[data-theme-toggle]").forEach((group) => {
    for (const [choice, label, paths] of THEMES) {
      const btn = el("button", { type: "button", "data-theme-choice": choice, "aria-label": label, title: label });
      btn.append(svgIcon(paths));
      btn.addEventListener("click", () => applyTheme(choice));
      group.append(btn);
    }
  });
  systemDark.addEventListener("change", () => {
    if ((localStorage.getItem("intakeTheme") || "dark") === "system") applyTheme("system");
  });
  applyTheme(localStorage.getItem("intakeTheme") || "dark");

  // ---------------------------------------------------------------- anonymous session (owns this browser's runs)
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

  async function api(method, path, body, retry = true) {
    await ensureSession();
    const resp = await fetch(`${cfg.apiUrl}${path}`, {
      method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.token}` },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 401 && retry) {          // session expired: start a fresh one (Outlook must reconnect)
      session = null;
      localStorage.removeItem(SESSION_KEY);
      localStorage.removeItem("lastRunId");
      setMicrosoft(null);
      return api(method, path, body, false);
    }
    if (resp.status === 409 && "microsoft" in data) setMicrosoft(null);
    if (!resp.ok && resp.status !== 502) throw new Error(data.error || data.message || `HTTP ${resp.status}`);
    return data;
  }

  // ---------------------------------------------------------------- Outlook (Microsoft device-code connect)
  function setMicrosoft(user) {
    microsoft = user;
    $("outlook-off").hidden = !!user;
    $("outlook-on").hidden = !user;
    $("outlook-state").textContent = user ? `Connected as ${user.email || user.name}` : "Optional · reads only your own mail";
    $("src-outlook").classList.toggle("on", !!user);
    $("user-chip").hidden = !user;
    $("who").textContent = user ? user.email : "";
    const name = user ? (user.name || user.email) : "";
    $("avatar").textContent = name.split(/[\s.@]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
    updateReady();
  }

  let loginTimer = null;
  let loginId = null;
  function outlookNote(message, isError = false) {
    $("outlook-note").textContent = message || "";
    $("outlook-note").className = `status-line ${isError ? "error" : "muted"}`;
  }
  function resetConnect(message, isError = false) {
    clearTimeout(loginTimer);
    loginId = null;
    $("device").hidden = true;
    $("connect").hidden = false;
    $("connect").disabled = false;
    outlookNote(message, isError);
  }
  async function pollLogin(intervalSec) {
    if (!loginId) return;
    try {
      const res = await api("POST", "/auth/device/poll", { login_id: loginId });
      if (res.status === "done") { resetConnect(); setMicrosoft(res.user); return; }
      const next = res.slow_down ? intervalSec + 5 : intervalSec;
      loginTimer = setTimeout(() => pollLogin(next), next * 1000);
    } catch (err) { resetConnect(err.message, true); }
  }
  $("connect").addEventListener("click", async () => {
    $("connect").disabled = true;
    outlookNote("Getting a sign-in code…");
    try {
      const res = await api("POST", "/auth/device/start");
      loginId = res.login_id;
      $("device-code").textContent = res.user_code;
      $("device-link").href = res.verification_uri;
      $("connect").hidden = true;
      $("device").hidden = false;
      outlookNote("");
      $("copy-code").focus();
      loginTimer = setTimeout(() => pollLogin(res.interval), res.interval * 1000);
    } catch (err) { resetConnect(`Could not start: ${err.message}`, true); }
  });
  $("copy-code").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("device-code").textContent);
      $("copy-code").textContent = "Copied";
      setTimeout(() => { $("copy-code").textContent = "Copy"; }, 1500);
    } catch { /* clipboard blocked: the code is visible to copy by hand */ }
  });
  $("device-cancel").addEventListener("click", () => resetConnect());
  $("disconnect").addEventListener("click", async () => {
    try { await api("POST", "/auth/disconnect"); } catch { /* disconnect locally anyway */ }
    setMicrosoft(null);
  });
  $("use-outlook").addEventListener("change", updateReady);
  document.querySelectorAll(".days").forEach((b) => b.addEventListener("click", () => {
    days = Number(b.dataset.days);
    document.querySelectorAll(".days").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    updateReady();
  }));

  // ---------------------------------------------------------------- CRM files + seller notes
  const KINDS = {
    crm: { types: { csv: "CSV", xlsx: "Excel" }, max: 3, list: "crm-list", input: "crm-input" },
    notes: { types: { pdf: "PDF", docx: "Word" }, max: 5, list: "notes-list", input: "notes-input" },
  };
  const fileExt = (f) => (f.name.split(".").pop() || "").toLowerCase();
  const fmtSize = (n) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
  const totalBytes = () => [...files.crm, ...files.notes].reduce((n, f) => n + f.size, 0);
  const toBase64 = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error(`could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
  function runNote(message, isError = false) {
    $("run-note").textContent = message || "";
    $("run-note").className = `status-line ${isError ? "error" : "muted"}`;
  }

  function renderFiles(kind) {
    const k = KINDS[kind];
    $(k.list).replaceChildren(...files[kind].map((f, i) => {
      const remove = el("button", { type: "button", class: "btn sm ghost", "aria-label": `Remove ${f.name}` }, "Remove");
      remove.disabled = running;
      remove.addEventListener("click", () => { files[kind].splice(i, 1); renderFiles(kind); });
      return el("li", {}, el("span", { class: "pill source" }, k.types[fileExt(f)]),
        el("span", { class: "file-name", title: f.name }, f.name), el("span", { class: "muted" }, fmtSize(f.size)), remove);
    }));
    $(`src-${kind}`).classList.toggle("on", files[kind].length > 0 || (kind === "notes" && !!$("notes-text").value.trim()));
    updateReady();
  }

  function addFiles(kind, list) {
    const { types, max } = KINDS[kind];
    const problems = [];
    for (const f of list) {
      if (!types[fileExt(f)]) { problems.push(`${f.name}: ${Object.values(types).join(" or ")} only`); continue; }
      if ([...files.crm, ...files.notes].some((p) => p.name === f.name)) continue;
      if (files[kind].length >= max) { problems.push(`at most ${max} files`); break; }
      if (totalBytes() + f.size > MAX_BYTES) { problems.push(`${f.name}: over 3 MB in total`); continue; }
      files[kind].push(f);
    }
    renderFiles(kind);
    runNote(problems.length ? `Not added: ${problems.join("; ")}.` : "", problems.length > 0);
  }

  // ---------------------------------------------------------------- sample files (ui/samples, listed in samples.js)
  const SAMPLES = Array.isArray(window.INTAKE_SAMPLES) ? window.INTAKE_SAMPLES : [];
  async function sampleFile(s) {
    const resp = await fetch(`samples/${encodeURIComponent(s.name)}`);
    if (!resp.ok) throw new Error(`${s.name}: HTTP ${resp.status}`);
    const blob = await resp.blob();
    return new File([blob], s.name, { type: blob.type });
  }
  async function addSamples(list, btn) {
    if (running) return;
    if (btn) btn.disabled = true;
    try {
      const loaded = await Promise.all(list.map(sampleFile));
      for (const kind of Object.keys(KINDS)) {
        const add = loaded.filter((f, i) => list[i].kind === kind);
        if (add.length) addFiles(kind, add);
      }
      if (!$("run-note").classList.contains("error")) {
        runNote(`Added sample ${list.length === 1 ? "file" : "files"}: ${list.map((s) => s.name).join(", ")}.`);
      }
    } catch (err) { runNote(`Could not load the sample: ${err.message}`, true); }
    if (btn) btn.disabled = false;
  }
  function renderSamples() {
    for (const kind of Object.keys(KINDS)) {
      const items = SAMPLES.filter((s) => s.kind === kind);
      $(`${kind}-samples`).closest("details").hidden = !items.length;
      $(`${kind}-samples`).replaceChildren(...items.map((s) => {
        const add = el("button", { type: "button", class: "btn sm ghost", "aria-label": `Add ${s.name}` }, "Add");
        add.addEventListener("click", () => addSamples([s], add));
        return el("li", {},
          el("span", { class: "pill source" }, KINDS[kind].types[s.name.split(".").pop().toLowerCase()] || ""),
          el("span", { class: "sample-text" },
            el("a", { class: "file-name", href: `samples/${encodeURIComponent(s.name)}`, target: "_blank", rel: "noopener", title: `Open ${s.name}` }, s.name),
            s.about ? el("span", { class: "muted" }, s.about) : null),
          add);
      }));
    }
    $("use-samples").closest(".sample-bar").hidden = !SAMPLES.length;
  }
  $("use-samples").addEventListener("click", () => addSamples(SAMPLES.filter((s) => s.default), $("use-samples")));
  renderSamples();

  for (const kind of Object.keys(KINDS)) {
    const input = $(KINDS[kind].input);
    input.addEventListener("change", () => { addFiles(kind, [...input.files]); input.value = ""; });
    const box = $(`src-${kind}`);
    ["dragenter", "dragover"].forEach((t) => box.addEventListener(t, (e) => { e.preventDefault(); if (!running) box.classList.add("over"); }));
    ["dragleave", "drop"].forEach((t) => box.addEventListener(t, (e) => { e.preventDefault(); box.classList.remove("over"); }));
    box.addEventListener("drop", (e) => { if (!running) addFiles(kind, [...e.dataTransfer.files]); });
  }
  $("paste-toggle").addEventListener("click", () => {
    const open = $("paste-box").hidden;
    $("paste-box").hidden = !open;
    $("paste-toggle").setAttribute("aria-expanded", String(open));
    if (open) $("notes-text").focus();
  });
  $("notes-text").addEventListener("input", () => {
    $("notes-count").textContent = `${$("notes-text").value.length.toLocaleString()} / ${MAX_TEXT.toLocaleString()}`;
    renderFiles("notes");
  });

  // ---------------------------------------------------------------- what will run
  const useOutlook = () => !!microsoft && $("use-outlook").checked;
  function plan() {
    const parts = [];
    if (useOutlook()) parts.push(`Outlook (${days} day${days === 1 ? "" : "s"})`);
    if (files.crm.length) parts.push(`${files.crm.length} CRM file${files.crm.length === 1 ? "" : "s"}`);
    const notes = files.notes.length + ($("notes-text").value.trim() ? 1 : 0);
    if (notes) parts.push(`${notes} seller note${notes === 1 ? "" : "s"}`);
    return parts;
  }
  function updateReady() {
    const parts = plan();
    $("ready").textContent = parts.length
      ? `Ready: ${parts.join(" + ")}${parts.length > 1 ? " · the agent will match them" : ""}`
      : "Add any one source to start: Outlook, CRM records or seller notes.";
    $("run").disabled = running || !parts.length || !cfg.apiUrl;
    $("run").textContent = running ? "Agent is running…" : "Run Intake agent";
    for (const id of ["crm-input", "notes-input", "notes-text", "use-outlook", "paste-toggle", "disconnect"]) $(id).disabled = running;
    document.querySelectorAll(".days").forEach((b) => { b.disabled = running || !useOutlook(); });
    document.querySelectorAll(".src-box").forEach((s) => s.classList.toggle("disabled", running));
  }
  function setRunning(value) {
    running = value;
    renderFiles("crm");
    renderFiles("notes");
  }

  $("run").addEventListener("click", async () => {
    clearTimeout(pollTimer);
    setRunning(true);
    runNote("Reading your files…");
    selectedKey = null;
    filter = "all";
    setBadge("QUEUED");
    setStats({}, 2);
    setPhase("queued");
    oppsEl.replaceChildren(emptyState("Starting the Intake agent…"));
    try {
      const enc = (list) => Promise.all(list.map(async (f) => ({ name: f.name, content_base64: await toBase64(f) })));
      const body = {
        outlook: useOutlook() ? { days } : null,
        crm_files: await enc(files.crm),
        notes_files: await enc(files.notes),
        notes_text: $("notes-text").value.trim(),
      };
      runNote("Uploading…");
      const res = await api("POST", "/runs", body);
      if (res.status === "FAILED") throw new Error(res.error);
      myRunId = res.run_id;
      runNote("Started. Opportunities appear on the right as soon as each one is ready.");
      await poll(res.run_id);
      loadHistory();
    } catch (err) {
      runNote(`Could not start: ${err.message}`, true);
      setBadge("FAILED");
      statusEl.textContent = "";
      oppsEl.replaceChildren();
      setRunning(false);
    }
  });

  // ---------------------------------------------------------------- rendering helpers
  const FIT_LABEL = { strong: "Strong fit", possible: "Possible fit", weak: "Weak fit", not_applicable: "Not applicable" };
  const PHASE_LABEL = { assess: "Assess", mobilize: "Mobilize", migrate_modernize: "Migrate & Modernize" };
  const STATUS_ICON = { met: "✓", not_met: "✕", unknown: "?" };

  function copyButton(getText, label = "Copy") {
    const btn = el("button", { type: "button", class: "btn sm ghost" }, label);
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(getText());
        btn.textContent = "Copied";
        setTimeout(() => { btn.textContent = label; }, 1500);
      } catch { /* clipboard blocked */ }
    });
    return btn;
  }

  const srcPill = (type) => el("span", { class: `pill src-${type}` }, SRC[type] || type);

  function matchPill(o, included) {
    if (included.length < 2) return null;
    if (o.matched) return el("span", { class: "pill matched", title: `Found in ${o.source_types.map((t) => SRC_LONG[t]).join(" and ")}` }, "✓ Matched");
    const missing = (o.missing_sources || []).map((t) => SRC_LONG[t]);
    return el("span", { class: "pill unmatched", title: `Not found in ${missing.join(" or ")}` },
      o.missing_sources?.includes("crm") ? "Not in CRM" : `Only in ${SRC_LONG[o.source_types[0]]}`);
  }

  function renderFitCard(name, title, p) {
    if (!p) return null;
    return el("div", { class: `fit fit-${p.fit}` },
      el("div", { class: "fit-head" },
        el("div", {}, el("b", {}, name), el("span", { class: "muted" }, ` · ${title}`)),
        el("span", { class: `pill fit-pill fit-${p.fit}` }, FIT_LABEL[p.fit] || p.fit)),
      p.phase && PHASE_LABEL[p.phase] ? el("div", { class: "fit-phase" }, `Suggested phase: ${PHASE_LABEL[p.phase]}`) : null,
      el("p", { class: "fit-why" }, p.rationale),
      el("ul", { class: "criteria" }, (p.criteria || []).map((c) => el("li", { class: `crit-${c.status}` },
        el("span", { class: "crit-icon", "aria-label": c.status.replace("_", " ") }, STATUS_ICON[c.status] || "?"),
        el("div", {}, c.criterion, c.source ? el("blockquote", {}, `“${c.source}”`) : null)))),
      p.next_step ? el("div", { class: "fit-next" }, el("b", {}, "Next step: "), p.next_step) : null);
  }

  const CATALOGUE = window.INTAKE_FIELDS || [];
  const FIELD_TOTAL = CATALOGUE.reduce((n, g) => n + g.fields.length, 0) || 40;
  $("catalogue").replaceChildren(...CATALOGUE.map((g) => el("details", { class: "cat-group" },
    el("summary", {}, el("b", {}, g.group), el("span", { class: "muted" },
      ` · ${g.fields.length} fields · ${g.fields.filter((f) => f.key).length} key`)),
    el("ul", {}, g.fields.map((f) => el("li", {},
      f.key ? el("span", { class: "key-star", title: "Key field" }, "★") : el("span", { class: "key-star dim" }, "·"),
      f.label))))));

  function renderFields(o) {
    const fields = o.fields || [];
    const multi = (o.sources || []).length > 1;
    const byId = Object.fromEntries(fields.map((f) => [f.field, f]));
    const gapIds = new Set((o.gaps || []).map((g) => g.field));
    const groups = CATALOGUE.length
      ? CATALOGUE.map((g) => ({ name: g.group, rows: g.fields.map((c) => (byId[c.id] ? { ...c, ...byId[c.id] } : { ...c, missing: true })) }))
      : [...new Set(fields.map((f) => f.group))].map((name) => ({ name, rows: fields.filter((f) => f.group === name) }));
    return el("div", { class: "field-table" }, groups.map((g) => [
      el("div", { class: "field-group" }, `${g.name} · ${g.rows.filter((r) => !r.missing).length} / ${g.rows.length}`),
      g.rows.map((f) => f.missing ? el("div", { class: "field-row missing" },
        el("div", { class: "field-label" }, f.key ? "★ " : "", f.label),
        el("div", { class: "muted" }, "Not in the sources",
          gapIds.has(f.id) ? el("span", { class: "pill gap sm-pill" }, "gap · question drafted") : null))
      : el("div", { class: "field-row" },
        el("div", { class: "field-label" }, f.key ? "★ " : "", f.label),
        el("div", {},
          el("div", { class: "field-value" }, f.value),
          f.source ? el("blockquote", { title: f.verified ? "Found word for word in the source" : "Not found word for word in the source: check it" },
            `“${f.source}”`,
            multi && f.from ? el("span", { class: `from src-${f.from}` }, ` ${SRC[f.from]}`) : null,
            el("span", { class: f.verified ? "src-ok" : "src-warn" }, f.verified ? " ✓ source" : " ⚠ check source")) : null)))]));
  }

  function renderGaps(gaps) {
    if (!gaps.length) return el("p", { class: "muted" }, "Nothing flagged");
    const all = () => gaps.map((g, i) => `${i + 1}. ${g.question}`).join("\n");
    return el("div", {},
      el("ol", { class: "gaps" }, gaps.map((g) => el("li", {},
        el("div", { class: "gap-head" }, el("span", { class: `pill prio-${g.priority}` }, g.priority), el("b", {}, g.label)),
        g.why ? el("div", { class: "muted gap-why" }, g.why) : null,
        el("div", { class: "gap-q" }, el("span", {}, g.question), copyButton(() => g.question))))),
      el("div", { class: "actions" }, copyButton(all, "Copy all questions")));
  }

  function renderSources(o) {
    return el("ul", { class: "linked" }, (o.sources || []).map((s) => el("li", {},
      srcPill(s.type),
      el("div", { class: "linked-main" },
        el("div", { class: "linked-title" }, s.subject || s.file_name || "Record"),
        el("div", { class: "muted" }, s.type === "email"
          ? [s.from, s.received_at ? new Date(s.received_at).toLocaleString() : "", s.emails_in_thread > 1 ? `${s.emails_in_thread} emails` : ""].filter(Boolean).join(" · ")
          : s.file_name || "")),
      s.web_link ? el("a", { class: "opp-link", href: s.web_link, target: "_blank", rel: "noopener noreferrer" }, "Open ↗") : null)));
  }

  function renderOpportunity(o, included) {
    const gaps = o.gaps || [];
    const fit = o.programme_fit || {};
    const conflicts = o.conflicts || [];
    const missing = (o.missing_sources || []).map((t) => SRC_LONG[t]);
    return el("article", { class: "opp" },
      el("div", { class: "opp-head" },
        el("div", {},
          el("h3", {}, o.customer_name || "Unknown customer", " · ", o.opportunity_title || o.source?.subject || "Opportunity"),
          el("div", { class: "opp-meta" }, (o.source_types || []).map(srcPill), matchPill(o, included),
            o.urgency && o.urgency !== "unknown" ? el("span", { class: "pill urgency" }, `${o.urgency} urgency`) : null)),
        el("span", { class: "confidence", title: "How sure the agent is that this is a real opportunity" },
          `${Math.round((o.confidence || 0) * 100)}% confidence`)),
      el("p", {}, o.summary),
      included.length > 1 && !o.matched && missing.length ? el("p", { class: "hint" },
        o.missing_sources.includes("crm") ? "Not found in your CRM records: consider creating a CRM opportunity."
          : `Not found in ${missing.join(" or ")}.`) : null,
      el("div", { class: "section-label" }, `Linked records (${(o.sources || []).length})`),
      renderSources(o),
      conflicts.length ? [el("div", { class: "section-label" }, `Conflicts between sources (${conflicts.length})`),
        el("ul", { class: "conflicts" }, conflicts.map((c) => el("li", {}, el("b", {}, c.label), " ", c.detail)))] : null,
      fit.MAP || fit.MPE ? [el("div", { class: "section-label" }, "Programme fit"),
        el("div", { class: "fit-grid" }, renderFitCard("MAP", "Migration Acceleration Program", fit.MAP),
          renderFitCard("MPE", "MAP Migration Program Engagement", fit.MPE))] : null,
      el("div", { class: "section-label" }, `Gaps and follow-up questions (${gaps.length})`),
      renderGaps(gaps),
      (o.fields || []).length ? el("details", { class: "fields-panel" },
        el("summary", {}, `Intake record · ${o.fields.length} of ${FIELD_TOTAL} fields filled · ${o.verified_count ?? 0} with verified source passages`),
        renderFields(o)) : null);
  }

  // ---------------------------------------------------------------- list -> detail
  let currentRun = null;
  let selectedKey = null;
  let filter = "all";

  function fitPill(name, p) {
    if (!p) return null;
    return el("span", { class: `pill fit-pill fit-${p.fit}`, title: `${name}: ${FIT_LABEL[p.fit] || p.fit}` },
      `${name} · ${(FIT_LABEL[p.fit] || p.fit).replace(" fit", "")}`);
  }

  function visibleOpps() {
    const opps = currentRun?.opportunities || [];
    return filter === "all" ? opps : opps.filter((o) => (filter === "matched") === !!o.matched);
  }

  function renderListItem(o, i, included) {
    const gaps = (o.gaps || []).length;
    const high = (o.gaps || []).filter((g) => g.priority === "high").length;
    const btn = el("button", { type: "button", class: "opp-item", "aria-label": `Open opportunity ${i + 1}: ${o.customer_name || "Unknown customer"}` },
      el("span", { class: "opp-num", "aria-hidden": "true" }, String(i + 1)),
      el("span", { class: "opp-item-main" },
        el("span", { class: "opp-item-title" }, o.customer_name || "Unknown customer", el("span", { class: "muted" }, ` · ${o.opportunity_title || o.source?.subject || "Opportunity"}`)),
        el("span", { class: "opp-item-meta" },
          (o.source_types || []).map(srcPill), matchPill(o, included),
          fitPill("MAP", o.programme_fit?.MAP),
          (o.conflicts || []).length ? el("span", { class: "pill urgency" }, `${o.conflicts.length} conflict${o.conflicts.length === 1 ? "" : "s"}`) : null,
          el("span", { class: "muted" }, `${(o.sources || []).length} record${(o.sources || []).length === 1 ? "" : "s"} · ${o.field_count ?? 0} fields · ${gaps} gaps${high ? ` (${high} high)` : ""}`))),
      el("span", { class: "opp-item-side" },
        el("span", { class: "confidence" }, `${Math.round((o.confidence || 0) * 100)}%`),
        el("span", { class: "chev", "aria-hidden": "true" }, "›")));
    btn.addEventListener("click", () => { selectedKey = o.thread_key; showResults(true); $("results-top").scrollIntoView({ block: "start", behavior: "smooth" }); });
    return el("li", {}, btn);
  }

  function showResults(moveFocus = false) {
    const included = currentRun?.sources_included || [];
    const opps = visibleOpps();
    const i = opps.findIndex((o) => o.thread_key === selectedKey);
    if (i >= 0) {
      const nav = el("div", { class: "detail-nav" },
        el("button", { type: "button", class: "btn sm", id: "back-to-list" }, "← All opportunities"),
        el("span", { class: "muted" }, `${i + 1} of ${opps.length}`),
        el("span", { class: "detail-pager" },
          el("button", { type: "button", class: "btn sm", ...(i === 0 ? { disabled: "" } : {}), "aria-label": "Previous opportunity" }, "‹ Prev"),
          el("button", { type: "button", class: "btn sm", ...(i === opps.length - 1 ? { disabled: "" } : {}), "aria-label": "Next opportunity" }, "Next ›")));
      const [prev, next] = nav.querySelectorAll(".detail-pager button");
      prev.addEventListener("click", () => { selectedKey = opps[i - 1].thread_key; showResults(true); });
      next.addEventListener("click", () => { selectedKey = opps[i + 1].thread_key; showResults(true); });
      nav.querySelector("#back-to-list").addEventListener("click", () => { selectedKey = null; showResults(true); });
      const card = renderOpportunity(opps[i], included);
      card.prepend(el("span", { class: "opp-num detail-num", "aria-hidden": "true" }, String(i + 1)));
      oppsEl.replaceChildren(nav, card);
      if (moveFocus) oppsEl.querySelector("#back-to-list").focus({ preventScroll: true });
    } else {
      selectedKey = null;
      oppsEl.replaceChildren(opps.length ? el("ol", { class: "opp-list" }, opps.map((o, n) => renderListItem(o, n, included)))
        : emptyState(`No ${filter} opportunities.`));
      if (moveFocus) oppsEl.querySelector(".opp-item")?.focus({ preventScroll: true });
    }
  }

  document.querySelectorAll("#filter button").forEach((b) => b.addEventListener("click", () => {
    filter = b.dataset.filter;
    selectedKey = null;
    document.querySelectorAll("#filter button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    showResults();
  }));

  function emptyState(text) {
    const box = el("div", { class: "empty" });
    box.append(svgIcon(["M3 7h18M6 7V5h12v2M5 7l1 13h12l1-13"]), el("div", {}, text));
    return box;
  }

  function setBadge(status) {
    const badge = $("run-badge");
    badge.textContent = { QUEUED: "Queued", RUNNING: "Running", COMPLETED: "Completed",
      COMPLETED_WITH_ERRORS: "Completed with errors", FAILED: "Failed" }[status] || "Idle";
    badge.className = "badge " + (status === "FAILED" ? "failed"
      : status && status.startsWith("COMPLETED") ? "done" : status ? "running" : "");
  }

  function setPhase(phase) {
    const at = PHASES.indexOf(phase);
    document.querySelectorAll("#phases li").forEach((li, n) => {
      li.className = phase === "done" || n < at ? "done" : n === at ? "active" : "";
    });
  }

  function setStats(c = {}, progress = 0) {
    const show = (v) => (v === undefined ? "–" : String(v));
    const read = c.candidates === undefined ? undefined : (c.threads || 0) + (c.crm_rows || 0) + (c.notes || 0);
    $("stat-read").textContent = show(read);
    $("stat-opps").textContent = show(c.opportunities);
    $("stat-matched").textContent = show(c.matched);
    $("stat-unmatched").textContent = show(c.unmatched);
    const pct = Math.max(0, Math.min(100, Math.round(progress)));
    $("progress-bar").style.width = `${pct}%`;
    $("progress").setAttribute("aria-valuenow", String(pct));
  }

  function progressOf(run) {
    const c = run.counts || {};
    if (DONE.has(run.status)) return 100;
    switch (run.phase) {
      case "triage": return 5 + 45 * ((c.triaged || 0) / Math.max(c.candidates || 1, 1));
      case "matching": return 52;
      case "building": return 55 + 45 * ((c.built || 0) / Math.max(c.groups || 1, 1));
      default: return 3;
    }
  }

  function describe(run) {
    const c = run.counts || {};
    const parts = [];
    if (run.days) parts.push(c.threads !== undefined && run.phase !== "reading" && !run.outlook_error
      ? `Outlook ${run.days}d: ${c.emails || 0} emails, ${c.threads || 0} threads` : `Outlook ${run.days}d`);
    const crm = (run.inputs || []).filter((i) => i.source === "crm");
    const notes = (run.inputs || []).filter((i) => i.source === "notes");
    if (crm.length) parts.push(`CRM: ${crm.reduce((n, i) => n + i.records, 0)} rows from ${crm.map((i) => i.name).join(", ")}`
      + (crm.some((i) => i.truncated) ? " (capped)" : ""));
    if (notes.length) parts.push(`${notes.length} seller note${notes.length === 1 ? "" : "s"}`);
    let text = parts.join(" · ");
    const step = { reading: "reading sources", triage: `triage ${c.triaged || 0} of ${c.candidates || 0}`,
      matching: "matching records across sources", building: `building ${c.built || 0} of ${c.groups || 0} opportunities` }[run.phase];
    if (step && !DONE.has(run.status)) text += ` — ${step}`;
    if (DONE.has(run.status) && c.skipped) text += ` · ${c.skipped} not opportunities`;
    if (c.failed) text += ` · ${c.failed} failed`;
    if (run.truncated) text += " · stopped at the email limit";
    if (run.outlook_error) text += ` · Outlook could not be read: ${run.outlook_error}`;
    if (run.error) text += ` · ${run.error}`;
    return text;
  }

  function render(run) {
    currentRun = run;
    const c = run.counts || {};
    setBadge(run.status);
    setPhase(run.phase);
    setStats(c, progressOf(run));
    statusEl.textContent = describe(run);
    statusEl.className = `card-sub ${run.status === "FAILED" ? "error" : "muted"}`;
    const multi = (run.sources_included || []).length > 1;
    $("filter").hidden = !multi || !(run.opportunities || []).length;
    const counts = { all: (run.opportunities || []).length, matched: c.matched || 0, unmatched: c.unmatched || 0 };
    document.querySelectorAll("#filter button").forEach((b) => {
      b.textContent = `${b.dataset.filter[0].toUpperCase()}${b.dataset.filter.slice(1)} (${counts[b.dataset.filter]})`;
    });
    if (!multi) filter = "all";
    if ((run.opportunities || []).length) showResults();
    else oppsEl.replaceChildren(emptyState(DONE.has(run.status) ? "No opportunities found in these sources."
      : run.phase === "building" ? "Building the first intake record (about a minute)…" : "The agent is reading and triaging your sources…"));
    $("log-panel").hidden = !(run.logs || []).length;
    $("logs").textContent = (run.logs || []).map((l) => `${l.at.slice(11, 19)}  ${l.level.padEnd(5)} ${l.step}: ${l.message}`).join("\n");
  }

  // ---------------------------------------------------------------- previous runs (saved server-side per browser session)
  const STATUS_LABEL = { QUEUED: "Queued", RUNNING: "Running", COMPLETED: "Completed",
    COMPLETED_WITH_ERRORS: "With errors", FAILED: "Failed" };
  let historyCursor = null;
  let historyRuns = [];
  let shownRunId = null;
  let myRunId = null;   // the run this page started (its inputs are cleared when it finishes)

  function historyItem(r) {
    const c = r.counts || {};
    const types = [r.days ? "email" : null, ...new Set((r.inputs || []).map((i) => i.source))].filter(Boolean);
    const when = r.created_at ? new Date(r.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
    const result = DONE.has(r.status) && r.status !== "FAILED"
      ? `${c.opportunities ?? 0} opportunit${c.opportunities === 1 ? "y" : "ies"}${c.matched ? ` · ${c.matched} matched` : ""}`
      : STATUS_LABEL[r.status] || r.status;
    const btn = el("button", { type: "button", class: `history-item${r.run_id === shownRunId ? " current" : ""}`,
      "aria-label": `Open run from ${when}`, ...(r.run_id === shownRunId ? { "aria-current": "true" } : {}) },
      el("span", { class: "history-main" },
        el("span", { class: "history-when" }, when),
        el("span", { class: "history-src" }, types.map(srcPill),
          r.days ? el("span", { class: "muted" }, `${r.days}d`) : null)),
      el("span", { class: `history-result${r.status === "FAILED" ? " error" : ""}` }, result));
    btn.addEventListener("click", () => openRun(r.run_id));
    return el("li", {}, btn);
  }

  function renderHistory() {
    $("history").replaceChildren(...(historyRuns.length ? historyRuns.map(historyItem)
      : [el("li", { class: "muted history-empty" }, "No runs yet.")]));
    $("history-more").hidden = !historyCursor;
  }

  async function loadHistory(more = false) {
    try {
      const res = await api("GET", `/runs${more && historyCursor ? `?cursor=${encodeURIComponent(historyCursor)}` : ""}`);
      historyRuns = more ? [...historyRuns, ...res.runs] : res.runs;
      historyCursor = res.next_cursor;
      renderHistory();
    } catch (err) {
      $("history").replaceChildren(el("li", { class: "error history-empty" }, `Could not load runs: ${err.message}`));
    }
  }

  function openRun(runId) {
    if (running && runId !== shownRunId) { runNote("Wait for the current run to finish first."); return; }
    clearTimeout(pollTimer);
    selectedKey = null;
    filter = "all";
    document.querySelectorAll("#filter button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.filter === "all")));
    oppsEl.replaceChildren(emptyState("Loading the run…"));
    poll(runId);
    $("results-top").scrollIntoView({ block: "start", behavior: "smooth" });
  }

  $("history-refresh").addEventListener("click", () => loadHistory());
  $("history-more").addEventListener("click", () => loadHistory(true));

  async function poll(runId) {
    try {
      const run = await api("GET", `/runs/${encodeURIComponent(runId)}`);
      const first = shownRunId !== runId;
      shownRunId = runId;
      localStorage.setItem("lastRunId", runId);
      render(run);
      if (first) renderHistory();
      if (!DONE.has(run.status)) {
        setRunning(true);
        pollTimer = setTimeout(() => poll(runId), POLL_MS);
        return;
      }
      const wasRunning = running;
      setRunning(false);
      if (!wasRunning) return;   // opened a finished run from history
      loadHistory();
      if (run.status !== "FAILED" && runId === myRunId) {   // clear the inputs only after this browser's new run
        files.crm = []; files.notes = [];
        $("notes-text").value = "";
        $("notes-text").dispatchEvent(new Event("input"));
        runNote("Done. Add more sources and run again any time.");
      }
    } catch (err) {
      setRunning(false);
      if (err.message === "run not found") {   // run of an expired session
        localStorage.removeItem("lastRunId");
        setBadge(null);
        oppsEl.replaceChildren(emptyState("No opportunities yet."));
        return;
      }
      statusEl.textContent = `Could not read the run: ${err.message}`;
      statusEl.className = "card-sub error";
    }
  }

  // ---------------------------------------------------------------- start
  setMicrosoft(null);
  if (!cfg.apiUrl) {
    statusEl.textContent = "ui/config.js has no apiUrl yet. Run scripts/deploy.sh first.";
    statusEl.className = "card-sub error";
    return;
  }
  try {
    const s = await api("GET", "/session");
    setMicrosoft(s.microsoft);
  } catch (err) {
    outlookNote(`Could not reach the Intake API: ${err.message}`, true);
  }
  loadHistory();
  const lastRun = localStorage.getItem("lastRunId");
  if (lastRun) poll(lastRun);   // reopen the last run shown (running or finished)
})();
