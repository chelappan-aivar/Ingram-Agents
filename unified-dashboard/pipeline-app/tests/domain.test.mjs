import assert from "node:assert/strict";
import { test } from "node:test";

import { briefingTranscript, discoveryDocuments, followUpTranscript } from "../js/domain/context.js";
import { chooseOpportunity, rankOpportunities } from "../js/domain/opportunities.js";
import { applyCostLink, architectureBlock, buildUpstream, costBlock, costRequirements, intakeBlock, liveAssistBlock, metaBlock } from "../js/domain/sow-inputs.js";
import { chunk, engagementName, splitList } from "../js/domain/text.js";

const field = (f, value, group = "Opportunity", label = f) => ({ field: f, label, group, value, verified: true });
const opp = (over = {}) => ({
  thread_key: "t1",
  is_opportunity: true,
  summary: "Migrate 40 servers to AWS.",
  urgency: "high",
  matched: true,
  sources: [{}, {}],
  source_types: ["crm", "notes"],
  fields: [
    field("customer_name", "Acme Corp", "Customer", "Customer"),
    field("industry", "Retail", "Customer"),
    field("contact_name", "Jo Park", "Contacts"),
    field("contact_email", "not-an-email", "Contacts"),
    field("business_problem", "Data centre lease ends in March."),
    field("desired_outcomes", "Move to AWS; Reduce cost by 20%"),
    field("compliance", "PCI DSS"),
  ],
  gaps: [{ field: "budget", label: "Budget", question: "What is the budget?", priority: "high" }],
  conflicts: [],
  ...over,
});

test("ranking prefers matched, multi-source, urgent opportunities", () => {
  const weak = opp({ thread_key: "weak", matched: false, sources: [{}], urgency: "low" });
  const strong = opp({ thread_key: "strong" });
  assert.deepEqual(rankOpportunities([weak, strong]).map((o) => o.thread_key), ["strong", "weak"]);
  assert.equal(chooseOpportunity([weak, strong], "weak").thread_key, "weak");
  assert.equal(chooseOpportunity([weak, strong], undefined).thread_key, "strong");
  assert.equal(chooseOpportunity([], null), null);
});

test("non-opportunities are excluded from ranking", () => {
  assert.equal(rankOpportunities([opp({ is_opportunity: false })]).length, 0);
});

test("intake block matches the UpstreamInputs contract", () => {
  const block = intakeBlock(opp());
  assert.equal(block.customer_name, "Acme Corp");
  assert.deepEqual(block.goals, ["Move to AWS", "Reduce cost by 20%"]);
  assert.deepEqual(block.constraints, ["PCI DSS"]);
  assert.equal(block.contacts[0].email, undefined, "an invalid email must be dropped, the schema requires a valid one");
  assert.equal(block.problem_statement, "Data centre lease ends in March.");
});

test("briefing transcript is Name: text lines and appends a real call after it", () => {
  const text = briefingTranscript(opp(), "Customer: We also use SQL Server.");
  const lines = text.split("\n");
  assert.ok(lines.every((l) => /^[^:]+: .+/.test(l)), "every line needs a speaker prefix");
  assert.ok(lines[0].startsWith("Ingram Seller: Briefing from the intake record for Acme Corp"));
  assert.equal(lines.at(-1), "Customer: We also use SQL Server.");
  assert.ok(lines.every((l) => l.length < 760));
});

test("discovery documents are named for sort order and skip empties", () => {
  const docs = discoveryDocuments({ opportunity: opp(), handoff: { captured: [{ label: "Goal", value: "Move" }], open_gaps: [], suggested_questions: ["Budget?"] }, callTranscript: "", extraText: "" });
  assert.deepEqual(docs.map((d) => d.name), ["00_intake_record.md", "01_live_assist_handoff.md"]);
  assert.match(docs[1].content, /Budget\?/);
  for (const d of docs) assert.match(d.name, /^\d{2}_[A-Za-z0-9._-]+\.(md|txt)$/);
});

test("follow-up transcript carries open discovery questions only", () => {
  const text = followUpTranscript(
    { use_cases: [{ rank: 1, category: "EXPLICIT", confidence: "high", title: "Lift and shift", problem: "Lease", validation_question: "Which VMs?" }], gaps_and_open_questions: [{ question: "RTO?", status: "open" }, { question: "Done?", status: "resolved" }] },
    { executive_summary: ["Acme wants out of its data centre."] },
  );
  assert.match(text, /Still open: RTO\?/);
  assert.doesNotMatch(text, /Done\?/);
});

test("live assist block folds follow-up questions into notes", () => {
  const block = liveAssistBlock({ captured: [{ label: "Region", value: "eu-west-1" }], open_gaps: [{ label: "DR" }], suggested_questions: ["RPO?"], summary: "S" }, "", { suggested_questions: ["RTO?"], open_gaps: [] });
  assert.equal(block.source, "live_assist");
  assert.match(block.notes, /RTO\? \| RPO\?/);
  assert.deepEqual(block.findings, ["Region: eu-west-1"]);
});

test("architecture and cost blocks, and upstream assembly", () => {
  const design = {
    title: "Acme", region: "eu-west-1", availability_zones: ["a", "b"],
    components: [{ category: "actor", name: "User" }, { service: "Amazon EC2", purpose: "Compute" }, { service: "Amazon EC2" }, { service: "Amazon RDS", role: "Database" }],
    assumptions: [{ text: "Windows licences are BYOL" }],
    requirements: [{ kind: "non_functional", text: "99.9% uptime" }, { kind: "functional", text: "x" }],
  };
  const arch = architectureBlock(design, "AAAA");
  assert.deepEqual(arch.services.map((s) => s.name), ["Amazon EC2", "Amazon RDS"]);
  assert.match(arch.assumptions[0], /2 Availability Zones/);
  assert.deepEqual(arch.nfrs, ["99.9% uptime"]);
  assert.equal(arch.diagram_images.length, 1);

  const cost = costBlock({ calculator_url: "https://calculator.aws/#/estimate?id=abc", estimate: { environments: { prod: { services: [{ service: "EC2", monthly_cost: "$1,200.50" }] } }, monthly_total: 1200.5 } });
  assert.equal(cost.source_link, "https://calculator.aws/#/estimate?id=abc");
  assert.deepEqual(cost.line_items, [{ service: "EC2", monthly: 1200.5 }]);
  assert.equal(cost.annual_total, 14406);

  const upstream = buildUpstream({ meta: metaBlock({ opportunity: opp(), templateId: "cva/full", agents: ["intake"] }), intake: intakeBlock(opp()), architecture: arch, cost, liveAssist: null, discovery: null });
  assert.deepEqual(Object.keys(upstream).sort(), ["architecture", "cost", "intake", "meta"]);
  assert.equal(upstream.meta.schema_version, "1.0");
  assert.equal(buildUpstream({ meta: {}, intake: {}, architecture: { services: [], assumptions: [], nfrs: [] }, cost: { line_items: [], source_link: null } }).architecture, undefined);
});

test("cost requirements come from usage figures, and thin input is rejected", () => {
  const text = costRequirements({ title: "Acme", region: "eu-west-1", availability_zones: ["a"] }, { services: { compute: [{ service: "EC2", instances: 4, hours: 730, explanation: "x" }] } });
  assert.match(text, /- EC2: instances 4, hours 730/);
  assert.doesNotMatch(text, /explanation/);
  assert.throws(() => costRequirements({}, {}), /neither usage figures nor components/);
});

test("applyCostLink replaces a placeholder, a link line, or appends a Pricing section", () => {
  const url = "https://calculator.aws/#/estimate?id=new";
  assert.equal(applyCostLink("Price: [Cost Calculator Link]", url).how, "placeholder");
  const replaced = applyCostLink("# Pricing\n\nAWS Pricing Calculator estimate: https://calculator.aws/#/estimate?id=old\n\n# Next", url);
  assert.equal(replaced.how, "replaced");
  assert.match(replaced.markdown, /id=new/);
  assert.doesNotMatch(replaced.markdown, /id=old/);
  const inserted = applyCostLink("# Pricing\n\nText.\n\n# Terms\nx", url);
  assert.equal(inserted.how, "inserted");
  assert.ok(inserted.markdown.indexOf(url) < inserted.markdown.indexOf("# Terms"));
  assert.equal(applyCostLink("# Scope\nx", url).how, "appended");
  assert.equal(applyCostLink(`has ${url}`, url).how, "unchanged");
});

test("text helpers", () => {
  assert.deepEqual(splitList("a; b\n- c"), ["a", "b", "c"]);
  assert.ok(chunk("word ".repeat(400), 100).every((c) => c.length <= 100));
  assert.match(engagementName("  ../Acme & Co!! ", new Date("2026-10-29T10:11:12Z")), /^[A-Za-z0-9][A-Za-z0-9._ -]{0,100}$/);
});
