# About Ingram Micro Holding Corporation

Ingram Micro Holding Corporation is a leading global distributor of information technology products, cloud, software, commerce and fulfillment services, and lifecycle solutions. Founded in 1979 and headquartered in Irvine, California, the company connects vendors with resellers, managed service providers, and other channel partners worldwide. As one of the world's largest technology distributors, Ingram Micro reported approximately $52.6 billion in net sales in fiscal year 2025, employs roughly 23,500 people, and serves more than 161,000 customers globally while expanding digital platform capabilities such as the Xvantage AI-powered B2B experience platform.

# Statement of Work

This Statement of Work (SOW) is entered into by and between Ingram Micro Holding Corporation ("Ingram Micro") and Fabrikam Insurance ("Customer"), as of the date last signed below, the Effective Date, for the purpose of providing certain Ingram Micro Services to Customer. This SOW is subject to the valid, written agreement signed by both parties for the provision of Services, the Agreement.

# Project Overview

## Executive Summary

Fabrikam Insurance, a US property and casualty insurer (home and auto) headquartered in Columbus, Ohio, receives about 18,000 first notice of loss (FNOL) reports per month by phone, e-mail, and web form. Claims intake agents re-key each report into Guidewire ClaimCenter, read the attached police reports, repair estimates, and photos, and check the policy for coverage. Average handling time is 22 minutes per FNOL, and 9% of new claims need rework because of missing or incorrect data. Storm-peak volumes reach 3x normal (up to 1,800 FNOLs per day) and create a 2-day backlog.

Ingram Micro proposes to design, develop, and implement a production-grade generative AI assistant that reads FNOL submissions and their attachments, pre-fills a draft claim in Guidewire ClaimCenter, and leaves every approval decision with the intake agent. The engagement will:

- Read FNOL e-mails, web-form submissions, and phone transcripts, together with attachments (PDF, images, Word)
- Extract 32 FNOL fields, including claimant, policy number, loss date, location, cause of loss, vehicles or property, injuries, and third parties, with a confidence score per field
- Check coverage against the policy wording (about 1,400 PDF documents across 6 product lines) using Retrieval Augmented Generation (RAG), with citations to the clauses relied upon
- Pre-fill a draft claim in Guidewire ClaimCenter through its REST (Cloud API) interface, with human approval by an intake agent before any claim is created
- Flag missing information and draft a follow-up e-mail to the claimant in English or Spanish
- Detect and redact PII in application logs, keeping all data within the US (us-east-2)
- Give intake supervisors a dashboard of volume, handling time, extraction accuracy, and approval rate
- Scale to absorb 3x storm-peak volumes without a backlog

The engagement targets the following outcomes:

- Average FNOL handling time reduced from 22 minutes to under 8 minutes
- Claim rework reduced from 9% to under 3%
- Field extraction accuracy of at least 95% on an agreed test set of 500 historical FNOLs
- 3x storm-peak volumes absorbed without a backlog

The pilot starts with the auto claims team (40 intake agents). Home claims follow after the pilot is accepted.

# Proposed Solution - Intelligent AI Platform

Ingram Micro will deliver an intelligent FNOL assistant on AWS that automates the capture, extraction, and coverage review of first notice of loss reports. The assistant proposes and the intake agent approves. It supports high-volume intake with low latency and resilient storm-peak handling.

The solution combines model orchestration, document extraction, policy retrieval, and governed integration with Guidewire ClaimCenter, so Fabrikam's claims teams can deploy, monitor, and improve AI-assisted intake safely.

Key Capabilities:

- Multichannel and bilingual support:
  - Phase 1 covers FNOL e-mails, web-form submissions, and phone transcripts produced by the contact centre, for the auto claims pilot
  - English and Spanish are supported for follow-up e-mail drafts (Spanish is used in about 18% of FNOLs)
  - Home claims are targeted for the phase that follows pilot acceptance
- AI-driven extraction: Extraction of 32 FNOL fields from submissions and attachments, with a confidence score per field so agents can focus review on low-confidence values
- Coverage check with citations: Retrieval over the policy wording documents, with the clause relied upon cited to the intake agent
- Systems integration workflow: Draft claims pushed securely into Guidewire ClaimCenter through the Cloud API only after the intake agent approves them
- Accuracy and compliance: Human approval before any claim is created, a complete audit trail of review decisions, PII redaction in application logs, and SOC 2 Type II aligned controls
- Supervisor dashboard: Live insights on volume, handling time, extraction accuracy, and approval rate

Business Benefits:

- Lower cost per FNOL by removing manual re-keying, which accounts for about 14 of the current 22 minutes
- Elastic capacity during storm peaks, avoiding multi-day backlogs and overtime
- Fewer reworked claims through consistent capture of policy number, loss location, and third-party details, the top causes of rework today
- Faster, language-appropriate follow-ups for Spanish-speaking claimants
- Clear tracking of every FNOL, review decision, and outcome for compliance and reporting
- Better use of senior adjusters by surfacing coverage citations and routing only complex cases to them

The following table summarizes the capabilities included in this engagement.

| Feature | Description | Phase |
| --- | --- | --- |
| FNOL Intake | Receive FNOLs from e-mail, the web form, and contact-centre phone transcripts through a secured intake API. | Phase-1 |
| Attachment Processing | Process police reports (PDF), repair estimates (PDF/Word), and up to 10 photos per claim, storing the originals in encrypted storage. | Phase-1 |
| Field Extraction | Extract 32 FNOL fields with a confidence score per field. | Phase-1 |
| Coverage Check | Retrieve relevant policy wording and provide a coverage assessment with citations. | Phase-1 |
| Draft Claim Creation | Pre-fill a draft claim in Guidewire ClaimCenter through the Cloud API. | Phase-1 |
| Human Approval | Review UI where the intake agent edits, approves, or rejects each draft; no claim is created without approval. | Phase-1 |
| Missing Information Follow-up | Flag missing information and draft a follow-up e-mail in English or Spanish. | Phase-1 |
| Supervisor Dashboard | Volume, handling time, extraction accuracy, and approval rate. | Phase-1 |
| PII Redaction in Logs | Detect and redact PII in application logs. | Phase-1 |
| Home Claims Extension | Extend the assistant to home claims after pilot acceptance. | Phase-2 |

## High Level Architecture Diagram

![Architecture Diagram](arch:1)

## AWS Architecture Design Overview

The architecture delivers enterprise-grade automation by combining document extraction, generative models, policy retrieval, and secure integration patterns. Components are selected to meet Fabrikam's requirements for latency, accuracy, and data residency. All components are deployed in AWS region us-east-2 (Ohio), and no customer data is used for model training.

The AWS-based design leverages:

- Amazon Bedrock with Claude models for field extraction, coverage reasoning, and follow-up e-mail drafting
- Amazon Bedrock AgentCore (Runtime) to host the FNOL agent
- Amazon Bedrock Knowledge Bases, backed by Amazon OpenSearch Serverless (vector store), for RAG over the policy wording documents
- Amazon Textract for text and form extraction from PDF and image attachments
- Amazon Comprehend for PII detection and redaction in application logs
- AWS Lambda for intake, orchestration, and ClaimCenter integration functions
- Amazon API Gateway for the review UI and web-form APIs
- Amazon S3 for encrypted storage of attachments and policy documents
- Amazon DynamoDB for FNOL state, review decisions, and the audit trail
- Amazon Cognito for federated sign-in with Microsoft Entra ID
- Amazon CloudWatch for logs, metrics, alarms, and the supervisor dashboard
- Encryption at rest (KMS) and in transit (TLS 1.2+)

### System Components

Experience Layer:

- Review UI for intake agents (the pilot starts with 40 auto claims agents) to inspect extracted fields, confidence scores, coverage citations, and the drafted follow-up e-mail, and to approve, edit, or reject each draft claim
- Supervisor dashboard showing volume, handling time, extraction accuracy, and approval rate
- Single sign-on for intake agents and supervisors using Fabrikam's Microsoft Entra ID, federated through Amazon Cognito

API and Integration Layer:

- Amazon API Gateway exposing the secured intake API for the web form, the review UI, and e-mail and phone-transcript submissions
- AWS Lambda functions for intake, orchestration, and integration with the Guidewire ClaimCenter 10 Cloud API for draft claim creation
- Authentication and access control for all backend integrations

AI Processing Pipeline:

- FNOL agent running on Amazon Bedrock AgentCore (Runtime), invoked by AWS Lambda orchestration
- Typical flow: input capture (e-mail, web form, or phone transcript) → attachment extraction → field extraction with confidence scores → policy retrieval and coverage check with citations → missing-information check → draft claim and follow-up e-mail
- Claude models on Amazon Bedrock for extraction, coverage reasoning, and e-mail drafts in English and Spanish
- Amazon Textract for PDF and image attachments, and Amazon Bedrock Knowledge Bases for retrieval over about 1,400 policy wording PDFs across 6 product lines

Data Management Layer:

- Amazon S3 for attachments and policy wording documents
- Amazon DynamoDB for FNOL state, review decisions, and the audit trail
- Amazon OpenSearch Serverless as the vector store for the knowledge base
- Amazon CloudWatch for logs, metrics, and alarms, with PII redacted by Amazon Comprehend

### Interaction Flow Processing

Inbound FNOL processing:

- An FNOL arrives by e-mail (shared mailbox), web form, or phone transcript (55% phone, 30% e-mail, 15% web form) and is submitted to the intake API
- The FNOL agent extracts the 32 fields from the submission and attachments and assigns a confidence score to each field
- The agent retrieves the relevant policy wording, assesses coverage, and cites the clauses relied upon
- Missing or low-confidence information is flagged, and a follow-up e-mail is drafted in the claimant's language (English or Spanish)
- The draft claim is presented in the review UI, and the intake agent reviews it and approves
- Only on approval is the draft claim created in Guidewire ClaimCenter through the Cloud API, and the decision is recorded in the audit trail

### Technical Enhancements

Document Handling:

- Text and form extraction from police reports, repair estimates, and photos
- Handling of up to 10 photos per claim
- The approach for Word-format attachments will be confirmed during the design workshops

Context Handling:

- Per-field confidence scoring so low-confidence values are highlighted for agent review
- Missing-information detection that targets the most frequent causes of rework: wrong policy number, missing loss location, and missing third-party details

Multilingual and Adaptive:

- Language detection and selection of English or Spanish for follow-up e-mail drafts
- Policy wording knowledge base refreshed on Fabrikam's quarterly policy update cycle

# Scope of Work – Technical Project Plan - AI Solution

## Project Kick-Off

Initial planning and design workshops will be conducted to define the scope of the AI implementation within Fabrikam Insurance. These workshops will focus on:

- Understanding the auto claims pilot use case, FNOL scenarios, and success metrics (handling time, rework rate, extraction accuracy, and storm-peak capacity)
- Assessing readiness of the Guidewire ClaimCenter 10 Cloud API sandbox, the contact-centre transcript feed, the shared mailbox, and the Microsoft Entra ID tenant
- Confirming the 32-field FNOL list, the integration approach for e-mail and phone-transcript submission, and the handling of Word-format attachments
- Establishing detailed tasks with clearly defined milestones
- Refining the solution flow based on the discovery sessions

Deliverables:

- Project charter and timeline
- Detailed requirements document
- Technical infrastructure assessment report

## Identification of Generative AI Use Cases

Ingram Micro will work closely with Fabrikam to confirm the generative AI use cases within the claims intake domain. This will involve:

- Stakeholder Interviews: Engaging Claims Operations, Enterprise Architecture, Claims Intake, and Information Security leaders to capture challenges and priorities
- Process Analysis: Reviewing the current intake process, the FNOL field list, and the top rework causes to confirm automation opportunities
- Pilot Design: Running the auto claims pilot with 40 intake agents against agreed success criteria before roll-out to home claims
- Feasibility Study: Mapping candidates to priority and implementation phase, including the home claims extension

## Generative AI Readiness

Ingram Micro will assess Fabrikam's readiness for AI deployment across:

- Data Infrastructure: Quality, availability, and governance of the ClaimCenter Cloud API, the policy wording documents, the phone transcripts, and the e-mail and web-form channels
- Technical Expertise: In-house AI, integration, and cloud capabilities
- Organizational Readiness: Intake agent and supervisor support and change management readiness for the review-and-approve workflow
- Strategic Alignment: Mapping AI adoption with claims operations and customer experience strategy
- Compliance & Governance: Evaluation of data privacy, US data residency (us-east-2), SOC 2 Type II controls, and the requirement that no customer data is used for model training

## AI System Design and Development

### Core Agent Development

Workflow and Prompt Design

- Design the FNOL extraction, coverage check, and missing-information workflows for the auto claims pilot
- Create prompts for extraction of the 32 FNOL fields with per-field confidence scores
- Create prompts for coverage reasoning with citations to policy wording, and for follow-up e-mail drafts in English and Spanish
- Response handling for incomplete submissions, low-confidence fields, and unclear inputs

AI Model Integration

- Configure access to Claude models on Amazon Bedrock for extraction, coverage reasoning, and e-mail drafting
- Build and run the FNOL agent on Amazon Bedrock AgentCore (Runtime)
- Ingest the policy wording documents into Amazon Bedrock Knowledge Bases, backed by Amazon OpenSearch Serverless (vector store), and document the refresh process for quarterly policy updates
- Tune prompts and parameters for accurate, on-policy responses against the 500-FNOL test set

Document Processing

- Configure Amazon Textract for text and form extraction from PDF and image attachments (police reports, repair estimates, and photos)

### System Integration

Enterprise Integration

- Integrate with the Guidewire ClaimCenter 10 Cloud API to create draft claims, only after intake-agent approval
- Integrate the intake channels (shared-mailbox e-mail, web form, and contact-centre phone transcripts) with the intake API, using an integration approach confirmed with Fabrikam during design
- Implement federated sign-in with Microsoft Entra ID through Amazon Cognito
- Implement safe data transfer and synchronization protocols

Frontend Development

- Build the review UI for intake agents to review, edit, approve, or reject draft claims, with confidence scores, coverage citations, and the drafted follow-up e-mail
- Build the supervisor dashboard in Amazon CloudWatch showing volume, handling time, extraction accuracy, and approval rate

### Infrastructure Setup

The following AWS services are in scope and will be deployed in us-east-2:

- Amazon Bedrock (Claude models for extraction, coverage reasoning, and e-mail drafts)
- Amazon Bedrock AgentCore (Runtime for the FNOL agent)
- Amazon Bedrock Knowledge Bases (RAG over the policy wording documents)
- Amazon OpenSearch Serverless (vector store for the knowledge base)
- Amazon Textract (text and form extraction from PDF and image attachments)
- Amazon Comprehend (PII detection and redaction in logs)
- AWS Lambda (intake, orchestration, and ClaimCenter integration functions)
- Amazon API Gateway (API for the review UI and the web form)
- Amazon S3 (attachments and policy document storage)
- Amazon DynamoDB (FNOL state, review decisions, and audit trail)
- Amazon Cognito (federated sign-in with Microsoft Entra ID)
- Amazon CloudWatch (logs, metrics, alarms, and the supervisor dashboard)

Server and API Configuration

- Deploy API services for the review UI, web form, and intake handling
- Configure auto-scaling to support 1,800 FNOLs per day at storm peak
- Implement role-based access control and monitoring, with CloudWatch alarms for availability and pipeline health

Data Storage and Management

- Configure encrypted storage (KMS at rest, TLS 1.2+ in transit) for attachments, policy documents, and operational data
- Use Amazon DynamoDB for FNOL state, review decisions, and the audit trail
- Implement PII detection and redaction in application logs using Amazon Comprehend
- Implement data retention, backup, and recovery policies as agreed with Fabrikam

## Delivery Methodology

Ingram Micro utilizes an agile project management methodology to ensure Customer has continuous visibility into project progress. A Delivery Manager will be assigned and will be the primary point of contact for all project-related updates and issue resolution. Weekly progress meetings will be held to review milestones and align expectations. Customer will have access to project management tools to monitor progress, track issues, view backlogs, and follow sprint activities in real time, enabling proactive communication throughout the engagement.

## Testing & Validation

Validation and Performance: Ingram Micro will perform functional, integration, performance, and load testing to verify:

- All FNOL extraction, coverage check, and follow-up workflows within scope
- Draft claim creation and synchronization with Guidewire ClaimCenter (sandbox), including enforcement of human approval before any claim is created
- Field extraction accuracy against the agreed test set of 500 historical FNOLs, targeting at least 95%
- Citation of policy wording in coverage checks
- Follow-up e-mail drafts in English and Spanish
- A draft claim ready in under 2 minutes for 95% of FNOLs
- Handling of 1,800 FNOLs per day at storm peak
- 99.9% availability during business hours

Customer will participate in ongoing testing cycles, including user acceptance testing with the auto claims intake team, and provide feedback for refinements.

Security Assessments: Ingram Micro will validate secure data handling, access controls, PII redaction in logs, encryption at rest and in transit, US data residency, Microsoft Entra ID single sign-on, and regulatory alignment, and will conduct vulnerability assessments and review the solution against the AWS Well-Architected Framework across its six pillars. Review findings and any accepted deviations from Well-Architected best practice, with their residual risk, are recorded and shared with the Customer.

Model Quality: Testing will verify behaviour against the agreed 500-FNOL test set. Further improvement beyond the default capabilities of the chosen models may require additional tuning or data work in future phases.

## Documentation & Knowledge Transfer

Comprehensive documentation will be provided, covering the AI solution architecture, integration diagrams, and detailed operational guidelines for Customer personnel. This documentation will also include troubleshooting steps and best practices to ensure optimal performance.

Documentation deliverables will include:

- System architecture documentation
- Deployment and configuration guides
- User manuals for intake agents and supervisors (review UI and dashboard)
- Troubleshooting guides and FAQs
- API documentation for integrations, including the ClaimCenter integration
- Guide to refreshing the policy wording knowledge base on the quarterly update cycle

Hands-on knowledge transfer sessions will be conducted throughout the project lifecycle, culminating in a comprehensive workshop before final deployment. These sessions will focus on:

- System administration and management
- Monitoring and troubleshooting
- Performance optimization
- Ongoing maintenance procedures

## Project Success Criteria

The project will be deemed successful upon:

- Functionality: The FNOL assistant extracts the 32 fields, performs coverage checks with citations, drafts follow-up e-mails in English and Spanish, and pre-fills draft claims in ClaimCenter, validated by Customer during UAT
- System Performance:
  - Field extraction accuracy of at least 95% on the agreed test set of 500 historical FNOLs
  - Draft claim ready in under 2 minutes for 95% of FNOLs
  - Handles 1,800 FNOLs per day at storm peak (3x normal volume) without a backlog
  - 99.9% availability during business hours
  - Model behaviour is understood to reflect the default capabilities of the Claude models selected unless additional tuning is in scope
- Integration: Successfully integrates with Guidewire ClaimCenter 10 (Cloud API), Microsoft Entra ID, and the intake channels listed in this SOW, with no claim created without intake-agent approval
- User Experience: Intake agents and supervisors in the auto claims pilot can review, approve, and monitor FNOLs through the review UI and dashboard
- Business Impact: Demonstrates average FNOL handling time under 8 minutes (from 22 minutes) and claim rework under 3% (from 9%) in the auto claims pilot, measured against the baselines agreed at kick-off
- Security and Compliance: Data remains in us-east-2, PII is redacted in application logs, and no customer data is used for model training
- Knowledge Transfer: Successful completion of training sessions and documentation handover, enabling Customer's team to operate and maintain the system

## Assumptions

It is assumed that Customer will provide:

- A Guidewire ClaimCenter sandbox with Cloud API access by week 2 of the project
- Access to the contact-centre phone transcripts (the contact centre already produces text transcripts of phone FNOLs), the shared mailbox and web-form submissions, and the policy wording documents (about 1,400 PDFs across 6 product lines)
- 500 labelled historical FNOLs for the test set, available in time for the accuracy evaluation
- Sample FNOLs and attachments, the 32-field FNOL list, and current follow-up e-mail templates for design, tuning, and testing
- Access to Fabrikam's Microsoft Entra ID tenant to configure single sign-on
- Dedicated resources to collaborate with Ingram Micro during the project (Business SMEs, Technical POCs)

It is also assumed that:

- Existing infrastructure can support the integration requirements described in this SOW
- The 10-week delivery schedule starts on 11 January 2027 and covers the auto claims pilot with 40 intake agents. The home claims extension follows acceptance of the pilot, and any additional effort or timeline for it will be agreed through a change request
- Average volume is 18,000 FNOLs per month, with storm peaks of 3x normal volume a few days per month
- The AWS run-rate estimate uses on-demand pricing in us-east-2 as of October 2026

The project scope is limited to the AI use cases described in this SOW only. Any additional features, use cases, or integrations beyond the defined scope will require a formal change request at additional applicable cost.

## Design Considerations

- Scalability: Designed for horizontal scaling to support 18,000 FNOLs per month and 1,800 FNOLs per day at storm peak
- Resilience: Automated failover and redundancy to maintain 99.9% availability during business hours
- Extensibility: Architecture enables the planned extension to home claims and future enhancements
- Security: Encryption at rest (KMS) and in transit (TLS 1.2+), secured IAM roles, single sign-on with Microsoft Entra ID, and strict access control for sensitive claimant data
- Observability: Comprehensive logging, monitoring, and alerting in Amazon CloudWatch, with PII redacted from logs
- AI Safety: Human approval before any claim is created, per-field confidence scores, policy citations for coverage checks, and a complete audit trail of review decisions
- Data Residency and Privacy: All data stays in the US (us-east-2), SOC 2 Type II controls apply, and no customer data is used for model training
- Cost Optimization: AWS-native services optimized for efficient use of compute and storage resources

## Customer Dependency

- Customer to provide Ingram Micro with access to necessary personnel, documentation, and systems to enable successful execution of the project
- Customer to share workflows, diagrams, FNOL field lists, templates, and sample data to support design and validation
- Designated personnel will be made available, including an executive sponsor with decision-making authority (Laura Chen, VP Claims Operations), technical points of contact for existing systems (Marcus Reid, Director, Enterprise Architecture), subject matter experts for claims intake (Elena Torres, Claims Intake Manager), a security point of contact (Sam Patel, Information Security Officer), and intake agents for testing and validation
- Customer to provision the ClaimCenter sandbox with Cloud API access by week 2, make the 500 labelled historical FNOLs available for the test set, and confirm the mechanism for delivering shared-mailbox e-mails and phone transcripts to the intake API
- Customer will ensure timely engagement, including review and feedback on deliverables within 5 business days, responses to inquiries within 2 business days, and participation in scheduled meetings, workshops, and milestone approvals. Any delays in providing these dependencies may impact project timelines or require scope adjustments

## RACI Matrix

| Phases/ Tasks/Activities | Responsible | Accountable | Consulted | Informed |
| --- | --- | --- | --- | --- |
| Project Kick-off | Ingram Micro | Ingram Micro | Customer | Customer |
| Requirements Gathering | Ingram Micro | Ingram Micro | Customer | Customer |
| Architecture Design | Ingram Micro | Ingram Micro | Customer | Customer |
| AI Solution Development | Ingram Micro | Ingram Micro | Customer | Customer |
| System Integration | Ingram Micro | Ingram Micro / Customer | Customer | Customer |
| Deployment | Ingram Micro | Ingram Micro | Customer | Customer |
| Knowledge Transfer | Ingram Micro | Ingram Micro | Customer | Customer |

## Out-Of-Scope

The following items are considered out of scope for this project:

- Commercial lines, fraud scoring, and payments
- Home claims roll-out within the 10-week pilot schedule; it follows pilot acceptance and is subject to a change request
- Voice, telephony, or speech-to-text services; phone FNOLs are processed from the transcripts the contact centre already produces
- Modifications to Guidewire ClaimCenter beyond draft claim creation through the Cloud API
- Integration with the policy administration system or any other system not explicitly listed in this SOW
- Automated claim creation without intake-agent approval, and automated sending of follow-up e-mails without intake-agent review
- Training or fine-tuning of models on Customer data
- Guaranteed model accuracy beyond the default capabilities of the chosen models, other than the agreed 95% target on the 500-FNOL test set
- Languages, channels, or regions not explicitly listed for Phase-1 (English and Spanish; e-mail, web form, and phone transcripts; US us-east-2 only)
- Advanced analytics and BI dashboards beyond the supervisor dashboard included in scope
- Physical deployment of on-premises infrastructure
- Operational use cases outside the workflows described in this SOW

In case of a scope change, it will be documented as a change request. This change request will undergo an approval process involving the technical team, project managers, and finance. Implementation will only commence after the change request form is signed off by Customer and Ingram Micro. Updated timelines and costs, as detailed in the approved change request, will be communicated to all relevant stakeholders.

## Standard Business Needs

Ingram Micro shall not be held responsible for delays or non-delivery resulting from unforeseen circumstances beyond its reasonable control. This includes, but is not limited to, acts of God, war, strikes, civil unrest, fire, government actions, and transportation disruptions.

Standard Non-Compete, Non-Solicitation and No-Hire clauses of Ingram Micro are applicable.

Working Hours: Ingram Micro works on all weekdays except for any public/regional holidays. Usual operating hours of the Ingram Micro team would be from 9.00 AM to 6.00 PM IST.

# Schedule I: Estimated Project Schedule

The 10-week engagement starts on 11 January 2027.

Week 1: Planning and Design – Initiate the project with stakeholder interviews, requirements gathering, confirmation of the 32-field FNOL list, architecture design, and workflow planning for the auto claims pilot.

Weeks 2–5: Development – Build the core AI system including Amazon Bedrock model integration, the FNOL agent on Amazon Bedrock AgentCore (Runtime), the policy knowledge base, Amazon Textract attachment processing, Amazon Cognito single sign-on with Microsoft Entra ID, the review UI, the supervisor dashboard, and the Guidewire ClaimCenter integration (sandbox available by week 2), and conduct initial system testing.

Weeks 6–7: Testing and Optimization – Perform end-to-end testing, evaluate extraction accuracy on the 500-FNOL test set, run storm-peak load testing, complete security validation, optimize quality and performance, finalize documentation, and conduct user acceptance and knowledge transfer sessions.

Weeks 8–9: Deployment and Stabilization – Deploy the solution to the auto claims pilot team (40 intake agents), monitor performance on the supervisor dashboard, and fine-tune the system.

Week 10: Handover – Deliver final documentation, conduct the closing knowledge transfer workshop, review pilot results against the success criteria, and complete project handover. The home claims extension follows acceptance of the pilot.

# Pricing
Pricing has two components: a one-time fixed-price delivery fee for the engagement, and the recurring AWS run-rate to operate the solution once live. The one-time fee covers design, build, integration, testing, and handover, and is stated as a fixed price for the agreed scope of USD 190,000.

The recurring AWS run-rate is not restated in this document. Refer to the cost estimate below, which is the authoritative source for the monthly and annual AWS run-rate:

AWS Pricing Calculator estimate: https://calculator.aws/#/estimate?id=sample-fabrikam-fnol
