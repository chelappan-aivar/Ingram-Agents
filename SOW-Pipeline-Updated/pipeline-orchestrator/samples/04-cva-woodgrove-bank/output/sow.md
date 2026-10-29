# Cloud Viability Assessment (Full) - Statement of Work
Prepared for work related to the AWS Cloud Viability Assessment (Full) for Woodgrove Bank, delivered through Contoso Cloud Partners.
# Preface
The information contained within this document is confidential between Ingram Micro and Contoso Cloud Partners (the Partner) and shall be used by the Partner only for the purposes of its engagement with its End-Customer, Woodgrove Bank.
## Document Purpose
This document defines the scope, activities, and deliverables that Ingram Micro will provide as part of the Cloud Viability Assessment (CVA) engagement delivered on behalf of the Partner to Woodgrove Bank. It establishes the services Ingram Micro will perform, the responsibilities of each party, and the outputs that will be produced to support Woodgrove Bank's assessment of AWS as a target platform.
# Service/Engagement Overview
## End-Customer Details
| Field | Detail |
| --- | --- |
| End-Customer Name | Woodgrove Bank (End-Customer) |
| End-Customer Country | Netherlands |
## Partner Details
| Field | Detail |
| --- | --- |
| Partner Business Name | Contoso Cloud Partners (Partner) |
| Partner Contact Name | Pieter Jansen |
| Partner Contact Email | pieter.jansen@contoso-cloud.example.com |
## Service/Engagement Details
| Field | Detail |
| --- | --- |
| USC Opportunity ID | USC-2026-0731 |
| Service/Engagement Title | AWS Cloud Viability Assessment (CVA) - Full |
| Service/Engagement Synopsis | An 8-week assessment of Woodgrove Bank's 420-server VMware estate for readiness, cost and licensing on AWS |
| Ingram Micro Entity | Ingram Micro BV (Netherlands) |
| Core Technologies | AWS |
| Solution Pillar(s) | Infrastructure and Application Modernisation |
| Service/Engagement Location(s)/Site Address | Remote |
## Linked Documentation
This Statement of Work is intended to be read in conjunction with the applicable country-specific or partner commercial agreement(s), which define the commercial terms, value, duration, and delivery milestones associated with this Statement of Work.
# Service/Engagement Scope
This section of the document provides an overview of the agreed Professional Services, to be delivered by Ingram Micro for Woodgrove Bank on behalf of Contoso Cloud Partners as part of this service/engagement.
## Service Overview
The Cloud Viability Assessment (CVA) gives Woodgrove Bank a clear, evidence-based view of its current infrastructure estate and its readiness, suitability, and commercial viability for migration to AWS. Woodgrove Bank's hosting contract for about 420 VMware servers ends in September 2027, and this assessment provides the facts needed to decide whether to renew or move.
The assessment collects live utilisation data for the estate (about 300 Windows, 90 Linux and 30 SQL Server hosts, around 180 TB of allocated storage) using the AWS Migration Evaluator collector over a 21-day observation period, then analyses it in line with the AWS Optimisation and Licensing Assessment (OLA) framework.
### Cloud Viability Assessment (Full)
Phase 1: Tool Deployment, Configuration and Validation. Ingram Micro installs, configures, and validates the AWS Migration Evaluator collector with read-only access to vCenter. Deliverables:
Tool deployment and configuration summary
Data collection readiness validation
Phase 2: Observation Period (Live Data Capture). The collector gathers CPU, RAM, storage IOPS and network metrics for 21 days. Deliverables:
Weekly data-collection health checks
Phase 3: Infrastructure Inventory and Workload Profiling. Deliverables:
Full server inventory
Licensing footprint for Windows Server and SQL Server
Phase 4: AWS Rightsizing and Instance Blueprinting in eu-west-1, with eu-central-1 as the alternative. Deliverables:
Utilisation-based AWS rightsizing outputs
Instance blueprint including family, type, count, tenancy, and region
Phase 5: Licensing Optimisation and Scenario Modelling. Deliverables:
BYOL versus License Included modelling for Windows Server and SQL Server
Phase 6: AWS Cost Modelling and Multi-Scenario Analysis. Deliverables:
Reserved Instance and Savings Plan modelling (1-year and 3-year)
5-year financial comparison with the current data centres
Phase 7: High-level Architecture and Migration Strategy. Deliverables:
A customer workshop in week 2 to capture applications, criticality and objectives
A high-level multi-AZ target architecture and landing zone design
Migration strategy indicators using the 6Rs framework
The appropriate MAP funding motion
Phase 8: Executive Report and Stakeholder Presentation. Deliverables:
CVA assessment report including cost analysis, architecture strategy and MAP funding motions
Stakeholder presentation and readout session
### Deliverable(s)
A comprehensive written assessment report, a stakeholder presentation and executive readout, and a right-sized AWS instance and cost baseline for migration planning.
### Exclusion(s)
Implementation, configuration, or modification of AWS services (no build, deployment, or hands-on engineering).
Remediation or optimisation of existing workloads.
Migration delivery or modernisation activities (no workload moves, refactoring, re-platforming, or PoCs).
Detailed solution design or low-level architectural documentation (LLDs, runbooks, IaC, etc.).
Licensing procurement or license entitlement validation.
Ongoing managed services, monitoring, support, or cost-optimisation beyond the assessment output.
### Caveats and Assumptions
The Partner will ensure Woodgrove Bank grants read-only vCenter access and the permissions needed to operate the data-collection tooling.
All analysis is non-intrusive and uses only performance, utilisation and configuration data; no personally identifiable information is collected.
All pricing and cost models are indicative, based on AWS public pricing for the agreed region and currency at the time of assessment.
Licensing information supplied by the Partner or End-Customer is assumed to be accurate, with no entitlement validation performed.
Timelines depend on the timely provision of access and stakeholder availability.
### Partner Responsibilities
Commercial Responsibility: provide a valid PO covering the associated AWS Funding Value if End-Customer sign-off is not received before funding expiration.
Access and Permissions: ensure Woodgrove Bank provides all required access to enable the collector.
Stakeholder Coordination, Timely Reviews and Scope Governance through Ingram Micro's change-control process.
## Target Architecture
The high-level AWS target architecture recommended by the assessment is shown below. It spans two Availability Zones in the production landing zone, as Woodgrove Bank's regulator expects, and is indicative; it is not a low-level design.

![Architecture Diagram](arch:1)

## Engagement Governance and Quality
Ingram Micro applies the Project Methodology Framework (PMF) and project management standards aligned with PMI, APM and PRINCE2. For this engagement the governance includes a Technical Consultant and a Project Coordinator.
# Pricing
The fee for this engagement is a one-time fixed price of USD 41,000, payable under the applicable commercial agreement referred to in Linked Documentation. The assessment does not build or run any AWS workload.

The cost of running the recommended AWS target environment is not restated in this document. Refer to the cost estimate below, which is the authoritative source for the monthly and annual AWS run-rate:

AWS Pricing Calculator estimate: [cost calculator link]
# Terms and Conditions
## Governing Agreement
Services described in this Statement of Work are delivered subject to Ingram Micro's terms and conditions of sale or other negotiated purchasing agreement executed between Ingram Micro and the Partner. In the event of any conflict, this Statement of Work shall prevail.
## Document Validity
The contents of this document are valid for thirty (30) days.
# Acceptance of Statement of Work
By signing the below, Contoso Cloud Partners confirms that the details contained within this Statement of Work are fit-for-purpose and will meet the intended needs of Woodgrove Bank as part of this engagement.

| Signed on behalf of the Partner | |
| --- | --- |
| Signatory Name | |
| Signature | ____________________ |
