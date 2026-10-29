// Small DOM helpers shared by app.js, overview.js and report.js. All API text goes in as text nodes, never as HTML.
(() => {
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === false || v === null || v === undefined) continue;
      if (k === "class") node.className = v; else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat(Infinity)) {
      if (child !== null && child !== undefined && child !== "" && child !== false) {
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
      }
    }
    return node;
  }
  const fill = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity)
    .filter((k) => k !== null && k !== undefined && k !== false && k !== ""));
  const usd = (n) => `$${Math.round(n).toLocaleString("en-US")}`;
  const day = (iso) => {
    if (!iso) return "";
    const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00` : iso);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString([], { dateStyle: "medium" });
  };
  // Only https links are rendered as links (the values come from other systems).
  const safeLink = (url, label, cls = "") => (typeof url === "string" && url.startsWith("https://")
    ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: cls }, label) : null);
  window.DiscoveryDom = { el, fill, usd, day, safeLink };
})();
