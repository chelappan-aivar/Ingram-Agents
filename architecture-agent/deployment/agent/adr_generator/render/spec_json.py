"""Deterministic renderer: Design -> spec.json dict (format defined in HANDOFF.md).

Consumed by the cost-calculator agent. Usage volumes (hours, requests, data transfer) are deliberately absent.
Actor/external components are kept; the consumer ignores them.
"""
from __future__ import annotations

from adr_generator.contracts import SCHEMA_VERSION, Design


def build_spec(design: Design, *, source_name: str, generated_at: str) -> dict:
    d = design
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source_srs": source_name,
        "title": d.title,
        "customer": d.customer,
        "region": d.region,
        "availability_zones": d.availability_zones,
        "boundaries": [{"id": b.id, "type": b.type, "name": b.name, "parent": b.parent} for b in d.boundaries],
        "components": [{
            "id": c.id, "name": c.name, "service": c.service, "category": c.category, "role": c.role,
            "quantity": c.quantity,
            "placement": c.boundary,
            "configuration": dict(c.configuration),
            "sku_summary": c.sku_summary,
            "assumed": c.assumed, "assumption_reason": c.assumption_reason,
            "requirement_ids": list(c.requirement_ids), "decision_ids": list(c.decision_ids),
        } for c in d.components],
        "relationships": [{"id": r.id, "source": r.source, "target": r.target, "label": r.label,
                           "protocol": r.protocol, "port": r.port} for r in d.relationships],
        "assumptions": [{"id": a.id, "text": a.text, "reason": a.reason} for a in d.assumptions],
    }
