# Project Catalyst pipeline orchestrator

Drives one engagement through the six stages, in the order the team actually works in:

```
SOW draft  ->  Solutions Architect review  ->  Well-Architected review  ->  architecture diagram  ->  cost estimate  ->  final SOW review
  (sow)            (architect, human)            (sow)                      (architecture)            (cost)            (sow)
```

It owns no model logic. It invokes the three existing agent runtimes in order and carries each
one's output into the next.

## Why this order

Each arrow is a data dependency, not a preference.

| Step | Why it sits here |
|---|---|
| SOW first | The solutions architect settles the service decisions (ECS vs EKS vs Fargate) and the in-scope / out-of-scope split while writing the SOW. |
| Solutions Architect review second | A human checkpoint on the draft, before anything is built on it. The SA edits the SOW and/or leaves comments, exactly as they do today, then signs off. The run pauses here. |
| Well-Architected review third | It runs against the SOW, **before** the diagram. A weak service decision caught here costs one SOW edit; caught after the diagram and the price it costs a loop through all three downstream stages. |
| Architecture fourth | It cannot run first: it needs the SOW's final scope as its input. |
| Cost fifth | It prices what the diagram shows. |
| Final SOW review last | The SOW's own cross-checks (service coverage, the cost estimate link) have nothing to check until the architecture and cost blocks exist. It also runs the SA document-review checklist over the final SOW (see below). |

Before this stack, the only declared ordering ran the other way: the SOW generator consumed an
architecture and cost handoff as "Stage 4", and a human pasted JSON between five separate web
apps.

## The Well-Architected stage, and the AWS managed agent

The review of the SOW is ours, in the SOW agent (`services/wa_review/`). It is deterministic
text rules across all six pillars, plus one optional advisory model call.

It is not the [AWS Well-Architected Agent](https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent.html)
(public preview, 1 Oct 2026) because that service's on-demand
[architecture review](https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent-architecture-reviews.html)
takes an S3 URI of CDK or Terraform and returns updated templates. It cannot read prose, so it
cannot review a SOW, and no stage upstream of the review emits IaC. Other constraints worth
knowing before planning around it: Business+ support tier or higher, five architecture reviews
per day per profile, and four pillars only (cost optimisation, security, reliability,
performance) rather than six.

The managed agent is the right tool the moment an engagement produces IaC. That is a later
stage, not this one.

## The Solutions Architect checkpoint

After the draft (stage 2), the run stops at `sa_review` so the solutions architect can work the
way they do today: edit the SOW with AI (the *Change this run with AI* panel, target **SOW**) and
leave comments of record, then approve. `POST /runs/{id}/sa-review` takes `{"comment"?, "sign_off"?}`:
a comment alone keeps the run stopped; `sign_off: true` records the approval and lets it continue
to the Well-Architected review. Approval is a per-run latch, so a later AI change does not re-stop a
run that already went through. Starting a run with `stop_on_blocked: false` is a fully automated run
and skips this checkpoint.

## Stopping for review

A high Well-Architected finding sets the stage to `blocked` and the pipeline stops. Nothing is
broken; a person has to decide. That is the entire value of running the review before the
diagram.

To fix the finding instead of overriding it, ask the AI to change the SOW (next section); the review runs again on the edited text.

`POST /runs/{id}/continue` overrides it deliberately and records who did so in the activity log.
Starting a run with `stop_on_blocked: false` disables the gate up front. Only a `blocked` stage
can be waved through — a `failed` stage produced no output for the next stage to build on.

## The SA document-review checklist (final SOW review)

The final stage runs a deterministic pass of the [`sa-doc-reviewer`](../sa-doc-reviewer-v1) checklist
over the finished SOW (`adapters.sa_review_findings`), on top of the SOW agent's rule check. It is
the cheap, mechanical half of that skill — the qualitative dimensions (tone, grammar, scope-clarity
judgement) still need a model and are not run here. Each finding is
`{dimension, severity, label, detail}` and the UI groups them by severity under *SA document review*.

| Check (sa-doc-reviewer dimension) | Severity |
|---|---|
| Unfilled placeholders / AI slop left in the draft (1, 7) | critical |
| Calculator link is a homepage placeholder with no estimate id (19) | critical |
| A cost line item with no matching service in the design (17, estimate → diagram orphan) | critical |
| No explicit Out of Scope section (9) | critical |
| A design service with no cost line item (17, diagram → estimate) | high |
| Service named in the SOW scope but neither drawn nor priced — KMS, a security agent, QuickSight (9) | high |
| Missing core SOW sections: Deliverables, Timeline, Assumptions, Acceptance (16) | high |
| Region mismatch between the design and the estimate (21) | high |
| No named contingency buffer (12) | medium |

A **critical** finding stops the stage exactly like a failed required rule — overridable with *Continue
anyway* (`POST /runs/{id}/continue`), or fixable by changing the SOW. Everything else is an advisory note.
Dimensions needing live AWS data (pricing accuracy 20, service availability 21, Well-Architected 22) and
the inline-`.docx`-comment output are deliberately left to the skill run by a person at delivery time.

## Changing a run with AI

Once a run has finished, or stopped for review, a person can say in plain words what to change, and pick what to
change: the **SOW**, the **architecture design** or the **cost estimate** (`POST /runs/{id}/revise`,
`{"target": "sow" | "architecture" | "cost", "instruction": "..."}`). The UI has this under *Change this run with AI*,
and each Well-Architected finding has a *Fix with AI* button that fills it in.

The change goes to the agent that owns the thing being changed, then everything after it is re-run, because its input
is now different:

| Target | What happens | Then re-runs |
|---|---|---|
| SOW | The SOW agent's `edit` action proposes changes. They are applied here (`orchestrator/sow_edit.py`). | Well-Architected review, architecture, cost, final SOW review |
| Architecture design | The architecture agent's `revise_adr`, then `estimate_usage`, then `generate_diagram` | Cost, final SOW review |
| Cost estimate | The cost agent prices the current architecture again with the instruction appended | Final SOW review |

- **A failed change changes nothing.** Each target works on a copy and writes to the run only when every call in it has
  succeeded, so a bad instruction never leaves a new design next to the old diagram. The attempt is kept in the run's
  change history with the reason.
- **Changing the architecture does not edit the SOW text.** If the services in the SOW no longer match, the final SOW
  review says so, and a SOW change fixes it.
- **If usage cannot be re-estimated**, the old usage is dropped (it described the old design) and the cost stage prices
  the new design's components instead.
- **Limits.** One change at a time per run, 25 changes per run, and `RevisionsPerIpHour` / `RevisionsPerDay` across
  callers. Each change is one to four model calls, plus the re-run stages.
- **The SOW edit port.** The SOW agent's editor returns proposals, and until now only its browser UI applied them
  (`sow-generator-agent/deployment/ui/draft.js`). `sow_edit.py` is a port of that file. Block ids must be numbered the
  way `draft.js` numbers them, so change the two together.

The *Architecture* tab also shows the design the architecture agent prepared (`GET /runs/{id}/architecture`): the
components with their service and configuration, the data flows, the key decisions, the assumptions, and the full ADR
and usage assumptions behind the cost estimate, under the diagram.

## Layout

```
deployment/api/orchestrator/   the pipeline logic (inside the CodeUri so sam build bundles it)
  contracts.py                 the stages, their order, and one run's state
  scope.py                     SOW markdown -> the in-scope / out-of-scope split
  adapters.py                  translations between the three agents' native shapes
  sow_edit.py                  applies the SOW editor's change proposals to the SOW markdown
  agents.py                    the one seam to the agent runtimes
  pipeline.py                  the sequencer
deployment/api/handler.py      the UI's API
deployment/api/worker.py       runs ONE stage, saves, schedules the next
deployment/api/store.py        persistence, with S3 offload for large payloads
deployment/template.yaml       SAM stack (backend)
deployment-ui/                 the Amplify web UI in Ingram format, with the "Use sample" buttons (see its README)
samples/                       this project's own sample (Woodgrove Bank, CVA); the other three come from the SOW agent
tests/                         129 tests, no AWS required
```

`advance()` runs exactly one stage, so the worker can run a stage, persist, and come back. The
whole sequence is six stages across three agents and a dozen model calls, well past the 15-minute
Lambda ceiling.

## Handoffs

Three translations, all in `adapters.py` and separately tested, because a quiet mismatch here
becomes a plausible-looking but wrong SOW:

- **SOW + scope -> architecture.** The SOW is passed as the SRS; in this flow it *is* the agreed
  requirements document. The scope block rides alongside as a binding constraint.
- **design + usage.json -> cost.** The cost agent takes prose, so the architecture output is
  rendered to text. It is rendered from `usage.json`, not the design, because usage.json carries
  the monthly quantities that drive price. The availability model is stated explicitly so
  per-zone resources are not priced once.
- **architecture + cost -> UpstreamInputs.** `calculator_url` becomes `cost.source_link`, which
  is the field the SOW's Pricing section reproduces. Totals are carried only so the SOW's
  reconciliation rule can verify the SOW does **not** restate them.

A cost stage that returns no `calculator_url` fails loudly: the SOW refers to that link instead
of restating figures, so without it the Pricing section has nothing to point at.

## Deploying

The three agent stacks deploy first; this stack takes their runtime ARNs.

```bash
cd deployment
sam build
sam deploy --guided \
  --parameter-overrides \
    SowAgentRuntimeArn=<arn> \
    ArchitectureAgentRuntimeArn=<arn> \
    CostAgentRuntimeArn=<arn> \
    SowJobsTableName=ingram-sow-runs \
    SowDataBucketName=ingram-sow-data-<account>-<region>
```

Then deploy the UI: `cd ../deployment-ui && cp config.env.example config.env && ./scripts/deploy.sh`. It creates the
Amplify app, allows its URL in the API's CORS list and publishes the page. See `deployment-ui/README.md`.

The SOW agent's runtime code and API changed with this work (the `wa_review` action, the CVA template category, the new
`pricing-refers-to-cost-estimate` rule), so redeploy it first: `sow-generator-agent/deployment/scripts/package_agent.sh`
then its `deploy.sh`.

One coupling is deliberate and worth knowing: the worker reads the SOW agent's job table
(`SowJobsTableName`) directly, because the SOW agent's `generate` and `check` actions are
asynchronous and report progress there. Reading the table was chosen over calling the SOW
agent's HTTP API so a pipeline run does not consume the public API's per-IP rate limit.

## The combined SOW

The final document is the SOW text, the architecture diagram drawn from that SOW, and the real cost estimate link, in one
branded `.docx` (`POST /runs/{id}/export`, rendered by the SOW agent).

The SOW is drafted before the estimate exists, so at draft time it can only carry a placeholder. Once the cost stage
returns the calculator URL, `adapters.apply_cost_link` writes it into the Pricing section: it replaces the
`[cost calculator link]` placeholder, or a stale link, or inserts one. Only the link is written, never a figure, so the
"refer to the cost estimate" policy holds. Without this step the final review would rightly fail on every run.

### The CVA template

`cva/full` is the EMEA USC *AWS Full CVA SOW Template* (`.dotx`) converted into the SOW generator's template format, with
two sections added so it can carry the pipeline's outputs: **Target Architecture** and **Pricing** (the one-time fee and
the cost estimate link). Its governing-agreement, acceptance and exclusion wording is unchanged.

An assessment deploys nothing, so for `cva/*` the Well-Architected review skips the build-oriented checks (backup,
encryption, observability, performance target, compute decision). It still catches an unsupported resiliency claim, a
single-AZ scope, and a missing out-of-scope list.

## Configuration

| Parameter | Default | Effect |
|---|---|---|
| `WaVerify` | `false` | Adds the advisory model pass to the Well-Architected review. The deterministic checks are free, so this is off. |
| `CombinedVerify` | `true` | Runs the LLM reviewer in the final SOW review. |
| `RunsPerDay` | `20` | Daily cap across all callers. One run is a dozen model calls. |
| `RunsPerIpHour` | `3` | Per-caller hourly cap. |
| `RevisionsPerDay` | `60` | Daily cap on AI changes across all callers. |
| `RevisionsPerIpHour` | `10` | Per-caller hourly cap on AI changes. |

## Tests

```bash
python -m pytest        # 129 tests, no AWS
```

The whole sequence runs against fake agent replies (`tests/fakes.py`), so the ordering, the
gates and the handoffs are all covered without a deployment. One test validates the merged
handoff against the SOW agent's real `UpstreamInputs` contract, which is the check that the
adapters actually produce something the SOW agent accepts.
