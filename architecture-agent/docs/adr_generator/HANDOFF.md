# ADR Generator: hand-off contract

The ADR Generator is one node of the Architecture agent. It reads an SRS and writes two files that other nodes consume. Nothing is shared in code; the files are the contract.

```
SRS (.md/.txt/.docx/.pdf) ──► ADR Generator ──► adr.md     ──► diagram node (diagram_generator.cli)
                                             └► spec.json  ──► cost-calculator agent
             design.json + SRS ──► Usage Estimator ──► usage.json ──► cost-calculator agent
```

Every run folder (`data/runs/<run_id>/`) contains:

| File | Consumer | Content |
|---|---|---|
| `adr.md` | diagram node, people, a web viewer | The low-level ADR (format below) |
| `spec.json` | cost-calculator agent | Exact services and provisioned configuration (format below) |
| `design.json` | debugging | The raw `submit_design` tool input, unchanged |
| `metadata.json` | tracing | Model, tokens, timing, counts, status (`RunMeta` in `app/contracts.py`) |

## adr.md

Markdown that renders cleanly in any basic viewer: `#`/`##`/`###` headings, GitHub tables, bullet and numbered lists, `**bold**`. No front matter, no raw HTML, no code fences, no ASCII art. Table cells never contain line breaks or unescaped `|`. IDs are stable and shared with `spec.json`: requirements `REQ-NN`, decisions `ADR-NNN`, components `C-NN`, flows `F-NN`, assumptions `A-NN`.

Section order (fixed):

1. `# Architecture Decision Record: <title>`, then a two-column metadata table: Customer, Status (`Proposed`), Date (run date, ISO), Region, Availability Zone, Source document, Components (count).
2. `## 1. Context`: the context paragraph.
3. `## 2. Requirements addressed`: table `ID | Requirement | Type`.
4. `## 3. Architecture decisions`: one `### ADR-001: <title>` per decision, each with lines **Status**, **Context**, **Decision**, **Consequences** (bullets), **Requirements** (ids), then a final line `**Alternatives considered (rejected, not part of this design):** <option> (<reason>); ...` or "None recorded".
5. `## 4. Network layout`: plain sentences generated from boundaries and component placement, e.g. "The solution runs in AWS Region us-east-1. It uses one VPC, Main VPC, in a single Availability Zone, us-east-1a. The Public subnet (us-east-1a) contains the Application Load Balancer and the NAT Gateway. ..." Region-level services are listed in one sentence ("Region-level services used: Amazon S3, Amazon Bedrock."). External systems and actors in one sentence.
6. `## 5. Components`: table `ID | Component | AWS service | SKU / configuration | Qty | Placement | Purpose`. SKU column = `sku_summary`, else `configuration` joined as `key: value; ...`. Placement = boundary display name, or "Region" / "External". Assumed components get " (assumed)" after the name.
7. `## 6. Request and data flow`: numbered list in `F-NN` order: `<sentence> (F-01; HTTPS 443)`. If `sentence` is empty, fall back to "`<source name>` `<label>` `<target name>`."
8. `## 7. Security and operations`: bullets.
9. `## 8. Assumptions`: table `ID | Assumption | Reason`, plus one row per assumed component that is not already covered (`C-NN` id, "<name>: <sku>", assumption_reason).
10. `## 9. Open questions`: bullets, or "None."

Empty values render as "Not specified"; empty lists render as "None." The renderer never raises on a valid `Design`.

## spec.json

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-10-09T12:00:00+00:00",
  "source_srs": "srs_billing_dispute_assistant.md",
  "title": "...", "customer": "...",
  "region": "us-east-1", "availability_zone": "us-east-1a",
  "boundaries": [{"id": "main_vpc", "type": "vpc", "name": "Main VPC", "parent": "region_use1"}],
  "components": [{
    "id": "C-03", "name": "Case Database", "service": "Amazon RDS for PostgreSQL", "category": "database",
    "role": "...", "quantity": 1, "placement": "private_subnet_a",
    "configuration": {"instance_type": "db.t4g.medium", "engine": "postgres", "engine_version": "16.4",
                      "deployment": "Single-AZ", "storage_type": "gp3", "storage_gb": "100"},
    "sku_summary": "db.t4g.medium, PostgreSQL 16.4, gp3 100 GiB, Single-AZ",
    "assumed": false, "assumption_reason": "", "requirement_ids": ["REQ-04"], "decision_ids": ["ADR-002"]
  }],
  "relationships": [{"id": "F-01", "source": "C-01", "target": "C-02", "label": "...", "protocol": "HTTPS", "port": "443"}],
  "assumptions": [{"id": "A-01", "text": "...", "reason": "..."}]
}
```

Components with category `actor` or `external` are kept (the cost calculator ignores them). **Running hours, request volumes, data transfer and stored-data growth are not in spec.json.** They are estimated by the Usage Estimator (`usage_estimator/`) into `usage.json`, the exact schema in `usage_estimator/expectedOutput.md`, with the reasoning in `usage_assumptions.md`. See `SYSTEM_DESIGN.md`, section 4.7.
