---
name: sa-doc-reviewer
description: >
  SA-focused document review combining general quality checks, SOW consulting review, and AWS
  cost estimate validation. Use whenever an SA or Aivar team member wants to review, audit,
  proofread, or QA any document before customer delivery — even if they say "check this doc",
  "review for quality", "is this ready to send", "review this SOW", "check my estimate", "run
  a quality check", "does this look right", "proofread this", "review before I share", or
  "SA checklist". Trigger when any DOCX, PDF, TXT, or MD is uploaded for review. Covers: AI
  slop, placeholders, entity/name mismatches, structural issues, typos, grammar, tone drift,
  SOW-specific checks (scope clarity, commitments, timeline, assumptions, change control), and
  — when an AWS cost estimate is provided — architecture-to-cost two-way audit, pricing
  accuracy, region validation, and cost calculator presentation. Always run before any Aivar
  document reaches a customer, especially SOWs and proposals.
---

# SA Document Review Skill

A comprehensive pre-delivery review skill for Aivar Solutions Architects. Combines document
quality review (AI slop, entity consistency, grammar), SOW consulting review (scope, commitments,
timeline, change control), and AWS cost estimate validation (architecture-to-cost audit, pricing
accuracy, region validation).

---

## Inputs Expected

| Input | Required | Notes |
|---|---|---|
| Document to review | Yes | `.docx`, `.pdf`, `.txt`, `.md`, or inline paste |
| AWS Cost Estimate CSV | Required for SOWs | CSV export from AWS Pricing Calculator — **ask for this before starting if not uploaded** |
| Customer-stated region | If known | Cross-checked against estimate region |

> **If an SOW is uploaded without a cost estimate CSV, stop and ask before running checks.**
> See "Cost Estimate Nudge" section below.

---

## Commercials Rule

> **Commercials (fees, payment milestones, day rates) are NOT required in SA-generated SOWs.**
> The Sales team owns commercials and shares them separately alongside the SOW.
> Do NOT flag missing commercials as a blocker. Instead, append at the end of the review:
> *"Sales team: please attach the commercial schedule when sharing this SOW with the customer."*

---

## Reading the Input Document

Identify the file type and read accordingly:

| Format | How to Read |
|--------|-------------|
| `.md` / `.txt` | Already in context or `cat` the file |
| `.docx` | Read the `docx` skill at `/mnt/skills/public/docx/SKILL.md`, then use `mammoth` to extract text |
| `.pdf` | Read the `pdf-reading` skill at `/mnt/skills/public/pdf-reading/SKILL.md`, then extract text |
| Inline paste | Use the text directly from the conversation |

Extract plain text first. Run all checks against plain text. You do not need to re-render the document for checks.

---

## Document Type Detection & Module Activation

Before running any checks, classify the document and activate the appropriate modules:

**Signals a document is an SOW:**
- Title/header contains "Statement of Work", "SOW", "Scope of Work", "Engagement Agreement"
- Contains sections named "Scope", "Deliverables", "Timeline", "Assumptions", "Out of Scope"
- References "Sprint", "Milestones", "Acceptance Criteria", "Change Control", or "Purchase Order"
- Structured as a bilateral agreement (references both service provider and customer)

| Document Type | Modules to Run |
|---|---|
| General document | Base (Dimensions 1–8) |
| SOW — no cost estimate uploaded | Base (1–8) + SOW Module (9–16) + **nudge for CSV before finishing** |
| SOW + cost estimate CSV uploaded | Base (1–8) + SOW Module (9–16) + Cost & Architecture Module (17–22) |

---

## Cost Estimate Nudge

**Trigger:** An SOW is detected AND no AWS Pricing Calculator export has been uploaded.

When this condition is met, after completing the base and SOW module checks, **pause and send this message before writing the final review file:**

---

> ⚠️ **Cost estimate not uploaded**
>
> The Cost & Architecture review (Dimensions 17–22) cannot run without the AWS Pricing Calculator export. This covers the architecture-to-cost two-way audit, NFR traceability, pricing accuracy, region validation, and AWS best practices alignment — these are the checks most likely to catch critical issues before customer delivery.
>
> **Please export and upload the cost estimate CSV:**
> 1. Open your estimate at [calculator.aws](https://calculator.aws)
> 2. Click **"Export"** → **"CSV"**
> 3. Upload the downloaded `.csv` file here
>
> Once uploaded, the full Cost & Architecture review will run and the action item list will be updated.

---

Do not write the final `<document-name>-review.md` file until either:
- The CSV is uploaded and Dimensions 17–22 are completed, **or**
- The user explicitly says they want the partial review without the cost estimate

If the user proceeds without uploading, add this item at the top of the action list regardless of other findings:

```
- [ ] **[Cost Estimate Missing]** — Upload AWS Pricing Calculator CSV export to enable architecture-to-cost audit, NFR traceability, and AWS best practices checks (Dims 17–22). Export: calculator.aws → Export → CSV.
```

---

## Severity Labels

| Label | Meaning |
|---|---|
| 🔴 CRITICAL | Would mislead the customer, embarrass Aivar, or cause a commercial dispute if sent |
| 🟡 HIGH | Should fix before sending; creates ambiguity or raises questions |
| 🟣 MEDIUM | Worth fixing; minor inconsistency or missing best-practice element |
| 🔵 LOW / CONSIDER | Style or polish suggestion; optional |

---

## Base Module — Dimensions 1–8 (All Documents)

Run all 8 dimensions on every document. Do not skip any even if the document looks clean.

---

### 1. AI Slop & Placeholder Detection
*Severity if failed: 🔴 CRITICAL*

The highest-priority check. AI-generated content leaves behind artifacts that signal it was never properly reviewed.

**Patterns to find:**

| Pattern Type | Examples |
|---|---|
| Open questions from LLM | "What would you like me to include here?", "Please provide more details about…" |
| Unfilled template slots | `[Client Name]`, `[INSERT DATE]`, `{{company}}`, `<TBD>`, `[PLACEHOLDER]`, `___` blanks |
| LLM hedge phrases | "It's important to note that…", "As an AI language model…", "I hope this helps!", "Certainly!", "Of course!" |
| Generic filler bullets | "Leverage best practices", "Ensure alignment with stakeholders", "Drive synergies" — flag if no concrete specifics follow |
| Numbered open questions | A numbered list of questions at end of a section asking the reader to clarify |
| "Note:" LLM blocks | `Note: You may want to customize this section…`, `[Add your specific metrics here]` |

**Output:** List every instance with exact location (section/paragraph) and quoted text.

---

### 2. Entity & Name Consistency
*Severity if failed: 🔴 CRITICAL (wrong entity) / 🟣 MEDIUM (stylistic variation)*

**What to look for:**
- **Wrong customer name** — Document refers to a different customer in some sections (dead giveaway of copy-paste reuse)
- **Customer name drift** — Switches between "Acme Corp", "Acme", "ACME Corporation", "the client" without clear reason
- **Project/product name drift** — "Phase 1" vs. "Pilot", "Convogent" vs. "the platform" used ambiguously
- **Contact/author name mismatches** — Name in footer, cover, or signature block doesn't match stated author
- **Date inconsistency** — "Q1 2025" in one section, "Q3 2025" in another without explanation
- **Version number drift** — "v1.0" on cover, "v2.1" in footer or change log

**Technique:** Extract all proper nouns (companies, products, people, dates, versions) and build an entity map. Check for unexpected variation.

---

### 3. Structural Inconsistency & Logic Gaps
*Severity if failed: 🟡 HIGH*

- **Section promises not fulfilled** — Introduction says "this covers X, Y, Z" but Z is missing or empty
- **TOC vs. body mismatch** — Table of contents headings don't match body headings
- **Numbering/ordering errors** — Section numbers skip or repeat (1.1, 1.2, 1.4 — no 1.3)
- **Cross-reference failures** — "See Section 3" but that section doesn't contain referenced details
- **Repeated paragraphs** — Same paragraph appears more than once (copy-paste duplication)
- **Cut-off content** — A sentence or bullet ends abruptly mid-thought
- **Contradictory statements** — "Timeline: 4 weeks" in one section vs. "8-week delivery" elsewhere

---

### 4. Typos & Spelling
*Severity if failed: 🟡 HIGH (customer/product names) / 🟣 MEDIUM (general)*

- Obvious misspellings
- Homophones used incorrectly (their/there/they're, its/it's, affect/effect)
- Proper noun misspellings (customer name, product name, technology names)
- Repeated words ("the the", "is is")
- Inconsistent capitalisation of the same term across the document

---

### 5. Grammar & Sentence Quality
*Severity if failed: 🟣 MEDIUM*

- Subject-verb agreement errors
- Dangling modifiers, run-on sentences, sentence fragments
- Tense inconsistency within a section
- Passive voice overuse (flag if >40% of sentences are passive — common in AI writing)
- Awkward or unnatural phrasing

---

### 6. Tone, Voice & Cognitive Readability
*Severity if failed: 🟣 MEDIUM*

> **Core principle:** Technical depth must be fully preserved. This check is about how clearly
> that depth is expressed — not about simplifying content. A well-written SOW should be
> readable by a CTO skimming it and technically airtight for an architect scrutinising it.

**Tone & Voice:**
- **Audience drift** — Starts formal/executive, drifts to casual or overly technical mid-section
- **Person shift** — Shifts between "we will deliver", "you will receive", "the client will receive" without purpose
- **Formality level** — Sudden informal contractions (can't, won't) in a formally structured document
- **AI-generated tone signals** — Excessive enthusiasm ("This exciting solution…"), over-explanation, unsolicited reassurances

**Cognitive Readability — Sentence Level:**
- **Sentence length** — Flag any paragraph where the majority of sentences exceed ~25 words. Long sentences force the reader to hold too much in working memory before reaching the point. The fix is splitting, not simplifying.
- **One idea per sentence** — Each sentence should carry one clear point. Sentences chaining multiple concepts with "and", "which", "where", "that" without a full stop are hard to parse. Flag these.
- **Front-loaded sentences** — The key point should come first; qualifications and caveats come after. "Given the customer's requirement for 99.9% uptime, Multi-AZ RDS is included" is clearer than "Multi-AZ RDS is included, which is relevant given the customer's uptime requirement."
- **Nested conditionals** — Flag sentences with more than one level of "if/unless/when/except" nesting. These exponentially increase cognitive load. Rewrite as a table or numbered conditions instead.

**Cognitive Readability — Paragraph & Section Level:**
- **Paragraph length** — Paragraphs beyond 5 lines are a red flag. A wall of text signals a single paragraph is doing too much. Flag for splitting or restructuring.
- **Section openers** — Each major section should open with 1–2 sentences stating what the section covers and why it matters. A reader dropped into the middle of the document should be able to orient themselves within one sentence.
- **Prose vs. structure** — If a paragraph is comparing, listing, or sequencing 3+ things, it should be a table or a numbered/bulleted list. Comparisons buried in prose sentences force the reader to construct the comparison mentally. Flag these.
- **Jargon density** — Technical terms are expected and should not be removed. But flag any sentence where 3+ unexplained acronyms appear together before any of them have been defined. The rule is: define on first use, then use freely.
- **Abstract before concrete** — Flag sections that jump straight into implementation detail without a one-sentence statement of what problem that detail solves. Concrete details are hard to follow without knowing what they're in service of.

**What this check is NOT:**
- Do not flag technical content for being "too complex" — complexity is appropriate for the domain.
- Do not suggest removing detail, caveats, or technical qualifications.
- Do not flag dense tables or architecture descriptions — structured technical content is fine.
- Only flag where the *writing style* creates unnecessary friction for a reader who is already technically capable.

---

### 7. Copy-Paste Artifacts, Formatting Ghosts & Links
*Severity if failed: 🔴 CRITICAL (broken/placeholder links) / 🟣 MEDIUM (formatting)*

- Inconsistent bullet styles (• vs – vs * mixing without reason)
- Broken table formatting (headers don't match column content)
- Leftover tracked-change markers or comment references
- Strange whitespace — double blank lines, unexpected indentation
- **Hyperlinks that say `[link]`, `[TBD]`, `[AWS Cost Link]`, `[URL]`, or any placeholder text** → 🔴 CRITICAL
- **Every hyperlink should be tested** — click and verify it opens the correct resource in an incognito/private browser
- **AWS Pricing Calculator placeholder test:** URL `https://calculator.aws/` (homepage) or `https://calculator.aws/#` (no estimate ID) → 🔴 CRITICAL
- Citations or footnotes from a different document (wrong title, wrong year)
- Page headers/footers that refer to a different document name

---

### 8. Completeness & Factual Plausibility
*Severity if failed: 🟡 HIGH (missing sections) / 🟣 MEDIUM (round numbers, undefined acronyms)*

- **Missing standard sections** — For SOWs, flag if Scope, Deliverables, Timeline are absent
- **Round-number suspicion** — "exactly 10 weeks / 100 hours" with no breakdown warrants a note to verify
- **Self-contradictory numbers** — A budget in the intro that doesn't match the pricing table
- **Undefined acronyms** — Acronyms used without ever being defined
- **Empty sections** — Section headings with no content under them

---

## SOW Module — Dimensions 9–16

**Only run this module if the document has been identified as an SOW.**

---

### 9. Scope Clarity — In-Scope, Out-of-Scope & Customer Requirements
*Severity if failed: 🔴 CRITICAL (no out-of-scope section, unmeasurable success criteria) / 🟡 HIGH (ambiguous deliverables)*

**In-Scope checks:**
- Is there a clearly labelled "In Scope" section?
- Are deliverables expressed as concrete, observable outputs — not vague activities? (Bad: "Support the client team." Good: "Deliver a configured Convogent pipeline with documented API endpoints.")
- Does each in-scope item have enough specificity to determine if it was delivered or not?
- Are there implicit commitments buried in prose not reflected in the formal scope list? (e.g., "we will train all users" in prose but training not listed as a deliverable)
- Each customer requirement is named explicitly — not inferred or paraphrased into vague scope language
- Success criteria are measurable and specific — not "improve performance" or "reduce cost" without a threshold
- Assumptions are separated from requirements; each assumption is specific and verifiable
- Every in-scope deliverable traces to at least one stated requirement or success criterion

**Out-of-Scope checks:**
- Is there an explicit "Out of Scope" section? Flag if missing → 🔴 CRITICAL
- Does it proactively address adjacent items a customer might assume are included? (infrastructure provisioning, data migration, third-party integrations, user training, hypercare support, licensing)
- Flag if the out-of-scope list is suspiciously short (only 1–2 items) — likely incomplete

**Boundary ambiguity:**
- Any item that could reasonably be read as either in or out of scope → 🟡 HIGH
- Any in-scope item that overlaps with or contradicts an out-of-scope item → 🟡 HIGH

---

### 10. Commitment Consistency & Contradiction Check
*Severity if failed: 🔴 CRITICAL (cross-section contradictions) / 🟡 HIGH (missing timeline entries)*

What constitutes a commitment: deliverables, timeline statements, capability claims, SLA/support statements, resource/staffing statements.

**Cross-check for contradictions:**
- Does the deliverable list match the timeline table? Any deliverable with no timeline entry, or vice versa?
- Do capability claims in the solution overview align with what's actually in scope?
- Are there conditional commitments ("if customer provides X, we deliver Y") that are consistent wherever they appear?
- Flag any commitment that appears in one section but is silently absent or contradicted in another

---

### 11. Customer Name & Party Consistency
*Severity if failed: 🔴 CRITICAL (wrong entity) / 🟡 HIGH (inconsistent role labels)*

Extends base Dimension 2 with SOW-specific party roles:
- **Service provider** — Is "Aivar Innovations" referred to consistently? Flag if "we", "Aivar", "Aivar Innovations", "the Vendor" are used interchangeably in a way that creates obligation ambiguity
- **Customer** — Is the customer referred to by one consistent name and role label throughout?
- **Signature block** — Do names and company names in the execution block exactly match how parties are referred to throughout?
- **Third parties** — If subcontractors or partners are mentioned, are they referred to consistently with a clearly defined role?

---

### 12. Timeline & Delivery Realism
*Severity if failed: 🔴 CRITICAL (arithmetic errors, no contingency buffer) / 🟡 HIGH (unrealistic durations)*

- **Timeline arithmetic** — Sum of all phase/sprint durations must equal the total stated engagement duration. Verify the maths.
- **Discovery is a named phase** — A Discovery/Kickoff line item must appear as the first explicit phase, covering requirements finalisation, environment access, and team onboarding. Discovery is never implicit.
- **Dependency sequencing** — Are milestone dependencies logical? (e.g., UAT scheduled before the system is deployed is impossible)
- **Every deliverable has a timeline entry** — No floating deliverables without a phase
- **Review cycle buffer** — At least 3–5 business days per major deliverable review cycle should be built in
- **Start anchor** — Timeline anchored to an execution date or a named trigger condition. If start depends on customer-provided artefacts, a delay clause is present
- **Post-delivery activities** — Vulnerability scans, pen-tests, code handover named and scheduled — not absorbed into the build window
- **Public holidays / leave** — For tight timelines, check whether known holiday periods or customer blackout windows are accounted for

**20% Contingency Buffer Check:**

Every SOW timeline must include a named contingency buffer of at least 20% of the total engagement duration to absorb unknown unknowns — unexpected technical blockers, customer delays, environment access issues, scope clarifications, or team availability.

**How to verify:**
1. Sum the durations of all named phases (excluding the buffer/contingency phase itself)
2. Calculate 20% of that sum
3. Check whether the SOW contains a named buffer — labelled "Contingency", "Buffer", "Risk Reserve", or equivalent — equal to or greater than that 20% value
4. If the buffer is implicit (absorbed into phase estimates silently), flag it — buffer must be explicit and named so the customer understands the schedule includes it

**Examples:**
- 8-week engagement → minimum 1.6 weeks (≈ 2 weeks) named contingency → flag if absent or less than 1.5 weeks
- 12-week engagement → minimum 2.4 weeks (≈ 2.5 weeks) named contingency → flag if absent or less than 2 weeks

**Severity guidance:**
- No contingency buffer at all → 🔴 CRITICAL: the timeline is a best-case plan with no resilience
- Buffer present but less than 10% of total duration → 🟡 HIGH: materially undersized
- Buffer between 10–20% → 🟣 MEDIUM: note the shortfall and recommend increasing to 20%
- Buffer ≥ 20% and explicitly named → pass

**What counts as a valid buffer:**
- A named phase called "Contingency", "Buffer Week", "Risk Reserve", "Float", or equivalent
- A documented percentage added to each phase (e.g., "+20% per sprint for risk")
- A stated policy such as "all estimates include a 20% contingency for unknowns"

**What does NOT count:**
- Vague language like "timelines are estimates" without a quantified buffer
- Review periods (customer feedback windows are not contingency — they are planned activities)
- Padding silently added to individual task estimates without disclosure

---

### 13. Commercial & Pricing Integrity
*Scope: Sales team only — skip this dimension*

> The Sales team owns all commercials (fees, day rates, payment milestones, T&M vs. Fixed Price,
> expense policy). SA-generated SOWs do not include these. Do not review or flag anything in this
> dimension. Append the standard Sales team note at the end of your report instead.

---

### 14. Assumptions, Dependencies & Risks
*Severity if failed: 🟡 HIGH (load-bearing assumptions buried in prose) / 🟣 MEDIUM (missing section)*

**Assumptions:**
- Is there an explicit Assumptions section?
- Does it cover: customer-provided access (environments, credentials, data), customer-provided resources (SMEs, approvers), technology prerequisites, data readiness?
- Assumptions stated as specific verifiable facts ("Customer will provide AWS console access by Day 1") — not vague hedges ("Subject to customer cooperation")
- Any assumption that is load-bearing for a deliverable but buried in prose rather than the Assumptions section → 🟡 HIGH
- The specific Bedrock model version required is named (e.g., "Claude 3.5 Sonnet") — not just "Amazon Bedrock access"

**Dependencies:**
- Are customer-side dependencies (decisions, approvals, data) explicitly called out with timing?
- Are third-party dependencies (vendor APIs, licensing, platform availability) documented?

**Risks:**
- If a risk section exists, are risks linked to mitigation actions or scope exclusions?

---

### 15. Acceptance Criteria & Change Control
*Severity if failed: 🟡 HIGH*

**Acceptance Criteria:**
- Is there a defined acceptance process for each major deliverable?
- Are criteria measurable and objective — not "client is satisfied"?
- Is a review/feedback period specified (e.g., "Client has 5 business days to review and accept each deliverable")?

**Change Control:**
- Is there a Change Control / Change Request section?
- Does it define: what constitutes a change, the process for raising one, and that out-of-scope work will be quoted separately?
- Flag if absent → 🟡 HIGH: without it, scope creep has no formal mechanism

**Termination:**
- Is there early termination language — by either party?

---

### 16. SOW Structural Completeness
*Severity if failed: 🟡 HIGH*

**Required sections checklist:**
- [ ] Executive Summary
- [ ] Background & Context
- [ ] Scope of Work — In Scope
- [ ] Scope of Work — Out of Scope
- [ ] Solution Architecture (with embedded diagram)
- [ ] Deliverables table
- [ ] Timeline & Milestones
- [ ] RACI Matrix (or equivalent Roles & Responsibilities)
- [ ] Assumptions & Dependencies
- [ ] AWS Infrastructure Cost (embedded table + calculator link)
- [ ] Acceptance Criteria
- [ ] Confidentiality & IP
- [ ] Signatures block (full legal entity names + date fields)

Both Aivar Innovations and the customer legal names must be stated correctly in the signature block.

---

## Cost & Architecture Module — Dimensions 17–21

**Only run this module when an AWS cost estimate (PDF export, URL, or embedded table) is provided alongside the SOW.**

---

### 17. Architecture Diagram & Two-Way Audit
*Severity if failed: 🔴 CRITICAL (missing diagram or service mismatch)*

- Architecture diagram is embedded in the SOW — not referenced as a future deliverable or separate attachment
- **Two-way audit — Diagram → Estimate:** Every AWS service in the diagram has a cost line item in the estimate
- **Two-way audit — Estimate → Diagram:** Every cost line item corresponds to a service in the diagram (no orphan line items)
- Architecture directly addresses each stated customer requirement
- Diagram uses official AWS service icons (awsicons.dev or AWS Architecture Center) — no generic cloud shapes for named services
- Diagram shows the correct AWS region label, matching the region in the cost estimate
- Architecture is proportionate — not over-engineered (unnecessary services) or under-engineered (missing required services)
- Diagram is simple enough for a non-architect stakeholder to read in 60 seconds: clean flow direction, labelled connections

**How to run the two-way audit:**
1. List every service in the architecture diagram → check each has a cost line item → mark orphans
2. List every cost line item → check each service appears in the diagram → mark orphans
Any mismatch is 🔴 CRITICAL.

**Note:** IAM Roles for Service Accounts (IRSA) and similar free AWS constructs correctly have no cost line item — do not flag as missing.

---

### 18. Cost Input Metrics & NFR Traceability
*Severity if failed: 🔴 CRITICAL*

- All cost-driving input metrics are explicitly documented: data volume, users, requests/day, retention, instance hours, token volumes, etc.
- Every input metric is either confirmed by the customer (cite source) or flagged as an assumption with basis stated
- **Spot-check:** Pick 3 line items and verify cost = input metric × unit price. If they don't reconcile → flag
- Input metrics are consistent across SOW narrative, architecture diagram, and cost calculator — no metric appearing differently in different sections
- Growth/scaling assumptions documented where inputs will change over time
- Bedrock line items are not left at placeholder config values (1 request/min, 1 input token, 1 output token) — verify actual values match documented input metrics

**NFR & Customer Requirement Traceability — two-way audit:**

Every cost calculator input volume must trace back to a customer-stated NFR, requirement, or documented assumption. Build a traceability table mentally (or explicitly in the report):

| Calculator Input | Value Used | Source in SOW/Requirements |
|---|---|---|
| Concurrent users | e.g. 500 | Customer NFR section / discovery call note |
| Requests/day | e.g. 1M | Customer stated throughput requirement |
| Data volume (GB) | e.g. 200 | Customer data size estimate / assumption |
| Token volume (Bedrock) | e.g. 10K tokens/req | Derived from avg document size assumption |

**Checks to run:**
- [ ] **Concurrency:** If the customer specified concurrent users or peak TPS/RPS, does the calculator reflect that load — not a generic or lower figure? Check EC2 sizing, Lambda concurrency, RDS instance class, EKS node count.
- [ ] **Throughput:** If the customer stated message volumes, API call rates, or batch sizes, are those figures directly traceable to queue depth, Lambda invocations, or API Gateway settings in the calculator?
- [ ] **Data volume:** If storage NFRs were stated (total data size, retention period, ingestion rate), do S3, RDS, DynamoDB, or EFS line items reflect those exact values?
- [ ] **AI/ML token volumes:** For Bedrock or SageMaker, are input/output token counts derived from a documented assumption (avg document length × expected document count) — not left at defaults?
- [ ] **Growth / scale assumptions:** If the SOW mentions phased rollout or expected growth, does the cost estimate reflect the relevant phase's load — and is the phase clearly stated?
- [ ] **No orphan assumptions:** Every assumption used to derive a calculator input must appear in the SOW Assumptions section. If a value was assumed without documenting it anywhere, flag as 🔴 CRITICAL.
- [ ] **No unsourced round numbers:** Input values like "1,000 users", "10 GB/day", "1M requests" with no traceability to a customer statement or named assumption are suspect — flag for verification.

**Severity guidance:**
- Calculator input with no traceable source anywhere in the SOW → 🔴 CRITICAL
- Calculator input contradicts a customer-stated NFR → 🔴 CRITICAL
- Calculator input derived from an assumption that exists but is vague (no basis) → 🟡 HIGH
- Calculator input is reasonable but undocumented growth assumption → 🟣 MEDIUM

**Common mismatch patterns:**
- SOW says "500 concurrent users" but calculator was sized for 100
- Discovery call captured "peak 2,000 TPS" but Lambda concurrency set to 200
- Customer NFR states "99.9% availability" but RDS is single-AZ (no cost for Multi-AZ in estimate)
- Bedrock token count based on no stated document size assumption — left at placeholder

---

### 19. Cost Calculator & Estimate Presentation
*Severity if failed: 🔴 CRITICAL (no embedded table or placeholder URL) / 🟡 HIGH (totals mismatch)*

- Cost estimate is presented as an **embedded table** in the SOW — not just a link (minimum columns: Service, Description, Monthly Cost USD)
- In addition to the embedded table, the AWS Pricing Calculator shareable link is included
- **Link has been tested in incognito/private browser** and opens the correct estimate in the correct region
- The total in the embedded table matches the total in the calculator link exactly (same currency, same rounding)
- No `[link]`, `[TBD]`, `[AWS Cost Link]`, or placeholder hyperlink text anywhere
- Currency stated explicitly (USD, INR, etc.) and consistent throughout
- Estimate distinguishes between one-time costs, recurring infrastructure (monthly/annual), and any licensing costs
- An executive cost summary is present for non-technical stakeholders
- **Placeholder link test:** URL `https://calculator.aws/` (homepage) or `https://calculator.aws/#` (no estimate ID) → 🔴 CRITICAL

---

### 20. AWS Pricing Accuracy
*Severity if failed: 🔴 CRITICAL (wrong region pricing) / 🟡 HIGH (stale or tiered pricing errors)*

- All unit prices validated against the AWS Pricing page for the **specific selected region** — not assumed from memory or a prior engagement
- AWS Pricing Calculator is set to the correct region — not accidentally left on default (us-east-1)
- For tiered pricing services (S3, Lambda, data transfer), the correct tier has been applied for the estimated usage volume
- Data transfer costs (ingress, egress, cross-AZ, cross-region) are included where applicable
- If Savings Plans, Reserved Instances, or Spot pricing is applied, the discount % and commitment term are documented
- For AI/ML services (Bedrock, SageMaker, Rekognition), the correct pricing model is applied — on-demand vs. provisioned throughput, input vs. output tokens, per-inference vs. per-hour

**Stale pricing rule:** AWS pricing changes. Never reuse figures from an estimate older than 6 months without re-validating.

---

### 21. AWS Region Validation
*Severity if failed: 🔴 CRITICAL*

- The AWS region in the estimate is confirmed to be the region the customer requested or approved
- **All services in the architecture are available in the selected region** — use the AWS Knowledge MCP (`aws___get_regional_availability`) to verify each service rather than checking manually
- If the customer has data residency or regulatory requirements (GDPR, DPDP, financial regulations), the selected region satisfies those constraints — and this is noted in the SOW
- Region name is consistent across all artefacts: SOW narrative, architecture diagram, cost calculator, and calculator link
- **Region naming standard:** Use format `Region Name (region-code)` e.g., `Asia Pacific (Mumbai) ap-south-1` — consistently, everywhere

**Using the AWS Knowledge MCP for region checks:**
```
# List all available AWS regions
aws___list_regions()

# Check if a specific service is available in the target region
aws___get_regional_availability(service="Amazon Bedrock", region="ap-south-1")

# For each service in the architecture, call:
aws___get_regional_availability(service="<service name>", region="<target region code>")
```
Call `aws___get_regional_availability` for every AWS service named in the architecture diagram. Any service returned as unavailable in the selected region is 🔴 CRITICAL.

**Region unavailability handling:** If the customer's preferred region cannot be used, document the reason, name the recommended fallback with rationale, get customer written approval, and update the architecture diagram, assumptions, and cost estimate.

---

### 22. AWS Best Practices & Architecture Alignment
*Severity if failed: 🔴 CRITICAL (anti-patterns) / 🟡 HIGH (missed best practice) / 🟣 MEDIUM (optimization opportunity)*

**Only run when a cost estimate or architecture description is provided. Uses the AWS Knowledge MCP.**

This dimension checks whether the proposed architecture aligns with AWS best practices, Well-Architected Framework pillars, and AWS reference architectures for the use case at hand. Do not rely solely on your training knowledge — actively query the AWS Knowledge MCP to get current guidance.

**Step 1 — Get AWS recommendations for the overall solution:**
```
# Get architecture recommendations based on the use case
aws___recommend(query="<describe the solution use case, e.g. 'serverless RAG pipeline with Bedrock and OpenSearch'>")

# Search for AWS best practice documentation for each key service
aws___search_documentation(query="<service name> best practices <use case>")
# Example: aws___search_documentation(query="Amazon Bedrock RAG best practices production")
# Example: aws___search_documentation(query="Amazon EKS security best practices")
# Example: aws___search_documentation(query="RDS Multi-AZ high availability patterns")
```

**Step 2 — Read relevant AWS documentation for flagged areas:**
```
# Read specific docs pages for deeper checks (use URLs returned by search)
aws___read_documentation(url="<documentation URL from search results>")

# Retrieve skill/pattern guidance for specific AWS services or patterns
aws___retrieve_skill(skill="<pattern name, e.g. 'bedrock-rag', 'eks-cluster', 'serverless'>")
```

**Step 3 — Well-Architected Framework five-pillar check:**

For each pillar, call `aws___search_documentation` with the service and pillar name to get current guidance, then assess the architecture:

| Pillar | Key questions to check |
|---|---|
| **Operational Excellence** | Are CloudWatch alarms, dashboards, and logging configured? Is there a deployment pipeline? Are runbooks referenced? |
| **Security** | Is data encrypted at rest and in transit? Are IAM roles least-privilege? Is VPC configured with private subnets for sensitive services? Is WAF or Shield mentioned for public endpoints? |
| **Reliability** | Are there redundancy mechanisms — Multi-AZ RDS, ALB across AZs, Lambda retry policies? Is there a DR or backup strategy? |
| **Performance Efficiency** | Is the compute choice (EC2 vs Lambda vs Fargate vs EKS) justified for the workload pattern? Are caching layers (ElastiCache, CloudFront) used where appropriate? |
| **Cost Optimization** | Are Savings Plans, Reserved Instances, or Spot considered? Are auto-scaling policies defined? Are there any obviously over-provisioned resources? |

**Step 4 — Service-specific best practice checks:**

For each AWS service in the architecture, search for its production best practices and flag gaps:
```
aws___search_documentation(query="<service> production best practices checklist")
```

Common patterns to look for:
- **Bedrock:** Knowledge Base chunking strategy, embedding model selection, guardrails configured, model access granted in correct region
- **EKS:** Node group sizing, cluster autoscaler configured, network policy applied, IRSA for pod IAM
- **RDS:** Multi-AZ for production, automated backups enabled, parameter group tuned, no public accessibility
- **S3:** Bucket policies, versioning, lifecycle rules, no public access unless CDN-fronted
- **Lambda:** Concurrency limits set, DLQ configured, VPC placement only if needed (latency tradeoff), layer reuse
- **OpenSearch / Elasticsearch:** Dedicated master nodes for clusters >10 nodes, UltraWarm for older indices, fine-grained access control

**Step 5 — Cross-reference with AWS reference architectures:**
```
aws___search_documentation(query="AWS reference architecture <use case keyword>")
# E.g.: "AWS reference architecture conversational AI" or "AWS generative AI RAG pipeline"
```
Check if the proposed architecture deviates from the AWS reference architecture for the same use case. Flag deviations with a reason — deviations are not automatically wrong, but must be justified.

**Severity guidance:**
- Known anti-pattern (e.g., storing secrets in environment variables, public S3 bucket for sensitive data, single-AZ RDS for production) → 🔴 CRITICAL
- Missed standard best practice (e.g., no CloudWatch alarms, no backup policy, no WAF on public ALB) → 🟡 HIGH
- Optimization opportunity identified via AWS docs (e.g., Graviton instances would reduce cost 20%, Spot feasible for batch workload) → 🟣 MEDIUM
- Minor deviation from reference architecture with reasonable justification → 🔵 LOW

**Output format for this dimension:**
For each finding, cite the specific AWS documentation URL or blog post that supports it — the MCP search results provide these. This makes findings actionable and credible.


---

## Quick Red-Flag Checklists

Use these as a 60-second pre-read before running the full review.

### All Documents
- [ ] Any `[brackets]`, `{{handlebars}}`, `<placeholders>`, or `___` blanks?
- [ ] Any open questions addressed to the reader?
- [ ] Any "I hope this helps" / "Let me know if…" LLM artifacts?
- [ ] Does the customer name stay consistent throughout?
- [ ] Sections that reference a different company or project?
- [ ] Any repeated paragraphs?
- [ ] Broken cross-references ("See Section X")?
- [ ] Sentences that end abruptly?
- [ ] Contradictory numbers or dates?
- [ ] Undefined acronyms?
- [ ] Tone consistent start to finish?
- [ ] All TOC headings present in the body?
- [ ] Any paragraphs longer than 5 lines that could be split or structured?
- [ ] Any sentences exceeding ~25 words that obscure the main point?
- [ ] Any paragraph comparing 3+ things in prose that should be a table?
- [ ] Any nested "if/unless/when/except" chains (more than one level deep)?
- [ ] Do all major sections open with a clear orienting sentence?

### SOW (additional)
- [ ] Explicit "Out of Scope" section present?
- [ ] All in-scope deliverables have a corresponding timeline entry?
- [ ] Timeline rows sum to the stated total duration?
- [ ] Is there an explicit named contingency buffer ≥ 20% of total engagement duration?
- [ ] Discovery is a named phase?
- [ ] Change control language present?
- [ ] Acceptance criteria are measurable (numbers, thresholds, observable conditions)?
- [ ] Both party legal names correct in signature block?
- [ ] Assumptions listed as specific, verifiable statements?
- [ ] *(Commercials: skip — Sales team handles separately)*

### Cost Estimate (additional)
- [ ] Calculator URL is not `https://calculator.aws/` (homepage placeholder)?
- [ ] Calculator URL tested in incognito?
- [ ] No line items show $0 for a service that should have cost (e.g., Bedrock at $0)?
- [ ] No line items with placeholder config values (1 req/min, 1 token, 1 record)?
- [ ] Region in calculator matches region stated in SOW?
- [ ] SES / email services present but email is Out of Scope → flag mismatch?
- [ ] EKS "hybrid nodes" for a standard cloud deployment → flag (likely wrong config)?
- [ ] Architecture diagram embedded in SOW (not just described)?

---

## Injecting Review Comments into the DOCX

**This is a mandatory step — not optional.** Every finding must be injected as an inline Word comment into the original DOCX so the author can address issues in context without cross-referencing a separate file.

**If the input document is not a `.docx`** (e.g., PDF or plain text), ask the user to upload the DOCX version before injecting comments:
> "Please upload the `.docx` version of this document so I can inject review comments directly into it."

**Workflow — run after all checks are complete:**

```bash
# 1. Copy the docx scripts into working directory
cp -r /mnt/skills/public/docx/scripts /home/claude/scripts

# 2. Unpack the DOCX
python /home/claude/scripts/office/unpack.py <input.docx> /tmp/sow-unpacked/

# 3. For each finding, inject one comment anchored to the relevant paragraph:
python /home/claude/scripts/comment.py /tmp/sow-unpacked/ <comment_id> "<comment_text>" --author "Aivar SA Review"

# 4. Inject the comment range markers into word/document.xml at the exact paragraph:
#    <w:commentRangeStart w:id="<comment_id>"/>  before the target run
#    <w:commentRangeEnd w:id="<comment_id>"/>    after the target run
#    <w:r><w:commentReference w:id="<comment_id>"/></w:r>

# 5. Re-pack into the annotated DOCX
python /home/claude/scripts/office/pack.py /tmp/sow-unpacked/ <document-name>-reviewed.docx --original <input.docx>
```

**Comment text format — keep it short and actionable:**
- Line 1: `[CRITICAL]`, `[HIGH]`, `[MEDIUM]`, or `[LOW]` — severity label
- Line 2: One sentence stating exactly what is wrong
- Line 3: One sentence stating exactly what to do

Example:
```
[CRITICAL]
Calculator URL is a homepage placeholder (https://calculator.aws/) — no estimate is linked.
Replace with the shareable URL from your saved estimate: calculator.aws → Share → Copy link.
```

**Placement rules:**
- Anchor to the most specific text that triggered the finding — a sentence, a table cell, a heading, a URL
- One comment per finding — never combine two issues in one comment
- For cost estimate issues: anchor to the calculator URL paragraph or the cost table heading
- For timeline issues: anchor to the specific row or the total duration cell
- For missing sections: anchor to the nearest heading where the section should appear

**Output:** Save the annotated file as `<document-name>-reviewed.docx` alongside the action item `.md` file.

---

## Output Format

Produce two output files:

| File | Purpose |
|---|---|
| `<document-name>-review.md` | Action item task list — one checkbox per finding, grouped by severity |
| `<document-name>-reviewed.docx` | Original DOCX with every finding injected as an inline Word comment |

The `.md` file is for tracking and sharing. The `.docx` file is for the author to open and address comments in context.

Do not write narrative summaries, praise, or dimension-by-dimension commentary in the `.md` file. Every finding becomes one action item. Dimensions with no findings are omitted entirely.

```markdown
# Review: [document filename]
**Document:** [filename] | **Customer:** [name] | **Type:** [SOW+Cost / SOW / Doc]
**Region:** [SOW stated] → [estimate] [✅ match / ❌ MISMATCH]  ← omit if no cost estimate
**Date:** [YYYY-MM-DD] | **Overall Risk:** [🔴 High / 🟡 Medium / 🟢 Low]

---

## Action Items

### 🔴 Critical — Fix before sending

- [ ] **[Dim#] [Short label]** — [Section/location]: [Exactly what is wrong. Exactly what to do to fix it.]
- [ ] **[Dim#] [Short label]** — [Section/location]: [Exactly what is wrong. Exactly what to do to fix it.]

### 🟡 High — Fix before sending

- [ ] **[Dim#] [Short label]** — [Section/location]: [Exactly what is wrong. Exactly what to do to fix it.]

### 🟣 Medium — Fix if time allows

- [ ] **[Dim#] [Short label]** — [Section/location]: [Exactly what is wrong. Exactly what to do to fix it.]

### 🔵 Low / Consider

- [ ] **[Dim#] [Short label]** — [Section/location]: [Exactly what is wrong. Exactly what to do to fix it.]

---

> 📎 Sales team: please attach the commercial schedule when sharing this SOW with the customer.
```

**Action item rules:**
- One line per finding. No paragraph explanations.
- Location must be specific: section name, table row, paragraph number, or quoted text fragment — not just "throughout the document" unless truly pervasive.
- The fix must be stated as a concrete action: "Change X to Y", "Add a row for Z", "Remove the placeholder in Section 4.2", "Re-run calculator with 500 concurrent users instead of 100."
- If a finding has a source URL from the AWS Knowledge MCP (dim 22), append it as a bare link on the same line.
- Omit any dimension that produced zero findings — no "✅ Passed" lines, no acknowledgements.
- Total output should be scannable in under 2 minutes.

---

## Workflow

1. **Read the document** using the appropriate method for its file type.
2. **Detect document type** — scan for SOW signals. If SOW is detected and no cost estimate CSV has been uploaded, trigger the Cost Estimate Nudge (see above) before proceeding to the final output.
3. **Build an entity map** — extract all company names, product names, people names, dates, version numbers, and (for SOWs) all deliverables, party labels, and (for cost estimates) all services, regions, and totals.
4. **Run base dimensions 1–8** in sequence.
5. **If SOW: run dimensions 9–16** in sequence.
6. **If cost estimate provided: run dimensions 17–22** in sequence. Do the two-way architecture ↔ cost audit (dim 17) first, then line-item checks (18–21), then call the AWS Knowledge MCP for region validation (dim 21) and best practices alignment (dim 22). For dim 22, use `aws___recommend`, `aws___search_documentation`, `aws___read_documentation`, and `aws___retrieve_skill` to ground findings in current AWS guidance rather than relying solely on training knowledge.
7. **Classify each finding** by severity (🔴 CRITICAL / 🟡 HIGH / 🟣 MEDIUM / 🔵 LOW).
8. **Write the action item list** using the output format above and save it as `<document-name>-review.md`. No narrative, no praise, no dimension summaries — only findings formatted as action items.
9. **Inject inline comments into the DOCX** — run the DOCX commenting workflow for every finding. If the input is not a `.docx`, ask the user to upload the DOCX version before this step. Save the annotated file as `<document-name>-reviewed.docx`.

---

## Notes on False Positives

- Intentional variations in entity naming (e.g., "Aivar" vs. "Aivar Innovations" in the same doc) should be flagged but marked 🔵 LOW unless context suggests a mistake.
- Industry jargon that sounds like buzzwords but is genuinely domain-specific should not be flagged.
- Short forms of company names ("the Company", "the Client") used after first full mention are acceptable.
- IRSA and similar free AWS constructs correctly have no cost line item — do not flag as missing.
- Route 53, ACM (Certificate Manager), and similar implicit hosting costs may not appear in the architecture table but can appear in the estimate — note as "implicit infrastructure — acceptable; consider adding for completeness" (🔵 LOW).
