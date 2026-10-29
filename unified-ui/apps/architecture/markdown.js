// Minimal, safe Markdown -> DOM renderer for the agent's ADR and usage notes.
// Builds nodes with textContent only (no innerHTML), so model output can never inject markup.
// Supports: # headings, paragraphs, - / * / 1. lists, | tables |, ``` code fences, > quotes, **bold**, *italic*, `code`.
(() => {
  function inline(parent, text) {
    const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
    let last = 0;
    for (const m of text.matchAll(re)) {
      if (m.index > last) parent.append(document.createTextNode(text.slice(last, m.index)));
      const tok = m[0];
      let node;
      if (tok.startsWith("**")) { node = document.createElement("strong"); node.textContent = tok.slice(2, -2); }
      else if (tok.startsWith("`")) { node = document.createElement("code"); node.textContent = tok.slice(1, -1); }
      else { node = document.createElement("em"); node.textContent = tok.slice(1, -1); }
      parent.append(node);
      last = m.index + tok.length;
    }
    if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
    return parent;
  }

  const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const isRule = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line);

  function render(md) {
    const root = document.createElement("div");
    root.className = "md";
    const lines = String(md || "").replace(/\r\n?/g, "\n").split("\n");
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i += 1; continue; }

      if (line.startsWith("```")) {                                   // code fence
        const pre = document.createElement("pre");
        const buf = [];
        i += 1;
        while (i < lines.length && !lines[i].startsWith("```")) { buf.push(lines[i]); i += 1; }
        i += 1;
        pre.textContent = buf.join("\n");
        root.append(pre);
        continue;
      }
      const h = /^(#{1,6})\s+(.*)$/.exec(line);                         // heading
      if (h) {
        root.append(inline(document.createElement(`h${Math.min(6, h[1].length + 1)}`), h[2]));
        i += 1;
        continue;
      }
      if (line.trim().startsWith("|") && i + 1 < lines.length && isRule(lines[i + 1])) {   // table
        const wrap = document.createElement("div");
        wrap.className = "table-wrap";
        const table = document.createElement("table");
        const thead = table.createTHead().insertRow();
        for (const c of cells(line)) inline(thead.appendChild(document.createElement("th")), c);
        const tbody = table.createTBody();
        i += 2;
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          const row = tbody.insertRow();
          for (const c of cells(lines[i])) inline(row.insertCell(), c);
          i += 1;
        }
        wrap.append(table);
        root.append(wrap);
        continue;
      }
      if (/^\s*([-*]|\d+\.)\s+/.test(line)) {                          // list (nesting flattened)
        const ordered = /^\s*\d+\./.test(line);
        const list = document.createElement(ordered ? "ol" : "ul");
        while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
          inline(list.appendChild(document.createElement("li")), lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
          i += 1;
        }
        root.append(list);
        continue;
      }
      if (line.startsWith(">")) {                                      // quote
        const q = document.createElement("blockquote");
        const buf = [];
        while (i < lines.length && lines[i].startsWith(">")) { buf.push(lines[i].replace(/^>\s?/, "")); i += 1; }
        inline(q, buf.join(" "));
        root.append(q);
        continue;
      }
      const buf = [];                                                  // paragraph
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|>|\s*([-*]|\d+\.)\s+)/.test(lines[i])
             && !(lines[i].trim().startsWith("|") && i + 1 < lines.length && isRule(lines[i + 1]))) {
        buf.push(lines[i].trim());
        i += 1;
      }
      if (!buf.length) { buf.push(line.trim()); i += 1; }              // never stall on an odd line
      root.append(inline(document.createElement("p"), buf.join(" ")));
    }
    return root;
  }

  window.renderMarkdown = render;
})();
