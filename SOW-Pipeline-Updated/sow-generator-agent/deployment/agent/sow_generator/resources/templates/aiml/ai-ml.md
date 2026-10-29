<sample>

[company-logo]

Statement of Work
for

[client-logo]

Important Notice:
The enclosed material is proprietary to Ingram Micro. This material is presented for the purpose of evaluating services and may not be disclosed in any manner to anyone other than the addressee and employees or authorized representatives of Customer named here within.
# Contents
# About Ingram Micro
Ingram Micro Holding Corporation is a leading global distributor of information technology products, cloud, software, commerce and fulfillment services, and lifecycle solutions. Founded in 1979 and headquartered in Irvine, California, the company connects vendors with resellers, managed service providers, and other channel partners worldwide. As one of the world's largest technology distributors, Ingram Micro reported approximately $52.6 billion in net sales in fiscal year 2025, employs roughly 23,500 people, and serves more than 161,000 customers globally while expanding digital platform capabilities such as the Xvantage AI-powered B2B experience platform.
# Statement of Work
This Statement of Work (SOW) is entered into by and between Ingram Micro Holding Corporation ("Ingram Micro") and Customer ("Customer"), as of the date last signed below, the Effective Date, for the purpose of providing certain Ingram Micro Services to Customer. This SOW is subject to the valid, written agreement signed by both parties for the provision of Services, the Agreement.
# Project Overview
## Executive Summary
Customer seeks to deliver intelligent, AI-powered experiences that automate high-volume interactions and augment human teams. Ingram Micro proposes to design, develop, and implement a production-grade AI solution combining generative models, orchestration, and secure enterprise integrations. The engagement will:
Automate targeted business workflows through conversational and agentic interfaces
Support multi-turn dialogue with context preservation and personalization
Integrate with Customer's systems of record and operational platforms
Capture structured outcomes for tracking, escalation, and reporting
Provide human-in-the-loop escalation when automation is insufficient
Apply Retrieval Augmented Generation (RAG) and knowledge grounding where appropriate
Scale with demand while meeting applicable security, privacy, and compliance requirements
# Proposed Solution - Intelligent AI Platform
Ingram Micro will deliver an intelligent AI platform that automates Customer's priority use cases with real-time language and multimodal AI, supporting high-volume interactions with low latency and resilient peak-load handling.
The solution combines model orchestration, speech and text interfaces where required, and governed integrations so teams can deploy, monitor, and improve AI-assisted workflows safely.
Key Capabilities:
Multilingual and multi-channel support:
Phase 1 coverage aligned to agreed languages and channels (e.g., voice, chat, messaging)
Additional languages or channels targeted for upcoming phases as prioritized
AI-driven conversations: Configurable flows and prompts tailored to Customer use cases
Systems integration workflow:
Structured outcomes (updates, escalations, user inputs) pushed securely into Customer systems for resolution and audit
Accuracy & compliance: Low-latency inference, quality controls, and complete audit trails
Operations dashboard: Live insights, analytics, and performance monitoring
Business Benefits:
Lower cost per interaction by automating repetitive tasks and follow-ups
Elastic capacity during campaigns and seasonal peaks
Better use of human experts by routing complex cases only
Consistent customer experience through governed prompts and policies
Clear tracking of interactions and outcomes for compliance and reporting
Actionable intelligence from interaction analytics
## High Level Architecture Diagram

[image]

## AWS Architecture Design Overview
The intelligent AI architecture delivers enterprise-grade automation by combining speech and language understanding, generative models, and secure integration patterns. Components are selected to match Customer requirements for latency, accuracy, and data residency.
The AWS-based design leverages:
Speech and language services tuned for agreed locales and domains, where voice is in scope
LLM pipeline (managed foundation models or equivalent) for natural dialogue and reasoning
Speech synthesis (TTS) with natural voices where voice channels are required
Secure APIs (e.g., Lambda or containerized services) for orchestration and integrations
Encrypted object storage for recordings, transcripts, and artifacts where applicable
Observability (metrics, logs, traces) for model and pipeline health
Queue or event streaming for high-volume, asynchronous workloads when required
### System Components
Experience Layer:
Administrative and operator dashboards for monitoring and analytics
Configuration controls for prompts, flows, and agent behaviour
API and Integration Layer:
API services for webhooks, real-time orchestration, and enterprise connectivity
Integration services for pushing structured outcomes into CRM, ticketing, or custom systems
Authentication and access control for all backend integrations
AI Processing Pipeline:
Modular chains using a conversational AI framework
Typical flow: input capture (text and/or speech) → model reasoning → response generation (text and/or speech)
Session and context tracking for coherent multi-turn interactions
Data Management Layer:
Durable storage for transcripts, recordings, and operational metadata per agreed retention
Session-level context for compliance, audit, and reporting
Media Infrastructure (when voice or realtime is in scope):
Realtime audio transport with low-latency handling
Audio enhancement through normalization and voice activity detection
### Interaction Flow Processing
Outbound and inbound (as scoped):
Interactions are triggered programmatically or by user initiation with context from integrated systems
The agent handles the conversation through the configured AI pipeline
Outcomes are captured and synchronized with Customer systems of record
### Technical Enhancements
Audio (when applicable):
Noise reduction, normalization, and dynamic range control
Voice activity detection for natural, efficient dialogue
Context Handling:
Maintains conversation context across interruptions
Recovers gracefully from unclear or off-topic user inputs
Multilingual and Adaptive:
Language detection and switching per agreed locale strategy
Adaptive response modelling aligned to Customer segments and policies
# Scope of Work – Technical Project Plan - AI Solution
## Project Kick-Off
Initial planning and design workshops will be conducted to define the scope of the AI implementation within Customer. These workshops will focus on:
Understanding priority use cases, scenarios, and success metrics
Assessing readiness of existing infrastructure and integration endpoints
Establishing detailed tasks with clearly defined milestones
Refining the solution flow based on discovery sessions
Deliverables:
Project charter and timeline
Detailed requirements document
Technical infrastructure assessment report
## Identification of Generative AI Use Cases
Ingram Micro will work closely with Customer to uncover impactful generative AI use cases within the agreed domain. This will involve:
Stakeholder Interviews: Engaging business and operations leaders to capture challenges and priorities
Process Analysis: Reviewing historical interaction data and workflows to identify automation opportunities
Pilot Design: Proposing a proof-of-concept with agreed volume and success criteria before full-scale roll-out
Feasibility Study: Mapping candidates to priority and implementation phase
## Generative AI Readiness
Ingram Micro will assess Customer's readiness for AI deployment across:
Data Infrastructure: Quality, availability, and governance of APIs, knowledge sources, and channels
Technical Expertise: In-house AI, integration, and cloud capabilities
Organizational Readiness: Stakeholder support and change management readiness
Strategic Alignment: Mapping AI adoption with business and customer experience strategy
Compliance & Governance: Evaluation of data privacy, residency, and regulatory adherence
## AI System Design and Development
### Core Agent Development
Conversation Flow Design
Design structured flows for agreed use cases
Create dialogue scripts and prompts for scoped languages and channels
Response handling for user variations and clarifying fallbacks

[image]

AI Model Integration
Configure model access through a unified gateway (e.g., LiteLLM) to Amazon Bedrock, SageMaker, or agreed providers
Tune prompts and parameters for natural, on-policy responses
Speech Processing Implementation (when in scope)
Integration with STT services supported by the target architecture
Integration with TTS services supported by the target architecture
Implement voice activity detection and audio enhancement where applicable
Optimize speech components for agreed languages
### System Integration
Enterprise Integration
Integrate with Customer systems to retrieve context and persist outcomes
Implement safe data transfer and synchronization protocols
Frontend Development
Build dashboards for monitoring and analytics
Provide administrative controls for configuration
### Infrastructure Setup
Media Infrastructure (when in scope)
Integrate realtime audio processing per agreed hosting model
Server and API Configuration
Deploy API services for webhook and orchestration handling
Configure load balancing and auto-scaling on AWS
Implement role-based access control and monitoring
Data Storage and Management
Configure encrypted storage for recordings, transcripts, and artifacts as scoped
Use agreed operational data stores for session and configuration data
Implement data retention, backup, and recovery policies
## Delivery Methodology
Ingram Micro utilizes an agile project management methodology to ensure Customer has continuous visibility into project progress. A Delivery Manager will be assigned and will be the primary point of contact for all project-related updates and issue resolution. Weekly progress meetings will be held to review milestones and align expectations. Customer will have access to project management tools to monitor progress, track issues, view backlogs, and follow sprint activities in real time, enabling proactive communication throughout the engagement.
## Testing & Validation
Validation and Performance: Ingram Micro will perform functional, integration, performance, and load testing to verify:
All conversation and agent workflows within scope
Synchronization with integrated systems
Multi-turn context preservation and intent handling
Channel integrations (e.g., telephony, messaging) where applicable
Ability to handle peak loads reliably
Customer will participate in ongoing testing cycles and provide feedback for refinements.
Security and Well-Architected Review: Ingram Micro will validate secure data handling, access controls and regulatory alignment, conduct vulnerability assessments, and review the solution against the AWS Well-Architected Framework across the operational excellence, security, reliability, performance efficiency, cost optimisation and sustainability pillars. The review findings and any accepted deviations from Well-Architected best practice, with their residual risk, are recorded and shared with the Customer
Model Quality: Testing will verify behaviour aligns with the default capabilities of the chosen models; further improvement may require additional tuning or data work in future phases
## Documentation & Knowledge Transfer
Comprehensive documentation will be provided, covering the AI solution architecture, integration diagrams, and detailed operational guidelines for Customer personnel. This documentation will also include troubleshooting steps and best practices to ensure optimal performance.
Documentation deliverables will include:
System architecture documentation
Deployment and configuration guides
User manuals for administrators and operators
Troubleshooting guides and FAQs
API documentation for integrations
Hands-on knowledge transfer sessions will be conducted throughout the project lifecycle, culminating in a comprehensive workshop before final deployment. These sessions will focus on:
System administration and management
Monitoring and troubleshooting
Performance optimization
Ongoing maintenance procedures
## Project Success Criteria
The project will be deemed successful upon:
Functionality: The AI solution conducts scoped interactions with accurate intent handling and appropriate responses validated by Customer during UAT
System Performance:
Handles expected interaction volume with capacity for peak loads
Maintains agreed response latency targets for the chosen architecture
Model behaviour is understood to reflect the default capabilities of models selected by Customer unless additional tuning is in scope
Integration: Successfully integrates with Customer's existing systems listed in this SOW
User Experience: Provides natural conversation flow and positive end-user experience
Business Impact: Demonstrates measurable improvement in efficiency and cost per interaction for agreed metrics
Knowledge Transfer: Successful completion of training sessions and documentation handover, enabling Customer's team to operate and maintain the system
## Assumptions
It is assumed that Customer will provide
Access to necessary systems and data for integration (APIs, knowledge sources, channel providers)
Required third-party models, licenses, or integration tools needed for development, if any
Infrastructure for realtime or telephony integration when those channels are in scope
Sample interaction data, policies, and scripts for design, training, and testing where applicable
Dedicated resources to collaborate with Ingram Micro during the project (Business SMEs, Technical POCs)
It is assumed that existing infrastructure can support the integration requirements described in this SOW
The project scope is limited to the AI use cases described in this SOW only. Any additional features, use cases, or integrations beyond the defined scope will require a formal change request at additional applicable cost
## Design Considerations
Scalability: Designed for horizontal scaling to support high volumes and peak spikes
Resilience: Automated failover and redundancy to maintain availability
Extensibility: Architecture enables future enhancements and additional channels
Security: Strong encryption, secured IAM roles, and strict access control for sensitive data
Observability: Comprehensive logging, monitoring, and alerting for proactive issue detection
AI Safety: Guardrails, content filtering, and escalation protocols for appropriate interactions and regulatory compliance
Cost Optimization: AWS-native services optimized for efficient use of compute and storage resources
## Customer Dependency
Customer to provide Ingram Micro with access to necessary personnel, documentation, and systems to enable successful execution of the project
Customer to share workflows, diagrams, scripts, and sample data to support design and validation
Designated personnel will be made available, including a project sponsor with decision-making authority, technical points of contact for existing systems, subject matter experts, and resources for testing and validation
Customer will ensure timely engagement, including review and feedback on deliverables within 5 business days, responses to inquiries within 2 business days, and participation in scheduled meetings, workshops, and milestone approvals. Any delays in providing these dependencies may impact project timelines or require scope adjustments
## RACI Matrix
## Out-Of-Scope
The following items are considered out of scope for this project:
Development of custom hardware or carrier infrastructure beyond agreed integration points
Modifications to Customer's systems beyond defined integration scope
Guaranteed model accuracy beyond the default capabilities of chosen models unless explicitly scoped
Ongoing expansion of new scripts or flows beyond the agreed initial workflows
Languages, channels, or regions not explicitly listed for Phase-1
Integration with systems not explicitly listed in this SOW
Advanced analytics and BI dashboards beyond standard reporting included in scope
Physical deployment of on-premises infrastructure
Operational use cases outside the workflows described in this SOW
In case of a scope change, it will be documented as a change request. This change request will undergo an approval process involving the technical team, project managers, and finance. Implementation will only commence after the change request form is signed off by Customer and Ingram Micro. Updated timelines and costs, as detailed in the approved change request, will be communicated to all relevant stakeholders.
## Standard Business Needs
Ingram Micro shall not be held responsible for delays or non-delivery resulting from unforeseen circumstances beyond its reasonable control. This includes, but is not limited to, acts of God, war, strikes, civil unrest, fire, government actions, and transportation disruptions.
Standard Non-Compete, Non-Solicitation and No-Hire clauses of Ingram Micro are applicable.
Working Hours: Ingram Micro works on all weekdays except for any public/regional holidays. Usual operating hours of the Ingram Micro team would be from 9.00 AM to 6.00 PM IST.
# Schedule I: Estimated Project Schedule
Week 1: Planning and Design – Initiate the project with stakeholder interviews, requirements gathering, architecture design, and initial flow planning for agreed use cases.
Weeks 2–3: Development – Build the core AI system including model integration, channel components, enterprise integrations, dashboard components, and conduct initial system testing.
Week 4: Testing and Optimization – Perform end-to-end testing, optimize quality and performance, finalize documentation, and conduct user acceptance and knowledge transfer sessions.
Weeks 5–6: Deployment and Stabilization – Deploy the solution in phases per the agreed pilot, monitor performance, fine-tune the system, deliver final documentation, conduct closing workshops, and complete project handover.
# Pricing
Pricing has two components: a one-time fixed-price delivery fee for the engagement, and the recurring AWS run-rate to operate the solution once live. The one-time fee covers design, build, integration, testing, and handover, and is stated as a fixed price for the agreed scope of USD [one-time total].

The recurring AWS run-rate is not restated in this document. Refer to the cost estimate below, which is the authoritative source for the monthly and annual AWS run-rate:

AWS Pricing Calculator estimate: [cost calculator link]
# Acceptance And Authorization
IN WITNESS WHEREOF, the parties below have executed this SoW as of the latest of the signature dates recorded below.

| Feature | Description | Feasibility Analysis | Priority (P0, P1, P2) | Phases |
| --- | --- | --- | --- | --- |
| Agent Creation | Option to create virtual agents based on requirements that connect with users and conduct conversations using prompts and configured LLM, TTS, and STT. | Readily Available |  | Phase-1 |
| Prompt Inventory | Option to create a prompt inventory to manage, organize, create, edit, delete, and assign prompts to specific agents or conversations. | Readily Available |  | Phase-1 |
| Telephony Integration | Option to integrate the virtual agent with existing SIP trunk and enable seamless connectivity with any SIP-based telephony system. | Readily Available |  | Phase-1 |
| LLM Model and Provider Support | Support for multiple LLM models and providers, including self-hosted options, allowing flexibility to choose the best model per use case. | Available with Customisation | P0 | Phase-1 |
| Outbound and Inbound Calls | Ability for the bot to make and receive calls with call forwarding, call recording, and post-call summaries. | Readily Available |  | Phase-1 |
| Call Retry and Disconnect Handling | Ability to handle call retries and abrupt disconnects to ensure calls are not lost and customer experience is maintained. | Available with Customisation | P2 | Phase-2 |
| Agent Tools | Tools for agents including sending SMS and WhatsApp messages and forwarding calls to human agents. | Available with Customisation | P0 | Phase-1 |
| Call Forwarding | Capability to forward calls to human agents to ensure timely customer assistance. | Readily Available |  | Phase-2 |
| Call Recording | All calls are recorded and stored in S3 with configurable storage options. | Readily Available |  | Phase-1 |
| Post-Call Summary and Disposition | Automatic generation of post-call summaries and dispositions using call transcripts and audio. | Available with Customisation | P0 | Phase-1 |
| Disposition Based on Standard List | Use of a predefined standard disposition list to ensure consistency and accuracy in call categorization. | Available with Customisation | P0 | Phase-1 |
| API | Expose API for queuing call requests for agents. | Readily Available |  | Phase-1 |
| Traffic Control | Option to configure parallel call limits based on available SIP channels and system resources. | Readily Available |  | Phase-1 |
| Testing | Ability to run A/B experiments on system prompts for both agent scripts and summarization. | Work In Progress |  | Phase-2 |
| Latency and Usage Metrics | Capture real-time metrics on latency, including: | Available with Customisation | P1 (As log is available, API exposure & UI Can be P1) | Phase-1 |
| Latency:  Average Latency per Call  Maximum Latency per Call  Minimum Latency per Call  STT-LLM-TTS Latency  Response Latency | Performance metrics related to call and response timing. | Available with Customisation | P1 (As log is available, API exposure & UI Can be P1) | Phase-1 |
| Usage:  LLM Input Tokens  LLM Response Tokens STT Usage  TTS Usage | Usage metrics for AI and speech components. | Available with Customisation | P1 (As log is available, API exposure & UI Can be P1) | Phase-1 |


| Phases/ Tasks/Activities | Responsible | Accountable | Consulted | Informed |
| --- | --- | --- | --- | --- |
| Project Kick-off | Ingram Micro | Ingram Micro | Customer | Customer |
| Requirements Gathering | Ingram Micro | Ingram Micro | Customer | Customer |
| Architecture Design | Ingram Micro | Ingram Micro | Customer | Customer |
| AI Solution Development | Ingram Micro | Ingram Micro | Customer | Customer |
| System Integration | Ingram Micro | Ingram Micro / Customer | Customer | Customer |
| Deployment | Ingram Micro | Ingram Micro | Customer | Customer |
| Knowledge Transfer | Ingram Micro | Ingram Micro | Customer | Customer |


| For Customer | For Ingram Micro Holding Corporation |
| --- | --- |
| Signature:   ____________________ | Signature:   ____________________ |
| Name: | Name: |
| Title: | Title: |
| Date: | Date |
</sample>