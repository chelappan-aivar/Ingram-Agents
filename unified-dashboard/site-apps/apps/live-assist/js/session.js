// One live call: start it, follow it by polling, end it. Polling is cheap: the server answers
// 304 when nothing changed, so a 1.5 s interval costs almost nothing.

import { api } from "./api.js";

const POLL_MS = 1500;
const BACKOFF_MS = 5000;
const FINAL_MINUTES = new Set(["ok", "empty", "error"]);

/** True once nothing more will change: ended, all lines processed, minutes final. */
export function isSettled(view) {
  const minutes = (view.minutes && view.minutes.status) || "none";
  return view.status === "ended" && !view.processing.busy && FINAL_MINUTES.has(minutes);
}

export class Session {
  constructor(sessionId, { onView, onError }) {
    this.id = sessionId;
    this.view = null;
    this._etag = null;
    this._timer = null;
    this._stopped = false;
    this._onView = onView;
    this._onError = onError;
    this._failures = 0;
  }

  start() {
    this._stopped = false;
    this._schedule(0);
  }

  stop() {
    this._stopped = true;
    clearTimeout(this._timer);
  }

  async end() {
    await api.endCall(this.id);
    this._etag = null;
    this.stop();
    this.start(); // Keep following until the last lines are processed and the minutes are written.
  }

  _schedule(delay) {
    clearTimeout(this._timer);
    if (!this._stopped) this._timer = setTimeout(() => this._poll(), delay);
  }

  async _poll() {
    try {
      const response = await api.getCall(this.id, this._etag);
      this._failures = 0;
      if (response.status !== 304) {
        this._etag = response.etag;
        this.view = response.data;
        this._onView(this.view);
      }
      if (this.view && isSettled(this.view)) return this.stop();
      this._schedule(POLL_MS);
    } catch (error) {
      this._failures += 1;
      if (this._failures === 1 || this._failures % 5 === 0) this._onError(error);
      if (error.status === 404) return this.stop();
      this._schedule(BACKOFF_MS);
    }
  }
}
