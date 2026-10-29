// Draws the call view as guidance: what to ask next, what is known, what is still open.
// It never shows the conversation itself; a short quote appears only as evidence for a captured fact.

import { $, clip, el, toast } from "./dom.js";

const QUOTE_CHARS = 180;
const BOT_STATES = {
  requested: ["JOINING", "wait", "Sending the bot to the meeting…"],
  joining: ["JOINING", "wait", "The bot is opening the meeting…"],
  awaiting_admission: ["WAITING", "wait", null], // The message names the bot, so it is built below.
  stopping: ["LEAVING", "wait", "The bot is leaving the meeting…"],
  failed: ["FAILED", "bad", "The bot could not stay in the meeting. Check the link, then start again."],
};

export function renderIdle() {
  setBadge("IDLE", "idle");
  $("assist-sub").textContent = "Add a source and start. Guidance appears here while the conversation is going on.";
  for (const id of ["stat-heard", "stat-captured", "stat-open", "stat-asked"]) $(id).textContent = "–";
  setSteps({});
  $("status-banner").hidden = true;
  $("empty").hidden = false;
  $("live").hidden = true;
}

function setBadge(text, state) {
  const badge = $("state-badge");
  badge.textContent = text;
  badge.dataset.state = state;
}

function setSteps(states) {
  for (const li of document.querySelectorAll("#steps li")) {
    const value = states[li.dataset.step];
    if (value) li.dataset.on = value; else delete li.dataset.on;
  }
}

function banner(text, tone) {
  const node = $("status-banner");
  node.hidden = !text;
  node.textContent = text || "";
  if (tone) node.dataset.tone = tone; else delete node.dataset.tone;
}

/** The badge, the banner and the progress pills, from the meeting bot and the call state. */
function describeState(view, botName) {
  const heard = view.processing.latest_seq;
  const minutes = (view.minutes && view.minutes.status) || "none";
  const bot = view.meeting && view.meeting.bot;

  if (view.status === "ended") {
    const failedEarly = heard === 0 && /fail|error/i.test(view.end_reason || "");
    if (failedEarly) {
      setBadge("FAILED", "bad");
      banner(`The call stopped before anything was heard: ${view.end_reason}.`, "bad");
      setSteps({});
      return;
    }
    const final = minutes === "ok" || minutes === "empty" || minutes === "error";
    setBadge(final ? "ENDED" : "WRAPPING UP", final ? "done" : "wait");
    banner(final ? "" : "Finishing the last lines and writing the minutes…", null);
    setSteps({ listen: "done", understand: view.processing.busy ? "active" : "done", suggest: "done",
      minutes: final ? "done" : "active" });
    return;
  }
  if (view.status === "paused") setBadge("PAUSED", "wait");
  if (bot && BOT_STATES[bot]) {
    const [badge, state, text] = BOT_STATES[bot];
    setBadge(badge, state);
    banner(text || `The bot is waiting in the lobby. Admit “${botName || "the bot"}” from the meeting to start.`,
      state === "bad" ? "bad" : null);
    setSteps({ listen: "active" });
    return;
  }
  if (view.status !== "paused") setBadge("LIVE", "live");
  banner(heard === 0 ? "Listening for the first words…" : "", heard === 0 ? "ok" : null);
  setSteps({
    listen: heard === 0 ? "active" : "done",
    understand: heard === 0 ? null : (view.processing.busy ? "active" : "done"),
    suggest: view.suggestions.length ? "done" : (heard === 0 ? null : "active"),
  });
}

function renderAsk(view) {
  const body = $("ask-body");
  const all = view.suggestions;
  if (!all.length) {
    const ended = view.status === "ended";
    body.replaceChildren(el("p", ended ? "No question was suggested in this call."
      : "Listening. The first question appears after a few exchanges.", "ask-wait"));
    $("earlier").hidden = true;
    return;
  }
  const latest = all[all.length - 1];
  const meta = el("div", null, "ask-meta");
  meta.append(el("span", latest.target_label || "Follow-up", "tag"));
  const copy = el("button", "Copy", "btn btn-outline", { type: "button" });
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(latest.question); toast("Question copied"); } catch { toast("Copy is not available here"); }
  });
  meta.append(copy);
  const nodes = [el("p", latest.question, "ask-q"), meta];
  if (latest.rationale) nodes.push(el("p", latest.rationale, "ask-why"));
  body.replaceChildren(...nodes);

  const earlier = all.slice(0, -1).reverse();
  $("earlier").hidden = !earlier.length;
  $("earlier-list").replaceChildren(...earlier.map((s) => {
    const li = el("li", s.question);
    if (s.target_label) li.append(el("span", `  ${s.target_label}`, "muted"));
    return li;
  }));
}

function renderKnown(view) {
  const quotes = new Map(view.turns.map((t) => [t.seq, t]));
  const groups = new Map();
  for (const item of view.state.captured) {
    const key = item.category || "Other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const nodes = [];
  for (const [category, items] of groups) {
    nodes.push(el("p", category, "group-title"));
    for (const item of items) {
      const card = el("div", null, "fact");
      card.append(el("div", item.label, "fact-label"), el("div", item.value, "fact-value"));
      const said = (item.evidence || []).map((seq) => quotes.get(seq)).filter(Boolean);
      if (said.length) {
        const details = el("details");
        details.append(el("summary", "What was said"));
        for (const t of said) details.append(el("blockquote", `${t.speaker_name || t.speaker}: “${clip(t.text, QUOTE_CHARS)}”`, "quote"));
        card.append(details);
      }
      nodes.push(card);
    }
  }
  $("known").replaceChildren(...(nodes.length ? nodes : [el("p", "Nothing confirmed yet.", "muted")]));
  const open = view.state.open_gaps;
  const chips = open.map((g) => el("li", g.label, null, { "data-required": String(!!g.required), title: g.description || "" }));
  $("open").replaceChildren(...(chips.length ? chips : [el("li", "Everything on the checklist is covered.")]));
}

function renderPrior(prior) {
  const box = $("prior-box");
  const parts = [];
  for (const [title, items] of [["About the customer", prior && prior.account], ["About this opportunity", prior && prior.opportunity]]) {
    if (!items || !items.length) continue;
    parts.push(el("p", title, "group-title"));
    const list = el("ul");
    for (const item of items) list.append(el("li", typeof item === "string" ? item : item.text || ""));
    parts.push(list);
  }
  box.hidden = !parts.length;
  $("prior").replaceChildren(...parts);
}

const MINUTES_SECTIONS = [["key_points", "Key points"], ["decisions", "Decisions"], ["action_items", "Action items"],
  ["risks", "Risks"], ["open_questions", "Open questions"], ["next_steps", "Next steps"]];

function renderMinutes(view) {
  const state = (view.minutes && view.minutes.status) || "none";
  const box = $("minutes-box");
  box.hidden = view.status !== "ended";
  if (box.hidden) return;
  const text = { none: "Writing the minutes…", generating: "Writing the minutes…", ok: "", error: "The minutes could not be written.",
    empty: "Nothing was said, so there are no minutes." }[state];
  $("minutes-status").textContent = text;
  $("minutes-download").hidden = state !== "ok";
  const nodes = [];
  const minutes = state === "ok" && view.minutes.minutes;
  if (minutes) {
    nodes.push(el("h4", "Summary"));
    for (const para of String(minutes.summary || "").split("\n").filter((p) => p.trim())) nodes.push(el("p", para));
    for (const [key, title] of MINUTES_SECTIONS) {
      const items = minutes[key] || [];
      if (!items.length) continue;
      nodes.push(el("h4", title));
      const list = el("ul");
      for (const item of items) {
        const extra = [item.owner && `owner: ${item.owner}`, item.due && `due: ${item.due}`].filter(Boolean);
        list.append(el("li", item.text + (extra.length ? ` (${extra.join("; ")})` : "")));
      }
      nodes.push(list);
    }
  }
  $("minutes").replaceChildren(...nodes);
}

/** The questions written at the start from the meeting's title and description. Hidden when none were asked for. */
function renderPrequestions(view) {
  const pre = view.prequestions || { status: "none" };
  const box = $("prep-box");
  box.hidden = pre.status === "none";
  if (box.hidden) return;
  $("prep-status").textContent = {
    generating: "Preparing questions from the meeting details…",
    error: "The questions could not be prepared. Live suggestions still work.",
  }[pre.status] || "";
  $("prep-list").replaceChildren(...(pre.questions || []).map((q) => {
    const item = el("li");
    item.append(el("span", q.question, "prep-q"));
    if (q.topic_label) item.append(el("span", q.topic_label, "tag"));
    if (q.why) item.append(el("div", q.why, "ask-why"));
    return item;
  }));
}

export function renderView(view, botName) {
  $("empty").hidden = true;
  $("live").hidden = false;
  $("assist-sub").textContent = view.title || "Live call";
  $("stat-heard").textContent = view.processing.latest_seq;
  $("stat-captured").textContent = view.state.captured.length;
  $("stat-open").textContent = view.state.open_gaps.length;
  $("stat-asked").textContent = view.suggestions.length;
  describeState(view, botName);
  renderPrequestions(view);
  renderAsk(view);
  const problem = view.state.problem_summary;
  $("problem-box").hidden = !problem;
  $("problem").textContent = problem || "";
  renderKnown(view);
  renderPrior(view.prior_context);
  renderMinutes(view);
  $("end-btn").hidden = view.status === "ended";
  $("new-btn").hidden = view.status !== "ended";
}
