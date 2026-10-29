// The Source card: choosing a source, reading its input, and building the API request for it.

import { api, setUploadMode } from "./api.js";
import { prepareAudioUpload, MAX_FILES } from "./audio.js";
import { $, el, showError } from "./dom.js";

const NAME_KEY = "live-assist-ba-name";
const MAX_TRANSCRIPT_FILE_BYTES = 1024 * 1024;
const platformNotes = {
  google_meet: "Google Meet link recognised. Meetings that only allow signed-in users will not let a guest bot in.",
  teams: "Microsoft Teams link recognised. Admit the bot from the lobby when it asks to join.",
  zoom: "Zoom link recognised. Zoom support is new: if the bot cannot join, try Teams or Meet.",
};
let audioEntries = []; // [{file, role}]
let limits = { transcriptMaxChars: 60000, meetingEnabled: true, meetingReason: "", needsVexaKey: true };

export function selectedSource() {
  return document.querySelector('input[name="source"]:checked').value;
}

function detectPlatform(value) {
  let host = "";
  try { host = new URL(/^[a-z]+:\/\//i.test(value) ? value : "https://" + value).hostname.toLowerCase(); } catch { return null; }
  if (host === "meet.google.com") return "google_meet";
  if (host === "teams.microsoft.com" || host === "teams.live.com") return "teams";
  if (host === "zoom.us" || host.endsWith(".zoom.us") || host.endsWith(".zoom.com")) return "zoom";
  return null;
}

function showSelected() {
  const chosen = selectedSource();
  for (const box of document.querySelectorAll(".source")) {
    box.querySelector(".source-body").hidden = box.dataset.source !== chosen;
  }
  $("bot-name-field").hidden = chosen !== "meeting";
  showError("");
}

function renderAudioList() {
  const list = $("audio-list");
  list.replaceChildren(...audioEntries.map((entry, index) => {
    const select = el("select", null, null, { "aria-label": `Who is speaking in ${entry.file.name}` });
    for (const [value, label] of [["cx", "Customer"], ["ba", "Me (partner)"]]) {
      const option = el("option", label);
      option.value = value;
      option.selected = entry.role === value;
      select.append(option);
    }
    select.addEventListener("change", () => { entry.role = select.value; });
    const remove = el("button", "×", null, { type: "button", "aria-label": `Remove ${entry.file.name}` });
    remove.addEventListener("click", () => { audioEntries.splice(index, 1); renderAudioList(); });
    const row = el("li", null, "file-item");
    row.append(el("span", `${entry.file.name} · ${(entry.file.size / 1048576).toFixed(1)} MB`, "name"), select, remove);
    return row;
  }));
  $("audio-pick").disabled = audioEntries.length >= MAX_FILES;
}

export function initSources(options) {
  const meeting = options.sources.meeting || {};
  setUploadMode((options.sources.audio || {}).upload);
  limits = {
    transcriptMaxChars: (options.sources.transcript || {}).max_chars || limits.transcriptMaxChars,
    meetingEnabled: meeting.enabled !== false,
    meetingReason: meeting.reason || "",
    needsVexaKey: meeting.needs_vexa_key !== false,
  };
  // Hidden in the page until the server says it needs them. With Recall the server sends the bot with its own
  // keys, so there is nothing to type.
  for (const id of ["vexa-api-key", "vexa-tx-key"]) $(id).closest("label").hidden = !limits.needsVexaKey;
  $("key-note").hidden = !limits.needsVexaKey;
  if (!limits.meetingEnabled) {
    for (const id of ["meeting-url", "vexa-api-key", "vexa-tx-key"]) $(id).disabled = true;
    $("meeting-note").textContent = limits.meetingReason || "Live meetings are not available on this server.";
    document.querySelector('input[value="meeting"]').disabled = true;
    document.querySelector('input[value="transcript"]').checked = true;
  }
  try { $("ba-names").value = localStorage.getItem(NAME_KEY) || ""; } catch { /* storage may be blocked */ }
  for (const radio of document.querySelectorAll('input[name="source"]')) radio.addEventListener("change", showSelected);
  showSelected();

  $("meeting-url").addEventListener("input", (event) => {
    const platform = detectPlatform(event.target.value.trim());
    $("meeting-note").textContent = platformNotes[platform] || "Admit the bot when it asks to join. It leaves when you end the call.";
  });

  $("audio-pick").addEventListener("click", () => $("audio-files").click());
  $("audio-files").addEventListener("change", (event) => {
    for (const file of event.target.files) {
      if (audioEntries.length < MAX_FILES) audioEntries.push({ file, role: audioEntries.length ? "ba" : "cx" });
    }
    event.target.value = "";
    renderAudioList();
  });

  $("transcript-pick").addEventListener("click", () => $("transcript-file").click());
  $("transcript-clear").addEventListener("click", () => { $("transcript-text").value = ""; });
  $("transcript-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > MAX_TRANSCRIPT_FILE_BYTES) return showError("That file is too large for a transcript.");
    $("transcript-text").value = await file.text();
    showError("");
  });
}

/** The API request for the chosen source. `progress(text)` reports slow steps such as uploads. */
export async function buildStartRequest(progress) {
  const chosen = selectedSource();
  const baNames = $("ba-names").value.trim();
  try { localStorage.setItem(NAME_KEY, baNames); } catch { /* ignore */ }
  const body = {
    assist_config: { scope: $("opt-scope").value, frequency: $("opt-frequency").value },
  };

  if (chosen === "meeting") {
    const url = $("meeting-url").value.trim();
    if (!url) throw new Error("Paste the meeting link first.");
    const botName = $("bot-name").value.trim();
    // Sent only when typed: the server asks the agent for pre-questions when it receives a title or a description.
    const meetingTitle = $("meeting-title").value.trim();
    const description = $("meeting-description").value.trim();
    if (meetingTitle) body.title = meetingTitle;
    if (description) body.description = description;
    body.source = {
      type: "meeting", meeting_url: url, ba_names: baNames, ...(botName ? { bot_name: botName } : {}),
    };
    if (limits.needsVexaKey) {
      const apiKey = $("vexa-api-key").value.trim();
      if (!apiKey) throw new Error("Enter your Vexa API key first.");
      const txKey = $("vexa-tx-key").value.trim();
      body.source.vexa_api_key = apiKey;
      if (txKey) body.source.vexa_tx_key = txKey;
    }
  } else if (chosen === "transcript") {
    const text = $("transcript-text").value;
    if (!text.trim()) throw new Error("Paste a transcript or add a file first.");
    if (text.length > limits.transcriptMaxChars) {
      throw new Error(`The transcript is too long (${text.length.toLocaleString()} characters; the limit is ${limits.transcriptMaxChars.toLocaleString()}). Split it.`);
    }
    body.title = "Transcript";
    body.source = { type: "transcript", text, ba_names: baNames, speed: Number($("transcript-speed").value) };
  } else {
    if (!audioEntries.length) throw new Error("Add an audio file first.");
    const roles = new Set(audioEntries.map((e) => e.role));
    if (roles.size < audioEntries.length) throw new Error("Choose a different speaker for each file.");
    body.title = "Audio recording";
    const uploadId = await prepareAudioUpload(audioEntries, baNames.split(",")[0].trim(), progress);
    body.source = { type: "audio", upload_id: uploadId, speed: 1 };
  }
  return body;
}

/** Starts the call. The Vexa keys live only in the form and the request: they are cleared once the call has started. */
export async function startCall(body) {
  const result = await api.startCall(body);
  $("vexa-api-key").value = "";
  $("vexa-tx-key").value = "";
  return result;
}
