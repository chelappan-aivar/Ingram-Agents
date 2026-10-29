# Programme criteria for the fit card

Edit this file to change how the agent scores programme fit. It is inserted into the
prompt as-is. Keep each criterion short and checkable from an email or CRM record.
Thresholds and rules change; confirm current terms in AWS Partner Central.

## MAP: AWS Migration Acceleration Program
Funding and methodology for migrating existing workloads to AWS, in three phases:
Assess (migration readiness assessment, TCO / business case), Mobilize (landing zone,
migration plan, pilot) and Migrate & Modernize.
Criteria:
1. Existing workloads move to AWS from on-premises, colocation or another cloud
   (net-new builds and pure GenAI or analytics projects are usually not MAP).
2. Meaningful committed AWS spend after migration (expected ARR is known or estimable).
3. Workload inventory is known or can be assessed (servers, databases, applications).
4. A timeline exists, with the migration starting within roughly the next 12 months.
5. The customer is willing to work with a MAP-qualified partner and the AWS account team.
Suggested phase: Assess when the inventory or business case is missing; Mobilize when
the scope is known but foundations are not; Migrate & Modernize when waves are planned.

## MPE: MAP Migration Program Engagement
The engagement registered with AWS once a MAP agreement is signed. It issues the MPE ID
used in the `map-migrated` resource tag, which is how migrated spend is credited.
Criteria:
1. The customer is MAP-eligible (see above) and has agreed, or is close to agreeing, to MAP.
2. AWS account IDs that will host the migrated workloads are known.
3. The workloads in scope are listed, so resources can be tagged with the MPE ID.
4. A migration start date is known.
5. An MPE ID or MAP agreement is mentioned (if so, capture it; the engagement already exists).
