import { STAGES } from "../pipeline/state.js";
import { formatDuration } from "../domain/text.js";
import { h } from "./dom.js";

const ICON = { done: "✓", running: "…", failed: "!", stopped: "■", paused: "‖", pending: "" };

/** The nine stages as a connected flow. Reads status from state.run.stages only. */
export function renderStepper(state) {
  const { stages, status, gate } = state.run;
  return h(
    "ol",
    { class: "stepper", "aria-label": "Pipeline stages" },
    STAGES.map((stage, index) => {
      const s = stages[stage.key];
      const shown = gate?.stageKey === stage.key ? "paused" : s.status;
      const elapsed = s.startedAt ? formatDuration((s.endedAt ?? Date.now()) - s.startedAt) : "";
      return h(
        "li",
        { class: `step step-${shown}`, "aria-current": shown === "running" ? "step" : undefined },
        h("span", { class: "step-dot", "aria-hidden": "true" }, ICON[shown] || String(index + 1)),
        h("span", { class: "step-label" }, stage.label),
        h("span", { class: "step-meta" }, s.error ? "Failed" : shown === "pending" ? stage.blurb : [s.detail, elapsed && ` · ${elapsed}`].filter(Boolean).join("")),
      );
    }),
  );
}
