# About Ingram Micro Holding Corporation

Ingram Micro Holding Corporation is a leading global distributor of information technology products, cloud, software, commerce and fulfillment services, and lifecycle solutions. Founded in 1979 and headquartered in Irvine, California, the company connects vendors with resellers, managed service providers, and other channel partners worldwide. As one of the world's largest technology distributors, Ingram Micro reported approximately $52.6 billion in net sales in fiscal year 2025, employs roughly 23,500 people, and serves more than 161,000 customers globally while expanding digital platform capabilities such as the Xvantage AI-powered B2B experience platform.

# Statement of Work

This Statement of Work (SOW) is entered into by and between Ingram Micro Holding Corporation ("Ingram Micro") and Tailspin Toys ("Customer"), as of the date last signed below, the Effective Date, for the purpose of providing certain Ingram Micro Services to Customer. This SOW is subject to the valid, written agreement signed by both parties for the provision of Services, the Agreement.

# Project Overview

## Executive Summary

Tailspin Toys, a toy manufacturer and online retailer headquartered in Frankfurt, Germany, seeks to modernize how its e-commerce platform is built, released, and operated. Today the platform is released once a month in a 6-hour Saturday maintenance window using manual PowerShell scripts that only two people know how to run. Two to three releases per quarter fail and are rolled back by hand. There is no central logging, incidents are typically reported by customers, and mean time to recovery (MTTR) is about 5 hours. Christmas traffic peaks at about 5x normal levels between mid-November and 24 December. Ingram Micro proposes to design and implement an automated, secure, and observable delivery platform in AWS eu-central-1 that lets Tailspin deploy each service at least weekly without a maintenance window. The engagement will:

- Establish Infrastructure as Code (IaC) with Terraform for repeatable, auditable provisioning of the dev, test, and prod environments, removing today's configuration drift
- Move source control and CI/CD from Azure DevOps Repos and Jenkins to GitHub Enterprise Cloud and GitHub Actions, with reusable build, test, scan, and deploy workflows
- Containerize the six .NET 8 services and the React storefront and run them on Amazon EKS with Karpenter node autoscaling, while the three .NET Framework 4.8 services stay on Windows EC2 and receive modern pipelines
- Implement canary deployments with automatic rollback on SLO breach, together with end-to-end observability and alerting to Microsoft Teams and PagerDuty
- Embed security controls across the delivery lifecycle (DevSecOps), including code, dependency, and image scanning, AWS Security Hub, and AWS Secrets Manager, with all data and logs kept in eu-central-1 to meet GDPR requirements
- Prove 5x Christmas peak capacity (about 2,500 requests per second) with a load test before the 1 November 2027 code freeze
- Transfer operational knowledge so Tailspin's four-person platform team can own and extend the platform

# Proposed Solution - DevOps Platform

Ingram Micro will deliver a cloud-native DevOps platform that standardizes how Tailspin builds, ships, and operates its e-commerce software. The platform emphasizes automation, repeatability, and guardrails so teams can deploy frequently with confidence and without downtime.

Key Capabilities:

- Infrastructure as Code: Version-controlled Terraform modules for networking, EKS, ECR, IAM, observability, and security, used across dev, test, and prod
- CI/CD Automation: Reusable GitHub Actions workflows for build, test, scan, and deploy stages, with approvals between environments
- Containerization & Orchestration: Standardized container images in Amazon ECR and an Amazon EKS runtime with Karpenter autoscaling for the seasonal peak
- Observability: Container Insights and logs in Amazon CloudWatch, metrics in Amazon Managed Service for Prometheus, dashboards in Amazon Managed Grafana, and alerting to Microsoft Teams and PagerDuty
- Security & Compliance: Automated code, dependency, and image scanning, centralized findings in AWS Security Hub, application secrets in AWS Secrets Manager, and least-privilege IAM
- Release Management: Canary deployments with Argo Rollouts and automatic rollback when error rate or latency breaches the SLO

Business Benefits:

- Faster, more predictable releases: from monthly to at least weekly per service, with lead time for changes under 1 day
- Reduced incident frequency and shorter recovery times: a change failure rate under 10% and MTTR under 1 hour through observability and automated rollback
- Lower operational risk through consistent, auditable environments that no longer depend on two people's manual scripts
- Confidence in peak-season performance, demonstrated by a load test at 5x peak before the code freeze
- Clear audit trails and EU data residency supporting GDPR compliance and governance requirements

## High Level Architecture Diagram

![Architecture Diagram](arch:1)

## AWS Architecture Design Overview

The DevOps platform is built on AWS-native services and GitHub tooling to provide secure, scalable, and automated software delivery in AWS eu-central-1. The design standardizes environment provisioning, pipeline execution, and runtime operations behind consistent interfaces.

The AWS-based design leverages:

- Terraform for provisioning all environments as code
- GitHub Enterprise Cloud with GitHub Actions for source control integration and automated pipeline triggers
- Amazon ECR for the container image registry and Amazon EKS for container orchestration, with Karpenter for node autoscaling
- Argo Rollouts for canary and blue/green deployments with automatic rollback
- AWS Secrets Manager for application secrets, replacing secrets held in web.config files
- Amazon CloudWatch, Amazon Managed Service for Prometheus, and Amazon Managed Grafana for observability
- Amazon Inspector and AWS Security Hub, together with SAST and dependency scanning in the pipeline, for security
- Application Load Balancer for ingress to the storefront and APIs

The platform must handle 5x normal traffic (about 2,500 requests per second) with p95 latency under 400 ms, deliver zero-downtime deployments, and provide 99.9% availability for the storefront. All data and logs remain in eu-central-1.

### System Components

Source & Build Layer:

- GitHub Enterprise Cloud repositories with branch protection and review gates, migrated from Azure DevOps Repos
- GitHub Actions workflows for automated build and packaging, with CodeQL and Dependabot scanning

API and Integration Layer:

- Reusable GitHub Actions workflows integrating build, test, scan, and deploy stages for dev, test, and prod with approvals
- Application Load Balancer providing ingress for the storefront and APIs
- Integration with Microsoft Teams and PagerDuty for release and incident visibility

Runtime & Orchestration Layer:

- Amazon EKS clusters (non-prod and prod) running the six .NET 8 services and the React storefront
- Karpenter adding and removing nodes as load grows and falls, including the Christmas peak
- Argo Rollouts managing canary releases and automatic rollback
- The three .NET Framework 4.8 services remaining on Windows EC2, with only their pipelines modernized

Data & State Management Layer:

- Secure Terraform state storage with locking and versioning
- AWS Secrets Manager for centralized application secrets
- Amazon ECR for versioned, scanned container images
- SQL Server is not part of this engagement; its move to Amazon RDS for SQL Server is delivered by the separate migration project

Observability Infrastructure:

- Amazon CloudWatch Container Insights, logs, and alarms
- Amazon Managed Service for Prometheus for metrics and Amazon Managed Grafana for dashboards
- Alert routing to Microsoft Teams and PagerDuty for on-call response

### Pipeline Flow Processing

Commit to Deploy:

- A commit to GitHub triggers a GitHub Actions workflow, which builds the artifact, runs automated tests, performs code and dependency scanning, builds the container image, pushes it to Amazon ECR, and scans it with Amazon Inspector.
- The change is promoted through dev, test, and prod with approvals. On Amazon EKS, Argo Rollouts shifts a small share of traffic to the new version first and rolls back automatically if error rate or latency breaches the SLO.
- Deployment outcomes and health signals are reported to the team through Amazon Managed Grafana dashboards and notifications in Microsoft Teams.

### Technical Enhancements

Automation:

- Automated environment provisioning from a single set of Terraform modules
- Self-service pipelines that replace manual PowerShell and MSI deployments

Reliability:

- Automatic canary rollback on SLO breach, health checks, and Karpenter-driven autoscaling
- Elimination of configuration drift across dev, test, and prod

Security:

- Shift-left scanning with CodeQL, Dependabot, and Amazon Inspector, with findings centralized in AWS Security Hub
- Secrets in AWS Secrets Manager and least-privilege IAM access controls

# Scope of Work – Technical Project Plan - DevOps Platform

## Project Kick-Off

Initial planning and design workshops will be conducted to define the scope of the DevOps engagement within Tailspin. These workshops will focus on:

- Understanding the current delivery process, environments, and pain points, including the monthly release window, manual scripts, and the single Jenkins server
- Assessing readiness of existing infrastructure, Azure DevOps repositories, and tooling
- Establishing detailed tasks with clearly defined milestones against the 12-week timeline and the 1 November 2027 code freeze
- Refining the platform design based on discovery sessions

Deliverables:

- Project charter and timeline
- Detailed requirements document
- Current-state assessment report

## Identification of Automation Opportunities

Ingram Micro will work closely with Tailspin to identify high-impact automation opportunities across the delivery lifecycle. This will involve:

- Stakeholder Interviews: Engaging Daniel Weber (CTO), Sophie Klein (IT Operations Lead), Jonas Becker (Lead Developer, E-commerce), and service owners to capture challenges and priorities
- Process Analysis: Reviewing the existing Jenkins builds and PowerShell and MSI deployment workflows for the ten applications to identify bottlenecks
- Pilot Design: Proposing a pilot pipeline for a representative .NET 8 service before rolling out to all services
- Feasibility Study: Mapping automation candidates to priority and implementation phase

## DevOps Readiness

Ingram Micro will assess Tailspin's readiness for the DevOps platform across:

- Infrastructure: Cloud account structure, networking, and provisioning maturity, including the landing zone and VPCs delivered by the migration project
- Technical Expertise: The platform team's in-house cloud, CI/CD, and containerization capabilities
- Organizational Readiness: Stakeholder support and change management readiness, including reducing dependence on the two people who currently run deployments
- Strategic Alignment: Mapping platform adoption to the DORA targets for deployment frequency, lead time, change failure rate, and MTTR
- Compliance & Governance: Evaluation of security, access control, and GDPR requirements for data and log residency in eu-central-1

## Platform Design and Implementation

The following AWS and DevOps services are in scope for design, build, and configuration:

- Amazon EKS: Kubernetes clusters for the e-commerce services (non-prod and prod)
- Karpenter: Node autoscaling for the 5x seasonal peak
- Amazon ECR: Container image registry with scanning
- GitHub Actions: CI/CD pipelines (GitHub Enterprise Cloud)
- Terraform: Infrastructure as code for all environments
- Argo Rollouts: Canary and blue/green deployments with automatic rollback
- Amazon Managed Service for Prometheus: Metrics
- Amazon Managed Grafana: Dashboards
- Amazon CloudWatch: Container Insights, logs, and alarms
- Amazon Inspector: Container image and EC2 vulnerability scanning
- AWS Security Hub: Security findings and standards
- AWS Secrets Manager: Application secrets
- Application Load Balancer: Ingress for the storefront and APIs

### Infrastructure as Code

Environment Provisioning

- Author modular, version-controlled Terraform modules for networking, EKS, ECR, IAM, observability, and security, including the NAT gateways and Application Load Balancer used by the platform
- Implement state management with locking, versioning, and drift detection
- Parameterize the dev, test, and prod environments in eu-central-1 for repeatable, auditable provisioning

![Architecture Diagram](arch:1)

Configuration Management

- Centralize configuration and secrets in AWS Secrets Manager with least-privilege access, replacing web.config secrets
- Standardize environment parity across dev, test, and prod to remove configuration drift

### CI/CD Pipeline Development

Pipeline Construction

- Migrate the ten application repositories (the storefront, six .NET 8 services, and three .NET Framework 4.8 services) from Azure DevOps Repos to GitHub Enterprise Cloud
- Build reusable GitHub Actions workflows for build, unit test, code and dependency scanning (CodeQL and Dependabot), image build, image scan, and deployment
- Implement artifact and image management in Amazon ECR and promotion across dev, test, and prod with approvals
- Provide pipelines for the three .NET Framework 4.8 services (order-sync, payment-gateway-adapter, and warehouse-feed), which remain on Windows EC2 and replace manual MSI deployment

Deployment Strategies

- Implement canary deployments with Argo Rollouts and automatic rollback on SLO breach for the services on Amazon EKS
- Add approval gates and release notifications in Microsoft Teams for visibility

### Containerization & Orchestration

Container Standardization

- Containerize the six .NET 8 services (catalog-api, cart-api, checkout-api, pricing-api, search-api, and customer-api) and the React storefront as reproducible container images with security hardening
- Establish Amazon ECR registry workflows and image scanning with Amazon Inspector

Orchestration Setup

- Deploy workloads to Amazon EKS clusters (non-prod and prod) with health checks and resource governance
- Configure Karpenter node autoscaling to handle 5x normal traffic during the Christmas season, with Application Load Balancer ingress
- Tailspin developers will fix application issues found during containerization

### Observability & Security

Observability

- Enable Amazon CloudWatch Container Insights, logs, and alarms to provide central logging
- Collect metrics in Amazon Managed Service for Prometheus and build dashboards and alerts per service in Amazon Managed Grafana
- Route alerts to Microsoft Teams and PagerDuty for on-call response
- Produce runbooks for the top 10 incidents

Security (DevSecOps)

- Integrate SAST (CodeQL), dependency scanning (Dependabot), and image scanning (Amazon Inspector and Amazon ECR) into the pipelines
- Centralize security findings and standards in AWS Security Hub
- Implement AWS Secrets Manager for application secrets and least-privilege IAM
- Keep all data and logs in eu-central-1 to meet GDPR requirements

## Delivery Methodology

Ingram Micro utilizes an agile project management methodology to ensure Tailspin Toys has continuous visibility into project progress. A Delivery Manager will be assigned and will be the primary point of contact for all project-related updates and issue resolution. Weekly progress meetings will be held to review milestones and align expectations. Customer will have access to project management tools to monitor progress, track issues, view backlogs, and follow sprint activities in real time, enabling proactive communication throughout the engagement.

## Testing & Validation

Validation and Performance: Ingram Micro will perform functional, integration, and performance testing to verify:

- Pipeline correctness across build, test, scan, and deploy stages
- Infrastructure provisioning and teardown via Terraform
- Canary deployment behavior and automatic rollback on SLO breach
- Observability signal accuracy and alert routing to Microsoft Teams and PagerDuty
- A load test at 5x peak (about 2,500 requests per second) before the 1 November 2027 code freeze, validating p95 latency under 400 ms and Karpenter scaling behavior

Tailspin will participate in ongoing testing cycles and provide feedback for refinements.

Security Assessments: Ingram Micro will validate secure configuration, access controls, secrets handling, and conduct vulnerability assessments and review the platform against the AWS Well-Architected Framework across its six pillars. Review findings and any accepted deviations from Well-Architected best practice, with their residual risk, are recorded and shared with the Customer.

Reliability: Testing will verify automated recovery, health checks, and resilience of Amazon EKS workloads under representative load, including zero-downtime deployments.

## Documentation & Knowledge Transfer

Comprehensive documentation will be provided, covering the platform architecture, pipeline design, Terraform modules, and detailed operational guidelines for Tailspin personnel. This documentation will also include troubleshooting steps and best practices to ensure optimal performance.

Documentation deliverables will include:

- Platform architecture documentation
- Pipeline and Terraform usage guides
- Runbooks for deployment, rollback, and the top 10 incidents
- Observability and alerting configuration guides
- Security and access-control documentation

Ingram Micro will pair with Tailspin's four-person platform team during the build. Hands-on knowledge transfer sessions will be conducted throughout the project lifecycle, culminating in handover workshops before final handover. These sessions will focus on:

- Platform administration and management
- Monitoring, alerting, and troubleshooting
- Performance and cost optimization
- Ongoing maintenance procedures

## Project Success Criteria

The project will be deemed successful upon:

- Functionality: Pipelines reliably build, test, scan, and deploy services, validated by Tailspin during UAT
- Platform Performance:
  - Repeatable environment provisioning via Terraform across dev, test, and prod
  - Automated zero-downtime deployments with canary rollout and automatic rollback on SLO breach
  - Each service able to deploy at least weekly without a maintenance window, with lead time for changes under 1 day
- Reliability: Health checks and Karpenter autoscaling operate as designed, and a load test at 5x peak (about 2,500 requests per second) passes with p95 latency under 400 ms before the 1 November 2027 code freeze, supporting 99.9% availability for the storefront
- Integration: Successfully integrates with Tailspin's existing systems:
  - GitHub Enterprise Cloud source control with SSO
  - The AWS landing zone and VPCs in eu-central-1
  - Microsoft Teams and PagerDuty for notifications and on-call
- Operability: Dashboards and alerts per service and runbooks for the top 10 incidents provide clear observability and actionable alerting, supporting a change failure rate under 10% and MTTR under 1 hour
- Compliance: All data and logs remain in eu-central-1
- Business Impact: Demonstrates measurable improvement in release velocity and reduction in operational toil, measured against the DORA metrics
- Knowledge Transfer: Successful completion of training sessions and documentation handover, enabling Tailspin's four-person platform team to operate and maintain the platform

## Assumptions

It is assumed that Tailspin will provide:

- Access to necessary AWS accounts, Azure DevOps repositories, Jenkins, and systems for integration
- A GitHub Enterprise Cloud organisation with SSO available by week 1. The licences are already purchased by Tailspin and are not included in this SOW or in the AWS run-rate
- The landing zone and VPCs in eu-central-1, delivered by the separate migration project by week 2
- Representative services and workloads for pilot and validation
- Sample pipelines, scripts, configurations, and runbooks where available for reference, including the current PowerShell deployment scripts
- Developers available to fix application issues found during containerization
- Dedicated resources to collaborate with Ingram Micro during the project (engineering leads and technical POCs)
- The three .NET Framework 4.8 services remain on Windows EC2, provided by the migration project. Only their pipelines are in scope
- Existing PagerDuty and Microsoft Teams accounts used for on-call and alerts
- The existing cloud infrastructure can support the integration requirements

The project scope is limited to the DevOps use cases described in this SOW only. Any additional features, use cases, or integrations beyond the defined scope will require a formal change request at additional applicable cost.

## Design Considerations

- Scalability: Designed for horizontal scaling of workloads with Karpenter and pipeline throughput to handle 5x seasonal peaks
- Resilience: Health checks, canary releases with automatic rollback, and redundancy to maintain 99.9% availability for the storefront
- Extensibility: Modular Terraform and reusable workflows enable future services and environments
- Security: AWS Secrets Manager, least-privilege IAM, and shift-left scanning
- Observability: Comprehensive logging, metrics, dashboards, and alerting for proactive issue detection
- Automation: End-to-end automation reduces manual toil and human error
- Data Residency: All data and logs stay in eu-central-1 to meet GDPR
- Cost Optimization: Cloud-native services sized for efficient use of compute and storage, with a 1-year Compute Savings Plan for baseline nodes

## Customer Dependency

- Tailspin to provide Ingram Micro with access to necessary personnel, documentation, and systems to enable successful execution of the project
- Tailspin to share delivery workflows, environment details, and configurations to support design and validation
- Designated personnel will be made available, including a project sponsor with decision-making authority (Daniel Weber, CTO), technical points of contact for existing systems (Sophie Klein, IT Operations Lead, and Jonas Becker, Lead Developer, E-commerce), subject matter experts for the qualification process, and resources for testing and validation
- Tailspin's four-person platform team to participate in pairing and handover workshops so it can run the platform after handover
- Tailspin will ensure timely engagement, including review and feedback on deliverables within 5 business days, responses to inquiries within 2 business days, and participation in scheduled meetings, workshops, and milestone approvals. Any delays in providing these dependencies may impact project timelines or require scope adjustments

## RACI Matrix

| Phase | Responsible | Accountable | Consulted | Informed |
| --- | --- | --- | --- | --- |
| Project Kick-off | Ingram Micro | Ingram Micro | Customer | Customer |
| Requirements Gathering | Ingram Micro | Ingram Micro | Customer | Customer |
| Platform Architecture Design | Ingram Micro | Ingram Micro | Customer | Customer |
| IaC & Pipeline Development | Ingram Micro | Ingram Micro | Customer | Customer |
| Containerization & Orchestration | Ingram Micro | Ingram Micro / Customer | Customer | Customer |
| Observability & Security | Ingram Micro | Ingram Micro | Customer | Customer |
| Knowledge Transfer | Ingram Micro | Ingram Micro | Customer | Customer |

## Out-Of-Scope

The following items are considered out of scope for this project:

- Development of application features or business logic beyond deployment automation, including fixes to application issues found during containerization, which Tailspin developers will carry out
- Modifications to Tailspin's systems beyond defined integration points
- Migration of workloads not explicitly listed in this SOW, including the three .NET Framework 4.8 services, which stay on Windows EC2 (only their pipelines move)
- The migration of SQL Server to Amazon RDS for SQL Server and the delivery of the landing zone and VPCs in eu-central-1, which are part of the separate migration project
- GitHub Enterprise Cloud licences, which are purchased and paid for by Tailspin
- Ongoing managed operations beyond the agreed knowledge transfer period
- Support for tools or platforms not explicitly listed in this SOW
- Advanced analytics and BI dashboards beyond standard observability reporting
- Physical deployment of on-premises infrastructure

In case of a scope change, it will be documented as a change request. This change request will undergo an approval process involving the technical team, project managers, and finance. Implementation will only commence after the change request form is signed off by Tailspin and Ingram Micro. Updated timelines and costs, as detailed in the approved change request, will be communicated to all relevant stakeholders.

## Standard Business Needs

Ingram Micro shall not be held responsible for delays or non-delivery resulting from unforeseen circumstances beyond its reasonable control. This includes, but is not limited to, acts of God, war, strikes, civil unrest, fire, government actions, and transportation disruptions.

Standard Non-Compete, Non-Solicitation and No-Hire clauses of Ingram Micro are applicable.

Working Hours: Ingram Micro works on all weekdays except for any public/regional holidays. Usual operating hours of the Ingram Micro team would be from 9.00 AM to 6.00 PM IST.

# Schedule I: Estimated Project Schedule

The engagement runs for 12 weeks, starting 1 March 2027, so the platform is live and load-tested well ahead of the 1 November 2027 code freeze.

Week 1: Planning and Design – Initiate the project with stakeholder interviews, requirements gathering, current-state assessment, and platform architecture design. The GitHub Enterprise Cloud organisation with SSO is available.

Weeks 2–3: Foundation – Build the Terraform modules for networking, EKS, ECR, IAM, observability, and security on the landing zone delivered by the migration project. Migrate repositories to GitHub Enterprise Cloud and build the reusable GitHub Actions workflows with a pilot pipeline.

Weeks 4–6: Containerization and Orchestration – Containerize the six .NET 8 services and the storefront, and deploy them to Amazon EKS with Karpenter, Amazon ECR, and Application Load Balancer ingress. Roll the pipelines out to all services, including the three .NET Framework 4.8 services on Windows EC2.

Weeks 7–8: Observability, Security, and Progressive Delivery – Configure Amazon CloudWatch, Amazon Managed Service for Prometheus, Amazon Managed Grafana, and alerting to Microsoft Teams and PagerDuty. Enable Amazon Inspector, AWS Security Hub, and AWS Secrets Manager, and implement canary deployments with automatic rollback using Argo Rollouts.

Weeks 9–10: Testing and Optimization – Perform end-to-end pipeline testing, validate deployment and rollback, conduct user acceptance testing, promote the platform to production, and develop runbooks for the top 10 incidents.

Week 11: Load Test – Run the load test at 5x peak (about 2,500 requests per second), tune Karpenter and service configuration, and confirm p95 latency under 400 ms.

Week 12: Stabilization and Handover – Fine-tune automation and observability, deliver final documentation, conduct handover workshops with Tailspin's platform team, and complete project handover.

# Pricing
Pricing has two components: a one-time fixed-price delivery fee for the engagement, and the recurring AWS run-rate to operate the platform once live. The one-time fee covers design, build, integration, testing, and handover, and is stated as a fixed price for the agreed scope of EUR 150,000 (about USD 165,000).

The recurring AWS run-rate is not restated in this document. Refer to the cost estimate below, which is the authoritative source for the monthly and annual AWS run-rate:

AWS Pricing Calculator estimate: https://calculator.aws/#/estimate?id=sample-tailspin-platform
