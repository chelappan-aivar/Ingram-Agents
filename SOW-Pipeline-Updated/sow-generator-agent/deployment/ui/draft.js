// SOW draft model for the AI editor: markdown <-> blocks, the editor's sections_map, and applying proposals.
// Pure functions (no DOM); used by app.js and testable with node (module.exports).
//
// Blocks follow the .docx export: every non-empty line is one block, except tables (one block per table) and
// ``` fences. Block ids ("b<n>") are deterministic for the same markdown, so the proposals the editor returns
// (which carry the block_id / table tag / heading it read through its tools, see
// agent/sow_generator/agents/tools/proposals.py) map back onto the snapshot that was sent.
//
// Proposal kinds and how they are applied:
//   edit_proposal                    {block_id, replace, heading, find, before_block_id, table_tag, old_table}
//       text: replace the block's line(s); "TABLE_JSON:[[...],...]": replace the whole markdown table
//   insert_proposal                  {after_heading, markdown}   insert after that section (and its subsections)
//   insert_after_paragraph_proposal  {block_id, markdown}        insert after that block
//   delete_proposal                  {heading}                    delete the section and its subsections
// Each resolved proposal keeps the exact text it targets; apply() finds that text in the current draft, so
// proposals still apply after the user applied another one or typed elsewhere. If the text is gone, the
// proposal is shown as a suggestion only.
(function (root) {
  const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
  const BULLET = /^(\s*(?:[-*+]|\d+\.)\s+)(.*)$/;
  const IMAGE = /^!\[([^\]]*)\]\(([^)]+)\)$/;
  const TABLE_PREFIX = "TABLE_JSON:";
  const isRule = (line) => /^\s*\|?[\s:\-|]+\|?\s*$/.test(line) && line.includes("-");
  const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const norm = (s) => String(s || "").replace(/^#+\s*/, "").replace(/\s+/g, " ").trim().toLowerCase();

  function parse(markdown) {
    const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    const sections = [];
    let section = null;
    let n = 0;
    let t = 0;
    const add = (b) => { b.id = `b${++n}`; b.section = section; blocks.push(b); if (section) section.blocks.push(b); return b; };
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const trimmed = line.trim();
      if (!trimmed) continue;
      const h = HEADING.exec(trimmed);
      if (h) {
        section = { heading: h[2], level: h[1].length, start: i, blocks: [], tables: [] };
        sections.push(section);
        add({ kind: "heading", start: i, end: i, text: h[2], prefix: `${h[1]} ` });
        continue;
      }
      if (trimmed.startsWith("```")) {
        let j = i + 1;
        while (j < lines.length && !lines[j].trim().startsWith("```")) j += 1;
        add({ kind: "code", start: i, end: Math.min(j, lines.length - 1), text: lines.slice(i + 1, j).join("\n") });
        i = j;
        continue;
      }
      if (trimmed.startsWith("|") && i + 1 < lines.length && isRule(lines[i + 1])) {
        let j = i + 2;
        const rows = [cells(line)];
        while (j < lines.length && lines[j].trim().startsWith("|")) { rows.push(cells(lines[j])); j += 1; }
        const before = section ? [...section.blocks].reverse().find((b) => b.kind !== "table") : null;
        const block = add({ kind: "table", start: i, end: j - 1, text: rows.map((r) => r.join("\t")).join("\n"),
          rows, tag: `t${++t}`, beforeId: before ? before.id : "" });
        if (section) section.tables.push(block);
        i = j - 1;
        continue;
      }
      const im = IMAGE.exec(trimmed);
      if (im) { add({ kind: "image", start: i, end: i, text: trimmed }); continue; }
      const b = BULLET.exec(line);
      if (b) { add({ kind: "bullet", start: i, end: i, text: b[2].trim(), prefix: b[1] }); continue; }
      add({ kind: "para", start: i, end: i, text: trimmed });
    }
    for (let s = 0; s < sections.length; s += 1) {            // a section ends where a heading of the same or higher level starts
      const sec = sections[s];
      let end = lines.length - 1;
      for (let k = s + 1; k < sections.length; k += 1) {
        if (sections[k].level <= sec.level) { end = sections[k].start - 1; break; }
      }
      sec.end = end;
    }
    return { lines, blocks, sections, byId: new Map(blocks.map((b) => [b.id, b])) };
  }

  // The editor contract (schemas/editing.py AgentEditRequest.sections_map): heading -> {paragraphs, tables}.
  function sectionsMap(model) {
    const map = {};
    const outline = [];
    const keyFor = (heading) => {
      let key = heading || "(untitled)";
      for (let k = 2; Object.prototype.hasOwnProperty.call(map, key); k += 1) key = `${heading} (${k})`;
      return key;
    };
    const pre = model.blocks.filter((b) => !b.section);
    if (pre.length) {
      map["(before the first heading)"] = { paragraphs: pre.filter((b) => b.kind !== "table").map(para), tables: [] };
    }
    for (const sec of model.sections) {
      const key = keyFor(sec.heading);
      outline.push(key);
      map[key] = {
        paragraphs: sec.blocks.filter((b) => b.kind !== "table" && b.kind !== "heading").map(para),
        tables: sec.tables.map((tb) => ({ tag: tb.tag, before_para_id: tb.beforeId, data: tb.rows })),
      };
    }
    return { sections_map: map, document_outline: outline };
  }
  function para(b) {
    return { block_id: b.id, text: b.text, style: b.kind === "bullet" ? "listParagraph" : b.kind === "image" ? "image" : "normal" };
  }

  const linesText = (model, start, end) => model.lines.slice(start, end + 1).join("\n");
  function findSection(model, heading) {
    const want = norm(heading);
    return model.sections.find((s) => norm(s.heading) === want)
      || model.sections.find((s) => norm(s.heading).includes(want) && want.length > 3) || null;
  }
  function tableMarkdown(rows) {
    const width = Math.max(1, ...rows.map((r) => r.length));
    const fmt = (r) => `| ${Array.from({ length: width }, (_, c) => String(r[c] ?? "").replace(/\|/g, "/").replace(/\n/g, " ")).join(" | ")} |`;
    return [fmt(rows[0] || []), `| ${Array(width).fill("---").join(" | ")} |`, ...rows.slice(1).map(fmt)].join("\n");
  }
  function parseTable(replace) {
    try {
      const rows = JSON.parse(replace.slice(replace.indexOf(TABLE_PREFIX) + TABLE_PREFIX.length));
      return Array.isArray(rows) && rows.length && rows.every(Array.isArray) ? rows : null;
    } catch { return null; }
  }
  function findTable(model, p, rows) {
    const tables = model.blocks.filter((b) => b.kind === "table");
    const byId = p.block_id && model.byId.get(p.block_id);
    return (p.table_tag && tables.find((tb) => tb.tag === p.table_tag))
      || (p.before_block_id && tables.find((tb) => tb.beforeId === p.before_block_id))
      || (byId && byId.kind === "table" ? byId : null)
      || (rows && tables.find((tb) => norm(tb.rows[0].join("|")) === norm(rows[0].join("|"))))
      || null;
  }

  // -> {ok, op: "replace" | "insert_after" | "delete", target, text, label, reason}
  function resolve(model, p) {
    const label = { edit_proposal: "Edit", insert_proposal: "New section", insert_after_paragraph_proposal: "Insert",
      delete_proposal: "Delete section" }[p.type] || p.type;
    const fail = (reason, text = "") => ({ ok: false, label, reason, text: text || p.replace || p.markdown || "" });
    if (p.type === "edit_proposal") {
      const replace = String(p.replace || "");
      if (replace.startsWith(TABLE_PREFIX)) {
        const rows = parseTable(replace);
        if (!rows) return fail("The proposed table could not be read.");
        const table = findTable(model, p, rows);
        if (!table) return fail("The table to change was not found in the draft.", tableMarkdown(rows));
        return { ok: true, op: "replace", label: "Edit table", target: linesText(model, table.start, table.end),
          text: tableMarkdown(rows) };
      }
      const block = model.byId.get(p.block_id);
      if (!block) return fail("The paragraph to change was not found in the draft.");
      if (block.kind === "table") return fail("A table edit must use the TABLE_JSON format.");
      let text = replace.trim();
      if (!text) return { ok: true, op: "delete", label: "Remove text", target: linesText(model, block.start, block.end), text: "" };
      if (block.kind === "bullet" && !BULLET.test(text)) text = block.prefix + text;
      if (block.kind === "heading" && !HEADING.test(text)) text = block.prefix + text;
      return { ok: true, op: "replace", label, target: linesText(model, block.start, block.end), text };
    }
    if (p.type === "insert_after_paragraph_proposal") {
      const block = model.byId.get(p.block_id);
      if (!block) return fail("The paragraph to insert after was not found.");
      return { ok: true, op: "insert_after", label, target: linesText(model, block.start, block.end),
        text: String(p.markdown || "").trim() };
    }
    if (p.type === "insert_proposal") {
      const sec = findSection(model, p.after_heading);
      if (!sec) return fail(`The section "${p.after_heading || ""}" was not found.`);
      return { ok: true, op: "insert_after", label, heading: sec.heading,
        target: linesText(model, sec.start, sec.end).replace(/\n+$/, ""), text: String(p.markdown || "").trim() };
    }
    if (p.type === "delete_proposal") {
      const sec = findSection(model, p.heading);
      if (!sec) return fail(`The section "${p.heading || ""}" was not found.`);
      return { ok: true, op: "delete", label, heading: sec.heading,
        target: linesText(model, sec.start, sec.end).replace(/\n+$/, ""), text: "" };
    }
    return fail("This kind of change cannot be applied automatically.");
  }

  // Apply one resolved proposal to the current markdown; -> new markdown, or null when the target is gone.
  // Block targets are found by their exact text; section targets (insert / delete section) by their heading.
  function apply(markdown, r) {
    if (!r || !r.ok || !r.target) return null;
    const md = String(markdown || "").replace(/\r\n?/g, "\n");
    if (r.heading) {                    // section-sized targets: re-find the section by its heading in the current draft
      const model = parse(md);
      const sec = findSection(model, r.heading);
      if (!sec) return null;
      r = { ...r, target: linesText(model, sec.start, sec.end).replace(/\n+$/, "") };
    }
    const at = md.indexOf(r.target);
    if (at < 0) return null;
    const before = md.slice(0, at);
    const after = md.slice(at + r.target.length);
    if (r.op === "replace") return before + r.text + after;
    if (r.op === "delete") return (before.replace(/\n+$/, "\n") + after.replace(/^\n+/, "\n")).replace(/^\n/, "");
    if (r.op === "insert_after") return `${before}${r.target}\n\n${r.text}\n${after.startsWith("\n") ? "" : "\n"}${after}`;
    return null;
  }

  const api = { parse, sectionsMap, resolve, apply, tableMarkdown };
  root.SowDraft = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
