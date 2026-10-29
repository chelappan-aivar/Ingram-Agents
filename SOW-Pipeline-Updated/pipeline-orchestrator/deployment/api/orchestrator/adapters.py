"""Translations between the three agents' native shapes.

Each agent was built standalone, so none of them speaks the others' contract: the architecture
agent emits a `Design` plus `usage.json`, the cost agent takes free text and returns a saved
calculator estimate, and the SOW agent consumes `UpstreamInputs v1`. These functions are the
seams. They are pure and separately tested, because this is where a quiet mismatch would show
up as a plausible-looking but wrong SOW.

Three translations, in pipeline order:

  `architecture_request`  SOW + scope           -> the architecture agent's payload
  `cost_requirements`     design + usage.json   -> the free-text description the cost agent prices
  `upstream_with_results` architecture + cost   -> the UpstreamInputs blocks the final SOW review reads
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterable, Optional

# The cost agent rejects anything outside this range (see its main.py).
COST_MIN_CHARS = 30
COST_MAX_CHARS = 20_000

MAX_SERVICES = 60
MAX_ASSUMPTIONS = 40
MAX_NFRS = 40
MAX_DIAGRAMS = 4


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


# ---------------------------------------------------------------- SOW -> architecture
def architecture_request(sow_markdown: str, scope: Optional[dict] = None,
                         srs_filename: str = "sow.md") -> dict[str, Any]:
    """The architecture agent's payload for a SOW-first run.

    The SOW is passed as the SRS. That is the right call rather than a shortcut: in this flow
    the SOW *is* the agreed requirements document, and it already carries the volumes, the
    constraints and the service decisions the architect made. The `scope` block rides alongside
    as the binding constraint, so the design is held to the agreed split rather than to whatever
    the prose implies.
    """
    payload: dict[str, Any] = {
        "srs_text": sow_markdown or "",
        "srs_filename": srs_filename,
    }
    if scope and (scope.get("in_scope") or scope.get("out_of_scope")):
        payload["scope"] = {
            "in_scope": list(scope.get("in_scope") or []),
            "out_of_scope": list(scope.get("out_of_scope") or []),
            "source": _text(scope.get("source")),
        }
    return payload


# ---------------------------------------------------------------- architecture -> cost
def _design_of(architecture: dict[str, Any]) -> dict[str, Any]:
    """The design, wherever the architecture agent put it.

    `run_pipeline` nests it under "adr"; the discrete `generate_adr` action returns it at the
    top level. Supporting both keeps the orchestrator working with either call style.
    """
    for candidate in (architecture.get("design"),
                      (architecture.get("adr") or {}).get("design"),
                      (architecture.get("adr") or {}).get("spec_json")):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def design_of(architecture: dict[str, Any]) -> dict[str, Any]:
    """The design inside an architecture stage output (public: the API and the revision path read it too)."""
    return _design_of(architecture or {})


def component_changes(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(added, removed) component names between two designs, for the change summary.

    Compared on the component's name, falling back to its service, and ignoring actors and
    external systems, which are not things the build or the price changes for.
    """
    def names(design: dict[str, Any]) -> dict[str, str]:
        found: dict[str, str] = {}
        for component in _components(design):
            if _text(component.get("category")).lower() in ("actor", "external"):
                continue
            label = _text(component.get("name")) or _text(component.get("service"))
            service = _text(component.get("service"))
            if label:
                found[label.lower()] = f"{label} ({service})" if service and service.lower() != label.lower() else label
        return found

    old, new = names(before), names(after)
    return ([new[k] for k in new if k not in old], [old[k] for k in old if k not in new])


def with_cost_adjustment(requirements: str, instruction: str) -> str:
    """The cost agent's description plus a person's adjustment, which wins where they conflict."""
    note = ("\n\nAdjustment requested by the user. Apply it when pricing; where it conflicts with "
            "the figures above, the adjustment wins:\n" + _text(instruction))
    return requirements[: max(0, COST_MAX_CHARS - len(note))] + note


def _usage_of(architecture: dict[str, Any]) -> dict[str, Any]:
    usage = architecture.get("usage")
    if isinstance(usage, dict):
        inner = usage.get("usage_json")
        if isinstance(inner, dict):
            return inner
    if isinstance(architecture.get("usage_json"), dict):
        return architecture["usage_json"]
    return {}


def _components(design: dict[str, Any]) -> list[dict[str, Any]]:
    components = design.get("components")
    return [c for c in components if isinstance(c, dict)] if isinstance(components, list) else []


def _zones(design: dict[str, Any]) -> list[str]:
    zones = design.get("availability_zones")
    if isinstance(zones, list):
        return [_text(z) for z in zones if _text(z)]
    single = _text(design.get("availability_zone"))
    return [single] if single else []


def _usage_lines(usage: dict[str, Any]) -> list[str]:
    """One line per priced service entry, with its usage figures."""
    lines: list[str] = []
    groups = usage.get("services")
    if not isinstance(groups, dict):
        return lines
    for entries in groups.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            service = _text(entry.get("service"))
            if not service:
                continue
            figures = ", ".join(
                f"{key} {_fmt(value)}" for key, value in entry.items()
                if key not in ("service", "explanation", "assumed_fields") and value not in (None, "")
            )
            lines.append(f"- {service}: {figures}" if figures else f"- {service}")
    return lines


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "none"
    return _text(value)


def cost_requirements(architecture: dict[str, Any]) -> str:
    """The free-text description the cost agent prices.

    The cost agent takes prose, not a schema, so the architecture output has to be rendered. It
    is rendered from `usage.json` rather than from the design, because usage.json carries the
    monthly quantities that actually drive price, and the availability model is stated explicitly
    so the per-zone resources are not quietly priced once.

    Raises ValueError when the architecture output is too thin to price, rather than sending a
    vague description and getting back a confident, wrong number.
    """
    design = _design_of(architecture)
    usage = _usage_of(architecture)

    region = _text(design.get("region")) or "us-east-1"
    zones = _zones(design)
    title = _text(design.get("title")) or "AWS solution"

    parts = [f"Price the following AWS solution: {title}.", ""]
    parts.append(f"Region: {region}.")
    if zones:
        word = "Availability Zone" if len(zones) == 1 else "Availability Zones"
        parts.append(f"Deployment: {len(zones)} {word} ({', '.join(zones)}). "
                     + ("Price per-zone resources once per zone, include the Multi-AZ standby where a "
                        "managed service has one, and include cross-AZ data transfer."
                        if len(zones) > 1 else
                        "Single zone: no standby instances and no cross-AZ transfer."))
    parts.append("")

    usage_lines = _usage_lines(usage)
    if usage_lines:
        parts.append("Monthly usage per service, already estimated. Use these figures as given:")
        parts.extend(usage_lines)
    else:
        components = _components(design)
        if not components:
            raise ValueError("The architecture output carries neither usage figures nor components to price.")
        parts.append("Services in the design, with their configuration:")
        for component in components[:MAX_SERVICES]:
            service = _text(component.get("service"))
            if not service:
                continue
            sku = _text(component.get("sku_summary"))
            quantity = component.get("quantity") or 1
            parts.append(f"- {service}" + (f" ({sku})" if sku else "")
                         + (f", quantity {quantity}" if quantity and int(quantity) > 1 else ""))

    text = "\n".join(parts).strip()
    if len(text) < COST_MIN_CHARS:
        raise ValueError("The architecture output is too thin to build a cost estimate from.")
    return text[:COST_MAX_CHARS]


# ---------------------------------------------------------------- -> UpstreamInputs
def architecture_block(architecture: dict[str, Any]) -> dict[str, Any]:
    """`UpstreamInputs.architecture` from the architecture agent's reply.

    Non-AWS components (actors, external systems) are left out: the SOW's service-coverage rule
    expects every listed service to appear in scope, and an actor is not a service.
    """
    design = _design_of(architecture)
    services: list[dict[str, str]] = []
    seen: set[str] = set()
    for component in _components(design):
        if _text(component.get("category")).lower() in ("actor", "external"):
            continue
        name = _text(component.get("service")) or _text(component.get("name"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        services.append({"name": name,
                         "purpose": _text(component.get("purpose")) or _text(component.get("role"))})

    assumptions = [_text(a.get("text") if isinstance(a, dict) else a)
                   for a in (design.get("assumptions") or [])]
    nfrs = [_text(r.get("text")) for r in (design.get("requirements") or [])
            if isinstance(r, dict) and _text(r.get("kind")) in ("non_functional", "constraint")]

    # Any out-of-scope component the architecture agent flagged is worth carrying into the SOW
    # review rather than dropping on the floor.
    warnings = [_text(w) for w in (architecture.get("scope_warnings") or []) if _text(w)]

    block: dict[str, Any] = {
        "services": services[:MAX_SERVICES],
        "assumptions": [a for a in (assumptions + warnings) if a][:MAX_ASSUMPTIONS],
        "nfrs": [n for n in nfrs if n][:MAX_NFRS],
    }

    zones = _zones(design)
    if zones:
        word = "Availability Zone" if len(zones) == 1 else "Availability Zones"
        block["assumptions"].insert(0, f"Deployed across {len(zones)} {word}: {', '.join(zones)}.")

    images = _diagram_images(architecture)
    if images:
        block["diagram_images"] = images
    return block


def _diagram_images(architecture: dict[str, Any]) -> list[dict[str, str]]:
    diagram = architecture.get("diagram")
    if not isinstance(diagram, dict):
        return []
    png = diagram.get("png_base64")
    if not isinstance(png, str) or not png:
        return []
    return [{"filename": "architecture.png", "content_b64": png}][:MAX_DIAGRAMS]


def cost_block(cost: dict[str, Any], currency: str = "USD") -> dict[str, Any]:
    """`UpstreamInputs.cost` from the cost agent's reply.

    This is the wiring that was missing: `calculator_url` becomes `source_link`, which is the
    field the SOW's Pricing section reproduces. Totals and line items are carried too, but only
    so the SOW's reconciliation rule can check that the SOW does *not* restate them -- the
    calculator stays the single source of truth for the run-rate.
    """
    estimate = cost.get("estimate") if isinstance(cost.get("estimate"), dict) else {}
    block: dict[str, Any] = {
        "currency": _text(estimate.get("currency")) or currency,
        "source_link": _text(cost.get("calculator_url")) or _text(estimate.get("calculator_url")) or None,
        "line_items": _cost_line_items(estimate),
        "assumptions": [a for a in (_text(x) for x in (estimate.get("assumptions") or [])) if a][:MAX_ASSUMPTIONS],
    }
    region = _text(estimate.get("region"))
    if region:
        block["region"] = region
    monthly = _number(estimate.get("monthly_total") or estimate.get("total_monthly_cost"))
    annual = _number(estimate.get("annual_total") or estimate.get("total_annual_cost"))
    if monthly is not None:
        block["monthly_total"] = monthly
    if annual is not None:
        block["annual_total"] = annual
    elif monthly is not None:
        block["annual_total"] = round(monthly * 12, 2)
    return block


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(value)) or "nan")
    except ValueError:
        return None


def _cost_line_items(estimate: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-service monthly figures, flattened out of the calculator's environment structure."""
    items: list[dict[str, Any]] = []
    environments = estimate.get("environments")
    if isinstance(environments, dict):
        for env in environments.values():
            for service in ((env or {}).get("services") or []) if isinstance(env, dict) else []:
                if not isinstance(service, dict):
                    continue
                name = _text(service.get("service") or service.get("name"))
                if not name:
                    continue
                monthly = _number(service.get("monthly_cost") or service.get("monthly"))
                items.append({"service": name, **({"monthly": monthly} if monthly is not None else {})})
    if not items and isinstance(estimate.get("line_items"), list):
        for raw in estimate["line_items"]:
            if isinstance(raw, dict) and _text(raw.get("service")):
                items.append({"service": _text(raw["service"]),
                              **({"monthly": m} if (m := _number(raw.get("monthly"))) is not None else {})})
    return items[:MAX_SERVICES]


# ---------------------------------------------------------------- final-review coverage
# Cross-cutting AWS services that routinely sit in a SOW's scope but never reach the diagram or
# the priced estimate: security, governance, identity, observability and analytics. The final
# review scans the SOW scope for these and flags any that neither the design nor the estimate
# accounts for, so a person confirms they are handled rather than silently dropped. Each entry is
# (canonical name, [lower-case aliases to look for in the scope text]).
_CROSS_CUTTING: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AWS KMS", ("kms", "key management")),
    ("AWS IAM", ("iam ", " iam", "identity and access")),
    ("Amazon Cognito", ("cognito",)),
    ("AWS Secrets Manager", ("secrets manager",)),
    ("Amazon CloudWatch", ("cloudwatch",)),
    ("AWS CloudTrail", ("cloudtrail",)),
    ("AWS Config", ("aws config",)),
    ("AWS WAF", ("waf", "web application firewall")),
    ("AWS Shield", ("shield",)),
    ("Amazon GuardDuty", ("guardduty", "guard duty")),
    ("Amazon Inspector", ("inspector",)),
    ("Amazon Macie", ("macie",)),
    ("AWS Security Hub", ("security hub",)),
    ("Amazon QuickSight", ("quicksight", "quick sight")),
    ("AWS Backup", ("aws backup",)),
    ("AWS Security agent", ("security agent", "endpoint agent")),
)


def _design_pool(architecture: Optional[dict[str, Any]]) -> list[str]:
    return [_text(s.get("name")).lower() for s in architecture_block(architecture or {}).get("services", [])
            if _text(s.get("name"))]


def _in_pool(name: str, pool: list[str]) -> bool:
    low = name.lower()
    return any(low in p or p in low for p in pool if p)


# Patterns that mean the draft was never finished: template slots, LLM artefacts and blanks. These
# are the sa-doc-reviewer's dimension 1/7 checks, kept deliberately specific so a real finding is a
# real finding (a generic "any [bracket]" scan would flag ordinary markdown links and prose).
_PLACEHOLDER_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\[\s*cost calculator link\s*\]",
    r"\[\s*(?:tbd|todo|placeholder|insert[^\]]*|client name|customer name|company name|date|url|"
    r"aws cost link|x{3,}|\.\.\.)\s*\]",
    r"\{\{[^}]{1,60}\}\}",
    r"<\s*(?:tbd|placeholder|insert[^>]{0,40})\s*>",
    r"\b(?:TBD|TODO|PLACEHOLDER|FIXME)\b",
    r"_{4,}",
))
_SLOP_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"as an ai language model", r"i hope this helps", r"let me know if you",
    r"here is the (?:draft|document|sow)", r"certainly!", r"feel free to", r"i can help you",
))
_CALCULATOR_RE = re.compile(r"https?://calculator\.aws/\S*", re.IGNORECASE)
_SECTION_NEEDLES = (
    ("Deliverables", ("deliverable",)),
    ("Timeline / Schedule", ("timeline", "schedule", "milestone")),
    ("Assumptions", ("assumption",)),
    ("Acceptance Criteria", ("acceptance",)),
)


def _finding(dimension: int, severity: str, label: str, detail: str) -> dict[str, Any]:
    return {"dimension": dimension, "severity": severity, "label": label, "detail": detail}


def _headings(markdown: str) -> list[str]:
    return [h.strip().lower() for h in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", markdown or "")]


def sa_review_findings(sow_markdown: str,
                       scope: Optional[dict[str, Any]],
                       architecture: Optional[dict[str, Any]],
                       cost: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """A deterministic pass of the sa-doc-reviewer checklist over the final SOW.

    This is the cheap, mechanical half of the Solutions Architect review (the qualitative dimensions
    -- tone, grammar, scope-clarity judgement -- need a model). Each finding is
    {"dimension", "severity" (critical|high|medium|low), "label", "detail"} so the UI can group them
    the way the skill does. The final-review stage blocks on a `critical`; everything else is an
    advisory note for a person to confirm before the SOW goes out.

    Covers, from sa-doc-reviewer-v1: placeholders / AI slop (1, 7), the calculator link (19), the
    two-way architecture <-> cost audit (17), the out-of-scope section (9), required SOW sections
    (16), region consistency (21) and the contingency buffer (12).
    """
    sow = sow_markdown or ""
    headings = _headings(sow)
    design_services = _design_pool(architecture)
    estimate = (cost or {}).get("estimate") if isinstance((cost or {}).get("estimate"), dict) else {}
    priced_names = [_text(i.get("service")) for i in _cost_line_items(estimate) if _text(i.get("service"))]
    priced = [n.lower() for n in priced_names]
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(dimension: int, severity: str, label: str, detail: str) -> None:
        key = f"{dimension}|{detail}"
        if key not in seen:
            seen.add(key)
            findings.append(_finding(dimension, severity, label, detail))

    # Dim 1/7 -- placeholders and AI slop left in the draft.
    for pattern in _PLACEHOLDER_RES:
        for match in pattern.findall(sow):
            text = match if isinstance(match, str) else next((m for m in match if m), "")
            add(1, "critical", "Unfilled placeholder", f"The SOW still contains '{str(text).strip()[:60]}'.")
    for pattern in _SLOP_RES:
        found = pattern.search(sow)
        if found:
            add(1, "critical", "AI drafting artefact", f"The SOW contains '{found.group(0)[:60]}'.")

    # Dim 19 -- the Pricing section must carry a real calculator estimate, not a homepage placeholder.
    urls = _CALCULATOR_RE.findall(sow)
    if not urls:
        add(19, "high", "No cost calculator link",
            "The SOW has no AWS Pricing Calculator link; the Pricing section should point at the estimate.")
    elif all("id=" not in u for u in urls):
        add(19, "critical", "Placeholder calculator link",
            "The only calculator.aws link is the homepage, with no saved estimate id.")

    # Dim 17 -- two-way architecture <-> cost audit.
    for name in [_text(s.get("name")) for s in architecture_block(architecture or {}).get("services", [])]:
        if name and priced and not _in_pool(name, priced):
            add(17, "high", "Service not priced",
                f"{name} is in the architecture design but has no line item in the cost estimate; "
                f"confirm it is priced or intentionally excluded.")
    for priced_name in priced_names:
        if design_services and not _in_pool(priced_name, design_services):
            add(17, "critical", "Priced service not in the design",
                f"'{priced_name}' is a cost line item with no matching service in the architecture "
                f"design; the estimate and the diagram disagree.")

    # Dim 9/17 -- cross-cutting services named in the SOW scope but neither drawn nor priced.
    scope_text = " ".join(_text(x) for x in ((scope or {}).get("in_scope") or [])).lower()
    for canonical, aliases in _CROSS_CUTTING:
        if any(alias in scope_text for alias in aliases) \
                and not _in_pool(canonical, design_services) and not _in_pool(canonical, priced):
            add(9, "high", "Scope service not covered",
                f"{canonical} is named in the SOW scope but is not in the architecture diagram or "
                f"the cost estimate; confirm it is accounted for.")

    # Dim 9 -- an explicit Out of Scope section is mandatory.
    has_out_of_scope = bool((scope or {}).get("out_of_scope")) \
        or any(re.search(r"out[\s-]?of[\s-]?scope", h) for h in headings)
    if not has_out_of_scope:
        add(9, "critical", "No Out of Scope section",
            "The SOW has no explicit Out of Scope section; scope boundaries are undefined.")

    # Dim 16 -- the core SOW sections a reader expects.
    missing = [label for label, needles in _SECTION_NEEDLES
               if not any(n in h for h in headings for n in needles)]
    if missing:
        add(16, "high", "Missing SOW sections",
            "The SOW has no section for: " + ", ".join(missing) + ".")

    # Dim 21 -- the region must be the same in the design and the estimate.
    design = _design_of(architecture or {})
    region_design = _text(design.get("region"))
    region_cost = _text(estimate.get("region"))
    if region_design and region_cost and region_design.lower() != region_cost.lower():
        add(21, "high", "Region mismatch",
            f"The design is in {region_design} but the cost estimate is in {region_cost}.")

    # Dim 12 -- a named contingency buffer (the 20% arithmetic needs a model; presence does not).
    if sow and not re.search(r"contingency|buffer|risk reserve|\bfloat\b", sow, re.IGNORECASE):
        add(12, "medium", "No contingency buffer",
            "No named contingency buffer is called out; confirm the timeline includes at least 20%.")

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return findings[:MAX_SERVICES]


def upstream_with_results(upstream: dict[str, Any],
                          architecture: Optional[dict[str, Any]] = None,
                          cost: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The intake handoff plus the architecture and cost blocks, for the combined review.

    The input is not mutated: a caller keeps its own copy of the original handoff.
    """
    merged = copy.deepcopy(upstream or {})
    if architecture:
        block = architecture_block(architecture)
        if block.get("services") or block.get("assumptions") or block.get("nfrs"):
            merged["architecture"] = block
    if cost:
        block = cost_block(cost)
        if block.get("source_link") or block.get("line_items") or block.get("monthly_total") is not None:
            merged["cost"] = block
    return merged


def json_size(value: Any) -> int:
    return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))


# ---------------------------------------------------------------- cost link -> SOW
PLACEHOLDER_RE = re.compile(r"\[\s*cost calculator link\s*\]", re.IGNORECASE)
CALCULATOR_URL_RE = re.compile(r"https?://calculator\.aws/[^\s)>\]]+", re.IGNORECASE)
LINK_LINE_RE = re.compile(r"^(?P<lead>\s*(?:AWS\s+)?Pricing\s+Calculator\s+estimate\s*:\s*).*$", re.IGNORECASE | re.MULTILINE)
PRICING_HEADING_RE = re.compile(r"^(#{1,6})\s+(?:.*\b)?(pricing|commercials|fees|investment)\b.*$", re.IGNORECASE | re.MULTILINE)


def apply_cost_link(sow_markdown: str, url: str) -> tuple[str, str]:
    """Write the real cost estimate link into the SOW's Pricing section.

    The SOW is drafted *before* the cost estimate exists, so at draft time it can only carry a
    placeholder (or a stale link from a sample). Once the cost stage has produced the calculator
    URL it has to be written back, otherwise the SOW would point at nothing and the final review
    would rightly fail. Returns `(markdown, how)` where `how` says what was done, for the log:
    `placeholder`, `replaced`, `inserted`, `appended` or `unchanged`.

    Never invents figures: only the link is written, matching the "refer to the cost estimate"
    policy.
    """
    url = (url or "").strip()
    if not url or not sow_markdown:
        return sow_markdown, "unchanged"
    if url in sow_markdown and not PLACEHOLDER_RE.search(sow_markdown):
        return sow_markdown, "unchanged"

    if PLACEHOLDER_RE.search(sow_markdown):
        return PLACEHOLDER_RE.sub(url, sow_markdown), "placeholder"

    pricing = PRICING_HEADING_RE.search(sow_markdown)
    start = pricing.end() if pricing else 0
    nxt = re.search(r"^#{1,6}\s", sow_markdown[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(sow_markdown)
    section = sow_markdown[start:end]

    if LINK_LINE_RE.search(section):
        section = LINK_LINE_RE.sub(lambda m: f"{m.group('lead')}{url}", section, count=1)
        return sow_markdown[:start] + section + sow_markdown[end:], "replaced"
    if CALCULATOR_URL_RE.search(section):
        section = CALCULATOR_URL_RE.sub(url, section, count=1)
        return sow_markdown[:start] + section + sow_markdown[end:], "replaced"

    line = f"\n\nRefer to the cost estimate below, which is the authoritative source for the AWS run-rate:\n\nAWS Pricing Calculator estimate: {url}\n"
    if pricing:
        return sow_markdown[:end].rstrip("\n") + line + sow_markdown[end:], "inserted"
    return sow_markdown.rstrip("\n") + "\n\n# Pricing" + line, "appended"
