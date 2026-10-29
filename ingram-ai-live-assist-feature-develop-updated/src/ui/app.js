// Minimal test client for the Live Assist API (via testui/serve.py at /api).
const POLL_MS = 1500;
let current = null; // { id, etag, view, timer }

const $ = (id) => document.getElementById(id);

function log(msg) {
  $("log").textContent = `${new Date().toLocaleTimeString()} ${msg}\n` + $("log").textContent;
}

// Builds an element with text content only (transcripts are untrusted).
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

async function api(method, path, body, headers = {}) {
  const res = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok && res.status !== 304) {
    const err = data && data.error ? `${data.error.code}: ${data.error.message}` : res.status;
    throw new Error(`${method} ${path} -> ${err}`);
  }
  return { status: res.status, data, etag: res.headers.get("etag") };
}

async function upload(url, blob) {
  const res = await fetch("/upload?url=" + encodeURIComponent(url), { method: "PUT", body: blob });
  if (!res.ok) throw new Error(`upload failed: ${res.status}`);
}

// --- start a call ---------------------------------------------------------------

function settingsFromForm() {
  const f = $("start").elements;
  return {
    scope: f.scope.value,
    frequency: f.frequency.value,
    max_suggestions: Number(f.max_suggestions.value),
    instructions: f.instructions.value.trim(),
    use_prior_context: f.use_prior_context.value,
  };
}

async function startCall(event) {
  event.preventDefault();
  const f = $("start").elements;
  const type = f.type.value;
  const body = { title: f.title.value.trim() || undefined, assist_config: settingsFromForm() };
  body.config_id = f.config_id.value || undefined;
  const scope = selectedScope();
  if (scope.account_id) body.account_id = scope.account_id;
  if (scope.opportunity_id) body.opportunity_id = scope.opportunity_id;

  if (type === "recording") {
    const file = f.recording.files[0];
    if (!file) throw new Error("choose a recording file");
    const up = (await api("POST", "/uploads", { kind: "recording" })).data;
    await upload(up.files[0].url, file);
    log(`uploaded ${file.name}`);
    body.source = { type, upload_id: up.upload_id, speed: Number(f.speed.value) };
  } else if (type === "audio") {
    body.source = { type, ...(await uploadAudio([...f.audio.files])), speed: Number(f.speed.value) };
    if (f.limit_seconds.value) body.source.limit_seconds = Number(f.limit_seconds.value);
  }

  const call = (await api("POST", "/calls", body)).data;
  log(`started ${call.session_id} (${call.source})`);
  loadCalls();
  if (scope.account_id) loadAccountPanel().catch((e) => log(e.message));
  openCall(call.session_id);
}

async function uploadAudio(files) {
  // Paths relative to the chosen folder: "audio/speakers/x.pcm" -> "speakers/x.pcm".
  const byPath = new Map(files.map((file) => [file.webkitRelativePath.split("/").slice(1).join("/"), file]));
  const manifestFile = byPath.get("manifest.json");
  if (!manifestFile) throw new Error("the folder has no manifest.json");
  const manifest = JSON.parse(await manifestFile.text());
  const paths = ["manifest.json", ...manifest.speakers.map((s) => s.pcm)];
  for (const p of paths) if (!byPath.has(p)) throw new Error(`missing ${p} in the folder`);

  const up = (await api("POST", "/uploads", { kind: "audio", files: paths })).data;
  for (const f of up.files) {
    log(`uploading ${f.path} (${(byPath.get(f.path).size / 1e6).toFixed(1)} MB)...`);
    await upload(f.url, byPath.get(f.path));
  }
  return { upload_id: up.upload_id };
}

// --- calls list -------------------------------------------------------------------

async function loadCalls() {
  const { data } = await api("GET", "/calls?limit=20");
  const list = $("calls");
  list.replaceChildren();
  for (const c of data.calls) {
    const li = el("li", `${c.created_at}  ${c.title}  [${c.source}, ${c.status}, ${c.turn_count} turns]` +
      (c.account_id ? `  · ${c.account_id}${c.opportunity_id ? " / " + c.opportunity_id : ""}` : ""));
    li.onclick = () => openCall(c.session_id);
    list.append(li);
  }
  if (!data.calls.length) list.append(el("li", "No calls yet", "muted"));
}

// --- live call ------------------------------------------------------------------

function openCall(id) {
  closeCall();
  current = { id, etag: null, view: null, timer: null };
  $("call").hidden = false;
  $("call-title").textContent = id;
  startPolling();
}

function closeCall() {
  stopPolling();
  current = null;
  $("call").hidden = true;
}

function startPolling() {
  if (!current) return;
  if (!current.timer) current.timer = setInterval(poll, POLL_MS);
  poll();
}

function stopPolling() {
  if (current?.timer) clearInterval(current.timer);
  if (current) current.timer = null;
}

// Nothing more will change: the call ended, its turns are processed and the minutes are final.
const MINUTES_FINAL = new Set(["ok", "empty", "error"]);
function isSettled(v) {
  // After Regenerate, the old result stays visible until the new attempt starts.
  const stale = current?.regenerateFrom && v.minutes?.started_at === current.regenerateFrom;
  return v.status === "ended" && !v.processing.busy && MINUTES_FINAL.has(v.minutes?.status) && !stale;
}

async function poll() {
  if (!current) return;
  const id = current.id;
  try {
    const res = await api("GET", `/calls/${id}`, undefined, current.etag ? { "If-None-Match": current.etag } : {});
    if (!current || current.id !== id || res.status === 304) return;
    current.etag = res.etag;
    current.view = res.data;
    render(res.data);
    if (isSettled(res.data)) stopPolling();
  } catch (e) {
    log(e.message);
  }
}

function render(v) {
  const p = v.processing;
  $("call-title").textContent = `${v.title} (${v.status})`;
  $("call-meta").textContent =
    `${v.session_id} · source ${v.source} · turns ${p.latest_seq} · processed ${p.last_processed_seq}` +
    ` · runs ${p.run_count}${p.busy ? " · agent working..." : ""}` +
    (v.end_reason ? ` · ended: ${v.end_reason}` : "");
  $("pause").disabled = v.status !== "live";
  $("resume").disabled = v.status !== "paused";
  $("end").disabled = $("apply").disabled = v.status === "ended";
  $("line").hidden = !v.accepts_manual_turns;

  // Timeline: each run with its turns, then turns not processed yet.
  const timeline = $("timeline");
  timeline.replaceChildren();
  const runs = new Map(v.runs.map((r) => [r.first_seq, r]));
  const groups = new Map();
  for (const t of v.turns) {
    const key = t.run_first_seq ?? "pending";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(t);
  }
  for (const [key, turns] of [...groups.entries()].reverse()) {
    const run = runs.get(key);
    const box = el("div", null, "run" + (run ? (run.status === "error" ? " error" : "") : " pending"));
    box.append(el("div", run
      ? `Run #${run.run_number ?? "?"} · turns ${run.first_seq}-${run.last_seq} · ${(run.duration_ms / 1000).toFixed(1)} s`
      : "Not processed yet", "muted"));
    for (const t of turns) {
      const line = el("div", null, "turn");
      line.id = `turn-${t.seq}`;
      line.tabIndex = -1; // Focus target for citation links.
      line.append(el("b", `#${t.seq}`), ` ${t.speaker_name || t.speaker}: ${t.text}`);
      box.append(line);
    }
    if (run) {
      if (run.status === "error") box.append(el("div", `Run failed: ${run.error}`));
      for (const n of run.narration || []) box.append(el("div", n, "agent"));
      if (run.captured_fields?.length) box.append(el("div", `Captured: ${run.captured_fields.join(", ")}`, "muted"));
      for (const s of run.suggestions || []) box.append(el("div", `ASK: ${s.question}  [${s.target_label || "follow-up"}]`, "ask"));
      for (const m of run.memory_lookups || []) {
        box.append(el("div", `Looked up "${m.query}" (${m.level}): ` +
          (m.ok ? `${m.hits} hit(s)` : "memory unavailable") + `, ${m.ms} ms`, "muted"));
      }
    }
    timeline.append(box);
  }
  if (!v.turns.length) timeline.append(el("p", "Waiting for the first turn...", "muted"));

  const suggestions = $("suggestions");
  suggestions.replaceChildren(...[...v.suggestions].reverse().map((s) =>
    el("li", `${s.question}  [${s.target_label || "follow-up"}]${s.rationale ? " — " + s.rationale : ""}`)));

  renderPrior(v.prior_context);
  $("call-meta").textContent += ` · config ${v.config_id} v${v.config_version}`;
  $("call-meta").textContent += v.account_id ? ` · account ${v.account_id}` +
    (v.opportunity_id ? ` / ${v.opportunity_id}` : "") : "";
  $("problem").textContent = v.state.problem_summary || "Not established yet.";
  $("captured").replaceChildren(...v.state.captured.map((c) => {
    const li = el("li", `${c.label}: ${c.value} `);
    li.append(...citations(c.evidence));
    return li;
  }));
  $("gaps").replaceChildren(...v.state.open_gaps.map((g) =>
    el("li", `${g.label}${g.required ? "" : " (optional)"}`)));
  renderMinutes(v);
}

// --- minutes ----------------------------------------------------------------------

const MINUTES_SECTIONS = [
  ["key_points", "Key points"], ["decisions", "Decisions"], ["action_items", "Action items"],
  ["risks", "Risks and concerns"], ["open_questions", "Open questions"], ["next_steps", "Next steps"],
];
const MINUTES_STATUS_TEXT = {
  none: "Minutes are written when the call ends.",
  generating: "Writing minutes...",
  ok: "Minutes ready.",
  empty: "The call had no turns, so there are no minutes.",
  error: "Minutes could not be written. Try Regenerate.",
};

// One button per cited turn; activating it scrolls to and focuses that turn.
function citations(seqs = []) {
  return seqs.map((seq) => {
    const b = el("button", `#${seq}`, "cite");
    b.type = "button";
    b.setAttribute("aria-label", `Go to turn ${seq}`);
    b.onclick = () => {
      const turn = $(`turn-${seq}`);
      if (!turn) return log(`turn ${seq} is not loaded`);
      turn.scrollIntoView({ block: "center" });
      turn.focus();
      turn.classList.add("highlight");
      setTimeout(() => turn.classList.remove("highlight"), 2000);
    };
    return b;
  });
}

function renderMinutes(v) {
  const m = v.minutes || { status: "none" };
  const ended = v.status === "ended";
  // Before the end the call is live; after it, minutes wait for the last turns.
  const status = m.status === "none" && ended ? "generating" : m.status;
  $("minutes-status").textContent = MINUTES_STATUS_TEXT[status] +
    (m.status === "error" && m.error_code ? ` (${m.error_code})` : "") +
    (m.generated_at ? ` Generated ${m.generated_at}.` : "");
  $("minutes-regenerate").disabled = !ended || status === "generating";
  $("minutes-md").disabled = $("minutes-json").disabled = m.status !== "ok";

  const box = $("minutes");
  box.replaceChildren();
  if (m.status !== "ok" || !m.minutes) return;
  const mm = m.minutes;
  if (mm.attendees?.length) {
    box.append(el("p", "Attendees: " + mm.attendees.map((a) =>
      `${a.name || "Unnamed"} (${a.speaker === "ba" ? "Partner BA" : "Customer"})`).join(", ")));
  }
  box.append(el("h4", "Summary"));
  for (const para of mm.summary.split("\n").filter((p) => p.trim())) box.append(el("p", para));
  for (const [key, title] of MINUTES_SECTIONS) {
    const items = mm[key] || [];
    if (!items.length) continue;
    box.append(el("h4", title));
    const list = el("ul");
    for (const item of items) {
      const extra = [item.owner && `owner: ${item.owner}`, item.due && `due: ${item.due}`].filter(Boolean);
      const li = el("li", item.text + (extra.length ? ` (${extra.join("; ")})` : "") + " ");
      li.append(...(item.uncited ? [el("span", "no citation", "muted")] : citations(item.citations)));
      list.append(li);
    }
    box.append(list);
  }
}

function download(name, type, text) {
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

// --- controls ---------------------------------------------------------------------

function control(fn) {
  return async (event) => {
    event?.preventDefault();
    try {
      await fn();
      if (current) { current.etag = null; poll(); }
    } catch (e) {
      log(e.message);
    }
  };
}

$("pause").onclick = control(() => api("PATCH", `/calls/${current.id}`, { status: "paused" }));
$("resume").onclick = control(() => api("PATCH", `/calls/${current.id}`, { status: "live" }));
$("apply").onclick = control(async () => {
  await api("PATCH", `/calls/${current.id}`, { assist_config: settingsFromForm() });
  log("settings apply from the next agent run");
});
$("end").onclick = control(() => api("POST", `/calls/${current.id}/end`, {}));
$("close").onclick = () => closeCall();
$("handoff").onclick = control(async () => {
  const { data } = await api("GET", `/calls/${current.id}/handoff`);
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  a.download = `${current.id}-handoff.json`;
  a.click();
});
$("minutes-regenerate").onclick = control(async () => {
  current.regenerateFrom = current.view?.minutes?.started_at;
  await api("POST", `/calls/${current.id}/minutes`);
  log("minutes regeneration queued");
  startPolling();
});
$("minutes-json").onclick = control(async () => {
  const { data } = await api("GET", `/calls/${current.id}/minutes`);
  download(`${current.id}-minutes.json`, "application/json", JSON.stringify(data, null, 2));
});
$("minutes-md").onclick = control(async () => {
  const res = await fetch(`/api/calls/${current.id}/minutes?format=md`);
  if (!res.ok) throw new Error(`GET minutes markdown -> ${res.status}`);
  download(`${current.id}-minutes.md`, "text/markdown", await res.text());
});
$("line").onsubmit = control(async () => {
  const f = $("line").elements;
  if (!f.text.value.trim()) return;
  const { data } = await api("POST", `/calls/${current.id}/turns`, { speaker: f.speaker.value, text: f.text.value.trim() });
  log(`line ${data.seq} sent`);
  f.text.value = "";
});
$("start").onsubmit = (e) => startCall(e).catch((err) => log(err.message));
$("refresh").onclick = () => loadCalls().catch((err) => log(err.message));

// --- init -----------------------------------------------------------------------------

(async () => {
  try {
    await api("GET", "/health");
    const opts = (await api("GET", "/options")).data;
    $("health").textContent = `API ok · ${opts.topics.length} checklist topics · audio speed ` +
      `${opts.sources.audio.speed.min}-${opts.sources.audio.speed.max}x`;
    const fill = (id, values, value) => $(id).replaceChildren(...values.map((v) => {
      const o = el("option", v); o.value = v; o.selected = v === value; return o;
    }));
    fill("scope", opts.assist_config.scope, "guided");
    fill("frequency", opts.assist_config.frequency, opts.assist_config.defaults.frequency);
    await loadCalls();
    await initAccounts(opts);
    await initConfigs();
  } catch (e) {
    $("health").textContent = "API not reachable: " + e.message;
  }
})();
