/**
 * Minimal DOM builder. Text is always inserted as text nodes, never as HTML, so model or API output
 * cannot inject markup.
 */
export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key in node && key !== "list" && key !== "form") node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const replace = (node, ...children) => {
  node.replaceChildren();
  return append(node, children);
};

const TONES = { done: "success", running: "primary", paused: "warning", failed: "danger", stopped: "warning", pending: "neutral", idle: "neutral" };
export const badge = (label, tone = "neutral") => h("span", { class: `badge badge-${tone}` }, label);
export const statusBadge = (status) => badge({ pending: "Not run", idle: "Idle" }[status] ?? status[0].toUpperCase() + status.slice(1), TONES[status] ?? "neutral");

export const empty = (message) => h("div", { class: "empty" }, message);

/** Simple data table. `columns`: [{ label, cell: row => Node|string, class? }]. */
export function table(columns, rows, { rowClass, onRow, caption } = {}) {
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table" },
      caption ? h("caption", { class: "sr-only" }, caption) : null,
      h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col", class: c.class }, c.label)))),
      h(
        "tbody",
        {},
        rows.map((row) =>
          h("tr", { class: rowClass?.(row), onClick: onRow ? () => onRow(row) : undefined }, columns.map((c) => h("td", { class: c.class }, c.cell(row)))),
        ),
      ),
    ),
  );
}

export const list = (items, cls = "bullets") => (items.length ? h("ul", { class: cls }, items.map((i) => h("li", {}, i))) : null);

export function section(title, ...body) {
  return h("section", { class: "block" }, h("h3", { class: "block-title" }, title), ...body);
}

export function button(label, { variant = "secondary", onClick, disabled = false, title, type = "button" } = {}) {
  return h("button", { type, class: `btn btn-${variant}`, onClick, disabled, title }, label);
}
