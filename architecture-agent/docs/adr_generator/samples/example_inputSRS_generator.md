# Prompt: generate a sample input SRS

This file holds a reusable prompt for producing a new example Software
Requirements Specification (SRS) shaped exactly as the ADR Generator expects
its input. Paste the prompt below into an LLM, fill in the two bracketed
placeholders (or leave them for the model to pick), and save the result as a
new file in this `samples/` folder (for example
`srs_<scenario>.md`), alongside `srs_billing_dispute_assistant.md`.

Use a new sample whenever you want a fresh scenario to exercise the pipeline
end to end (`run_adr.py` then `run_v2.py`) without reusing the billing
dispute example.

---

## The prompt

```
You are a business analyst on a Discovery/Live-Assist team. You have just
finished a discovery workshop series with a customer and must write the
Software Requirements Specification (SRS) that hands off to solution design.
This SRS is the only input the solution architect will receive: it must be
detailed and specific enough that every architecture decision can be made
from it alone, with no follow-up questions.

Scenario: [describe the application in one or two sentences, e.g. "a
field-technician inspection app for a utility company" or "a claims intake
assistant for a regional insurer" — or write "pick a realistic, distinct
AWS-hosted business application" and choose one yourself, avoiding anything
already used in this samples folder].

Customer: invent a fictional company name and a one-line description of
what it does. Never use a real company, product or person.

Write the SRS in Markdown with exactly this structure and these section
numbers:

- Title line, then a metadata table: Document, Version, Status, Date, Owner,
  Prepared by.
- 1 Introduction
  - 1.1 Purpose
  - 1.2 Scope (In scope / Out of scope as bullet lists; out of scope must
    include "Multi-Region or multi-AZ deployment during the pilot")
  - 1.3 Definitions and acronyms (table)
  - 1.4 References (bullet list of fictional workshop notes and policy
    documents, with dates)
- 2 Overall description
  - 2.1 Product perspective
  - 2.2 User classes and characteristics (table, with a named volume or
    headcount per class)
  - 2.3 Operating environment — state plainly that the system runs on AWS
    in a single Region (name it, default us-east-1) and only in one named
    Availability Zone (default `<region>a`); say the customer has
    explicitly accepted single-AZ availability for the pilot
  - 2.4 Design and implementation constraints (numbered C-01, C-02, ...),
    including one that pins every zonal resource to that single AZ and one
    about AWS-native managed services only
  - 2.5 Assumptions and dependencies (bullet list)
- 3 Functional requirements — table with ID (FR-01, FR-02, ...),
  Requirement, Priority (Must/Should/Could); 10 to 16 rows
- 4 Non-functional requirements — table with ID (NFR-01, ...), Category,
  Requirement; cover at least Volume, Performance (two or more rows),
  Availability, Recovery (RPO/RTO), Retention, Security, Scalability,
  Observability, Cost (a stated monthly budget ceiling), Compliance,
  Maintainability
- 5 External interface requirements — table of every external system,
  human interface and file feed named in the scope, each with its
  integration style (API, webhook, file drop, SSO protocol, email, etc.)
- 6 Data requirements — table of entities with description and an
  indicative volume or count, plus bullets for growth, data classification
  and retention
- 7 Acceptance criteria — table, ID (AC-01, ...), measurable criteria tied
  back to the non-functional requirements
- 8 Open issues — table, ID (OI-01, ...), issue, owner

Hard rules:
- Every requirement, volume, latency figure, retention period, budget and
  user count is a concrete number or named value. Never write "TBD", "as
  needed", "appropriate", "various" or a bare range with no target.
- Name every external system, identity provider and data format (file
  type, approximate size, frequency).
- State one Region and one Availability Zone and nowhere imply high
  availability, standby capacity or a second zone.
- Say "customer", never "client". Write in English, present tense,
  professional tone. Do not mention MVPs, demos, prototypes or mock data
  anywhere in the document.
- Length: about 1,200 to 1,800 words.
- Do not propose AWS services, instance types or an architecture — that is
  the solution architect's job downstream. Describe needs and constraints
  only.

Output only the finished Markdown document, starting with the title line.
```
