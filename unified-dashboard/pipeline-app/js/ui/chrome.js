/** Run banner, review gate, final result card and the activity log. */
import { buildBundle, openItems, verdict } from "../domain/summary.js";
import { formatDuration } from "../domain/text.js";
import { downloadBlob } from "../core/files.js";
import { badge, button, h, list } from "./dom.js";
import { STAGES } from "../pipeline/state.js";

const label = (key) => STAGES.find((s) => s.key === key)?.label ?? key;

export function renderBanner(state, actions) {
  const { status, gate, error, stages } = state.run;
  if (gate) {
    return h("div", { class: "banner banner-warning", role: "alert" },
      h("div", {}, h("strong", {}, gate.title), h("p", {}, gate.message)),
      h("div", { class: "toolbar-actions" },
        gate.kind === "select" ? button("Continue with the selected opportunity", { variant: "primary", onClick: () => actions.choose("continue") }) : button("Continue anyway", { variant: "primary", onClick: () => actions.choose("continue") }),
        button("Stop", { onClick: () => actions.choose("stop") })));
  }
  if (status === "failed") {
    const failed = Object.entries(stages).find(([, s]) => s.status === "failed");
    return h("div", { class: "banner banner-danger", role: "alert" },
      h("div", {}, h("strong", {}, failed ? `${label(failed[0])} stopped the pipeline` : "The pipeline stopped"), h("p", {}, error || failed?.[1].error || "")),
      h("div", { class: "toolbar-actions" }, button("Resume from here", { variant: "primary", onClick: actions.resume }), button("Exit pipeline", { onClick: actions.exit })));
  }
  if (status === "stopped") return h("div", { class: "banner banner-warning" }, h("div", {}, h("strong", {}, "Stopped.")), button("Resume", { variant: "primary", onClick: actions.resume }));
  return null;
}

export function renderResult(state) {
  if (state.run.stages.final_review.status !== "done") return null;
  const items = openItems(state);
  const ready = verdict(state) === "ready";
  const elapsed = state.run.startedAt && state.run.finishedAt ? formatDuration(state.run.finishedAt - state.run.startedAt) : "";
  return h("section", { class: `card result ${ready ? "result-ready" : "result-review"}` },
    h("div", { class: "toolbar" },
      h("div", {}, h("h2", { class: "card-title" }, "Final SOW delivered ", badge(ready ? "Ready for review" : "Needs attention", ready ? "success" : "warning")), h("p", { class: "muted" }, elapsed ? `Pipeline finished in ${elapsed}.` : "")),
      h("div", { class: "toolbar-actions" },
        state.sow.docx?.download_url ? h("a", { class: "btn btn-primary", href: state.sow.docx.download_url, rel: "noopener noreferrer" }, "Download Word document") : null,
        button("Download Markdown", { onClick: () => downloadBlob("statement-of-work.md", state.sow.combined.markdown, "text/markdown") }),
        button("Download all outputs (JSON)", { onClick: () => downloadBlob("pipeline-outputs.json", JSON.stringify(buildBundle(state), null, 2), "application/json") }))),
    items.length ? [h("h3", { class: "block-title" }, `${items.length} item${items.length === 1 ? "" : "s"} to confirm before this goes to the customer`), list(items.map((i) => `${i.source}: ${i.text}`))] : h("p", {}, "No open items."),
    h("p", { class: "muted" }, "Download what you need before you exit: leaving the pipeline clears everything on this page."));
}

export function renderLog(state) {
  const rows = state.run.log;
  if (!rows.length) return null;
  const fmt = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return h("details", { class: "doc log" }, h("summary", {}, `Activity log (${rows.length})`),
    h("ol", { class: "log-list" }, rows.slice().reverse().map((r) => h("li", { class: `log-${r.level}` }, h("time", {}, fmt(r.at)), " ", r.message))));
}
