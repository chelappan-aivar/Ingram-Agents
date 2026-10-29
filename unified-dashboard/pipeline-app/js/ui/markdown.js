/**
 * A deliberately small Markdown renderer for SOW/ADR previews. It builds DOM nodes (no innerHTML)
 * and only turns http(s) URLs into links, so untrusted model output cannot inject markup or
 * javascript: URLs.
 */
import { h } from "./dom.js";

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s<>)\]]+)/g;

function inline(text) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const token = m[0];
    if (token.startsWith("**")) out.push(h("strong", {}, token.slice(2, -2)));
    else if (token.startsWith("`")) out.push(h("code", {}, token.slice(1, -1)));
    else if (token.startsWith("[")) {
      const [, label, href] = /^\[([^\]]+)\]\((.+)\)$/.exec(token);
      out.push(link(href, label));
    } else out.push(link(token, token));
    last = m.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const link = (href, label) => h("a", { href, target: "_blank", rel: "noopener noreferrer" }, label);

export function renderMarkdown(markdown) {
  const root = h("div", { class: "md" });
  const lines = String(markdown ?? "").replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  let paragraph = [];
  const flush = () => { if (paragraph.length) { root.append(h("p", {}, inline(paragraph.join(" ")))); paragraph = []; } };

  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      flush();
      const code = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) code.push(lines[i]);
      root.append(h("pre", { class: "md-pre" }, code.join("\n")));
      i++;
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) { flush(); root.append(h(`h${Math.min(6, heading[1].length + 2)}`, {}, inline(heading[2]))); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flush();
      const rows = [];
      for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) rows.push(lines[i].trim().slice(1, -1).split("|").map((c) => c.trim()));
      const body = rows.filter((r) => !r.every((c) => /^:?-{2,}:?$/.test(c)));
      const [head, ...rest] = body;
      root.append(h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, head.map((c) => h("th", {}, inline(c))))), h("tbody", {}, rest.map((r) => h("tr", {}, r.map((c) => h("td", {}, inline(c)))))))));
      continue;
    }
    if (/^\s*([-*]|\d+[.)])\s+/.test(line)) {
      flush();
      const ordered = /^\s*\d/.test(line);
      const items = [];
      for (; i < lines.length && /^\s*([-*]|\d+[.)])\s+/.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*]|\d+[.)])\s+/, ""));
      root.append(h(ordered ? "ol" : "ul", { class: "bullets" }, items.map((t) => h("li", {}, inline(t)))));
      continue;
    }
    if (!line.trim()) { flush(); i++; continue; }
    paragraph.push(line.trim());
    i++;
  }
  flush();
  return root;
}
