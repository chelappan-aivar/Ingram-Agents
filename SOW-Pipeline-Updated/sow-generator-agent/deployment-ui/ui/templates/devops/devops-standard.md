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
Customer seeks to modernize its software delivery and cloud operations through a comprehensive DevOps engagement. Ingram Micro proposes to design and implement an automated, secure, and observable delivery platform that accelerates release velocity while improving reliability. The engagement will:
Establish Infrastructure as Code (IaC) for repeatable, auditable environment provisioning
Build CI/CD pipelines that automate build, test, security scanning, and deployment
Containerize workloads and introduce orchestration for scalable, resilient operations
Implement end-to-end observability covering metrics, logs, traces, and alerting
Embed security and compliance controls across the delivery lifecycle (DevSecOps)
Transfer operational knowledge so Customer's team can own and extend the platform
# Proposed Solution - DevOps Platform
Ingram Micro will deliver a cloud-native DevOps platform that standardizes how Customer builds, ships, and operates software. The platform emphasizes automation, repeatability, and guardrails so teams can deploy frequently with confidence.
Key Capabilities:
Infrastructure as Code: Version-controlled, modular IaC for all environments
CI/CD Automation: Pipelines for build, automated testing, artifact management, and progressive deployment
Containerization & Orchestration: Standardized container images and orchestrated runtime for scaling and self-healing
Observability: Unified dashboards, structured logging, distributed tracing, and actionable alerting
Security & Compliance: Automated scanning, secrets management, policy-as-code, and least-privilege access
Release Management: Blue/green and canary strategies with automated rollback
Business Benefits:
Faster, more predictable releases through automation of manual steps
Reduced incident frequency and shorter recovery times via observability and automated rollback
Lower operational risk through consistent, auditable, policy-enforced environments
Improved developer productivity by removing undifferentiated operational toil
Clear audit trails supporting compliance and governance requirements
## High Level Architecture Diagram

[image]

## AWS Architecture Design Overview
The DevOps platform is built on AWS-native and cloud-agnostic tooling to provide secure, scalable, and automated software delivery. The design standardizes environment provisioning, pipeline execution, and runtime operations behind consistent interfaces.
The AWS-based design leverages:
Infrastructure as Code (Terraform or equivalent) for provisioning and drift detection
Source control integration with automated pipeline triggers
CI/CD orchestration (e.g., GitHub Actions, GitLab CI, or AWS CodePipeline/CodeBuild)
Container registry and orchestration (Amazon ECR with Amazon EKS/ECS)
Secrets management (AWS Secrets Manager / Parameter Store)
Observability stack (Amazon CloudWatch, OpenTelemetry, and dashboards)
Security scanning integrated into the pipeline (SAST, dependency, and image scanning)
### System Components
Source & Build Layer:
Version-controlled repositories with branch protection and review gates
Automated build and artifact packaging with reproducible outputs
API and Integration Layer:
Pipeline orchestration integrating source, build, test, scan, and deploy stages
Integration with ticketing and notification systems for release visibility
Runtime & Orchestration Layer:
Containerized workloads deployed to an orchestrated runtime
Autoscaling, health checks, and self-healing for resilient operations
Data & State Management Layer:
Secure state storage for IaC with locking and versioning
Centralized secrets and configuration management
Observability Infrastructure:
Metrics, logs, and traces aggregated into unified dashboards
Alerting and on-call routing for proactive incident response
### Pipeline Flow Processing
Commit to Deploy:
A commit triggers the pipeline, which builds artifacts, runs automated tests, performs security scans, and promotes through environments.
Progressive deployment strategies (blue/green or canary) roll changes out safely with automated rollback on failure.
Deployment outcomes and health signals are reported back to the team through dashboards and notifications.
### Technical Enhancements
Automation:
Policy-as-code enforcement and automated environment provisioning
Self-service pipelines that remove manual deployment steps
Reliability:
Automated rollback, health checks, and progressive delivery
Infrastructure drift detection and remediation
Security:
Shift-left scanning, secrets management, and least-privilege access controls
Continuous compliance checks integrated into the delivery lifecycle
# Scope of Work – Technical Project Plan - DevOps Platform
## Project Kick-Off
Initial planning and design workshops will be conducted to define the scope of the DevOps engagement within Customer. These workshops will focus on:
Understanding the current delivery process, environments, and pain points
Assessing readiness of existing infrastructure, repositories, and tooling
Establishing detailed tasks with clearly defined milestones
Refining the platform design based on discovery sessions
Deliverables:
Project charter and timeline
Detailed requirements document
Current-state assessment report
## Identification of Automation Opportunities
Ingram Micro will work closely with Customer to identify high-impact automation opportunities across the delivery lifecycle. This will involve:
Stakeholder Interviews: Engaging engineering and operations leaders to capture challenges and priorities
Process Analysis: Reviewing existing build, test, and deployment workflows to identify bottlenecks
Pilot Design: Proposing a pilot pipeline for a representative service before full-scale rollout
Feasibility Study: Mapping automation candidates to priority and implementation phase
## DevOps Readiness
Ingram Micro will assess Customer's readiness for the DevOps platform across:
Infrastructure: Cloud account structure, networking, and provisioning maturity
Technical Expertise: In-house cloud, CI/CD, and containerization capabilities
Organizational Readiness: Stakeholder support and change management readiness
Strategic Alignment: Mapping platform adoption to delivery and reliability goals
Compliance & Governance: Evaluation of security, access control, and regulatory adherence
## Platform Design and Implementation
### Infrastructure as Code
Environment Provisioning
Author modular, version-controlled IaC for all target environments
Implement state management with locking, versioning, and drift detection
Parameterize environments for repeatable, auditable provisioning

[image]

Configuration Management
Centralize configuration and secrets with least-privilege access
Standardize environment parity across development, staging, and production
### CI/CD Pipeline Development
Pipeline Construction
Build pipelines for build, automated test, security scanning, and deployment
Implement artifact management and promotion across environments
Deployment Strategies
Implement blue/green and canary deployment with automated rollback
Add approval gates and release notifications for visibility
### Containerization & Orchestration
Container Standardization
Define reproducible container images with security hardening
Establish registry workflows and image scanning
Orchestration Setup
Deploy workloads to an orchestrated runtime with autoscaling and health checks
Configure self-healing and resource governance
### Observability & Security
Observability
Instrument services for metrics, logs, and distributed tracing
Build dashboards and configure alerting with on-call routing
Security (DevSecOps)
Integrate SAST, dependency, and image scanning into pipelines
Implement secrets management and policy-as-code enforcement
## Delivery Methodology
Ingram Micro utilizes an agile project management methodology to ensure Customer has continuous visibility into project progress. A Delivery Manager will be assigned and will be the primary point of contact for all project-related updates and issue resolution. Weekly progress meetings will be held to review milestones and align expectations. Customer will have access to project management tools to monitor progress, track issues, view backlogs, and follow sprint activities in real time, enabling proactive communication throughout the engagement.
## Testing & Validation
Validation and Performance: Ingram Micro will perform functional, integration, and performance testing to verify:
Pipeline correctness across build, test, scan, and deploy stages
Infrastructure provisioning and teardown via IaC
Deployment strategies including rollback behaviour
Observability signal accuracy and alert routing
Customer will participate in ongoing testing cycles and provide feedback for refinements.
Security and Well-Architected Review: Ingram Micro will validate secure configuration, access controls and secrets handling, conduct vulnerability assessments, and review the platform against the AWS Well-Architected Framework across the operational excellence, security, reliability, performance efficiency, cost optimisation and sustainability pillars. The review findings and any accepted deviations from Well-Architected best practice, with their residual risk, are recorded and shared with the Customer.
Reliability: Testing will verify automated recovery, health checks, and resilience of orchestrated workloads under representative load.
## Documentation & Knowledge Transfer
Comprehensive documentation will be provided, covering the platform architecture, pipeline design, IaC modules, and detailed operational guidelines for Customer personnel. This documentation will also include troubleshooting steps and best practices to ensure optimal performance.
Documentation deliverables will include:
Platform architecture documentation
Pipeline and IaC usage guides
Runbooks for deployment, rollback, and incident response
Observability and alerting configuration guides
Security and access-control documentation
Hands-on knowledge transfer sessions will be conducted throughout the project lifecycle, culminating in a comprehensive workshop before final handover. These sessions will focus on:
Platform administration and management
Monitoring, alerting, and troubleshooting
Performance and cost optimization
Ongoing maintenance procedures
## Project Success Criteria
The project will be deemed successful upon:
Functionality: Pipelines reliably build, test, scan, and deploy services validated by Customer during UAT
Platform Performance:
Repeatable environment provisioning via IaC
Automated deployments with safe rollback
Reliability: Health checks, autoscaling, and self-healing operate as designed
Integration: Successfully integrates with Customer's existing systems:
Source control and artifact repositories
Cloud accounts and networking
Notification and ticketing systems
Operability: Provides clear observability and actionable alerting
Business Impact: Demonstrates measurable improvement in release velocity and reduction in operational toil
Knowledge Transfer: Successful completion of training sessions and documentation handover, enabling Customer's team to operate and maintain the platform.
## Assumptions
It is assumed that Customer will provide
Access to necessary cloud accounts, repositories, and systems for integration
All required third-party tools or licenses needed for development, if any
Representative services and workloads for pilot and validation
Sample pipelines, configurations, and runbooks where available for reference
Customer to assign dedicated resources to collaborate with Ingram Micro during the project (Engineering leads, Technical POCs)
It is assumed that the existing cloud infrastructure can support the integration requirements
The project scope is limited to the DevOps use cases described in this SOW only. Any additional features, use cases, or integrations beyond the defined scope will require a formal change request at additional applicable cost
## Design Considerations
Scalability: Designed for horizontal scaling of workloads and pipeline throughput
Resilience: Automated failover, health checks, and redundancy to maintain availability
Extensibility: Modular design enables future services and environments
Security: Secrets management, least-privilege IAM, and policy-as-code enforcement
Observability: Comprehensive logging, monitoring, tracing, and alerting for proactive issue detection
Automation: End-to-end automation reduces manual toil and human error
Cost Optimization: Cloud-native services optimized for efficient use of compute/storage resources
## Customer Dependency
Customer to provide Ingram Micro with access to necessary personnel, documentation, and systems to enable successful execution of the project
Customer to share delivery workflows, environment details, and configurations to support design and validation
Designated personnel will be made available, including a project sponsor with decision-making authority, technical points of contact for existing systems, subject matter experts for the qualification process, and resources for testing and validation
Customer will ensure timely engagement, including review and feedback on deliverables within 5 business days, responses to inquiries within 2 business days, and participation in scheduled meetings, workshops, and milestone approvals. Any delays in providing these dependencies may impact project timelines or require scope adjustments
## RACI Matrix
## Out-Of-Scope
The following items are considered out of scope for this project:
Development of application features or business logic beyond deployment automation
Modifications to Customer's systems beyond defined integration points
Migration of workloads not explicitly listed in this SOW
Ongoing managed operations beyond the agreed knowledge transfer period
Support for tools or platforms not explicitly listed in this SOW
Advanced analytics and BI dashboards beyond standard observability reporting
Physical deployment of on-premises infrastructure
In case of a scope change, it will be documented as a change request. This change request will undergo an approval process involving the technical team, project managers, and finance. Implementation will only commence after the change request form is signed off by Customer and Ingram Micro. Updated timelines and costs, as detailed in the approved change request, will be communicated to all relevant stakeholders.
## Standard Business Needs
Ingram Micro shall not be held responsible for delays or non-delivery resulting from unforeseen circumstances beyond its reasonable control. This includes, but is not limited to, acts of God, war, strikes, civil unrest, fire, government actions, and transportation disruptions.
Standard Non-Compete, Non-Solicitation and No-Hire clauses of Ingram Micro are applicable.
Working Hours: Ingram Micro works on all weekdays except for any public/regional holidays. Usual operating hours of the Ingram Micro team would be from 9.00 AM to 6.00 PM IST.
# Schedule I: Estimated Project Schedule
Week 1: Planning and Design – Initiate the project with stakeholder interviews, requirements gathering, current-state assessment, and platform architecture design.
Weeks 2–3: Implementation – Build IaC modules, CI/CD pipelines, containerization and orchestration, and conduct initial platform testing.
Week 4: Testing and Optimization – Perform end-to-end pipeline testing, validate deployment and rollback, finalize documentation, and conduct user acceptance and knowledge transfer sessions.
Weeks 5–6: Deployment and Stabilization – Roll out the platform in phases, monitor performance, fine-tune automation and observability, deliver final documentation, conduct closing workshops, and complete project handover.
# Pricing
Pricing has two components: a one-time fixed-price delivery fee for the engagement, and the recurring AWS run-rate to operate the platform once live. The one-time fee covers design, build, integration, testing, and handover, and is stated as a fixed price for the agreed scope of USD [one-time total].

The recurring AWS run-rate is not restated in this document. Refer to the cost estimate below, which is the authoritative source for the monthly and annual AWS run-rate:

AWS Pricing Calculator estimate: [cost calculator link]
# Acceptance And Authorization
IN WITNESS WHEREOF, the parties below have executed this SoW as of the latest of the signature dates recorded below.

| Phase | Responsible | Accountable | Consulted | Informed |
| --- | --- | --- | --- | --- |
| Project Kick-off | Ingram Micro | Ingram Micro | Customer | Customer |
| Requirements Gathering | Ingram Micro | Ingram Micro | Customer | Customer |
| Platform Architecture Design | Ingram Micro | Ingram Micro | Customer | Customer |
| IaC & Pipeline Development | Ingram Micro | Ingram Micro | Customer | Customer |
| Containerization & Orchestration | Ingram Micro | Ingram Micro / Customer | Customer | Customer |
| Observability & Security | Ingram Micro | Ingram Micro | Customer | Customer |
| Knowledge Transfer | Ingram Micro | Ingram Micro | Customer | Customer |


| For Customer | For Ingram Micro Holding Corporation |
| --- | --- |
| Signature:   ____________________ | Signature:   ____________________ |
| Name: | Name: |
| Title: | Title: |
| Date: | Date |
</sample>