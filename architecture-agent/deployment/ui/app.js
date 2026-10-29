// Ingram Architecture agent console: SRS in -> ADR, AWS diagram, usage estimate out.
// All agent output is rendered as text nodes (no innerHTML). Files come through the API as JSON (base64 or text).
(() => {
  const cfg = window.ARCH_CONFIG || {};
  const POLL_MS = 4000;
  const MAX_TEXT = 200000;
  const MIN_TEXT = 200;
  const MAX_FILE_BYTES = 4 * 1024 * 1024;
  const FILE_EXT = ["md", "markdown", "txt", "docx", "pdf"];
  const ACTIVE = new Set(["queued", "running"]);
  const STATUS = { queued: "Queued", running: "Working", completed: "Ready", partial: "Ready with gaps", failed: "Failed" };
  const STEPS = [["ingest", "Read SRS"], ["adr", "Design (ADR)"], ["diagram", "Diagram"], ["usage", "Usage estimate"]];
  const DOWNLOADS = [["diagram.png", "PNG"], ["diagram.svg", "SVG"], ["diagram.drawio", "draw.io"], ["adr.md", "ADR (.md)"],
    ["spec.json", "spec.json"], ["usage.json", "usage.json"], ["design.json", "design.json"]];

  const $ = (id) => document.getElementById(id);
  // replaceChildren() turns null into the text "null"; fill() drops empty children first.
  const fill = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false && k !== ""));
  let pickedFile = null;
  let current = null;          // {run, versions, log}
  let viewVersion = null;      // null = follow the latest version
  let pollTimer = null;
  let outTab = ["diagram", "adr", "usage", "review"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "diagram";
  const fileCache = new Map(); // `${runId}/${version}/${name}` -> {content_type, encoding, data}
  let renderSig = "";

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
  const fmtSize = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  const fmtNum = (n) => (typeof n === "number" ? n.toLocaleString() : String(n ?? "–"));
  const CHECK = { pass: "Passed", pass_with_warnings: "Passed", fail: "Failed" };
  const CHECK_TEXT = { pass: "passed", pass_with_warnings: "passed with warnings (see Gaps & assumptions)", fail: "failed" };
  const statusPill = (s) => el("span", { class: `pill s-${s}` }, STATUS[s] || s);
  const humanKey = (k) => k.replace(/_/g, " ").replace(/\b(gb|tb|mb|vcpu|iops|az|lcu|api|rps)\b/gi, (m) => m.toUpperCase());

  // ---------------------------------------------------------------- theme
  const THEMES = [
    ["light", "Light theme", ["M12 4V2M12 22v-2M4 12H2M22 12h-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"]],
    ["dark", "Dark theme", ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"]],
    ["system", "System theme", ["M3 4h18v12H3zM8 20h8M12 16v4"]],
  ];
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(choice) {
    localStorage.setItem("archTheme", choice);
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
  systemDark.addEventListener("change", () => { if (localStorage.getItem("archTheme") === "system") applyTheme("system"); });
  applyTheme(localStorage.getItem("archTheme") || "dark");

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

  const toBase64 = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error(`could not read ${file.name}`));
    reader.readAsDataURL(file);
  });

  function showQuota(res) {
    if (res && typeof res.runs_today === "number") $("quota").textContent = `${res.runs_today} / ${res.runs_per_day} runs today`;
  }

  // ---------------------------------------------------------------- new run form
  function formStatus(text, isError = false) {
    $("form-status").textContent = text;
    $("form-status").className = `status-line ${isError ? "error" : "muted"}`;
  }
  function refreshForm() {
    const n = $("text").value.length;
    $("text-count").textContent = `${n.toLocaleString()} / ${MAX_TEXT.toLocaleString()}`;
    $("text").disabled = !!pickedFile;
    $("start").disabled = !(pickedFile || $("text").value.trim().length >= MIN_TEXT);
    $("clear").disabled = !(pickedFile || n || $("title").value);
    const list = $("file-list");
    fill(list);
    if (pickedFile) {
      const remove = el("button", { type: "button", class: "btn sm ghost", "aria-label": `Remove ${pickedFile.name}` }, "Remove");
      remove.addEventListener("click", () => { pickedFile = null; refreshForm(); });
      list.append(el("li", {}, el("span", { class: "file-name" }, pickedFile.name), el("span", { class: "muted" }, fmtSize(pickedFile.size)), remove));
    }
  }
  function pickFile(file) {
    if (!file) return;
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    if (!FILE_EXT.includes(ext)) { formStatus("Upload a .md, .txt, .docx or .pdf file.", true); return; }
    if (file.size > MAX_FILE_BYTES) { formStatus(`${file.name} is ${fmtSize(file.size)}; files up to 4 MB.`, true); return; }
    pickedFile = file;
    formStatus("The file is used instead of the pasted text.");
    refreshForm();
  }
  $("file").addEventListener("change", () => { pickFile($("file").files[0]); $("file").value = ""; });
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));
  $("text").addEventListener("input", refreshForm);
  $("title").addEventListener("input", refreshForm);
  $("clear").addEventListener("click", () => { pickedFile = null; $("text").value = ""; $("title").value = ""; formStatus(""); refreshForm(); });
  $("sample").addEventListener("click", async () => {
    try {
      const resp = await fetch("sample_srs.md");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      pickedFile = null;
      $("text").value = await resp.text();
      if (!$("title").value) $("title").value = "Sample: billing dispute assistant";
      formStatus("Sample requirements loaded.");
      refreshForm();
    } catch (err) { formStatus(`Could not load the sample: ${err.message}`, true); }
  });

  $("run-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("start").disabled = true;
    formStatus("Starting…");
    try {
      const body = { title: $("title").value.trim() };
      if (pickedFile) body.file = { name: pickedFile.name, content_base64: await toBase64(pickedFile) };
      else body.srs_text = $("text").value;
      const res = await api("POST", "/runs", body);
      formStatus("Started. The result appears on the right; you can leave this page and come back.");
      pickedFile = null;
      $("text").value = "";
      $("title").value = "";
      refreshForm();
      viewVersion = null;
      await openRun(res.run_id);
      loadHistory();
      api("GET", "/session").then(showQuota).catch(() => {});
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
      fill(list, ...(runs.length ? runs.map(historyItem) : [el("li", { class: "empty" }, "No runs yet.")]));
    } catch (err) {
      fill(list, el("li", { class: "error" }, `Could not load the history: ${err.message}`));
    }
  }
  function historyItem(r) {
    const btn = el("button", { type: "button", class: "card-item", "data-run": r.run_id, "aria-current": current && current.run.run_id === r.run_id ? "true" : "false" },
      el("div", { class: "card-main" },
        el("span", { class: "card-title" }, r.title || r.srs_filename || "Untitled"),
        el("span", { class: "card-meta" }, statusPill(r.status), el("span", { class: "muted" }, fmtTime(r.created_at)),
          r.version > 1 ? el("span", { class: "muted" }, `v${r.version}`) : null)));
    btn.addEventListener("click", () => { viewVersion = null; openRun(r.run_id); });
    return el("li", {}, btn);
  }
  $("history-refresh").addEventListener("click", loadHistory);

  // ---------------------------------------------------------------- one run
  async function openRun(runId) {
    clearTimeout(pollTimer);
    if (!current || current.run.run_id !== runId) renderSig = "";
    try {
      const data = await api("GET", `/runs/${encodeURIComponent(runId)}`);
      const wasActive = current && current.run.run_id === runId && ACTIVE.has(current.run.status);
      current = data;
      sessionStorage.setItem("archRunId", runId);
      renderRun();
      if (ACTIVE.has(data.run.status)) pollTimer = setTimeout(() => openRun(runId), POLL_MS);
      else if (wasActive) loadHistory();
    } catch (err) {
      if (String(err.message).includes("not found")) { sessionStorage.removeItem("archRunId"); return; }
      pollTimer = setTimeout(() => openRun(runId), POLL_MS * 2);
    }
    document.querySelectorAll("#history .card-item").forEach((b) => b.setAttribute("aria-current", String(b.dataset.run === runId)));
  }

  function selectedVersion() {
    const versions = current.versions || [];
    if (viewVersion) return versions.find((v) => v.version === viewVersion) || versions[versions.length - 1];
    const latest = versions[versions.length - 1];
    if (latest && (latest.status === "failed") && current.run.latest_complete_version) {
      return versions.find((v) => v.version === current.run.latest_complete_version) || latest;
    }
    return latest;
  }
  const hasOutputs = (v) => v && v.files && v.files["adr.md"];

  function renderRun() {
    const { run, versions, log } = current;
    $("result-empty").hidden = true;
    $("result").hidden = false;
    const v = selectedVersion();
    $("run-title").textContent = v?.title || run.title || run.srs_filename || "Untitled";
    $("run-sub").textContent = [`Started ${fmtTime(run.created_at)}`,
      run.input_kind === "file" ? `from ${run.srs_filename}` : "from pasted text",
      run.input_chars ? `${fmtNum(run.input_chars)} characters` : ""].filter(Boolean).join(" · ");
    fill($("status-pill"), statusPill(run.status));

    const pick = $("version-pick");
    fill(pick, ...versions.map((x) => el("option", { value: x.version, selected: v && x.version === v.version },
      `Version ${x.version}${x.kind === "revise" ? " (revised)" : ""} · ${STATUS[x.status] || x.status}`)));
    pick.hidden = versions.length < 2;

    // progress of the newest version while it runs
    const latest = versions[versions.length - 1];
    const active = ACTIVE.has(run.status);
    $("running").hidden = !active;
    if (active) {
      const pct = Math.max(3, Number(run.progress || 0));
      $("progress-bar").style.width = `${pct}%`;
      $("progress").setAttribute("aria-valuenow", String(pct));
      const order = STEPS.map(([k]) => k);
      const at = order.indexOf(run.stage);
      fill($("steps"), ...STEPS.filter(([k]) => k !== "ingest" || run.input_kind === "file" && latest.version === 1)
        .map(([k, label]) => {
          const i = order.indexOf(k);
          const cls = at < 0 ? "" : i < at ? "done" : i === at ? "active" : "";
          return el("li", { class: cls }, label);
        }));
    }
    const err = (latest && latest.status === "failed" && latest.error) || (run.status === "failed" && run.error);
    $("run-error").hidden = !err;
    $("run-error").textContent = err ? `Version ${latest.version} failed: ${err}` : "";

    renderStats(v);
    $("revise").disabled = active || !run.latest_complete_version;
    renderLog(log);

    const sig = JSON.stringify([run.run_id, v?.version, v?.status, Object.keys(v?.files || {}).sort(), v?.diagram?.status, v?.usage?.status]);
    if (sig !== renderSig) {
      renderSig = sig;
      renderOutputs(v);
    }
  }

  function stat(value, label) { return el("div", { class: "stat" }, el("b", {}, value), el("span", {}, label)); }
  function renderStats(v) {
    const box = $("stats");
    if (!v || !v.adr) { fill(box); return; }
    const c = v.adr.counts || {};
    const d = v.diagram || {};
    const services = v.usage && v.usage.counts ? v.usage.counts.entries : undefined;
    fill(box, 
      stat(fmtNum(c.decisions), "Decisions"),
      stat(fmtNum(c.components), "Components"),
      stat(fmtNum(c.requirements), "Requirements"),
      stat(fmtNum((c.assumptions || 0) + (c.assumed_components || 0)), "Assumptions"),
      stat(d.status === "completed" ? (CHECK[d.report_status] || "Passed") : d.status === "failed" ? "Failed" : "…",
        d.report_status === "pass_with_warnings" ? "Diagram check (warnings)" : "Diagram check"),
      services !== undefined ? stat(fmtNum(services), "Services estimated") : null,
      v.seconds ? stat(`${Math.round(v.seconds / 6) / 10} min`, "Agent time") : null,
    );
  }

  function renderLog(log) {
    fill($("log"), ...(log || []).slice().reverse().map((l) =>
      el("li", { class: `audit-item lvl-${l.level}` },
        el("div", { class: "audit-head" }, el("span", { class: "stage" }, l.stage), el("span", {}, l.message),
          el("span", { class: "muted" }, new Date(l.at).toLocaleTimeString())))));
  }

  $("version-pick").addEventListener("change", () => {
    viewVersion = Number($("version-pick").value);
    renderSig = "";
    renderRun();
  });

  // ---------------------------------------------------------------- outputs
  document.querySelectorAll(".subtab").forEach((t) => t.addEventListener("click", () => showOut(t.dataset.out)));
  function showOut(name) {
    outTab = name;
    document.querySelectorAll(".subtab").forEach((t) => t.setAttribute("aria-current", t.dataset.out === name ? "page" : "false"));
    document.querySelectorAll("[data-out-panel]").forEach((p) => { p.hidden = p.dataset.outPanel !== name; });
  }

  async function getFile(version, name) {
    const key = `${current.run.run_id}/${version}/${name}`;
    if (!fileCache.has(key)) {
      fileCache.set(key, api("GET", `/runs/${current.run.run_id}/files/${encodeURIComponent(name)}?version=${version}`)
        .catch((err) => { fileCache.delete(key); throw err; }));
    }
    return fileCache.get(key);
  }
  function toBlob(file) {
    if (file.encoding === "base64") {
      const bin = atob(file.data);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
      return new Blob([bytes], { type: file.content_type });
    }
    return new Blob([file.data], { type: `${file.content_type};charset=utf-8` });
  }
  function downloads(v, names) {
    const files = v.files || {};
    const runName = (v.title || current.run.title || "architecture").replace(/[^\w-]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "architecture";
    const btns = DOWNLOADS.filter(([n]) => names.includes(n) && files[n]).map(([n, label]) => {
      const b = el("button", { type: "button", class: "btn sm ghost", title: `Download ${n} (${fmtSize(files[n])})` }, label);
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const url = URL.createObjectURL(toBlob(await getFile(v.version, n)));
          const a = el("a", { href: url, download: `${runName}-v${v.version}-${n}` });
          document.body.append(a);
          a.click();
          a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 10000);
        } catch (err) { b.textContent = `${label}: ${err.message}`; }
        b.disabled = false;
      });
      return b;
    });
    return btns.length ? el("div", { class: "downloads" }, el("span", { class: "muted small" }, "Download"), btns) : null;
  }
  const waiting = (text) => el("div", { class: "loading" }, text);

  function renderOutputs(v) {
    const panels = { diagram: $("out-diagram"), adr: $("out-adr"), usage: $("out-usage"), review: $("out-review") };
    if (!hasOutputs(v)) {
      const text = v && v.status === "failed" ? "This version has no outputs." : "The outputs appear here as each step finishes.";
      Object.values(panels).forEach((p) => fill(p, waiting(text)));
      showOut(outTab);
      return;
    }
    renderDiagram(v, panels.diagram);
    renderAdr(v, panels.adr);
    renderUsage(v, panels.usage);
    renderReview(v, panels.review);
    showOut(outTab);
  }

  async function renderDiagram(v, box) {
    const d = v.diagram || {};
    if (!d.status) { fill(box, waiting("Drawing the diagram…")); return; }
    if (d.status === "failed" && !v.files["diagram.png"]) {
      fill(box, el("p", { class: "error" }, `The diagram could not be drawn: ${d.error || "unknown error"}`),
        el("p", { class: "out-note" }, "The decision record is still available. Revise the design or start again."));
      return;
    }
    const s = d.summary || {};
    const note = el("p", { class: "out-note" }, `${fmtNum(s.components)} components, ${fmtNum(s.relationships)} relationships, ${fmtNum(s.boundaries)} groups · layout check: ${CHECK_TEXT[d.report_status] || d.report_status || "–"} · ${d.model_calls ? `${d.model_calls} model calls` : "drawn from the design, no model call"}`);
    const frame = el("div", { class: "diagram-frame" }, waiting("Loading the diagram…"));
    fill(box, downloads(v, ["diagram.png", "diagram.svg", "diagram.drawio"]), note, frame);
    try {
      const file = await getFile(v.version, "diagram.png");
      const img = el("img", { src: `data:image/png;base64,${file.data}`, alt: `AWS architecture diagram: ${v.title || "architecture"}` });
      img.addEventListener("click", () => frame.classList.toggle("zoomed"));
      fill(frame, img);
    } catch (err) { fill(frame, el("p", { class: "error" }, `Could not load the diagram: ${err.message}`)); }
  }

  async function renderAdr(v, box) {
    const body = el("div", { class: "md-scroll" }, waiting("Loading the decision record…"));
    fill(box, downloads(v, ["adr.md", "spec.json", "design.json"]), body);
    try {
      const file = await getFile(v.version, "adr.md");
      fill(body, window.renderMarkdown(file.data));
    } catch (err) { fill(body, el("p", { class: "error" }, `Could not load the ADR: ${err.message}`)); }
  }

  function kvList(obj) {
    return el("div", { class: "kv" }, Object.entries(obj).filter(([k]) => k !== "service").map(([k, val]) =>
      el("span", {}, el("b", {}, humanKey(k)), typeof val === "object" && val !== null ? JSON.stringify(val) : fmtNum(val))));
  }
  async function renderUsage(v, box) {
    const u = v.usage || {};
    if (!u.status) { fill(box, waiting("Estimating usage…")); return; }
    if (u.status === "failed") {
      fill(box, el("p", { class: "error" }, `The usage estimate failed: ${u.error || "unknown error"}`),
        el("p", { class: "out-note" }, "Create a new version to try again."));
      return;
    }
    const body = el("div", {}, waiting("Loading the usage estimate…"));
    fill(box, downloads(v, ["usage.json"]),
      el("p", { class: "out-note" }, "Monthly usage per service, in the shape the cost calculator reads. Figures are estimates; check the assumptions below."), body);
    try {
      const usage = JSON.parse((await getFile(v.version, "usage.json")).data);
      const groups = usage && typeof usage.services === "object" && usage.services ? usage.services : {};
      const parts = [];
      for (const [cat, items] of Object.entries(groups)) {
        if (!Array.isArray(items) || !items.length) continue;
        parts.push(el("h3", { class: "usage-cat" }, humanKey(cat).replace(/^./, (c) => c.toUpperCase())));
        parts.push(el("div", { class: "table-wrap" }, el("table", { class: "usage" },
          el("thead", {}, el("tr", {}, el("th", {}, "Service"), el("th", {}, "Configuration and monthly usage"))),
          el("tbody", {}, items.map((it) => el("tr", {}, el("td", { class: "svc" }, it.service || "–"), el("td", {}, kvList(it))))))));
      }
      if (!parts.length) parts.push(el("pre", { class: "package" }, JSON.stringify(usage, null, 2)));
      let notes = null;
      if (v.files["usage_assumptions.md"]) {
        try {
          const md = (await getFile(v.version, "usage_assumptions.md")).data;
          if (md.trim()) notes = el("details", { class: "section", open: true }, el("summary", {}, "Usage assumptions"), window.renderMarkdown(md));
        } catch { /* optional */ }
      }
      fill(body, ...parts, notes || "");
    } catch (err) { fill(body, el("p", { class: "error" }, `Could not load the usage estimate: ${err.message}`)); }
  }

  function gapGroup(title, items, cls = "") {
    if (!items.length) return null;
    return el("section", { class: "gap-group" }, el("h3", {}, title, el("span", { class: "count" }, items.length)),
      el("ul", { class: "gap-list" }, items.map((x) => el("li", { class: cls }, x))));
  }
  async function renderReview(v, box) {
    fill(box, waiting("Loading…"));
    let design = null;
    try { design = JSON.parse((await getFile(v.version, "design.json")).data); } catch { /* shown below */ }
    const d = v.diagram || {};
    const groups = [];
    if (design) {
      groups.push(gapGroup("Open questions for the customer", (design.open_questions || []).map((q) => (typeof q === "string" ? q : q.text || JSON.stringify(q))), "red"));
      groups.push(gapGroup("Assumptions in the design", (design.assumptions || []).map((a) =>
        el("div", {}, el("b", {}, `${a.id || ""} `), a.text || "", a.reason ? el("div", { class: "muted" }, a.reason) : null))));
      groups.push(gapGroup("Components the SRS did not state", (design.components || []).filter((c) => c.assumed).map((c) =>
        el("div", {}, el("b", {}, `${c.name} `), el("span", { class: "muted" }, c.service || ""), c.assumption_reason ? el("div", { class: "muted" }, c.assumption_reason) : null)), "blue"));
    }
    groups.push(gapGroup("Diagram: items needing confirmation", (d.unresolved || []).map((u) =>
      el("div", {}, el("b", {}, `${u.about} `), u.suggestion || "", u.reasoning ? el("div", { class: "muted" }, u.reasoning) : null)), "red"));
    groups.push(gapGroup("Diagram: contradictions in the input", ((d.contradictions || {}).contradictions || []).map((c) =>
      el("div", {}, el("b", {}, `${c.subject}: `), `${c.claim_a?.statement || ""} vs ${c.claim_b?.statement || ""}`, c.note ? el("div", { class: "muted" }, c.note) : null)), "red"));
    groups.push(gapGroup("Diagram suggestions and warnings", [...(d.suggestions || []), ...(d.warnings || [])].map((s) => (typeof s === "string" ? s : s.message || JSON.stringify(s))), "blue"));
    const shown = groups.filter(Boolean);
    fill(box, ...(shown.length ? shown : [el("div", { class: "empty" }, design ? "No gaps or assumptions were flagged." : "The design file could not be loaded.")]));
  }

  // ---------------------------------------------------------------- revise
  $("revise-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const instruction = $("instruction").value.trim();
    const status = $("revise-status");
    if (instruction.length < 5) { status.textContent = "Say what to change in the design."; status.className = "status-line error"; return; }
    $("revise").disabled = true;
    status.textContent = "Starting a new version…";
    status.className = "status-line muted";
    try {
      const res = await api("POST", `/runs/${current.run.run_id}/revise`, { instruction });
      $("instruction").value = "";
      status.textContent = `Version ${res.version} started.`;
      viewVersion = null;
      await openRun(current.run.run_id);
      loadHistory();
      api("GET", "/session").then(showQuota).catch(() => {});
    } catch (err) {
      status.textContent = `Not started: ${err.message}`;
      status.className = "status-line error";
      $("revise").disabled = false;
    }
  });

  // ---------------------------------------------------------------- start
  refreshForm();
  loadHistory();
  api("GET", "/session").then(showQuota).catch(() => {});
  const lastRun = sessionStorage.getItem("archRunId");
  if (lastRun) openRun(lastRun);
})();
