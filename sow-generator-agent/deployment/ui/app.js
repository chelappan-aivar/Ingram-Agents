// Ingram SOW generator console: template + customer inputs in -> Ingram Micro SOW draft, rules check, AI edits, .docx.
// All agent output is rendered as text nodes (no innerHTML); markdown goes through the safe renderer (markdown.js).
(() => {
  const cfg = window.SOW_CONFIG || {};
  const D = window.SowDraft;
  const POLL_MS = 2500;
  const MAX_BODY_CHARS = 4.4 * 1024 * 1024;      // the API accepts 4.5 MB request bodies (files travel as base64)
  const MAX_IMAGE_BYTES = 3.75 * 1024 * 1024;    // Bedrock's per-image limit
  const MAX_DETAILS = 100000;
  const TRANSCRIPT_EXT = ["pdf", "docx", "txt", "md", "csv", "xlsx"];
  const IMAGE_EXT = ["png", "jpg", "jpeg"];
  const ACTIVE = new Set(["queued", "running"]);
  const STATUS = { queued: "Queued", running: "Working", completed: "Ready", failed: "Failed" };
  const STEPS = [["reading_inputs", "Read inputs"], ["drafting", "Draft the SOW"], ["done", "Ready"]];
  const SEVERITY = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };

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
  const ext = (name) => (String(name).split(".").pop() || "").toLowerCase();
  const statusPill = (s) => el("span", { class: `pill s-${s}` }, STATUS[s] || s);
  const templateLabel = (id) => (state.templates.find((t) => t.id === id) || {}).label || id || "";

  // ---------------------------------------------------------------- theme
  const THEMES = [
    ["light", "Light theme", ["M12 4V2M12 22v-2M4 12H2M22 12h-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"]],
    ["dark", "Dark theme", ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"]],
    ["system", "System theme", ["M3 4h18v12H3zM8 20h8M12 16v4"]],
  ];
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(choice) {
    localStorage.setItem("sowTheme", choice);
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
  systemDark.addEventListener("change", () => { if (localStorage.getItem("sowTheme") === "system") applyTheme("system"); });
  applyTheme(localStorage.getItem("sowTheme") || "dark");

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
  const readDataUrl = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error(`could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
  function showQuota(res) {
    if (res && typeof res.runs_today === "number") $("quota").textContent = `${res.runs_today} / ${res.runs_per_day} AI runs today`;
  }
  const refreshQuota = () => api("GET", "/session").then(showQuota).catch(() => {});

  // ---------------------------------------------------------------- state
  const state = {
    templates: [],
    transcripts: [],          // [{name, size, dataUrl}]
    images: [],               // [{name, size, dataUrl}]
    draft: null,              // {job, log}
    markdown: "",             // the working copy of the current draft
    dirty: false,
    pollTimer: null,
    editTimer: null,
    checkTimer: null,
    proposals: [],            // [{p, r, state: "open" | "applied" | "dismissed" | "stale"}]
    chat: new Map(),          // draft id -> [{role, content}]
    attachments: new Map(),   // draft id -> {images: [{filename, content_b64}], upstream}  (this tab only)
  };

  // ---------------------------------------------------------------- templates
  async function loadTemplates() {
    const select = $("template");
    try {
      const { templates } = await api("GET", "/templates");
      state.templates = templates || [];
      fill(select, el("option", { value: "" }, "Choose a template"),
        state.templates.map((t) => el("option", { value: t.id }, t.label || t.id)));
      $("template-status").textContent = state.templates.length ? "" : "No templates are available.";
    } catch (err) {
      fill(select, el("option", { value: "" }, "Templates unavailable"));
      $("template-status").textContent = `Could not load the templates: ${err.message}`;
    }
    refreshForm();
  }
  $("template").addEventListener("change", refreshForm);
  $("template-view").addEventListener("click", async () => {
    const id = $("template").value;
    if (!id) return;
    $("template-title").textContent = templateLabel(id);
    fill($("template-body"), el("div", { class: "loading" }, "Loading the template…"));
    showTemplatePreview(true);
    try {
      const { markdown } = await api("GET", `/templates/${id.split("/").map(encodeURIComponent).join("/")}`);
      // The template wraps the sample SOW in <sample> and its rules in <requirements>; show them as text.
      fill($("template-body"), window.renderMarkdown(String(markdown || "").replace(/<\/?(sample|requirements)>/gi, ""), { lineBreaks: true }));
    } catch (err) { fill($("template-body"), el("p", { class: "error" }, `Could not load the template: ${err.message}`)); }
  });
  $("template-close").addEventListener("click", () => showTemplatePreview(false));
  function showTemplatePreview(on) {
    $("template-preview").hidden = !on;
    $("draft").hidden = on || !state.draft;
    $("draft-empty").hidden = on || !!state.draft;
    if (on) $("template-close").focus();
  }

  // ---------------------------------------------------------------- new SOW form
  function formStatus(text, isError = false) {
    $("form-status").textContent = text;
    $("form-status").className = `status-line ${isError ? "error" : "muted"}`;
  }
  function bodySize() {
    return [...state.transcripts, ...state.images].reduce((n, f) => n + f.dataUrl.length, 0)
      + $("details").value.length + $("upstream").value.length;
  }
  function upstreamValue() {
    const raw = $("upstream").value.trim();
    if (!raw) return { ok: true, value: null };
    try {
      const value = JSON.parse(raw);
      if (!value || typeof value !== "object" || Array.isArray(value)) return { ok: false, error: "Upstream data must be a JSON object." };
      return { ok: true, value };
    } catch (err) { return { ok: false, error: `Upstream data is not valid JSON: ${err.message}` }; }
  }
  function fileList(listId, files, kind) {
    fill($(listId), files.map((f, i) => {
      const remove = el("button", { type: "button", class: "btn sm ghost", "aria-label": `Remove ${f.name}` }, "Remove");
      remove.addEventListener("click", () => { files.splice(i, 1); refreshForm(); });
      return el("li", {}, el("span", { class: `pill ft-${ext(f.name)}` }, ext(f.name).toUpperCase()),
        el("span", { class: "file-name" }, f.name), el("span", { class: "muted" }, fmtSize(f.size)),
        kind === "image" ? el("span", { class: "muted small" }, `arch:${i + 1}`) : null, remove);
    }));
  }
  function refreshForm() {
    const n = $("details").value.length;
    $("details-count").textContent = `${n.toLocaleString()} / ${MAX_DETAILS.toLocaleString()}`;
    fileList("transcript-list", state.transcripts, "transcript");
    fileList("image-list", state.images, "image");
    const size = bodySize();
    const tooBig = size > MAX_BODY_CHARS;
    $("size-note").textContent = state.transcripts.length || state.images.length
      ? `Upload size ${fmtSize(size)} of ${fmtSize(MAX_BODY_CHARS)}${tooBig ? " – remove a file to continue." : ""}` : "";
    $("size-note").className = `hint ${tooBig ? "error" : ""}`;
    const up = upstreamValue();
    $("upstream-status").textContent = up.ok ? (up.value ? "Valid JSON." : "UpstreamInputs v1: meta, intake, discovery, architecture (services, assumptions, nfrs), cost (line items, assumptions, source_link = cost calculator URL).") : up.error;
    $("upstream-status").className = `hint ${up.ok ? "" : "error"}`;
    const hasInput = $("details").value.trim() || state.transcripts.length || (up.ok && up.value);
    $("generate").disabled = !($("customer").value.trim() && $("template").value && hasInput && !tooBig && up.ok);
    $("template-view").disabled = !$("template").value;
  }
  async function addFiles(fileListObj, kind) {
    const allowed = kind === "image" ? IMAGE_EXT : TRANSCRIPT_EXT;
    const target = kind === "image" ? state.images : state.transcripts;
    for (const file of fileListObj) {
      if (!allowed.includes(ext(file.name))) { formStatus(`${file.name}: use ${allowed.join(", ")} files.`, true); continue; }
      if (kind === "image" && file.size > MAX_IMAGE_BYTES) { formStatus(`${file.name} is ${fmtSize(file.size)}; images up to 3.75 MB.`, true); continue; }
      if (target.length >= (kind === "image" ? 6 : 10)) { formStatus(`Up to ${kind === "image" ? 6 : 10} ${kind}s.`, true); break; }
      try { target.push({ name: file.name, size: file.size, dataUrl: await readDataUrl(file) }); }
      catch (err) { formStatus(err.message, true); }
    }
    refreshForm();
  }
  for (const [input, drop, kind] of [["transcripts", "transcripts-drop", "transcript"], ["images", "images-drop", "image"]]) {
    $(input).addEventListener("change", async () => { await addFiles($(input).files, kind); $(input).value = ""; });
    const zone = $(drop);
    ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("over"); }));
    ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
    zone.addEventListener("drop", (e) => addFiles(e.dataTransfer.files, kind));
  }
  ["customer", "details", "upstream", "opportunity"].forEach((id) => $(id).addEventListener("input", refreshForm));
  $("clear").addEventListener("click", () => {
    $("gen-form").reset();
    state.transcripts.length = 0;
    state.images.length = 0;
    formStatus("");
    refreshForm();
  });

  $("gen-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const up = upstreamValue();
    if (!up.ok) { formStatus(up.error, true); return; }
    $("generate").disabled = true;
    formStatus("Starting…");
    const images = state.images.map((f) => ({ filename: f.name, content_b64: f.dataUrl }));
    const body = {
      customer_name: $("customer").value.trim(),
      opportunity_id: $("opportunity").value.trim() || null,
      template_id: $("template").value,
      project_details: $("details").value,
      transcript_files: state.transcripts.map((f) => ({ name: f.name, content_b64: f.dataUrl })),
      architecture_image_b64s: images.map((i) => i.content_b64),
      arch_image_filenames: images.map((i) => i.filename),
      upstream: up.value,
    };
    try {
      const res = await api("POST", "/generations", body);
      state.attachments.set(res.job_id, { images, upstream: up.value });
      formStatus("Started. The draft appears on the right as it is written; you can leave and come back.");
      openDraft(res.job_id);
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
      const { jobs } = await api("GET", "/jobs");
      fill(list, ...(jobs.length ? jobs.map(historyItem) : [el("li", { class: "empty" }, "No drafts yet.")]));
    } catch (err) {
      fill(list, el("li", { class: "error" }, `Could not load the history: ${err.message}`));
    }
  }
  function historyItem(j) {
    const btn = el("button", { type: "button", class: "card-item", "data-job": j.job_id,
      "aria-current": state.draft && state.draft.job.job_id === j.job_id ? "true" : "false" },
      el("div", { class: "card-main" },
        el("span", { class: "card-title" }, j.customer_name || "Untitled"),
        el("span", { class: "card-meta" }, statusPill(j.status), el("span", { class: "muted" }, templateLabel(j.template_id)),
          el("span", { class: "muted" }, fmtTime(j.created_at)), j.opportunity_id ? el("span", { class: "muted" }, j.opportunity_id) : null)));
    btn.addEventListener("click", () => openDraft(j.job_id));
    return el("li", {}, btn);
  }
  $("history-refresh").addEventListener("click", loadHistory);

  // ---------------------------------------------------------------- one draft
  function resetPanels() {
    clearTimeout(state.editTimer);
    clearTimeout(state.checkTimer);
    state.proposals = [];
    $("edit-panel").hidden = true;
    $("edit-open").setAttribute("aria-expanded", "false");
    $("check-panel").hidden = true;
    $("edit-reply").hidden = true;
    $("proposals-head").hidden = true;
    fill($("proposals"));
    $("edit-status").textContent = "";
    $("toolbar-status").textContent = "";
    setRaw(false);
  }
  async function openDraft(jobId) {
    clearTimeout(state.pollTimer);
    const switching = !state.draft || state.draft.job.job_id !== jobId;
    if (switching) {
      resetPanels();
      const saved = sessionStorage.getItem(`sowDraft:${jobId}`);
      state.markdown = saved || "";
      state.dirty = !!saved;
    }
    showTemplatePreview(false);
    try {
      const data = await api("GET", `/jobs/${encodeURIComponent(jobId)}`);
      if (data.job.kind !== "generation") throw new Error("not a draft");
      state.draft = data;
      sessionStorage.setItem("sowDraftId", jobId);
      if (!state.dirty) state.markdown = data.job.markdown || "";
      renderDraft();
      if (ACTIVE.has(data.job.status)) state.pollTimer = setTimeout(() => openDraft(jobId), POLL_MS);
      else if (!switching) loadHistory();
    } catch (err) {
      if (/not found|not a draft/i.test(err.message)) { sessionStorage.removeItem("sowDraftId"); return; }
      state.pollTimer = setTimeout(() => openDraft(jobId), POLL_MS * 2);
    }
    document.querySelectorAll("#history .card-item").forEach((b) => b.setAttribute("aria-current", String(b.dataset.job === jobId)));
  }

  function resolveImage(target) {
    const att = state.draft && state.attachments.get(state.draft.job.job_id);
    if (!att) return null;
    const m = /^arch:(\d+)$/i.exec(target.trim());
    const img = m ? att.images[Number(m[1]) - 1] : att.images.find((i) => i.filename === target.trim());
    return img ? img.content_b64 : null;
  }
  function renderPreview() {
    fill($("preview"), state.markdown ? window.renderMarkdown(state.markdown, { lineBreaks: true, resolveImage })
      : el("div", { class: "loading" }, "The draft appears here as the agent writes it."));
  }

  function renderDraft() {
    const { job, log } = state.draft;
    $("draft-empty").hidden = true;
    $("draft").hidden = false;
    $("draft-title").textContent = job.customer_name || "Untitled";
    const inputs = job.inputs || {};
    $("draft-sub").textContent = [templateLabel(job.template_id), job.opportunity_id, `started ${fmtTime(job.created_at)}`,
      inputs.transcripts && inputs.transcripts.length ? `${inputs.transcripts.length} transcript(s)` : "",
      inputs.architecture_images ? `${inputs.architecture_images} image(s)` : "",
      job.chars ? `${fmtNum(job.chars)} characters` : ""].filter(Boolean).join(" · ");
    fill($("status-pill"), statusPill(job.status));

    const active = ACTIVE.has(job.status);
    $("running").hidden = !active;
    if (active) {
      const pct = Math.max(3, Number(job.progress || 0));
      $("progress-bar").style.width = `${pct}%`;
      $("progress").setAttribute("aria-valuenow", String(pct));
      const order = STEPS.map(([k]) => k);
      const at = Math.max(0, order.indexOf(job.stage));
      fill($("steps"), STEPS.map(([k, label], i) => el("li", { class: i < at ? "done" : i === at ? "active" : "" },
        k === "drafting" && job.chars ? `${label} (${fmtNum(job.chars)} characters)` : label)));
    }
    $("draft-error").hidden = !(job.status === "failed" && job.error);
    $("draft-error").textContent = job.status === "failed" ? `The draft failed: ${job.error || "unknown error"}` : "";
    $("toolbar").hidden = job.status !== "completed";
    $("dirty").hidden = !state.dirty;
    if ($("raw-wrap").hidden) renderPreview();
    else if (!state.dirty) $("raw").value = state.markdown;
    renderLog(log);
  }
  function renderLog(log) {
    fill($("log"), (log || []).slice().reverse().map((l) =>
      el("li", { class: `audit-item lvl-${l.level}` },
        el("div", { class: "audit-head" }, el("span", { class: "stage" }, l.stage), el("span", {}, l.message),
          el("span", { class: "muted" }, new Date(l.at).toLocaleTimeString())))));
  }
  function setMarkdown(md, dirty = true) {
    state.markdown = md;
    state.dirty = dirty;
    if (state.draft) sessionStorage.setItem(`sowDraft:${state.draft.job.job_id}`, md);
    $("dirty").hidden = !dirty;
    if (!$("raw-wrap").hidden) $("raw").value = md;
    renderPreview();
  }

  // raw markdown editing
  function setRaw(on) {
    $("raw-wrap").hidden = !on;
    $("preview").hidden = on;
    $("raw-toggle").setAttribute("aria-pressed", String(on));
    $("raw-toggle").textContent = on ? "Show preview" : "Edit markdown";
    if (on) { $("raw").value = state.markdown; $("raw").focus(); } else renderPreview();
  }
  $("raw-toggle").addEventListener("click", () => setRaw($("raw-wrap").hidden));
  $("raw").addEventListener("input", () => {
    state.markdown = $("raw").value;
    state.dirty = true;
    $("dirty").hidden = false;
    if (state.draft) sessionStorage.setItem(`sowDraft:${state.draft.job.job_id}`, state.markdown);
  });

  // ---------------------------------------------------------------- side jobs (edit, check): poll until done
  async function pollJob(jobId, timerKey, onUpdate) {
    try {
      const data = await api("GET", `/jobs/${encodeURIComponent(jobId)}`);
      onUpdate(data.job);
      if (ACTIVE.has(data.job.status)) state[timerKey] = setTimeout(() => pollJob(jobId, timerKey, onUpdate), POLL_MS);
    } catch (err) {
      state[timerKey] = setTimeout(() => pollJob(jobId, timerKey, onUpdate), POLL_MS * 2);
    }
  }
  function toolbarStatus(text, isError = false) {
    $("toolbar-status").textContent = text;
    $("toolbar-status").className = `status-line ${isError ? "error" : "muted"}`;
  }

  // ---------------------------------------------------------------- check draft
  $("check").addEventListener("click", async () => {
    if (!state.markdown.trim()) return;
    const verify = $("check-ai").checked;
    $("check").disabled = true;
    toolbarStatus(verify ? "Checking the rules and asking the AI reviewer (about a minute)…" : "Checking the rules…");
    const att = state.attachments.get(state.draft.job.job_id) || {};
    try {
      const res = await api("POST", "/checks", { draft_id: state.draft.job.job_id, sow_markdown: state.markdown,
        verify, upstream: att.upstream || null });
      clearTimeout(state.checkTimer);
      pollJob(res.job_id, "checkTimer", (job) => {
        if (job.status === "completed") { $("check").disabled = false; toolbarStatus(""); renderCheck(job.check || {}); }
        if (job.status === "failed") { $("check").disabled = false; toolbarStatus(`The check failed: ${job.error || "unknown error"}`, true); }
      });
      if (verify) refreshQuota();
    } catch (err) { $("check").disabled = false; toolbarStatus(`Not checked: ${err.message}`, true); }
  });

  function renderCheck(result) {
    const rules = result.rules || [];
    const issues = result.issues || [];
    const s = result.summary || {};
    $("check-panel").hidden = false;
    $("check-summary").textContent = `${fmtNum(s.passed)} of ${fmtNum(s.total)} rules passed · ${issues.length} issue(s)`;
    const requiredFailed = rules.filter((r) => r.severity === "required" && r.status === "fail");
    const banner = $("check-banner");
    banner.hidden = false;
    banner.className = `banner ${requiredFailed.length ? "bad" : "good"}`;
    banner.textContent = requiredFailed.length
      ? `${requiredFailed.length} required rule(s) failed: fix them before sending the SOW. ${result.can_export === false ? "Export is blocked." : "You can still export the draft."}`
      : "All required rules passed.";
    const review = result.ai_review;
    const reviewNote = review && review.requested
      ? el("p", { class: "hint" }, review.status === "ok" ? `AI review: ${review.issues_added} issue(s) added (${review.model_id}).`
        : `AI review ${review.status.replace("_", " ")}: ${review.detail || "no details"}`)
      : el("p", { class: "hint" }, "AI review not requested: rules only.");
    fill($("check-issues"), reviewNote, issues.length
      ? el("ul", { class: "gap-list" }, issues.map(issueItem))
      : el("p", { class: "muted" }, "No issues found."));
    fill($("check-rules"), rules.map((r) => el("li", { class: `rule r-${r.status}` },
      el("span", { class: `pill st-${r.status}` }, r.status === "pass" ? "Pass" : r.status === "fail" ? "Fail" : "n/a"),
      el("div", {}, el("b", {}, r.name), el("span", { class: "muted small" }, ` ${r.severity}`),
        r.detail ? el("div", { class: "muted small" }, r.detail) : null))));
    $("check-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function issueItem(issue) {
    const fix = el("button", { type: "button", class: "btn sm ghost" }, "Fix with AI");
    fix.addEventListener("click", () => {
      openEditor();
      $("instruction").value = `Resolve: ${issue.title}. ${issue.suggestion || issue.detail || ""}`.trim();
      $("instruction").dataset.issue = JSON.stringify(issue);
      $("instruction").focus();
    });
    return el("li", { class: issue.severity === "critical" || issue.severity === "high" ? "red" : issue.source === "model" ? "blue" : "" },
      el("div", { class: "issue-head" }, el("span", { class: `pill sev-${issue.severity}` }, SEVERITY[issue.severity] || issue.severity),
        el("b", {}, issue.title), issue.section ? el("span", { class: "muted small" }, issue.section) : null,
        el("span", { class: "muted small" }, issue.source === "model" ? "AI reviewer" : "rule"), fix),
      issue.detail ? el("div", {}, issue.detail) : null,
      issue.suggestion ? el("div", { class: "muted" }, `Suggestion: ${issue.suggestion}`) : null);
  }

  // ---------------------------------------------------------------- edit with AI
  function openEditor() {
    $("edit-panel").hidden = false;
    $("edit-open").setAttribute("aria-expanded", "true");
  }
  $("edit-open").addEventListener("click", () => {
    const open = $("edit-panel").hidden;
    $("edit-panel").hidden = !open;
    $("edit-open").setAttribute("aria-expanded", String(open));
    if (open) $("instruction").focus();
  });
  function editStatus(text, isError = false) {
    $("edit-status").textContent = text;
    $("edit-status").className = `status-line ${isError ? "error" : "muted"}`;
  }
  $("edit-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const instruction = $("instruction").value.trim();
    if (instruction.length < 3) { editStatus("Say what to change in the draft.", true); return; }
    const job = state.draft.job;
    const snapshot = D.parse(state.markdown);
    const { sections_map, document_outline } = D.sectionsMap(snapshot);
    const history = state.chat.get(job.job_id) || [];
    let issue = null;
    try { issue = $("instruction").dataset.issue ? JSON.parse($("instruction").dataset.issue) : null; } catch { issue = null; }
    $("edit-send").disabled = true;
    $("raw").readOnly = true;
    editStatus("The editor is reading the draft (usually under a minute)…");
    try {
      const res = await api("POST", "/edits", { draft_id: job.job_id, instruction, customer_name: job.customer_name,
        template_id: job.template_id, sections_map, document_outline, chat_history: history.slice(-10),
        resolving_issue: issue });
      delete $("instruction").dataset.issue;
      refreshQuota();
      clearTimeout(state.editTimer);
      pollJob(res.job_id, "editTimer", (ej) => {
        if (ACTIVE.has(ej.status)) {
          const calls = (ej.tool_calls || []).length;
          editStatus(`The editor is working${calls ? ` (${calls} tool call${calls > 1 ? "s" : ""})` : ""}…`);
          return;
        }
        $("edit-send").disabled = false;
        $("raw").readOnly = false;
        if (ej.status === "failed") { editStatus(`The editor failed: ${ej.error || "unknown error"}`, true); return; }
        history.push({ role: "user", content: instruction });
        if (ej.assistant_text) history.push({ role: "assistant", content: ej.assistant_text });
        state.chat.set(job.job_id, history);
        $("instruction").value = "";
        showProposals(ej, snapshot);
      });
    } catch (err) {
      $("edit-send").disabled = false;
      $("raw").readOnly = false;
      editStatus(`Not sent: ${err.message}`, true);
    }
  });

  function showProposals(ej, snapshot) {
    const list = ej.proposals || [];
    $("edit-reply").hidden = !ej.assistant_text;
    fill($("edit-reply"), ej.assistant_text ? window.renderMarkdown(ej.assistant_text) : "");
    state.proposals = list.map((p) => ({ p, r: D.resolve(snapshot, p), state: "open" }));
    editStatus(list.length ? `${list.length} proposed change(s). Review and apply the ones you want.`
      : (ej.rejected || []).length ? "The editor could not make a valid change. Rephrase the instruction." : "No changes were proposed.");
    renderProposals();
  }
  function renderProposals() {
    const open = state.proposals.filter((x) => x.state === "open" && x.r.ok);
    $("proposals-head").hidden = !state.proposals.length;
    $("apply-all").disabled = !open.length;
    fill($("proposals"), state.proposals.map((x, i) => proposalItem(x, i)));
  }
  function proposalItem(x, i) {
    const { p, r } = x;
    const where = p.heading || p.after_heading || "";
    const current = r.ok ? r.target : (p.find || "");
    const proposed = r.text || "";
    const actions = el("div", { class: "actions" });
    if (x.state === "open" && r.ok) {
      const apply = el("button", { type: "button", class: "btn sm primary" }, "Apply");
      apply.addEventListener("click", () => applyProposal(i));
      actions.append(apply);
    }
    if (x.state === "open") {
      const dismiss = el("button", { type: "button", class: "btn sm ghost" }, "Dismiss");
      dismiss.addEventListener("click", () => { x.state = "dismissed"; renderProposals(); });
      actions.append(dismiss);
    }
    if (!r.ok && proposed) {
      const copy = el("button", { type: "button", class: "btn sm ghost" }, "Copy text");
      copy.addEventListener("click", () => navigator.clipboard && navigator.clipboard.writeText(proposed).then(() => { copy.textContent = "Copied"; }));
      actions.append(copy);
    }
    const stateNote = { applied: "Applied", dismissed: "Dismissed", stale: "No longer matches the draft" }[x.state];
    return el("li", { class: `proposal p-${x.state}` },
      el("div", { class: "issue-head" }, el("span", { class: "pill source" }, r.label), where ? el("b", {}, where) : null,
        !r.ok ? el("span", { class: "pill sev-medium" }, "Suggestion only") : null,
        stateNote ? el("span", { class: "muted small" }, stateNote) : null),
      p.rationale ? el("p", { class: "muted small" }, p.rationale) : null,
      !r.ok ? el("p", { class: "small" }, r.reason) : null,
      current && r.op !== "insert_after" ? el("div", { class: "diff" }, el("span", { class: "diff-label" }, r.op === "delete" ? "Remove" : "Current"),
        el("pre", { class: "diff-old" }, current.length > 1200 ? `${current.slice(0, 1200)}…` : current)) : null,
      proposed ? el("div", { class: "diff" }, el("span", { class: "diff-label" }, r.op === "insert_after" ? "Insert" : "Proposed"),
        el("pre", { class: "diff-new" }, proposed)) : null,
      actions);
  }
  function applyProposal(i, quiet = false) {
    const x = state.proposals[i];
    const next = D.apply(state.markdown, x.r);
    if (next === null) { x.state = "stale"; }
    else { x.state = "applied"; setMarkdown(next, true); }
    if (!quiet) renderProposals();
    return next !== null;
  }
  $("apply-all").addEventListener("click", () => {
    let ok = 0;
    state.proposals.forEach((x, i) => { if (x.state === "open" && x.r.ok && applyProposal(i, true)) ok += 1; });
    renderProposals();
    editStatus(`${ok} change(s) applied.`);
  });

  // ---------------------------------------------------------------- download .docx
  $("download").addEventListener("click", async () => {
    const job = state.draft.job;
    if (!state.markdown.trim()) return;
    $("download").disabled = true;
    toolbarStatus("Building the .docx…");
    const att = state.attachments.get(job.job_id);
    try {
      const res = await api("POST", "/exports/docx", { draft_id: job.job_id, customer_name: job.customer_name,
        opportunity_id: job.opportunity_id || null, sow_markdown: state.markdown,
        architecture_images: att ? att.images : [] });
      toolbarStatus(`${res.filename} is downloading.${att || !(job.inputs || {}).architecture_images ? "" : " Architecture images are only included when the draft was generated in this tab."}`);
      window.location.assign(res.download_url);     // a navigation (Content-Disposition: attachment), not a fetch
    } catch (err) { toolbarStatus(`Download failed: ${err.message}`, true); }
    $("download").disabled = false;
  });

  // ---------------------------------------------------------------- start
  refreshForm();
  loadTemplates().then(loadHistory);
  refreshQuota();
  const last = sessionStorage.getItem("sowDraftId");
  if (last) openDraft(last);
})();
