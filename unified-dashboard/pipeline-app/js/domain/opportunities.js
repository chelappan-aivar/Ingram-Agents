/** Intake opportunities: ranking, selection and the flat view used by the shared Opportunities table. */
import { clean } from "./text.js";

const URGENCY_RANK = { high: 3, medium: 2, low: 1, unknown: 0 };

/** { customer_name: "Acme", ... } from the opportunity's fields[] list. */
export function fieldMap(opportunity) {
  const map = {};
  for (const f of opportunity?.fields ?? []) {
    const value = clean(f?.value);
    if (f?.field && value && !(f.field in map)) map[f.field] = value;
  }
  return map;
}

export const opportunityId = (opportunity) =>
  clean(opportunity?.dynamics_id) || clean(opportunity?.thread_key) || "unknown";

export const customerOf = (opportunity) =>
  fieldMap(opportunity).customer_name || clean(opportunity?.customer_name) || "Unknown customer";

export const titleOf = (opportunity) =>
  fieldMap(opportunity).opportunity_title || clean(opportunity?.opportunity_title) || clean(opportunity?.summary).slice(0, 80) || "Untitled opportunity";

/** Best first: matched across sources, more linked documents, higher urgency, more verified fields. */
export function rankOpportunities(list) {
  return [...(list ?? [])]
    .filter((o) => o && o.is_opportunity !== false)
    .sort(
      (a, b) =>
        Number(!!b.matched) - Number(!!a.matched) ||
        (b.sources?.length ?? 0) - (a.sources?.length ?? 0) ||
        (URGENCY_RANK[b.urgency] ?? 0) - (URGENCY_RANK[a.urgency] ?? 0) ||
        (b.verified_count ?? 0) - (a.verified_count ?? 0),
    );
}

export function toRow(opportunity) {
  const fit = opportunity.programme_fit ?? {};
  return {
    key: opportunity.thread_key,
    id: opportunityId(opportunity),
    customer: customerOf(opportunity),
    title: titleOf(opportunity),
    type: fieldMap(opportunity).opportunity_type || "",
    urgency: opportunity.urgency || "unknown",
    sources: (opportunity.source_types ?? []).join(", "),
    matched: !!opportunity.matched,
    fields: opportunity.field_count ?? opportunity.fields?.length ?? 0,
    verified: opportunity.verified_count ?? 0,
    gaps: opportunity.gaps?.length ?? 0,
    fit: Object.entries(fit)
      .filter(([, v]) => v && v.fit && v.fit !== "not_applicable")
      .map(([name, v]) => `${name}: ${v.fit}`)
      .join(" · "),
  };
}

/** The opportunity the pipeline should work on: the user's pick, else the top-ranked one. */
export function chooseOpportunity(list, selectedKey) {
  const ranked = rankOpportunities(list);
  return ranked.find((o) => o.thread_key === selectedKey) ?? ranked[0] ?? null;
}
