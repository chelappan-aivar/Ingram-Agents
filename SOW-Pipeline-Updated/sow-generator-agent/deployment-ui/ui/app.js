// Ingram SOW generator console for the SowGenAgent API (API Gateway -> invoker Lambda -> AgentCore runtime).
//   POST {api}/sessions             -> {session_id}
//   POST {api}/generations/stream   -> SSE: session, chunk {text}, usage, done | error {message}
//   POST {api}/edits/stream         -> SSE: text {chunk}, tool_call(_complete), tool_result, *_proposal, done | error
//   POST {api}/checks               -> {rules, issues, summary, can_export, ai_review}
//   POST {api}/exports/docx         -> {download_url, filename, expires_in, session_id}
// The API keeps no draft history, so drafts are saved in this browser (localStorage).
// All agent output is rendered as text nodes (no innerHTML); markdown goes through the safe renderer (markdown.js).
(() => {
  const cfg = window.SOW_CONFIG || {};
  const D = window.SowDraft;
  const MAX_BODY_CHARS = 4.4 * 1024 * 1024;      // keep request bodies well under the 6 MB Lambda payload limit
  const MAX_IMAGE_BYTES = 3.75 * 1024 * 1024;    // Bedrock's per-image limit
  const MAX_DETAILS = 100000;
  const MAX_DRAFTS = 20;
  const TRANSCRIPT_EXT = ["pdf", "docx", "txt", "md", "csv", "xlsx"];
  const IMAGE_EXT = ["png", "jpg", "jpeg"];
  const PROPOSAL_TYPES = new Set(["edit_proposal", "insert_proposal", "insert_after_paragraph_proposal", "delete_proposal"]);
  const STATUS = { running: "Working", completed: "Ready", failed: "Failed", interrupted: "Interrupted" };
  const STATUS_CLASS = { running: "s-queued", completed: "s-completed", failed: "s-failed", interrupted: "s-partial" };
  const STEPS = [["reading_inputs", "Read inputs"], ["drafting", "Draft the SOW"], ["done", "Ready"]];
  const SEVERITY = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
  const STORE_KEY = "sowDrafts.v1";

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
  const nowIso = () => new Date().toISOString();
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");
  const fmtSize = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  const fmtNum = (n) => (typeof n === "number" ? n.toLocaleString() : String(n ?? "–"));
  const ext = (name) => (String(name).split(".").pop() || "").toLowerCase();
  const statusPill = (s) => el("span", { class: `pill ${STATUS_CLASS[s] || ""}` }, STATUS[s] || s);
  const templates = Array.isArray(window.SOW_TEMPLATES) ? window.SOW_TEMPLATES : [];
  const templateLabel = (id) => (templates.find((t) => t.id === id) || {}).label || id || "";

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
  // An anonymous, per-browser id: the backend uses it only to group this browser's files in S3 (attribution, not auth).
  const userId = (() => {
    let id = localStorage.getItem("sowUserId");
    if (!/^web-[0-9a-f]{24}$/.test(id || "")) {
      const bytes = crypto.getRandomValues(new Uint8Array(12));
      id = `web-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
      localStorage.setItem("sowUserId", id);
    }
    return id;
  })();
  async function request(path, body) {
    if (!cfg.apiUrl) throw new Error("The API URL is not configured (config.js).");
    const resp = await fetch(`${cfg.apiUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": userId },
      body: JSON.stringify(body || {}),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      const msg = data.error || data.message || `HTTP ${resp.status}`;
      throw new Error(resp.status === 429 ? `${msg} The service is busy; try again in a minute.` : msg);
    }
    return resp;
  }
  const api = async (path, body) => (await request(path, body)).json();

  // POST and read a server-sent event stream; onEvent(obj) for every `data:` JSON line.
  async function stream(path, body, onEvent) {
    const resp = await request(path, body);
    if (!resp.body) throw new Error("This browser cannot read streamed responses.");
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    const flush = (block) => {
      const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
      if (!data) return;
      let evt;
      try { evt = JSON.parse(data); } catch { return; }
      onEvent(evt);
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true }).replace(/\r\n?/g, "\n");
      let at;
      while ((at = buf.indexOf("\n\n")) >= 0) { flush(buf.slice(0, at)); buf = buf.slice(at + 2); }
    }
    buf += decoder.decode();
    if (buf.trim()) flush(buf);
  }
  const readDataUrl = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error(`could not read ${file.name}`));
    reader.readAsDataURL(file);
  });

  // ---------------------------------------------------------------- local draft store
  // draft: {id (= session id), customer_name, template_id, opportunity_id, created_at, finished_at, status, error,
  //         markdown, dirty, usage, inputs: {transcripts, images}, log: [{at, stage, level, message}]}
  function loadDrafts() {
    try {
      const list = JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
      return Array.isArray(list) ? list.filter((d) => d && typeof d.id === "string") : [];
    } catch { return []; }
  }
  function saveDrafts() {
    let list = state.drafts.slice(0, MAX_DRAFTS);
    for (;;) {
      try { localStorage.setItem(STORE_KEY, JSON.stringify(list)); return; }
      catch (err) {                                   // storage full: drop the oldest drafts
        if (list.length <= 1) { console.warn("could not save the drafts", err); return; }
        list = list.slice(0, -1);
        state.drafts = state.drafts.slice(0, list.length);
      }
    }
  }
  const draftById = (id) => state.drafts.find((d) => d.id === id) || null;
  function log(draft, stage, message, level = "info") {
    draft.log = (draft.log || []).concat({ at: nowIso(), stage, level, message }).slice(-50);
  }

  // ---------------------------------------------------------------- state
  const state = {
    drafts: loadDrafts(),
    current: null,            // the open draft
    transcripts: [],          // [{name, size, dataUrl}]
    images: [],               // [{name, size, dataUrl}]
    generating: new Set(),    // draft ids streaming in this tab
    proposals: [],            // [{p, r, state: "open" | "applied" | "dismissed" | "stale"}]
    chat: new Map(),          // draft id -> [{role, content}]
    attachments: new Map(),   // draft id -> {images: [{filename, content_b64}], upstream}  (this tab only)
  };
  // A generation only streams into the tab that started it; one left "running" by a closed tab is interrupted.
  for (const d of state.drafts) {
    if (d.status === "running") {
      d.status = "interrupted";
      d.error = "The page was closed or reloaded while the draft was being written. The partial draft is kept.";
    }
  }
  saveDrafts();

  // ---------------------------------------------------------------- templates (static copies, see scripts/sync_templates.py)
  function loadTemplates() {
    const select = $("template");
    fill(select, el("option", { value: "" }, templates.length ? "Choose a template" : "No templates"),
      templates.map((t) => el("option", { value: t.id }, t.label || t.id)));
    $("template-status").textContent = templates.length ? "" : "No templates are available (templates.js).";
  }
  $("template").addEventListener("change", refreshForm);
  $("template-view").addEventListener("click", async () => {
    const t = templates.find((x) => x.id === $("template").value);
    if (!t) return;
    $("template-title").textContent = t.label;
    fill($("template-body"), el("div", { class: "loading" }, "Loading the template…"));
    showTemplatePreview(true);
    try {
      const resp = await fetch(t.path);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const markdown = await resp.text();
      // The template wraps the sample SOW in <sample> and its rules in <requirements>; show them as text.
      fill($("template-body"), window.renderMarkdown(markdown.replace(/<\/?(sample|requirements)>/gi, ""), { lineBreaks: true }));
    } catch (err) { fill($("template-body"), el("p", { class: "error" }, `Could not load the template: ${err.message}`)); }
  });
  $("template-close").addEventListener("click", () => showTemplatePreview(false));

  // ---------------------------------------------------------------- samples (static copies, see scripts/sync_samples.py)
  const samples = Array.isArray(window.SOW_SAMPLES) ? window.SOW_SAMPLES : [];
  async function fetchOk(path) {
    const resp = await fetch(path);
    if (!resp.ok) throw new Error(`${path.split("/").pop()}: HTTP ${resp.status}`);
    return resp;
  }
  function fileLink(path, name, size) {
    return el("li", {}, el("span", { class: `pill ft-${ext(name)}` }, ext(name).toUpperCase()),
      el("a", { class: "file-name", href: path, target: "_blank", rel: "noopener", title: `Open ${name}` }, name),
      size ? el("span", { class: "muted" }, fmtSize(size)) : null);
  }
  function renderSamples() {
    if (!samples.length) { fill($("samples"), el("li", { class: "empty" }, "No samples are available (samples.js).")); return; }
    fill($("samples"), samples.map((s) => {
      const use = el("button", { type: "button", class: "btn sm primary", title: "Fill the New SOW form with this sample" }, "Use sample");
      use.addEventListener("click", () => useSample(s, use));
      const view = s.example_md ? el("button", { type: "button", class: "btn sm", title: "Show the SOW the agent generated from this sample" }, "View example") : null;
      if (view) view.addEventListener("click", () => viewExample(s));
      const inputs = [
        s.details ? fileLink(s.details, "project-details.txt") : null,
        ...s.files.map((f) => fileLink(f.path, f.name, f.size)),
        s.upstream ? fileLink(s.upstream, "upstream.json") : null,
      ];
      return el("li", { class: "sample" },
        el("div", { class: "sample-head" }, el("b", {}, s.title || s.id), el("span", { class: "muted small" }, templateLabel(s.template_id))),
        s.summary ? el("p", { class: "muted small" }, s.summary) : null,
        el("details", { class: "section" }, el("summary", {}, `Input files (${inputs.filter(Boolean).length})`),
          el("ul", { class: "file-list", "aria-label": `${s.title} input files` }, inputs)),
        el("div", { class: "actions" }, use, view,
          s.example_docx ? el("a", { class: "btn sm ghost", href: s.example_docx, download: s.example_docx.split("/").pop(), title: "Download the example SOW as Word" }, "Example .docx") : null));
    }));
  }
  async function useSample(s, btn) {
    if (state.generating.size) { formStatus("Wait for the current draft to finish before loading a sample.", true); return; }
    btn.disabled = true;
    formStatus(`Loading the ${s.customer_name} sample…`);
    try {
      const [details, upstream, files] = await Promise.all([
        s.details ? fetchOk(s.details).then((r) => r.text()) : "",
        s.upstream ? fetchOk(s.upstream).then((r) => r.text()) : "",
        Promise.all(s.files.map(async (f) => ({ ...f, dataUrl: await readDataUrl(await fetchOk(f.path).then((r) => r.blob())) }))),
      ]);
      $("gen-form").reset();
      $("customer").value = s.customer_name || "";
      $("opportunity").value = s.opportunity_id || "";
      $("template").value = s.template_id || "";
      $("details").value = details.trim();
      $("upstream").value = upstream.trim();
      $("upstream-panel").open = !!upstream;
      state.transcripts = files.filter((f) => f.kind === "transcript").map(({ name, size, dataUrl }) => ({ name, size, dataUrl }));
      state.images = files.filter((f) => f.kind === "image").map(({ name, size, dataUrl }) => ({ name, size, dataUrl }));
      refreshForm();
      formStatus(`${s.customer_name} sample loaded: ${files.length} file(s)${upstream ? " and upstream data" : ""}. Review the form, then Generate SOW.`);
      $("new-title").scrollIntoView({ behavior: "smooth", block: "start" });
      $("customer").focus({ preventScroll: true });
    } catch (err) { formStatus(`Could not load the sample: ${err.message}`, true); }
    btn.disabled = false;
  }
  async function viewExample(s) {
    $("template-title").textContent = `Example SOW: ${s.customer_name}`;
    fill($("template-body"), el("div", { class: "loading" }, "Loading the example…"));
    showTemplatePreview(true);
    try {
      const images = await Promise.all(s.files.filter((f) => f.kind === "image")
        .map(async (f) => ({ filename: f.name, content_b64: await readDataUrl(await fetchOk(f.path).then((r) => r.blob())) })));
      const markdown = await fetchOk(s.example_md).then((r) => r.text());
      const resolve = (target) => {
        const m = /^arch:(\d+)$/i.exec(target.trim());
        const img = m ? images[Number(m[1]) - 1] : images.find((i) => i.filename === target.trim());
        return img ? img.content_b64 : null;
      };
      fill($("template-body"),
        el("p", { class: "hint" }, "Produced by the agent from this sample's inputs. Each run differs. ",
          s.example_docx ? el("a", { href: s.example_docx, download: s.example_docx.split("/").pop() }, "Download the .docx") : null),
        window.renderMarkdown(markdown, { lineBreaks: true, resolveImage: resolve }));
    } catch (err) { fill($("template-body"), el("p", { class: "error" }, `Could not load the example: ${err.message}`)); }
  }
  function showTemplatePreview(on) {
    $("template-preview").hidden = !on;
    $("draft").hidden = on || !state.current;
    $("draft-empty").hidden = on || !!state.current;
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
    let value;
    try { value = JSON.parse(raw); } catch (err) { return { ok: false, error: `Upstream data is not valid JSON: ${err.message}` }; }
    if (!value || typeof value !== "object" || Array.isArray(value)) return { ok: false, error: "Upstream data must be a JSON object." };
    const meta = value.meta || {};
    const intake = value.intake || {};
    if (!meta.opportunity_id || !/^[^/]+\/[^/]+$/.test(meta.template_id || "")) {
      return { ok: false, error: "Upstream data needs meta.opportunity_id and meta.template_id (e.g. \"aiml/ai-ml\")." };
    }
    if (!String(intake.customer_name || "").trim()) return { ok: false, error: "Upstream data needs intake.customer_name." };
    return { ok: true, value };
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
    let sessionId;
    try { ({ session_id: sessionId } = await api("/sessions")); }
    catch (err) { formStatus(`Not started: ${err.message}`, true); refreshForm(); return; }
    const body = {
      session_id: sessionId,
      customer_name: $("customer").value.trim(),
      opportunity_id: $("opportunity").value.trim() || null,
      template_id: $("template").value,
      project_details: $("details").value,
      transcript_files: state.transcripts.map((f) => ({ name: f.name, content_b64: f.dataUrl })),
      architecture_image_b64s: images.map((i) => i.content_b64),
      arch_image_filenames: images.map((i) => i.filename),
      upstream: up.value,
    };
    const draft = {
      id: sessionId, customer_name: body.customer_name, template_id: body.template_id, opportunity_id: body.opportunity_id,
      created_at: nowIso(), status: "running", stage: "reading_inputs", markdown: "", dirty: false,
      inputs: { transcripts: state.transcripts.length, images: images.length }, log: [],
    };
    log(draft, "queued", `Draft started (${templateLabel(draft.template_id)})`);
    state.drafts.unshift(draft);
    saveDrafts();
    state.attachments.set(draft.id, { images, upstream: up.value });
    formStatus("Started. The draft appears on the right as it is written. Keep this tab open.");
    openDraft(draft.id);
    renderHistory();
    generate(draft, body).finally(refreshForm);
  });

  async function generate(draft, body) {
    state.generating.add(draft.id);
    const parts = [];
    let renderTimer = null;
    const scheduleRender = () => {
      if (renderTimer) return;
      renderTimer = setTimeout(() => { renderTimer = null; draft.markdown = parts.join(""); if (state.current === draft) renderDraft(); }, 400);
    };
    let finished = false;
    try {
      await stream("/generations/stream", body, (evt) => {
        if (evt.type === "session") log(draft, "reading_inputs", "Reading the inputs and the template");
        else if (evt.type === "chunk") {
          if (draft.stage !== "drafting") { draft.stage = "drafting"; log(draft, "drafting", "Writing the draft"); }
          parts.push(String(evt.text || ""));
          scheduleRender();
        } else if (evt.type === "usage") {
          draft.usage = evt.usage || {};
        } else if (evt.type === "done") {
          finished = true;
          draft.status = "completed";
        } else if (evt.type === "error") {
          finished = true;
          draft.status = "failed";
          draft.error = String(evt.message || "The draft failed.");
        }
      });
      if (!finished) { draft.status = "failed"; draft.error = "The connection closed before the draft was finished."; }
    } catch (err) {
      draft.status = "failed";
      draft.error = err.message;
    }
    clearTimeout(renderTimer);
    state.generating.delete(draft.id);
    draft.markdown = parts.join("").trim();
    draft.stage = "done";
    draft.finished_at = nowIso();
    const secs = Math.round((Date.parse(draft.finished_at) - Date.parse(draft.created_at)) / 1000);
    if (draft.status === "completed" && !draft.markdown) { draft.status = "failed"; draft.error = "The model returned no draft."; }
    if (draft.status === "completed") {
      const u = draft.usage || {};
      log(draft, "done", `Draft ready: ${fmtNum(draft.markdown.length)} characters (${fmtNum(u.inputTokens || 0)} in / ${fmtNum(u.outputTokens || 0)} out tokens, ${secs} s)`);
    } else {
      log(draft, "failed", draft.error, "error");
    }
    saveDrafts();
    renderHistory();
    if (state.current === draft) { renderDraft(); renderPreview(); }
    if (state.current === draft && draft.status === "completed") formStatus("");
  }

  // ---------------------------------------------------------------- history
  function renderHistory() {
    const list = $("history");
    fill(list, state.drafts.length ? state.drafts.map(historyItem) : [el("li", { class: "empty" }, "No drafts yet.")]);
  }
  function historyItem(d) {
    const btn = el("button", { type: "button", class: "card-item", "data-job": d.id,
      "aria-current": state.current && state.current.id === d.id ? "true" : "false" },
      el("div", { class: "card-main" },
        el("span", { class: "card-title" }, d.customer_name || "Untitled"),
        el("span", { class: "card-meta" }, statusPill(d.status), el("span", { class: "muted" }, templateLabel(d.template_id)),
          el("span", { class: "muted" }, fmtTime(d.created_at)), d.opportunity_id ? el("span", { class: "muted" }, d.opportunity_id) : null)));
    btn.addEventListener("click", () => openDraft(d.id));
    const remove = el("button", { type: "button", class: "btn sm ghost", "aria-label": `Remove the ${d.customer_name || "untitled"} draft from this browser` }, "Remove");
    remove.disabled = state.generating.has(d.id);
    remove.addEventListener("click", () => {
      state.drafts = state.drafts.filter((x) => x.id !== d.id);
      saveDrafts();
      if (state.current && state.current.id === d.id) {
        state.current = null;
        sessionStorage.removeItem("sowDraftId");
        $("draft").hidden = true;
        $("draft-empty").hidden = false;
      }
      renderHistory();
    });
    return el("li", { class: "inline-row" }, btn, remove);
  }

  // ---------------------------------------------------------------- one draft
  function resetPanels() {
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
  function openDraft(id) {
    const draft = draftById(id);
    if (!draft) { sessionStorage.removeItem("sowDraftId"); return; }
    if (state.current !== draft) resetPanels();
    state.current = draft;
    sessionStorage.setItem("sowDraftId", id);
    showTemplatePreview(false);
    renderDraft();
    renderPreview();
    document.querySelectorAll("#history .card-item").forEach((b) => b.setAttribute("aria-current", String(b.dataset.job === id)));
  }

  function resolveImage(target) {
    const att = state.current && state.attachments.get(state.current.id);
    if (!att) return null;
    const m = /^arch:(\d+)$/i.exec(target.trim());
    const img = m ? att.images[Number(m[1]) - 1] : att.images.find((i) => i.filename === target.trim());
    return img ? img.content_b64 : null;
  }
  function renderPreview() {
    const md = state.current ? state.current.markdown : "";
    fill($("preview"), md ? window.renderMarkdown(md, { lineBreaks: true, resolveImage })
      : el("div", { class: "loading" }, "The draft appears here as the agent writes it."));
  }

  function renderDraft() {
    const d = state.current;
    if (!d) return;
    $("draft-empty").hidden = true;
    $("draft").hidden = false;
    $("draft-title").textContent = d.customer_name || "Untitled";
    const inputs = d.inputs || {};
    const chars = (d.markdown || "").length;
    $("draft-sub").textContent = [templateLabel(d.template_id), d.opportunity_id, `started ${fmtTime(d.created_at)}`,
      inputs.transcripts ? `${inputs.transcripts} transcript(s)` : "",
      inputs.images ? `${inputs.images} image(s)` : "",
      chars ? `${fmtNum(chars)} characters` : ""].filter(Boolean).join(" · ");
    fill($("status-pill"), statusPill(d.status));

    const active = d.status === "running";
    $("running").hidden = !active;
    if (active) {
      const pct = Math.min(95, Math.round(5 + chars / 400));     // rough estimate: a full SOW is ~35k characters
      $("progress-bar").style.width = `${pct}%`;
      $("progress").setAttribute("aria-valuenow", String(pct));
      const order = STEPS.map(([k]) => k);
      const at = Math.max(0, order.indexOf(d.stage));
      fill($("steps"), STEPS.map(([k, label], i) => el("li", { class: i < at ? "done" : i === at ? "active" : "" },
        k === "drafting" && chars ? `${label} (${fmtNum(chars)} characters)` : label)));
    }
    const failed = d.status === "failed" || d.status === "interrupted";
    $("draft-error").hidden = !(failed && d.error);
    $("draft-error").textContent = failed ? `${d.status === "failed" ? "The draft failed" : "Interrupted"}: ${d.error || "unknown error"}` : "";
    $("toolbar").hidden = active || !d.markdown;
    $("dirty").hidden = !d.dirty;
    if ($("raw-wrap").hidden) renderPreview();
    else if (!d.dirty) $("raw").value = d.markdown;
    renderLog(d.log);
  }
  function renderLog(entries) {
    fill($("log"), (entries || []).slice().reverse().map((l) =>
      el("li", { class: `audit-item lvl-${l.level}` },
        el("div", { class: "audit-head" }, el("span", { class: "stage" }, l.stage), el("span", {}, l.message),
          el("span", { class: "muted" }, new Date(l.at).toLocaleTimeString())))));
  }
  function setMarkdown(md) {
    const d = state.current;
    d.markdown = md;
    d.dirty = true;
    saveDrafts();
    $("dirty").hidden = false;
    if (!$("raw-wrap").hidden) $("raw").value = md;
    renderPreview();
  }

  // raw markdown editing
  function setRaw(on) {
    $("raw-wrap").hidden = !on;
    $("preview").hidden = on;
    $("raw-toggle").setAttribute("aria-pressed", String(on));
    $("raw-toggle").textContent = on ? "Show preview" : "Edit markdown";
    if (on) { $("raw").value = state.current ? state.current.markdown : ""; $("raw").focus(); } else renderPreview();
  }
  $("raw-toggle").addEventListener("click", () => setRaw($("raw-wrap").hidden));
  let rawSave = null;
  $("raw").addEventListener("input", () => {
    if (!state.current) return;
    state.current.markdown = $("raw").value;
    state.current.dirty = true;
    $("dirty").hidden = false;
    clearTimeout(rawSave);
    rawSave = setTimeout(saveDrafts, 500);
  });

  function toolbarStatus(text, isError = false) {
    $("toolbar-status").textContent = text;
    $("toolbar-status").className = `status-line ${isError ? "error" : "muted"}`;
  }

  // ---------------------------------------------------------------- check draft
  $("check").addEventListener("click", async () => {
    const d = state.current;
    if (!d || !d.markdown.trim()) return;
    const verify = $("check-ai").checked;
    $("check").disabled = true;
    toolbarStatus(verify ? "Checking the rules and asking the AI reviewer (about a minute)…" : "Checking the rules…");
    const att = state.attachments.get(d.id) || {};
    try {
      const res = await api("/checks", { sow_markdown: d.markdown, verify, upstream: att.upstream || null });
      toolbarStatus("");
      const s = res.summary || {};
      log(d, "check", `${fmtNum(s.passed)}/${fmtNum(s.total)} rules passed, ${(res.issues || []).length} issue(s)`
        + (res.ai_review && res.ai_review.requested ? `, AI review ${res.ai_review.status}` : ""));
      saveDrafts();
      if (state.current === d) { renderLog(d.log); renderCheck(res); }
    } catch (err) { toolbarStatus(`Not checked: ${err.message}`, true); }
    $("check").disabled = false;
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
        : `AI review ${String(review.status).replace("_", " ")}: ${review.detail || "no details"}`)
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

  // ---------------------------------------------------------------- edit with AI (streamed)
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
    const d = state.current;
    const instruction = $("instruction").value.trim();
    if (instruction.length < 3) { editStatus("Say what to change in the draft.", true); return; }
    const snapshot = D.parse(d.markdown);
    const { sections_map, document_outline } = D.sectionsMap(snapshot);
    const history = state.chat.get(d.id) || [];
    let issue = null;
    try { issue = $("instruction").dataset.issue ? JSON.parse($("instruction").dataset.issue) : null; } catch { issue = null; }
    $("edit-send").disabled = true;
    $("raw").readOnly = true;
    editStatus("The editor is reading the draft (usually under a minute)…");
    const result = { text: [], proposals: [], rejected: [], calls: 0, error: null, done: false };
    try {
      await stream("/edits/stream", { instruction, customer_name: d.customer_name, template_id: d.template_id,
        sections_map, document_outline, chat_history: history.slice(-10), resolving_issue: issue }, (evt) => {
        if (evt.type === "text") result.text.push(String(evt.chunk || ""));
        else if (evt.type === "tool_call_complete") {
          result.calls += 1;
          editStatus(`The editor is working (${result.calls} tool call${result.calls > 1 ? "s" : ""})…`);
        } else if (PROPOSAL_TYPES.has(evt.type)) result.proposals.push(evt);
        else if (evt.type === "tool_result" && evt.tool === "edit") result.rejected.push(String(evt.result || ""));
        else if (evt.type === "done") result.done = true;
        else if (evt.type === "error") result.error = String(evt.message || "The editor failed.");
      });
      delete $("instruction").dataset.issue;
      if ((result.error || !result.done) && !result.proposals.length) {
        editStatus(`The editor failed: ${result.error || "the connection closed early"}`, true);
      } else {
        const assistant = result.text.join("").trim();
        history.push({ role: "user", content: instruction });
        if (assistant) history.push({ role: "assistant", content: assistant.slice(0, 4000) });
        state.chat.set(d.id, history);
        log(d, "edit", `${result.proposals.length} change proposal(s) after ${result.calls} tool call(s): ${instruction.slice(0, 120)}`);
        saveDrafts();
        renderLog(d.log);
        $("instruction").value = "";
        if (state.current === d) showProposals(assistant, result, snapshot);
      }
    } catch (err) {
      editStatus(`Not sent: ${err.message}`, true);
    }
    $("edit-send").disabled = false;
    $("raw").readOnly = false;
  });

  function showProposals(assistant, result, snapshot) {
    const list = result.proposals;
    $("edit-reply").hidden = !assistant;
    fill($("edit-reply"), assistant ? window.renderMarkdown(assistant) : "");
    state.proposals = list.map((p) => ({ p, r: D.resolve(snapshot, p), state: "open" }));
    editStatus(list.length ? `${list.length} proposed change(s). Review and apply the ones you want.`
      : result.rejected.length ? "The editor could not make a valid change. Rephrase the instruction." : "No changes were proposed.");
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
    const next = D.apply(state.current.markdown, x.r);
    if (next === null) { x.state = "stale"; }
    else { x.state = "applied"; setMarkdown(next); }
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
    const d = state.current;
    if (!d || !d.markdown.trim()) return;
    $("download").disabled = true;
    toolbarStatus("Building the .docx…");
    const att = state.attachments.get(d.id);
    try {
      const res = await api("/exports/docx", { session_id: d.id, customer_name: d.customer_name,
        opportunity_id: d.opportunity_id || null, sow_markdown: d.markdown, persist_output: true,
        architecture_images: att ? att.images : [] });
      log(d, "export", `Exported ${res.filename}`);
      saveDrafts();
      renderLog(d.log);
      toolbarStatus(`${res.filename} is downloading.${att || !(d.inputs || {}).images ? "" : " Architecture images are only included when the draft was generated in this tab."}`);
      window.location.assign(res.download_url);     // a navigation (Content-Disposition: attachment), not a fetch
    } catch (err) { toolbarStatus(`Download failed: ${err.message}`, true); }
    $("download").disabled = false;
  });

  // Warn before closing the tab while a draft is still streaming in.
  window.addEventListener("beforeunload", (e) => { if (state.generating.size) { e.preventDefault(); e.returnValue = ""; } });

  // ---------------------------------------------------------------- start
  loadTemplates();
  renderSamples();
  refreshForm();
  renderHistory();
  const last = sessionStorage.getItem("sowDraftId");
  if (last) openDraft(last);
})();
