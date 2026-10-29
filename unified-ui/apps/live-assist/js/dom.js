// Small DOM helpers. Conversation text is untrusted, so elements are built with textContent only.

export const $ = (id) => document.getElementById(id);

export function el(tag, text, className, attrs) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  if (attrs) for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

let toastTimer = null;
export function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
}

export function showError(message) {
  const node = $("start-error");
  node.textContent = message || "";
  node.hidden = !message;
}

export function clip(text, max) {
  const t = String(text ?? "").replace(/\s+/g, " ").trim();
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
}
