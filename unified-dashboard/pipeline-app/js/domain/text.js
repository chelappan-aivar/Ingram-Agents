/** Small, pure text helpers shared by the mappers. */

export const clean = (value) => (value == null ? "" : String(value).replace(/\s+/g, " ").trim());

export const truncate = (value, max) => {
  const text = clean(value);
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
};

/** Split free text such as "a; b\n- c" into trimmed, non-empty items. */
export function splitList(value) {
  if (Array.isArray(value)) return value.map(clean).filter(Boolean);
  return String(value ?? "")
    .split(/\r?\n|;|•|\u2022|(?:^|\s)-\s(?=\S)/)
    .map((part) => clean(part.replace(/^[-*\d.)\s]+/, "")))
    .filter(Boolean);
}

export const unique = (items) => [...new Set(items.filter(Boolean))];

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const isEmail = (value) => EMAIL.test(clean(value));

/** Split long text into chunks no longer than `max`, preferring sentence boundaries. */
export function chunk(text, max) {
  const out = [];
  let rest = clean(text);
  while (rest.length > max) {
    const window = rest.slice(0, max);
    const cut = Math.max(window.lastIndexOf(". "), window.lastIndexOf("; "), window.lastIndexOf(", "));
    const at = cut > max * 0.4 ? cut + 1 : max;
    out.push(rest.slice(0, at).trim());
    rest = rest.slice(at).trim();
  }
  if (rest) out.push(rest);
  return out;
}

/** A name that satisfies Discovery's engagement rule: ^[A-Za-z0-9][A-Za-z0-9._ -]{0,100}$ */
export function engagementName(customer, now = new Date()) {
  const stamp = now.toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
  const base = clean(customer).replace(/[^A-Za-z0-9._ -]/g, "").replace(/^[^A-Za-z0-9]+/, "").slice(0, 60).trim();
  return `${base || "Pipeline"} ${stamp}`;
}

export const formatDuration = (ms) => {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  return m ? `${m}m ${String(total % 60).padStart(2, "0")}s` : `${total}s`;
};
