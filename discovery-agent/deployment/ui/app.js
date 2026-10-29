// Ingram Discovery agent console: engagement documents in -> discovery report out.
// Upload: POST /engagements/{name}/upload-urls -> PUT each file straight to S3 -> POST /runs -> poll GET /runs/{id}.
// All API output is rendered as text nodes (no innerHTML).
(() => {
  const cfg = window.DISCOVERY_CONFIG || {};
  const POLL_MS = 5000;
  const MAX_FILES = 200;
  const MAX_FILE_BYTES = 25 * 1024 * 1024;
  const MAX_INSTRUCTIONS = 4000;
  const FILE_EXT = ["txt", "md", "eml", "docx", "pdf"];
  const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._ -]{0,100}$/;
  const ACTIVE = new Set(["starting", "running"]);
  const STATUS = { starting: "Starting", running: "Working", awaiting_clarification: "Needs your input",
    succeeded: "Ready", failed: "Failed" };
  const FILES = [["README.md", "Report (.md)"], ["discovery_report.docx", "Report (.docx)"],
    ["discovery.json", "discovery.json"], ["narrative.json", "narrative.json"]];

  const $ = (id) => document.getElementById(id);
  const { el, fill } = window.DiscoveryDom;
  let picked = [];             // File objects to upload
  let current = null;          // {engagement, run_id}
  let lastDoc = null;          // last status.json seen for `current`
  let pollTimer = null;
  let questionsFor = "";       // run id whose questions are rendered
  let resultsFor = "";         // run id whose download buttons are rendered
  let overviewFor = "";        // "<run id>:<state>" the overview was loaded for
  let reportFor = "";          // run id whose report is rendered
  let answeredFor = "";        // run id whose answers were sent (status.json says "awaiting" until phase 2 starts)
  const activity = [];         // client-side activity lines for the current run

  // ---------------------------------------------------------------- DOM helpers
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
  const statusPill = (s) => el("span", { class: `pill s-${s}` }, STATUS[s] || s || "Unknown");
  const qs = (engagement) => `?engagement=${encodeURIComponent(engagement)}`;

  // ---------------------------------------------------------------- theme
  const THEMES = [
    ["light", "Light theme", ["M12 4V2M12 22v-2M4 12H2M22 12h-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"]],
    ["dark", "Dark theme", ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"]],
    ["system", "System theme", ["M3 4h18v12H3zM8 20h8M12 16v4"]],
  ];
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(choice) {
    localStorage.setItem("discoveryTheme", choice);
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
  systemDark.addEventListener("change", () => { if (localStorage.getItem("discoveryTheme") === "system") applyTheme("system"); });
  applyTheme(localStorage.getItem("discoveryTheme") || "dark");

  // ---------------------------------------------------------------- API (open: no sign-in)
  async function api(method, path, body) {
    if (!cfg.apiUrl) throw new Error("The API URL is not configured (config.js).");
    const headers = body ? { "Content-Type": "application/json" } : {};
    const resp = await fetch(`${cfg.apiUrl}${path}`, {
      method, headers, body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error(data.error || data.message || `HTTP ${resp.status}`);
      err.status = resp.status;
      throw err;
    }
    return data;
  }
  function showQuota() {
    api("GET", "/session").then((res) => {
      $("quota").textContent = `${res.runs_today} / ${res.runs_per_day} runs today`;
    }).catch(() => {});
  }

  // ---------------------------------------------------------------- new run form
  function formStatus(text, isError = false) {
    $("form-status").textContent = text;
    $("form-status").className = `status-line ${isError ? "error" : "muted"}`;
  }
  function refreshForm() {
    const name = $("engagement").value.trim();
    const n = $("instructions").value.length;
    $("instructions-count").textContent = `${n.toLocaleString()} / ${MAX_INSTRUCTIONS.toLocaleString()}`;
    $("start").disabled = !(picked.length && NAME_RE.test(name));
    $("clear").disabled = !(picked.length || name || n);
    fill($("file-list"), picked.map((f, i) => {
      const remove = el("button", { type: "button", class: "btn sm ghost", "aria-label": `Remove ${f.name}` }, "Remove");
      remove.addEventListener("click", () => { picked.splice(i, 1); refreshForm(); });
      return el("li", {}, el("span", { class: "file-name" }, f.name), el("span", { class: "muted" }, fmtSize(f.size)), remove);
    }));
  }
  function addFiles(list) {
    const problems = [];
    for (const f of list) {
      const ext = f.name.includes(".") ? f.name.split(".").pop().toLowerCase() : "";
      if (ext && !FILE_EXT.includes(ext)) problems.push(`${f.name}: unsupported type`);
      else if (f.name.startsWith(".") || f.name.startsWith("_")) problems.push(`${f.name}: name must not start with . or _`);
      else if (!f.size) problems.push(`${f.name}: empty file`);
      else if (f.size > MAX_FILE_BYTES) problems.push(`${f.name}: larger than 25 MB`);
      else if (picked.length >= MAX_FILES) problems.push(`${f.name}: more than ${MAX_FILES} files`);
      else if (!picked.some((x) => x.name === f.name)) picked.push(f);
    }
    formStatus(problems.length ? `Skipped ${problems.join("; ")}.` : "", problems.length > 0);
    refreshForm();
  }
  $("file").addEventListener("change", () => { addFiles($("file").files); $("file").value = ""; });
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
  $("engagement").addEventListener("input", refreshForm);
  $("instructions").addEventListener("input", refreshForm);
  $("clear").addEventListener("click", () => {
    picked = []; $("engagement").value = ""; $("instructions").value = ""; $("clarify").checked = false;
    formStatus(""); refreshForm();
  });

  $("run-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const engagement = $("engagement").value.trim();
    if (!NAME_RE.test(engagement)) { formStatus("Engagement name: letters, digits, spaces and . _ - only.", true); return; }
    $("start").disabled = true;
    try {
      formStatus("Requesting upload links…");
      const up = await api("POST", `/engagements/${encodeURIComponent(engagement)}/upload-urls`,
        { files: picked.map((f) => ({ name: f.name, size: f.size })) });
      let i = 0;
      for (const f of picked) {
        i += 1;
        formStatus(`Uploading ${i} of ${picked.length}: ${f.name}`);
        const put = await fetch(up.urls[f.name], { method: "PUT", headers: up.required_headers || {}, body: f });
        if (!put.ok) throw new Error(`upload of ${f.name} failed (HTTP ${put.status})`);
      }
      formStatus("Starting the run…");
      const body = { engagement, clarify: $("clarify").checked };
      const instructions = $("instructions").value.trim();
      if (instructions) body.instructions = instructions;
      const res = await api("POST", "/runs", body);
      formStatus("Started. Progress appears on the right; you can leave this page and come back.");
      picked = [];
      $("instructions").value = "";
      refreshForm();
      await openRun(engagement, res.run_id);
      showQuota();
    } catch (err) {
      formStatus(`Not started: ${err.message}`, true);
      refreshForm();
    }
  });


  // ---------------------------------------------------------------- one run
  function logLine(text, level = "info") {
    activity.push({ at: new Date(), text, level });
    fill($("log"), activity.slice().reverse().map((l) =>
      el("li", { class: `audit-item lvl-${l.level}` }, el("span", {}, l.text), el("span", { class: "muted" }, l.at.toLocaleTimeString()))));
  }

  async function openRun(engagement, runId) {
    clearTimeout(pollTimer);
    const switching = !current || current.run_id !== runId || current.engagement !== engagement;
    if (switching) {
      current = { engagement, run_id: runId };
      lastDoc = null; questionsFor = ""; resultsFor = ""; overviewFor = ""; reportFor = ""; activity.length = 0;
      window.DiscoveryOverview.render($("overview-head"), $("overview-detail"), null);
      window.DiscoveryReport.render($("report"), null);
      fill($("log")); fill($("questions")); fill($("downloads"));
      $("answers-status").textContent = "";
      $("submit-answers").disabled = false;
      sessionStorage.setItem("discoveryRun", JSON.stringify(current));
    }
    try {
      let doc = await api("GET", `/runs/${encodeURIComponent(runId)}${qs(engagement)}`);
      if (!current || current.run_id !== runId) return;   // the user opened another run meanwhile
      if (doc.state === "awaiting_clarification" && answeredFor === runId) doc = { ...doc, state: "running" };
      noteChanges(doc);
      lastDoc = doc;
      renderRun(doc);
      if (ACTIVE.has(doc.state)) pollTimer = setTimeout(() => openRun(engagement, runId), POLL_MS);
    } catch (err) {
      if (err.status === 404) {
        sessionStorage.removeItem("discoveryRun");
        renderMissing(engagement, runId);
        return;
      }
      logLine(`Could not read the status: ${err.message}`, "error");
      pollTimer = setTimeout(() => openRun(engagement, runId), POLL_MS * 2);
    }
  }
  $("run-refresh").addEventListener("click", () => current && openRun(current.engagement, current.run_id));

  function noteChanges(doc) {
    const before = lastDoc || { completed_steps: [] };
    if (!lastDoc || before.state !== doc.state) logLine(`Status: ${STATUS[doc.state] || doc.state}`, doc.state === "failed" ? "error" : "info");
  }

  function renderMissing(engagement, runId) {
    $("result-empty").hidden = true;
    $("result").hidden = false;
    $("run-title").textContent = engagement;
    $("run-sub").textContent = `Run ${runId}`;
    fill($("status-pill"), statusPill("failed"));
    $("run-error").hidden = false;
    $("run-error").textContent = "This run was not found.";
  }

  function renderRun(doc) {
    $("result-empty").hidden = true;
    $("result").hidden = false;
    $("run-title").textContent = current.engagement;
    $("run-sub").textContent = [`Run ${current.run_id}`, doc.updated_at ? `updated ${fmtTime(doc.updated_at)}` : ""].filter(Boolean).join(" · ");
    fill($("status-pill"), statusPill(doc.state));

    $("progress-text").textContent = ACTIVE.has(doc.state)
      ? "The agent is reading the documents and writing the report. This usually takes 10 to 30 minutes; you can leave this page and come back."
      : "";

    $("run-error").hidden = !doc.error;
    $("run-error").textContent = doc.error ? `The run failed: ${doc.error}` : "";
    $("run-instructions").hidden = !doc.instructions;
    $("run-instructions-text").textContent = doc.instructions || "";

    $("clarify-panel").hidden = doc.state !== "awaiting_clarification";
    if (doc.state === "awaiting_clarification" && questionsFor !== current.run_id) loadQuestions();
    $("results").hidden = doc.state !== "succeeded";
    if (doc.state === "succeeded" && resultsFor !== current.run_id) loadResults();
    if (doc.state === "succeeded" && reportFor !== current.run_id) loadReport();
    if (overviewFor !== `${current.run_id}:${doc.state}`) loadOverview(doc.state);
  }

  // ---------------------------------------------------------------- overview + report (shown on the page, not only as files)
  async function loadOverview(state) {
    const run = { ...current };
    overviewFor = `${run.run_id}:${state}`;
    try {
      const data = await api("GET", `/runs/${encodeURIComponent(run.run_id)}/overview${qs(run.engagement)}`);
      if (!current || current.run_id !== run.run_id) return;
      window.DiscoveryOverview.render($("overview-head"), $("overview-detail"), data);
    } catch (err) {
      overviewFor = "";
      logLine(`Could not load the overview: ${err.message}`, "error");
    }
  }
  async function loadReport() {
    const run = { ...current };
    reportFor = run.run_id;
    fill($("report"), el("p", { class: "muted" }, "Loading the report…"));
    try {
      const data = await api("GET", `/runs/${encodeURIComponent(run.run_id)}/report${qs(run.engagement)}`);
      if (!current || current.run_id !== run.run_id) return;
      window.DiscoveryReport.render($("report"), data);
    } catch (err) {
      reportFor = "";
      fill($("report"), el("p", { class: "error" }, `Could not show the report here (${err.message}). Use the downloads above.`));
    }
  }

  // ---------------------------------------------------------------- clarification round
  async function loadQuestions() {
    const run = { ...current };
    questionsFor = run.run_id;
    fill($("questions"), el("li", { class: "muted" }, "Loading the questions…"));
    try {
      const { questions } = await api("GET", `/runs/${encodeURIComponent(run.run_id)}/questions${qs(run.engagement)}`);
      if (!current || current.run_id !== run.run_id) return;
      if (!questions.length) {
        fill($("questions"), el("li", { class: "muted" }, "No open questions. Finishing the report…"));
        submitAnswers({});
        return;
      }
      fill($("questions"), questions.map((q, i) => {
        const id = `answer-${i}`;
        const type = q.type === "conflict" ? "conflict" : "gap";
        return el("li", { class: "question" },
          el("label", { for: id }, el("span", { class: `pill t-${type}` }, type === "conflict" ? "Conflict" : "Gap"), q.question || ""),
          q.reason ? el("p", { class: "reason" }, q.reason) : null,
          el("input", { id, type: "text", maxlength: "2000", "data-qid": String(q.id || `Q-${i + 1}`), placeholder: "Your answer (blank = skip)" }));
      }));
    } catch (err) {
      questionsFor = "";
      fill($("questions"), el("li", { class: "error" }, `Could not load the questions: ${err.message}`));
    }
  }
  async function submitAnswers(answers) {
    const run = { ...current };
    $("submit-answers").disabled = true;
    $("answers-status").textContent = "Sending the answers…";
    try {
      await api("POST", `/runs/${encodeURIComponent(run.run_id)}/answers`, { engagement: run.engagement, answers });
      $("answers-status").textContent = "Thanks. The agent is resolving the answers and finishing the report.";
      logLine(`Answers sent (${Object.keys(answers).length})`);
      answeredFor = run.run_id;
      lastDoc = { ...(lastDoc || {}), state: "running" };
      renderRun(lastDoc);
      pollTimer = setTimeout(() => openRun(run.engagement, run.run_id), 1500);
    } catch (err) {
      $("answers-status").textContent = `Not sent: ${err.message}`;
      $("submit-answers").disabled = false;
    }
  }
  $("answers-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const answers = {};
    document.querySelectorAll("#questions input[data-qid]").forEach((input) => {
      answers[input.dataset.qid] = input.value.trim() || "skip";
    });
    submitAnswers(answers);
  });

  // ---------------------------------------------------------------- results (fresh presigned links on every click)
  function loadResults() {
    const run = { ...current };
    resultsFor = run.run_id;
    const names = (lastDoc && lastDoc.output_keys || []).map((k) => k.split("/").pop());
    fill($("downloads"), FILES.filter(([n]) => !names.length || names.includes(n)).map(([name, label]) => {
      const b = el("button", { type: "button", class: "btn sm", title: `Download ${name}` }, label);
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const { urls } = await api("GET", `/runs/${encodeURIComponent(run.run_id)}/results${qs(run.engagement)}`);
          if (!urls[name]) throw new Error("not produced for this run");
          const a = el("a", { href: urls[name], rel: "noopener" });
          document.body.append(a);
          a.click();
          a.remove();
        } catch (err) { logLine(`${name}: ${err.message}`, "error"); }
        b.disabled = false;
      });
      return b;
    }));
  }

  // ---------------------------------------------------------------- Fetch: Intake + Discovery outputs in one call
  let pipelineFocus = "";   // opportunity id from #opportunity=<id> (the Live Assist console links here)
  function pipelineStatus(text, isError = false) {
    $("pipeline-status").textContent = text;
    $("pipeline-status").className = `status-line ${isError ? "error" : "muted"}`;
  }
  async function fetchOutputs() {
    const button = $("pipeline-fetch");
    button.disabled = true;
    pipelineStatus("Fetching…");
    try {
      const out = await api("GET", `/pipeline/overview${$("pipeline-latest").checked ? "?scope=latest" : ""}`);
      window.DiscoveryPipeline.render($("pipeline-list"), out.rows, { focus: pipelineFocus, onOpen: openRun, onRun: runForOpportunity });
      pipelineStatus(`${out.count} opportunities, ${out.with_discovery} with a Discovery run` +
        `${out.repeats_hidden ? `; ${out.repeats_hidden} repeats from earlier runs hidden` : ""}. Fetched ${fmtTime(out.fetched_at)}.`);
    } catch (err) {
      fill($("pipeline-list"));
      pipelineStatus(err.message, true);
    }
    button.disabled = false;
  }
  async function runForOpportunity(o, button) {
    button.disabled = true;
    try {
      pipelineStatus(`Importing ${o.name} from Live Assist…`);
      const imp = await api("POST", `/pipeline/opportunities/${encodeURIComponent(o.intake_opportunity_id)}/import`, {});
      const body = { engagement: imp.engagement, clarify: $("clarify").checked, pipeline_opportunity_id: o.intake_opportunity_id,
        pipeline_call_ids: imp.call_ids };
      const res = await api("POST", "/runs", body);
      pipelineStatus(res.linked_to_live_assist === false
        ? "Started, but Live Assist could not be told: the report will not show there."
        : "Started. The report will also show on this opportunity in Live Assist.", res.linked_to_live_assist === false);
      await openRun(imp.engagement, res.run_id);
      showQuota();
    } catch (err) {
      pipelineStatus(`Not started: ${err.message}`, true);
    }
    button.disabled = false;
  }
  $("pipeline-fetch").addEventListener("click", fetchOutputs);
  $("pipeline-latest").addEventListener("change", fetchOutputs);
  {
    // Links from the Live Assist console: #opportunity=<id> (highlight it) or #run=<engagement>/<run_id> (open one).
    const hash = new URLSearchParams(location.hash.slice(1));
    pipelineFocus = hash.get("opportunity") || "";
    const runRef = hash.get("run");
    if (runRef && runRef.includes("/")) {
      const i = runRef.lastIndexOf("/");
      sessionStorage.setItem("discoveryRun", JSON.stringify({ engagement: runRef.slice(0, i), run_id: runRef.slice(i + 1) }));
    }
    if (pipelineFocus || runRef) history.replaceState(null, "", location.pathname);
  }
  fetchOutputs();

  // ---------------------------------------------------------------- start
  refreshForm();
  showQuota();
  if (!cfg.apiUrl) formStatus("The API URL is not configured (config.js is written by scripts/deploy.sh).", true);
  try {
    const last = JSON.parse(sessionStorage.getItem("discoveryRun") || "null");
    if (last && last.engagement && last.run_id) openRun(last.engagement, last.run_id);
  } catch { sessionStorage.removeItem("discoveryRun"); }
})();
