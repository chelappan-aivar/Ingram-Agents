// The deployed SOW exporter writes the Table of Contents as an empty Word field ("Right-click to update...") and does not
// ask Word to refresh it. This fills the field with the document's own headings and sets Word's update-on-open flag,
// the same structure the exporter's current source writes. Needs the vendored JSZip (loaded as a global).
const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const decode = (t) => t.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
const PLACEHOLDER = /<w:p>(?:<w:pPr>.*?<\/w:pPr>)?<w:r><w:fldChar w:fldCharType="begin"\/><w:instrText[^>]*>\s*TOC[^<]*<\/w:instrText><w:fldChar w:fldCharType="separate"\/><w:t[^>]*>[^<]*<\/w:t><w:fldChar w:fldCharType="end"\/><\/w:r><\/w:p>/;

export function headingsOf(documentXml) {
  const out = [];
  for (const m of documentXml.matchAll(/<w:p>(?:(?!<\/w:p>).)*?<w:pStyle w:val="Heading([1-3])"\/>(?:(?!<\/w:p>).)*?<\/w:p>/gs)) {
    const text = [...m[0].matchAll(/<w:t[^>]*>([^<]*)<\/w:t>/g)].map((t) => decode(t[1])).join("").trim();
    if (text) out.push({ level: Number(m[1]), text });
  }
  return out;
}

export function tocParagraphs(headings) {
  const field = (type) => `<w:r><w:fldChar w:fldCharType="${type}"/></w:r>`;
  return headings.map((h, i) =>
    `<w:p><w:pPr><w:spacing w:after="60"/><w:ind w:left="${360 * (h.level - 1)}"/></w:pPr>`
    + (i === 0 ? `${field("begin")}<w:r><w:instrText xml:space="preserve">TOC \\o "1-3" \\h \\z \\u</w:instrText></w:r>${field("separate")}` : "")
    + `<w:r><w:rPr>${h.level === 1 ? "<w:b/>" : ""}</w:rPr><w:t xml:space="preserve">${esc(h.text)}</w:t></w:r>`
    + (i === headings.length - 1 ? field("end") : "") + "</w:p>").join("");
}

// Returns {blob, filled}. Never throws on an unexpected layout: it returns the file unchanged with filled = false.
export async function fillToc(arrayBuffer) {
  const zip = await globalThis.JSZip.loadAsync(arrayBuffer);
  const docFile = zip.file("word/document.xml");
  if (!docFile) return { buffer: arrayBuffer, filled: false };
  let xml = await docFile.async("string");
  const heads = headingsOf(xml);
  if (!heads.length || !PLACEHOLDER.test(xml)) return { buffer: arrayBuffer, filled: false };
  xml = xml.replace(PLACEHOLDER, () => tocParagraphs(heads));
  zip.file("word/document.xml", xml);
  const settingsFile = zip.file("word/settings.xml");
  if (settingsFile) {
    let st = await settingsFile.async("string");
    if (!st.includes("<w:updateFields")) {
      const flag = '<w:updateFields w:val="true"/>';
      const anchor = ["<w:hdrShapeDefaults", "<w:footnotePr", "<w:endnotePr", "<w:compat", "<w:docVars", "<w:rsids", "<m:mathPr", "<w:themeFontLang", "<w:clrSchemeMapping"].find((a) => st.includes(a));
      st = anchor ? st.replace(anchor, flag + anchor) : st.replace("</w:settings>", flag + "</w:settings>");
      zip.file("word/settings.xml", st);
    }
  }
  return { buffer: await zip.generateAsync({ type: "arraybuffer", compression: "DEFLATE" }), filled: true, entries: heads.length };
}
