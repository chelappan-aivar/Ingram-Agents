# Statement of Work: Partner Quote Intelligence Assistant

| Field | Value |
|---|---|
| Document | Statement of Work - Partner Quote Intelligence Assistant |
| Customer | Meridian Tech Solutions, Inc. ("Customer") |
| Provider | Ingram Micro Holding Corporation ("Ingram Micro") |
| Version | 1.0 |
| Status | Approved for solution design |
| Date | 2026-10-20 |
| Customer sponsor | VP Sales Operations, Meridian Tech Solutions |
| Prepared by | Ingram Micro AI/ML Practice |

**Important Notice:** The enclosed material is proprietary to Ingram Micro. This material is presented for the purpose of evaluating services and may not be disclosed in any manner to anyone other than the addressee and employees or authorized representatives of Customer named here within.

## 1 About Ingram Micro
Ingram Micro Holding Corporation is a global distributor of information technology products, cloud, software, commerce and fulfillment services, and lifecycle solutions. It connects vendors with resellers, managed service providers and other channel partners worldwide.

## 2 Statement of Work
This Statement of Work (SOW) is entered into by and between Ingram Micro and Customer, as of the date last signed below (the Effective Date), for the purpose of providing certain Ingram Micro Services to Customer. This SOW is subject to the valid, written agreement signed by both parties for the provision of Services (the Agreement).

## 3 Project Overview

### 3.1 Executive summary
Meridian Tech Solutions is a value-added reseller that sells hardware, software licences and cloud subscriptions to mid-market customers. Its inside sales team receives about 2,400 requests for quotation (RFQs) per month by email, usually as free-text emails with PDF or Excel bill-of-materials (BOM) attachments. Today a sales representative re-keys each line, searches the product catalogue for the matching SKU, checks price and stock in the ERP, and builds the quote in Salesforce by hand. Average quote turnaround is 2.5 business days and about 6% of quotes contain a SKU or pricing error.

Ingram Micro will design, build and deploy a production-grade AI assistant on AWS that reads incoming RFQs, extracts the requested items, matches them to the correct catalogue SKUs, applies Customer pricing rules, and prepares a draft quote that a sales representative reviews and approves before it is pushed to Salesforce. The engagement will:
- Automate RFQ intake from a shared mailbox and from a web upload screen.
- Extract line items from email bodies and PDF, Excel and CSV attachments.
- Match each line to the product catalogue using semantic search, with a confidence score and alternatives.
- Retrieve price and availability from the ERP and apply Customer discount and margin rules.
- Generate a draft quote and a short customer-facing cover note using generative AI.
- Keep a human in the loop: nothing is sent to a customer or written to Salesforce without representative approval.
- Provide a full audit trail and an operations dashboard.

### 3.2 Business objectives
- Reduce average quote turnaround from 2.5 business days to under 4 business hours.
- Reduce quote line errors from about 6% to under 1%.
- Allow the existing team of 55 representatives to absorb 2x RFQ growth without additional hires.

## 4 Proposed Solution

### 4.1 Solution description
The solution is a new web application with a back-end processing pipeline hosted in Customer's AWS account. It sits between the RFQ mailbox, the product catalogue, the ERP (Customer's SAP S/4HANA, system of record for price and stock) and Salesforce Sales Cloud (system of record for quotes and opportunities). It does not replace any of these systems.

### 4.2 Key capabilities

| Feature | Description | Priority | Phase |
|---|---|---|---|
| RFQ email intake | Receive RFQ emails sent to quotes@meridiantech.example, store the raw message and attachments, and create an RFQ record. | P0 | Phase-1 |
| Web upload intake | Representatives upload an RFQ file (PDF, XLSX, CSV, up to 10 MB) and enter customer and opportunity details. | P0 | Phase-1 |
| Document extraction | Extract text, tables and line items (part number, description, quantity, requested term) from emails and attachments, including scanned PDFs. | P0 | Phase-1 |
| Catalogue matching | Match each extracted line to the catalogue by part number and by semantic similarity of the description; return top 3 candidates with a confidence score. | P0 | Phase-1 |
| Price and availability | Call the ERP for list price, Customer cost, stock and lead time for every matched SKU. | P0 | Phase-1 |
| Pricing rules | Apply Customer discount tiers, minimum margin (default 12%) and approval thresholds per customer segment. | P0 | Phase-1 |
| AI draft quote | Generate the draft quote and a cover note with Amazon Bedrock; flag low-confidence lines for review. | P0 | Phase-1 |
| Review and approval | Representative reviews, edits, swaps SKUs, approves or rejects the draft; quotes below minimum margin require Sales Manager approval. | P0 | Phase-1 |
| Salesforce sync | Create or update the Opportunity, Quote and Quote Line Items in Salesforce after approval; retry on failure. | P0 | Phase-1 |
| Audit trail | Record every extraction, match, AI generation (model, prompt version, inputs, output) and user action as an immutable event. | P0 | Phase-1 |
| Notifications | Email the assigned representative when a draft is ready and the Sales Manager when an approval is needed. | P1 | Phase-1 |
| Operations dashboard | RFQ volume, turnaround, match confidence, approval rate and error rate by team and customer. | P1 | Phase-1 |
| Quote search | Search past RFQs and quotes by customer, SKU, amount, status and free text. | P1 | Phase-1 |
| Customer self-service portal | Customers submit RFQs and track status directly. | P2 | Phase-2 (out of scope) |

## 5 Technical Requirements
The following requirements are binding for solution design and acceptance.

### 5.1 Hosting and constraints
- TC-01: The solution runs in Customer's AWS account in a single AWS Region, us-east-1.
- TC-02: For Phase-1, all zonal resources are deployed in a single Availability Zone, us-east-1a. Customer accepts single-AZ availability for Phase-1; recovery is from backups.
- TC-03: AWS-native managed and serverless services are preferred; no self-managed software on EC2 unless no managed option exists.
- TC-04: Generative AI and embeddings are provided only through Amazon Bedrock. Customer data must not be used to train models.
- TC-05: All data, backups and logs remain in us-east-1.
- TC-06: Infrastructure is defined as code (AWS CDK or AWS CloudFormation) and deployed through a pipeline to three environments: development, test and production.
- TC-07: The user interface and generated content are in English only for Phase-1.

### 5.2 Users and volumes

| Item | Value |
|---|---|
| Sales representatives | 55 named users, up to 35 concurrent at peak |
| Sales Managers (approvers) | 8 users |
| Sales Operations administrators | 4 users |
| Read-only finance users | 5 users, must not see Customer cost or margin |
| RFQs received | About 2,400 per month; peak 200 per day at quarter-end |
| Line items per RFQ | Average 18, maximum 500 |
| Attachments | About 70% of RFQs carry an attachment; average 1.2 MB, maximum 10 MB; about 15% are scanned PDFs |
| AI drafts generated | About 3,000 per month including regenerations |
| Product catalogue | About 1.8 million SKUs, 6 GB as a nightly full CSV export; about 40,000 SKU changes per day |
| Growth | 2x RFQs and users within 24 months without redesign |

### 5.3 Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Performance | Web UI p95 response under 2 s for page loads and searches. |
| NFR-02 | Performance | From RFQ received to draft quote ready: p95 under 3 minutes for RFQs up to 50 lines; under 15 minutes for 500 lines. |
| NFR-03 | Performance | Catalogue matching returns top 3 candidates per line in under 1 s p95. |
| NFR-04 | Freshness | Nightly catalogue export indexed and searchable within 2 h of arrival; price and stock always read live from the ERP. |
| NFR-05 | Availability | 99.5% during business hours (Monday-Friday, 07:00-20:00 US Eastern). |
| NFR-06 | Recovery | RPO 12 h and RTO 8 h. |
| NFR-07 | Retention | RFQs, quotes and audit events retained 7 years. Raw emails and attachments move to lower-cost storage after 90 days and remain retrievable within 12 h. |
| NFR-08 | Security | Single sign-on through Customer's Microsoft Entra ID (OIDC) with MFA enforced by the identity provider; role-based access; least-privilege IAM; encryption at rest with customer managed AWS KMS keys and TLS 1.2+ in transit; no publicly accessible data stores; web application firewall on public endpoints. |
| NFR-09 | Observability | Central logs, metrics, traces and alarms for intake, extraction, matching, AI latency and errors, ERP and Salesforce integration failures; alarms routed to the Customer operations team by email and Microsoft Teams. |
| NFR-10 | Cost | Monthly AWS run-rate for production must not exceed USD 5,000 at Phase-1 volumes. Prefer serverless; use Graviton where compute is required. |
| NFR-11 | Compliance | Data includes customer contact names, email addresses and commercial pricing (classified Confidential). No payment card data. SOC 2 aligned controls. |
| NFR-12 | Quality | Catalogue match top-1 accuracy at least 92% and top-3 at least 98% on Customer's labelled test set of 2,000 historical lines. |

### 5.4 Integrations

| System | Requirement |
|---|---|
| RFQ mailbox | Customer's Microsoft 365 forwards mail sent to quotes@meridiantech.example to an inbound email endpoint provided by the solution. Messages up to 25 MB. |
| SAP S/4HANA (ERP) | Price and availability through an OData REST API exposed by SAP API Management on Customer's network, reached over an AWS Site-to-Site VPN to the Customer data centre. OAuth 2.0 client credentials. Rate limit 20 requests per second. |
| Product catalogue | Nightly full export (CSV, about 6 GB) pushed by the ERP team to an SFTP endpoint provided by the solution, authenticated by SSH key. |
| Salesforce Sales Cloud | REST API for Opportunity, Quote and Quote Line Item; OAuth 2.0 JWT bearer flow. Daily API limit 100,000 calls shared with other integrations. |
| Microsoft Entra ID | OIDC sign-in; roles supplied as group claims. |
| Outbound email | Notifications and approved quote emails sent from a meridiantech.example sender address with SPF and DKIM. |
| Microsoft Teams | Operational alarms posted to an incoming webhook channel. |

### 5.5 Data

| Entity | Description | Indicative volume |
|---|---|---|
| RFQ | Source, customer, received time, raw message and attachments, status, assignee | ~2,400 per month |
| ExtractedLine | Part number, description, quantity, term, source location | ~43,000 per month |
| CatalogueItem | SKU, manufacturer part number, description, category, attributes, embedding | ~1.8 million items |
| Quote | Header, lines, prices, margin, approval state, Salesforce ids | ~2,400 per month |
| AIGeneration | Prompt version, model, inputs referenced, output, tokens, latency | ~3,000 per month |
| AuditEvent | Immutable record of system and user actions; tamper-evident | ~60,000 per month |

## 6 Scope of Work

### 6.1 Project kick-off
Planning and design workshops to confirm use cases, success metrics, integration endpoints and environments.
Deliverables: project charter and timeline, confirmed requirements, technical infrastructure assessment.

### 6.2 Solution architecture and design
Ingram Micro will produce the Architecture Decision Record (ADR), AWS architecture diagram and monthly AWS usage estimate for Customer review and sign-off before build.

### 6.3 Build
- Intake services for email, web upload and SFTP catalogue file.
- Extraction pipeline for emails and attachments, including OCR of scanned PDFs.
- Catalogue indexing pipeline (lexical and vector index) refreshed nightly.
- Matching, pricing rules and AI drafting services.
- Web application for review, approval, search and dashboard.
- ERP, Salesforce, Entra ID, email and Teams integrations.
- Audit trail, observability, backup and retention policies.
- CI/CD pipeline and infrastructure as code for three environments.

### 6.4 Testing and validation
Functional, integration, performance and load testing (35 concurrent users and 200 RFQs per day), security assessment, matching accuracy evaluation against the labelled test set, and Customer UAT.

### 6.5 Documentation and knowledge transfer
System architecture documentation, deployment and configuration guides, runbooks, user guides for representatives and administrators, API documentation, and two hands-on knowledge transfer workshops.

## 7 Acceptance Criteria

| ID | Criterion |
|---|---|
| AC-01 | During a 6-week pilot, average quote turnaround is under 4 business hours. |
| AC-02 | Catalogue match accuracy meets NFR-12 on the labelled test set. |
| AC-03 | At least 75% of AI drafts are approved with changes to fewer than 10% of lines. |
| AC-04 | Performance targets NFR-01 to NFR-03 are met at 35 concurrent users during load testing. |
| AC-05 | Every AI generation and user action is traceable in the audit trail with zero gaps in a sample review. |
| AC-06 | Security review confirms SSO enforcement, encryption, private data stores, WAF and cost/margin hiding for read-only finance users. |
| AC-07 | A restore test demonstrates recovery within RPO 12 h and RTO 8 h. |
| AC-08 | Production AWS run-rate at pilot volumes is within NFR-10. |

## 8 Assumptions
- Customer provides access to the RFQ mailbox forwarding rule, SAP API Management, Salesforce sandbox and production orgs, and Entra ID app registration.
- Customer provides a labelled set of 2,000 historical RFQ lines with the correct SKU for accuracy testing.
- The Site-to-Site VPN to the Customer data centre is approved by Customer network security; Customer configures its side of the tunnel.
- Amazon Bedrock model access for the selected models is enabled in us-east-1 in Customer's account.
- Customer pricing and discount rules are documented and stable for the duration of the build.
- Scope is limited to the features in section 4.2 marked Phase-1. Additional features require a change request.

## 9 Customer Dependencies
- A project sponsor with decision authority, technical contacts for SAP, Salesforce, Microsoft 365 and networking, and business SMEs for testing.
- Review and feedback on deliverables within 5 business days and responses to queries within 2 business days.

## 10 Out of Scope
- Customer self-service portal (Phase-2).
- Changes to SAP S/4HANA or Salesforce beyond the integration points listed in section 5.4.
- Automatic sending of quotes without representative approval.
- Languages other than English.
- Multi-AZ or multi-Region deployment in Phase-1.
- Migration of historical quotes from Salesforce into the solution.

## 11 Open Issues

| ID | Issue | Owner |
|---|---|---|
| OI-01 | Selection of the Bedrock text and embedding models, to be confirmed with Customer InfoSec. | Customer InfoSec / Ingram Micro |
| OI-02 | Whether the production availability target after the pilot requires multi-AZ. | VP Sales Operations |
| OI-03 | Whether manufacturer datasheets should be added to the catalogue index in Phase-2. | Sales Operations Lead |
| OI-04 | Confirmation of the SAP API Management rate limit at quarter-end. | SAP platform owner |

## 12 RACI Matrix

| Activity | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Project kick-off | Ingram Micro | Ingram Micro | Customer | Customer |
| Requirements confirmation | Ingram Micro | Customer | Customer | Customer |
| Architecture design | Ingram Micro | Ingram Micro | Customer | Customer |
| Build and integration | Ingram Micro | Ingram Micro | Customer | Customer |
| VPN and identity setup | Customer | Customer | Ingram Micro | Ingram Micro |
| Testing and UAT | Ingram Micro / Customer | Customer | Ingram Micro | Customer |
| Deployment | Ingram Micro | Ingram Micro | Customer | Customer |
| Knowledge transfer | Ingram Micro | Ingram Micro | Customer | Customer |

## 13 Schedule
- Weeks 1-2: Kick-off, requirements confirmation, architecture design and sign-off.
- Weeks 3-8: Build of intake, extraction, catalogue indexing, matching, pricing, AI drafting, web application and integrations.
- Weeks 9-10: System, performance and security testing; UAT.
- Weeks 11-16: Production deployment, 6-week pilot, stabilisation and handover.

## 14 Pricing

| Item | Amount |
|---|---|
| One-time delivery (fixed price) | USD [one-time total] |
| Monthly AWS run-rate | USD [monthly total] |
| Annual AWS run-rate | USD [annual total] |

## 15 Acceptance and Authorization

| For Customer | For Ingram Micro Holding Corporation |
|---|---|
| Signature: ____________________ | Signature: ____________________ |
| Name: | Name: |
| Title: | Title: |
| Date: | Date: |
