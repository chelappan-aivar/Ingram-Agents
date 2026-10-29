// Turns audio files into what the Live Assist audio source expects, entirely in the browser:
// 16 kHz mono 16-bit PCM, one track per speaker, plus a manifest. Uploaded through presigned URLs.

import { api, uploadFile } from "./api.js";

export const SAMPLE_RATE = 16000;
export const MAX_FILES = 2;
export const MAX_FILE_BYTES = 100 * 1024 * 1024;
export const MAX_SECONDS = 60 * 60;

/** Decode any audio file the browser understands to mono 16 kHz samples (Float32). */
export async function decodeToMono16k(file) {
  if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name} is larger than ${MAX_FILE_BYTES / 1048576} MB.`);
  const context = new (window.AudioContext || window.webkitAudioContext)();
  let decoded;
  try {
    decoded = await context.decodeAudioData(await file.arrayBuffer());
  } catch {
    throw new Error(`${file.name} could not be read as audio. Try WAV or MP3.`);
  } finally {
    context.close();
  }
  if (decoded.duration > MAX_SECONDS) throw new Error(`${file.name} is longer than ${MAX_SECONDS / 60} minutes.`);
  // Rendering into a 1-channel, 16 kHz offline context mixes down and resamples in one step.
  const length = Math.max(1, Math.ceil(decoded.duration * SAMPLE_RATE));
  const offline = new OfflineAudioContext(1, length, SAMPLE_RATE);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const rendered = await offline.startRendering();
  return { samples: rendered.getChannelData(0), seconds: decoded.duration };
}

export function toPcm16(samples) {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/** Build the manifest the audio source reads. `tracks`: [{id, name, role, seconds}]. */
export function buildManifest(tracks) {
  return {
    audio: { encoding: "pcm_s16le", sample_rate: SAMPLE_RATE, channels: 1 },
    duration_sec: Math.max(...tracks.map((t) => t.seconds)),
    speakers: tracks.map((t) => ({ speaker_id: t.id, name: t.name, role: t.role, pcm: `speakers/${t.id}.pcm` })),
  };
}

/**
 * Decode, upload and return the upload id.
 * `entries`: [{file, role}]  ("ba" or "cx").  `onProgress(text)` reports each step.
 */
export async function prepareAudioUpload(entries, baName, onProgress) {
  const tracks = [];
  for (const [index, { file, role }] of entries.entries()) {
    onProgress(`Reading ${file.name}…`);
    const { samples, seconds } = await decodeToMono16k(file);
    const id = `${role}${index + 1}`;
    tracks.push({ id, role, seconds, pcm: toPcm16(samples),
      name: role === "ba" ? (baName || "Partner BA") : "Customer" });
  }
  const manifest = buildManifest(tracks);
  const paths = ["manifest.json", ...manifest.speakers.map((s) => s.pcm)];
  const upload = await api.createUpload({ kind: "audio", files: paths });
  const blobs = new Map([
    ["manifest.json", new Blob([JSON.stringify(manifest)], { type: "application/json" })],
    ...tracks.map((t) => [`speakers/${t.id}.pcm`, new Blob([t.pcm.buffer], { type: "application/octet-stream" })]),
  ]);
  for (const target of upload.files) {
    onProgress(`Uploading ${target.path.split("/").pop()}…`);
    await uploadFile(target.url, blobs.get(target.path));
  }
  return upload.upload_id;
}
