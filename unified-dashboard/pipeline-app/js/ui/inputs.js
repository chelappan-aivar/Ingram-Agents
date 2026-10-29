import { h } from "./dom.js";

/**
 * The inputs card is built once and kept in the DOM (so typing and file pickers are never reset by a
 * re-render). `sync` pushes store changes back into it, which is how Exit / Clear empty the form.
 */
export function createInputsCard(store, { connectOutlook, disconnectOutlook } = {}) {
  const set = (patch) => store.update("inputs", patch);
  const bindings = [];

  const field = (label, control, hint) => h("label", { class: "field" }, h("span", { class: "field-label" }, label), control, hint ? h("span", { class: "field-hint" }, hint) : null);

  const textarea = (key, { rows = 4, placeholder = "", maxLength } = {}) => {
    const el = h("textarea", { rows, placeholder, maxLength, spellcheck: true, onInput: () => set({ [key]: el.value }) });
    bindings.push((s) => { if (el.value !== s.inputs[key]) el.value = s.inputs[key]; });
    return el;
  };
  const checkbox = (key, label, hint) => {
    const el = h("input", { type: "checkbox", onChange: () => set({ [key]: el.checked }) });
    bindings.push((s) => { el.checked = s.inputs[key]; });
    return h("label", { class: "check" }, el, h("span", {}, h("strong", {}, label), hint ? h("span", { class: "field-hint" }, hint) : null));
  };

  const outlookBox = h("div", { class: "source-box" });
  const useOutlook = checkbox("useOutlook", "Include Outlook in this run");
  const outlookDays = h("select", { onChange: () => set({ outlookDays: Number(outlookDays.value) }) }, [1, 7, 30].map((d) => h("option", { value: String(d) }, `Last ${d} day${d === 1 ? "" : "s"}`)));
  bindings.push((s) => { outlookDays.value = String(s.inputs.outlookDays); });
  let shown = "";
  bindings.push((s) => {
    const o = s.outlook;
    const key = JSON.stringify(o);
    if (key === shown) return;
    shown = key;
    outlookBox.replaceChildren(
      h("strong", {}, "Outlook inbox"),
      o.user
        ? h("div", {}, h("p", { class: "field-hint" }, `Connected as ${o.user.email || o.user.name}. Reads only your own mail.`),
            useOutlook, field("Mail window", outlookDays),
            h("button", { type: "button", class: "btn", onClick: () => disconnectOutlook?.() }, "Disconnect"))
        : h("div", {}, o.code ? h("p", {}, "Open ", h("a", { href: o.uri, target: "_blank", rel: "noopener noreferrer" }, o.uri), " and enter the code ", h("strong", {}, o.code), ".") : null,
            o.message ? h("p", { class: "field-hint", role: "status" }, o.message) : null,
            h("button", { type: "button", class: "btn", disabled: o.busy, onClick: () => connectOutlook?.() }, o.busy ? "Waiting for sign-in…" : "Connect Outlook")));
  });
  const dynamicsLimit = h("select", { onChange: () => set({ dynamicsLimit: Number(dynamicsLimit.value) }) }, [10, 25, 50].map((n) => h("option", { value: String(n) }, `Up to ${n} opportunities`)));
  bindings.push((s) => { dynamicsLimit.value = String(s.inputs.dynamicsLimit); });

  const card = h(
    "div",
    { class: "card inputs" },
    h("h2", { class: "card-title" }, "Inputs"),
    h("p", { class: "card-sub" }, "Choose where the pipeline starts: Outlook, Dynamics 365, or both. Nothing is kept once you exit the pipeline."),
    h("fieldset", {}, h("legend", {}, "1 · Intake sources"),
      outlookBox,
      h("div", { class: "source-box" }, checkbox("useDynamics", "Dynamics 365", "Reads your open opportunities."), field("How many", dynamicsLimit))),
    h("details", { class: "advanced" }, h("summary", {}, "Run one agent on its own"),
      h("p", { class: "field-hint" }, "Leave these blank to use the previous agent's output."),
      field("Discovery: extra documents or notes", textarea("discoveryText", { rows: 3 })),
      field("Architecture: SOW or requirements text", textarea("architectureText", { rows: 3 })),
      field("Cost: what to price", textarea("costText", { rows: 3, placeholder: "e.g. 4 × m5.xlarge in eu-west-1, 2 TB gp3, RDS PostgreSQL Multi-AZ…" }))),
    h("div", { class: "options" },
      checkbox("autoSelect", "Use the top-ranked opportunity", "Untick to choose when Intake finds several."),
      checkbox("pauseOnHigh", "Pause on high findings", "Stop for your decision if the SOW draft has high Well-Architected findings.")),
  );

  return { node: card, sync: (state) => bindings.forEach((fn) => fn(state)) };
}
