# About Ingram Micro Holding Corporation

Ingram Micro Holding Corporation is a leading global distributor of information technology products, cloud, software, commerce and fulfillment services, and lifecycle solutions. Founded in 1979 and headquartered in Irvine, California, the company connects vendors with resellers, managed service providers, and other channel partners worldwide. As one of the world's largest technology distributors, Ingram Micro reported approximately $52.6 billion in net sales in fiscal year 2025, employs roughly 23,500 people, and serves more than 161,000 customers globally while expanding digital platform capabilities such as the Xvantage AI-powered B2B experience platform.

# Statement of Work

This Statement of Work (SOW) is entered into by and between Ingram Micro Holding Corporation ("Ingram Micro") and Northwind Traders ("Customer"), as of the date last signed below, the Effective Date, for the purpose of providing certain Ingram Micro Services to Customer. This SOW is subject to the valid, written agreement signed by both parties for the provision of Services, the Agreement.

# Project Overview

## Executive Summary

Northwind Traders is a food distributor based in Seattle with approximately 400 employees. Its accounts payable (AP) team of six people processes approximately 4,000 supplier invoices per month. The invoices arrive as PDF e-mail attachments and are typed manually into Microsoft Dynamics 365 Business Central. Each invoice takes approximately 6 minutes to process, and approximately 4% contain errors. Northwind Traders seeks to reduce this manual effort and improve accuracy while keeping AP clerks in control of every posting decision.

Ingram Micro proposes to design, develop, and implement a production-grade AI invoice processing assistant on AWS that combines Amazon Textract and Amazon Bedrock with a governed integration to Microsoft Dynamics 365 Business Central. The engagement will:

- Read supplier invoices received as PDF e-mail attachments using Amazon Textract and Amazon Bedrock
- Extract supplier, invoice number, dates, line items, tax, and totals
- Match each invoice to its corresponding purchase order
- Create a draft purchase invoice in Microsoft Dynamics 365 Business Central for an AP clerk to review and approve
- Route exceptions, such as invoices with no purchase order match or price differences over 2%, to a review queue for human-in-the-loop handling
- Deliver under 1 minute of AP clerk time per invoice and an invoice error rate under 1%
- Run in AWS Region us-west-2

# Proposed Solution - Intelligent AI Platform

Ingram Micro will deliver an AI invoice processing assistant that automates Northwind Traders' manual invoice entry. It will turn unstructured PDF invoices into structured, validated draft purchase invoices that are ready for clerk approval.

The solution combines document extraction, generative AI reasoning, purchase order matching, and a governed integration with Microsoft Dynamics 365 Business Central. AP clerks stay in the approval loop, and only exceptions require deeper investigation.

Key Capabilities:

- Invoice data extraction: Amazon Textract and Amazon Bedrock read supplier invoice PDFs and extract supplier, invoice number, dates, line items, tax, and totals.
- Purchase order matching: Each extracted invoice is matched to its purchase order. Invoices with no PO match, or with price differences over 2%, are flagged as exceptions.
- Draft invoice creation: Matched invoices are created as draft purchase invoices in Microsoft Dynamics 365 Business Central for an AP clerk to approve.
- Exception review queue: Exceptions are routed to a review queue so clerks can focus on the invoices that need human judgment.

Business Benefits:

- Reduces AP clerk time per invoice from approximately 6 minutes to under 1 minute
- Reduces the invoice error rate from approximately 4% to under 1%
- Frees the six-person AP team from repetitive data entry across approximately 4,000 invoices per month
- Keeps a clerk approval step before invoices are finalized
- Applies a consistent 2% price-difference check to every invoice

## High Level Architecture Diagram

The high-level flow begins when a supplier invoice arrives as a PDF e-mail attachment. Amazon Textract and Amazon Bedrock read the document and extract the invoice fields. The solution then matches the invoice to a purchase order and creates a draft purchase invoice in Microsoft Dynamics 365 Business Central for clerk approval. Invoices with no PO match, or with price differences over 2%, are placed in a review queue. All components are deployed in AWS Region us-west-2.

## AWS Architecture Design Overview

The architecture delivers enterprise-grade invoice automation by combining document extraction, generative AI, and a secure integration with Northwind Traders' ERP. Components are deployed in AWS Region us-west-2.

The AWS-based design leverages:

- Amazon Textract for reading and extracting content from invoice PDFs
- Amazon Bedrock for generative AI interpretation of invoice content and structuring of extracted fields
- A secure integration with Microsoft Dynamics 365 Business Central for draft purchase invoice creation
- A review queue for exception handling

### System Components

Experience Layer:

- AP clerk review and approval of draft purchase invoices in Microsoft Dynamics 365 Business Central
- Review queue for exceptions requiring clerk attention

API and Integration Layer:

- Integration with Microsoft Dynamics 365 Business Central to retrieve purchase order context and create draft purchase invoices
- Authentication and access control for all backend integrations

AI Processing Pipeline:

- Typical flow: invoice PDF capture → Amazon Textract extraction → Amazon Bedrock reasoning and field structuring → purchase order matching → draft invoice creation or exception routing
- Extracted fields: supplier, invoice number, dates, line items, tax, and totals

Data Management Layer:

- Durable storage of source invoices and extracted data per retention requirements agreed with Customer
- Processing records that support audit and reporting

### Interaction Flow Processing

Invoice processing:

- Supplier invoices arrive as PDF e-mail attachments and enter the processing pipeline
- Amazon Textract and Amazon Bedrock extract the invoice data
- The extracted data is matched to a purchase order
- Matched invoices are created as draft purchase invoices in Microsoft Dynamics 365 Business Central for an AP clerk to approve
- Invoices with no PO match, or with price differences over 2%, are sent to the review queue

### Technical Enhancements

Matching and Validation:

- A 2% price-difference threshold is applied between invoice and purchase order values
- Exceptions are surfaced for clerk review instead of being posted automatically

Human-in-the-Loop:

- An AP clerk approves every draft purchase invoice before it is finalized

# Scope of Work – Technical Project Plan - AI Solution

## Project Kick-Off

Initial planning and design workshops will be conducted to define the scope of the AI implementation within Northwind Traders. These workshops will focus on:

- Understanding the invoice processing workflow, exception scenarios, and success metrics
- Assessing readiness of existing infrastructure and integration endpoints, including Microsoft Dynamics 365 Business Central
- Establishing detailed tasks with clearly defined milestones
- Refining the solution flow based on discovery sessions

Deliverables:

- Project charter and timeline
- Detailed requirements document
- Technical infrastructure assessment report

## Identification of Generative AI Use Cases

Ingram Micro will work closely with Customer to confirm the invoice processing use case and its boundaries. This will involve:

- Stakeholder Interviews: Engaging AP and finance stakeholders to capture challenges and priorities
- Process Analysis: Reviewing the current manual workflow of approximately 4,000 invoices per month, including the roughly 6 minutes of effort per invoice and the 4% error rate, to establish the baseline
- Pilot Design: Agreeing on the invoice volume and success criteria used to validate the solution before full-scale roll-out
- Feasibility Study: Confirming extraction, PO matching, and draft invoice creation against sample supplier invoices

## Generative AI Readiness

Ingram Micro will assess Customer's readiness for AI deployment across:

- Data Infrastructure: Quality and availability of supplier invoices, purchase order data, and Microsoft Dynamics 365 Business Central integration endpoints
- Technical Expertise: In-house AI, integration, and cloud capabilities
- Organizational Readiness: AP team support and change management readiness for a review-and-approve workflow
- Strategic Alignment: Mapping AI adoption with Northwind Traders' finance operations goals
- Compliance & Governance: Evaluation of data privacy, residency (AWS Region us-west-2), and financial controls over invoice approval

## AI System Design and Development

### Core Agent Development

Invoice Extraction Design

- Design the extraction flow for supplier, invoice number, dates, line items, tax, and totals
- Create and tune prompts for interpreting invoice content and structuring extracted fields
- Handle variations in supplier invoice layouts and clarify low-confidence fields through exception routing

AI Model Integration

- Configure Amazon Textract for document extraction from invoice PDFs
- Configure model access to Amazon Bedrock and tune prompts and parameters for accurate, consistent extraction

Purchase Order Matching and Exception Logic

- Match extracted invoice data to purchase orders
- Flag invoices with no PO match
- Flag invoices with price differences over 2%
- Route flagged invoices to the review queue

### System Integration

Enterprise Integration

- Integrate with Microsoft Dynamics 365 Business Central to retrieve purchase order context and create draft purchase invoices for AP clerk approval
- Implement safe data transfer and synchronization protocols

Review Queue

- Provide a review queue so AP clerks can handle exceptions

### Infrastructure Setup

Server and API Configuration

- Deploy the processing services on AWS in Region us-west-2
- Implement role-based access control and monitoring

Data Storage and Management

- Configure encrypted storage for invoice documents and extracted data as scoped
- Implement data retention, backup, and recovery policies agreed with Customer

## Delivery Methodology

Ingram Micro utilizes an agile project management methodology to ensure Customer has continuous visibility into project progress. A Delivery Manager will be assigned and will be the primary point of contact for all project-related updates and issue resolution. Weekly progress meetings will be held to review milestones and align expectations. Customer will have access to project management tools to monitor progress, track issues, view backlogs, and follow sprint activities in real time, enabling proactive communication throughout the engagement.

## Testing & Validation

Validation and Performance: Ingram Micro will perform functional, integration, and performance testing to verify:

- Extraction of supplier, invoice number, dates, line items, tax, and totals from supplier invoice PDFs
- Purchase order matching and the exception rules for no PO match and price differences over 2%
- Creation of draft purchase invoices in Microsoft Dynamics 365 Business Central
- Routing of exceptions to the review queue
- Handling of expected monthly invoice volume
- Measured AP clerk time per invoice and invoice error rate against the targets of under 1 minute and under 1%

Customer will participate in ongoing testing cycles, including user acceptance testing (UAT) by AP clerks, and provide feedback for refinements.

Security Assessments: Ingram Micro will validate secure data handling, access controls, and regulatory alignment, and conduct vulnerability assessments and review the solution against the AWS Well-Architected Framework across its six pillars. Review findings and any accepted deviations from Well-Architected best practice, with their residual risk, are recorded and shared with the Customer.

Model Quality: Testing will verify that behaviour aligns with the default capabilities of Amazon Textract and the Amazon Bedrock models selected; further improvement may require additional tuning or data work in future phases.

## Documentation & Knowledge Transfer

Comprehensive documentation will be provided, covering the AI solution architecture, integration diagrams, and detailed operational guidelines for Customer personnel. This documentation will also include troubleshooting steps and best practices to ensure optimal performance.

Documentation deliverables will include:

- System architecture documentation
- Deployment and configuration guides
- User guides for AP clerks and administrators, including review queue handling
- Troubleshooting guides and FAQs
- Integration documentation for Microsoft Dynamics 365 Business Central

Hands-on knowledge transfer sessions will be conducted throughout the project lifecycle, culminating in a comprehensive workshop before final deployment. These sessions will focus on:

- System administration and management
- Monitoring and troubleshooting
- Performance optimization
- Ongoing maintenance procedures

## Project Success Criteria

The project will be deemed successful upon:

- Functionality: The solution extracts supplier, invoice number, dates, line items, tax, and totals, matches invoices to purchase orders, and creates draft purchase invoices in Microsoft Dynamics 365 Business Central, as validated by Customer during UAT
- System Performance:
  - Handles the expected volume of approximately 4,000 invoices per month
  - Model behaviour is understood to reflect the default capabilities of the models selected unless additional tuning is in scope
- Integration: Successfully integrates with Microsoft Dynamics 365 Business Central and routes exceptions (no PO match, price differences over 2%) to the review queue
- Business Impact: Achieves under 1 minute of AP clerk time per invoice and an invoice error rate under 1%
- Knowledge Transfer: Successful completion of training sessions and documentation handover, enabling Customer's team to operate and maintain the system

## Assumptions

It is assumed that Customer will provide:

- Access to Microsoft Dynamics 365 Business Central and purchase order data necessary for integration
- Access to the supplier invoice PDFs and the e-mail channel through which they are received
- Sample supplier invoices and purchase orders for design, tuning, and testing
- An AWS environment in Region us-west-2, with the access required for deployment
- Dedicated resources to collaborate with Ingram Micro during the project (AP SMEs, Technical POCs)

It is assumed that existing infrastructure can support the integration requirements described in this SOW.

The project scope is limited to the AI use case described in this SOW only. Any additional features, use cases, or integrations beyond the defined scope will require a formal change request at additional applicable cost.

## Design Considerations

- Scalability: Designed to handle the expected invoice volume with capacity for month-end and other peaks
- Resilience: Automated recovery and redundancy to maintain availability
- Extensibility: Architecture enables future enhancements
- Security: Strong encryption, secured IAM roles, and strict access control for financial data
- Observability: Comprehensive logging, monitoring, and alerting for proactive issue detection
- AI Safety: Draft-only invoice creation, clerk approval, and exception routing keep a human in control of financial postings
- Cost Optimization: AWS-native services optimized for efficient use of compute and storage resources, with an expected AWS run-rate of approximately USD 1,200 per month

## Customer Dependency

- Customer to provide Ingram Micro with access to necessary personnel, documentation, and systems to enable successful execution of the project
- Customer to share workflows, diagrams, and sample data (supplier invoices and purchase orders) to support design and validation
- Designated personnel will be made available, including a project sponsor with decision-making authority, technical points of contact for Microsoft Dynamics 365 Business Central, AP subject matter experts, and resources for testing and validation
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

- Modifications to Northwind Traders' systems, including Microsoft Dynamics 365 Business Central, beyond the defined integration scope
- Automatic approval or posting of invoices without AP clerk approval
- Guaranteed model accuracy beyond the default capabilities of chosen models unless explicitly scoped
- Invoice formats or document types other than supplier invoices received as PDF e-mail attachments
- Integration with systems not explicitly listed in this SOW
- Deployment in AWS Regions other than us-west-2
- Operational use cases outside the workflows described in this SOW

In case of a scope change, it will be documented as a change request. This change request will undergo an approval process involving the technical team, project managers, and finance. Implementation will only commence after the change request form is signed off by Customer and Ingram Micro. Updated timelines and costs, as detailed in the approved change request, will be communicated to all relevant stakeholders.

## Standard Business Needs

Ingram Micro shall not be held responsible for delays or non-delivery resulting from unforeseen circumstances beyond its reasonable control. This includes, but is not limited to, acts of God, war, strikes, civil unrest, fire, government actions, and transportation disruptions.

Standard Non-Compete, Non-Solicitation and No-Hire clauses of Ingram Micro are applicable.

Working Hours: Ingram Micro works on all weekdays except for any public/regional holidays. Usual operating hours of the Ingram Micro team would be from 9.00 AM to 6.00 PM IST.

# Schedule I: Estimated Project Schedule

Week 1: Planning and Design – Initiate the project with stakeholder interviews, requirements gathering, architecture design, and initial flow planning for the invoice processing use case.

Weeks 2–5: Development – Build the core AI system including Amazon Textract and Amazon Bedrock integration, purchase order matching and exception logic, the Microsoft Dynamics 365 Business Central integration, and the review queue, and conduct initial system testing.

Week 6: Testing and Optimization – Perform end-to-end testing, optimize extraction quality and performance against the clerk-time and error-rate targets, finalize documentation, and conduct user acceptance and knowledge transfer sessions.

Weeks 7–8: Deployment and Stabilization – Deploy the solution in AWS Region us-west-2, monitor performance, fine-tune the system, deliver final documentation, conduct closing workshops, and complete project handover.

# Pricing
Pricing has two components: a one-time fixed-price delivery fee for the engagement, and the recurring AWS run-rate to operate the solution once live. The one-time fee covers design, build, integration, testing, and handover, and is stated as a fixed price for the agreed scope of USD 90,000.

The recurring AWS run-rate is not restated in this document. Refer to the cost estimate below, which is the authoritative source for the monthly and annual AWS run-rate:

AWS Pricing Calculator estimate: https://calculator.aws/#/estimate?id=sample-northwind-quote
