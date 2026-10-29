/** Picks a SOW template for an opportunity by matching its type/title words against template labels. */
import { fieldMap, titleOf } from "./opportunities.js";

const WORDS = /[a-z0-9]{3,}/g;
const STOP = new Set(["and", "the", "for", "with", "aws", "sow", "full", "template"]);

export function suggestTemplate(templates, opportunity) {
  if (!templates?.length) return null;
  if (!opportunity) return null;
  const f = fieldMap(opportunity);
  const wanted = new Set(`${f.opportunity_type ?? ""} ${titleOf(opportunity)} ${opportunity.summary ?? ""}`.toLowerCase().match(WORDS)?.filter((w) => !STOP.has(w)) ?? []);
  let best = null;
  let bestScore = 0;
  for (const t of templates) {
    const words = `${t.label ?? ""} ${t.category ?? ""} ${t.filename ?? ""}`.toLowerCase().match(WORDS)?.filter((w) => !STOP.has(w)) ?? [];
    const score = words.filter((w) => wanted.has(w)).length;
    if (score > bestScore) { best = t; bestScore = score; }
  }
  return best ?? null;
}
