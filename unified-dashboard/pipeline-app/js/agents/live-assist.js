/** Live Assist client, via the dashboard's authenticated proxy (the access key never reaches the browser). */
import { LIVE_ASSIST, POLLING } from "../config.js";
import { AbortedError } from "../core/http.js";
import { pollUntil } from "../core/poll.js";
import { truncate } from "../domain/text.js";

const MINUTES_SETTLED = new Set(["ok", "empty", "error"]);
const isFinished = (view) => view.status === "ended" && !view.processing?.busy && MINUTES_SETTLED.has(view.minutes?.status);

export function createLiveAssistAgent(http) {
  return {
    /**
     * Replays a transcript as a call, waits for the agent runs and the minutes, and returns the handoff.
     * If the call does not settle in time it is ended and whatever was captured so far is returned (partial: true).
     */
    async runTranscript({ title, text, baNames = [LIVE_ASSIST.seller] }, { signal, onUpdate } = {}) {
      const call = await http.post(
        "/calls",
        { title: truncate(title, 200) || "Pipeline call", assist_config: LIVE_ASSIST.assist, source: { type: "transcript", text, ba_names: baNames, speed: LIVE_ASSIST.replaySpeed } },
        { signal },
      );
      const sessionId = call.session_id;
      const poll = () => http.get(`/calls/${encodeURIComponent(sessionId)}`, { signal });
      let view;
      let partial = false;
      try {
        view = await pollUntil(poll, { isDone: isFinished, ...POLLING.liveAssist, signal, onTick: onUpdate, what: "Live Assist" });
      } catch (err) {
        if (err instanceof AbortedError || err.code !== "timeout") throw err;
        partial = true;
        await http.post(`/calls/${encodeURIComponent(sessionId)}/end`, { reason: "pipeline timeout" }).catch(() => {});
        view = await poll();
      }
      const handoff = await http.get(`/calls/${encodeURIComponent(sessionId)}/handoff`, { signal });
      return { sessionId, view, handoff, partial };
    },
  };
}
