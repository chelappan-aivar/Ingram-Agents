// Wires the console together: theme, sources, starting and ending a call, and following it.

import { api, getAccessKey, setAccessKey, usesAccessKey } from "./api.js";
import { $, showError, toast } from "./dom.js";
import { renderIdle, renderView } from "./render.js";
import { Session } from "./session.js";
import { buildStartRequest, initSources, startCall } from "./sources.js";
import { initTheme } from "./theme.js";
import { initIntake, intakeCallFields, loadIntake, recordIntakeCall } from "./intake.js";

let session = null;
let botName = "";

function setBusy(busy, label) {
  const button = $("start-btn");
  button.disabled = busy;
  button.textContent = busy ? label : "Start live assist";
}

let optionsLoaded = false;

/** Loads the server's settings once. Asks for the access key if it is not known yet. */
async function loadOptions() {
  if (optionsLoaded) return;
  initSources(await api.options());
  optionsLoaded = true;
  loadIntake();
}

async function onStart(event) {
  event.preventDefault();
  showError("");
  if (usesAccessKey && !getAccessKey()) {
    showError("Enter the access key first.");
    $("access-key").focus();
    return;
  }
  setBusy(true, "Preparing…");
  try {
    await loadOptions(); // Retries after a cancelled or wrong access key.
    const body = { ...(await buildStartRequest((text) => setBusy(true, text))), ...intakeCallFields() };
    setBusy(true, "Starting…");
    botName = (body.source.bot_name || $("bot-name").placeholder || "").trim();
    const call = await startCall(body);
    follow(call.session_id);
    recordIntakeCall(call);
  } catch (error) {
    showError(error.message || "Something went wrong.");
  } finally {
    setBusy(false);
  }
}

function follow(sessionId) {
  session?.stop();
  renderIdle();
  $("empty").hidden = true;
  $("live").hidden = false;
  session = new Session(sessionId, {
    onView: (view) => renderView(view, botName),
    onError: (error) => toast(error.message),
  });
  session.start();
}

async function onEnd() {
  if (!session) return;
  $("end-btn").disabled = true;
  try {
    await session.end();
  } catch (error) {
    toast(error.message);
  } finally {
    $("end-btn").disabled = false;
  }
}

async function onDownloadMinutes() {
  try {
    const text = await api.minutesMarkdown(session.id);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
    link.download = `${session.id}-minutes.md`;
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) {
    toast(error.message);
  }
}

function onNew() {
  session?.stop();
  session = null;
  renderIdle();
}

async function init() {
  initTheme();
  renderIdle();
  // Handlers first: without one, the browser would submit the form as a page load and put every field
  // (including any key typed in it) in the address bar.
  $("start-form").addEventListener("submit", onStart);
  $("end-btn").addEventListener("click", onEnd);
  $("new-btn").addEventListener("click", onNew);
  $("minutes-download").addEventListener("click", onDownloadMinutes);
  initIntake();
  if (usesAccessKey) initAccessKey();
  if (usesAccessKey && !getAccessKey()) return; // Nothing to ask the server until the key is entered.
  try {
    await loadOptions();
  } catch (error) {
    showError(error.message); // Pressing Start tries again.
  }
}

/** The hosted console calls the API itself, so the user enters the shared access key here. */
function initAccessKey() {
  const input = $("access-key");
  $("access-key-field").hidden = false;
  input.value = getAccessKey();
  input.addEventListener("input", () => setAccessKey(input.value));
  input.addEventListener("change", async () => {
    // A pasted or typed key is checked right away by loading the server's settings.
    showError("");
    optionsLoaded = false;
    if (!getAccessKey()) return;
    try {
      await loadOptions();
      toast("Access key accepted");
    } catch (error) {
      showError(error.message);
    }
  });
}

init();
