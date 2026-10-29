# Sources for the skill and the service sheet

`app/prompts/skill.md` and `app/prompts/services.md` were distilled once, on 2026-10-09, from the sources below. Nothing is fetched at run time. The full digests, with a source URL for each finding and a list of what could not be verified, are in `docs/research_skill.md` and `docs/research_services.md`. The first drafts are kept in `docs/draft_skill.md` and `docs/draft_services.md`.

## Skill
- AWS Prescriptive Guidance, ADR process: https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html
- Nygard ADR format and MADR: https://adr.github.io/
- awslabs/agent-plugins, deploy-on-aws `deploy` skill (SKILL.md, references/defaults.md, references/security.md): https://github.com/awslabs/agent-plugins
- aws-samples/sample-well-architected-skills-and-steering, wa-builder ADR mode: https://github.com/aws-samples/sample-well-architected-skills-and-steering
- AWS Well-Architected Framework, six pillars: https://docs.aws.amazon.com/wellarchitected/latest/framework/
- AWS Decision Guides: https://docs.aws.amazon.com/decision-guides/

## Service sheet
- Canonical service names: `diagram_generator/catalog/components.yaml` (so every component resolves to a diagram icon).
- AWS documentation for each service, covering instance classes, engine versions, Fargate task sizes, ElastiCache node types, OpenSearch instance types, Bedrock model IDs, and single-AZ constraints for ALB, DB subnet groups and NAT Gateway (see `docs/research_services.md`).

## Choices made when merging the drafts
- Usage figures (hours, requests, tokens, GB per month) were removed from the sheet, because the cost calculator decides usage.
- Second-AZ subnets that AWS requires (ALB, DB subnet group) are recorded as configuration, not as boundaries.
- An ECS service on Fargate is one component with service "AWS Fargate", and tasks are its quantity.
- Conservative versions where availability was not confirmed for every instance class: PostgreSQL 17, Valkey 8.0, OpenSearch 2.19, Glue 5.0.
