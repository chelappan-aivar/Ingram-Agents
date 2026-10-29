#!/usr/bin/env node
/**
 * Developer tool (not part of `npm test`): exercises the REAL Intake and SOW APIs with the same client
 * modules the page uses, to confirm that what the page sends is accepted. It makes one small Intake
 * run (a few model calls) and free rule-based SOW calls. Discovery, Architecture, Cost and Live Assist
 * are intentionally not run here (long, quota-limited, or behind the portal session).
 *
 *   node scripts/live_contract_check.mjs
 */
globalThis.window = { location: { origin: "https://d257wmhppqdfnv.cloudfront.net" } };

const root = new URL("../pipeline-app/js/", import.meta.url);
const load = (p) => import(new URL(p, root));
const { createIntakeAgent } = await load("agents/intake.js");
const { createSowAgent } = await load("agents/sow.js");
const { briefingTranscript, discoveryDocuments } = await load("domain/context.js");
const { chooseOpportunity, customerOf } = await load("domain/opportunities.js");
const { buildUpstream, intakeBlock, metaBlock } = await load("domain/sow-inputs.js");
const { suggestTemplate } = await load("domain/templates.js");

const NOTES = `From: priya.nair@northwind-logistics.example
Subject: Moving our Frankfurt data centres to AWS

Hi, Northwind Logistics runs its fleet-tracking platform from two colocation data centres in Frankfurt.
The lease ends in March, so we want to migrate about 60 Windows and Linux servers and two SQL Server databases to AWS eu-central-1.
We need PCI DSS compliance and a recovery time objective under 4 hours. Budget is roughly 250k USD and we want go-live by end of Q1.
Priya Nair, Head of Infrastructure, is the decision maker.`;

const ok = (m) => console.log(`  ✓ ${m}`);
const sow = createSowAgent();
const intake = createIntakeAgent();

console.log("SOW templates");
const templates = await sow.templates();
ok(`${templates.length} templates, e.g. ${templates.slice(0, 3).map((t) => t.id).join(", ")}`);

console.log("Intake run (real)");
const runId = await intake.start({ notesText: NOTES });
ok(`started ${runId}`);
const run = await intake.waitForRun(runId, { onUpdate: (r) => process.stdout.write(`\r  … ${r.status} ${r.phase ?? ""} ${JSON.stringify(r.counts ?? {}).slice(0, 80)}   `) });
console.log();
ok(`finished as ${run.status} with ${run.opportunities.length} opportunity(ies)`);
const opp = chooseOpportunity(run.opportunities, null);
if (!opp) throw new Error("Intake returned no opportunity");
ok(`customer "${customerOf(opp)}", ${opp.fields.length} fields, ${opp.gaps?.length ?? 0} gaps`);

console.log("Mapping");
const briefing = briefingTranscript(opp, "");
ok(`Live Assist briefing: ${briefing.split("\n").length} lines, ${briefing.length} chars`);
const docs = discoveryDocuments({ opportunity: opp, handoff: null, callTranscript: "", extraText: "" });
ok(`Discovery documents: ${docs.map((d) => d.name).join(", ")}`);

const template = suggestTemplate(templates, opp) ?? templates[0];
const upstream = buildUpstream({ meta: metaBlock({ opportunity: opp, templateId: template.id, agents: ["intake"] }), intake: intakeBlock(opp, "") });
ok(`upstream built for template ${template.id}`);

console.log("SOW agent accepts the upstream");
const sampleSow = `# Statement of Work: ${customerOf(opp)}\n\n## Scope\nIn scope: migrate servers and databases to AWS eu-central-1.\n\n## Out of Scope\nApplication refactoring.\n\n## Pricing\nAWS Pricing Calculator estimate: [Cost Calculator Link]\n`;
const review = await sow.waReview({ markdown: sampleSow, upstream, templateId: template.id });
ok(`WA review returned ${review.findings?.length ?? 0} finding(s)`);
const check = await sow.check({ markdown: sampleSow, upstream });
ok(`rule check: ${check.summary?.passed}/${check.summary?.total} rules passed`);
console.log("\nAll live contract checks passed.");
