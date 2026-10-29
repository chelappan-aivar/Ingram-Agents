import assert from "node:assert/strict";
import { test } from "node:test";

import { createStore } from "../js/core/store.js";
import { createOrchestrator } from "../js/pipeline/orchestrator.js";
import { initialState } from "../js/pipeline/state.js";

const BODY = "In scope: migrate the workloads to AWS. ".repeat(6);
const calls = [];
const note = (name, payload) => calls.push({ name, payload });

const opportunity = {
  thread_key: "t1", is_opportunity: true, matched: true, sources: [{}], source_types: ["notes"], urgency: "high",
  summary: "Migrate to AWS", fields: [{ field: "customer_name", label: "Customer", group: "Customer", value: "Acme", verified: true }, { field: "business_problem", label: "Problem", group: "Opportunity", value: "Lease ends", verified: true }],
};

function fakeAgents({ highFindings = 0, failAt = null } = {}) {
  const maybeFail = (name) => { if (failAt === name) throw new Error(`${name} exploded`); };
  return {
    intake: {
      reset() {},
      async start(input) { note("intake.start", input); return "run-1234567890"; },
      async waitForRun() { maybeFail("intake"); return { status: "COMPLETED", opportunities: [opportunity], counts: {} }; },
    },
    liveAssist: {
      async runTranscript(input) {
        note("liveAssist.run", input); maybeFail("liveAssist");
        return { sessionId: "s", partial: false, view: {}, handoff: { captured: [{ label: "Goal", value: "Move" }], open_gaps: [{ label: "DR" }], suggested_questions: ["RTO?"], summary: "S" } };
      },
    },
    discovery: {
      quota: async () => ({ runs_today: 0, runs_per_day: 20, runs_per_ip_hour: 4 }),
      async run(input) {
        note("discovery.run", input); maybeFail("discovery");
        return { runId: "d1", engagement: input.engagement, report: { use_cases: [{ rank: 1, category: "EXPLICIT", confidence: "high", title: "Lift", problem: "p" }], gaps_and_open_questions: [{ question: "RPO?", status: "open" }], what_we_heard: {} }, narrative: { executive_summary: ["Acme wants AWS."] } };
      },
    },
    sow: {
      quota: async () => ({ runs_today: 0, runs_per_day: 60, runs_per_ip_hour: 15 }),
      async generate(input) { note("sow.generate", input); return { jobId: `j${calls.length}`, markdown: input.upstream.cost ? `# Pricing\n\nCombined SOW ${BODY}` : `# Draft SOW ${BODY}` }; },
      async waReview(input) { note("sow.waReview", input); return { findings: [], summary: { total: highFindings, high: highFindings, medium: 0, low: 0 } }; },
      async check(input) { note("sow.check", input); return { rules: [{ status: "pass" }], summary: { passed: 1, total: 1 } }; },
      async exportDocx(input) { note("sow.exportDocx", input); return { download_url: "https://example.test/x.docx", filename: "x.docx" }; },
    },
    architecture: {
      quota: async () => ({ runs_today: 0, runs_per_day: 40, runs_per_ip_hour: 6 }),
      async run(input) {
        note("architecture.run", input);
        return { runId: "a1", status: "completed", design: { title: "Acme", region: "us-east-1", components: [{ service: "Amazon EC2" }] }, usage: { services: { c: [{ service: "EC2", instances: 2 }] } }, diagramBase64: "AAAA" };
      },
    },
    cost: {
      quota: async () => ({ runs_today: 0, runs_per_day: 40, runs_per_ip_hour: 6 }),
      async run(input) { note("cost.run", input); return { runId: "c1", calculator_url: "https://calculator.aws/#/estimate?id=1", services_count: 1, estimate: { monthly_total: 100 } }; },
    },
  };
}

function setup(opts) {
  calls.length = 0;
  const store = createStore(initialState);
  store.update("inputs", { notesText: "Acme wants to move to AWS.", templateId: "cva/full", autoSelect: true });
  const orchestrator = createOrchestrator({ store, agents: fakeAgents(opts) });
  return { store, orchestrator };
}

test("full pipeline runs nine stages in order and hands each output to the next agent", async () => {
  const { store, orchestrator } = setup();
  await orchestrator.runFull();
  const s = store.get();
  assert.equal(s.run.status, "completed");
  assert.deepEqual(calls.map((c) => c.name), [
    "intake.start", "liveAssist.run", "discovery.run", "liveAssist.run", "sow.generate", "sow.waReview",
    "architecture.run", "cost.run", "sow.generate", "sow.waReview", "sow.check", "sow.exportDocx",
  ]);
  const [, brief, discovery, followup, draft, , arch, cost, combined] = calls;
  assert.match(brief.payload.text, /Acme/, "Live Assist receives the Intake output");
  assert.ok(discovery.payload.documents.some((d) => d.name === "00_intake_record.md"), "Discovery receives Intake");
  assert.ok(discovery.payload.documents.some((d) => d.name === "01_live_assist_handoff.md"), "Discovery receives Live Assist");
  assert.match(followup.payload.text, /Still open: RPO\?/, "Live Assist receives the Discovery report");
  assert.ok(draft.payload.upstream.discovery && draft.payload.upstream.live_assist && draft.payload.upstream.intake, "SOW draft has Intake + Live Assist + Discovery");
  assert.equal(draft.payload.upstream.architecture, undefined, "no architecture before it exists");
  assert.ok(arch.payload.srsText.startsWith("# Draft SOW"), "Architecture receives the SOW draft");
  assert.match(cost.payload.requirements, /EC2: instances 2/, "Cost receives the architecture");
  assert.ok(combined.payload.upstream.architecture && combined.payload.upstream.cost, "Combined SOW receives architecture and cost");
  assert.match(s.sow.combined.markdown, /Combined SOW/);
  assert.equal(s.run.stages.final_review.status, "done");
  assert.equal(s.sow.docx.filename, "x.docx");
});

test("a failed stage stops the run, keeps earlier results, and resume continues from it", async () => {
  const { store, orchestrator } = setup({ failAt: "discovery" });
  await orchestrator.runFull();
  assert.equal(store.get().run.status, "failed");
  assert.equal(store.get().run.stages.discovery.status, "failed");
  assert.equal(store.get().run.stages.intake.status, "done");
  assert.ok(store.get().intake.opportunities.length === 1);
  assert.equal(calls.filter((c) => c.name === "sow.generate").length, 0, "nothing downstream ran");
});

test("a high Well-Architected finding pauses the pipeline until the user decides", async () => {
  const { store, orchestrator } = setup({ highFindings: 2 });
  const finished = orchestrator.runFull();
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(store.get().run.status, "paused");
  assert.equal(store.get().run.gate.kind, "review");
  assert.equal(calls.some((c) => c.name === "architecture.run"), false);
  orchestrator.choose("continue");
  await finished;
  assert.equal(calls.some((c) => c.name === "architecture.run"), true);
});

test("stopping at a gate ends the run as stopped", async () => {
  const { store, orchestrator } = setup({ highFindings: 1 });
  const finished = orchestrator.runFull();
  await new Promise((r) => setTimeout(r, 50));
  orchestrator.choose("stop");
  await finished;
  assert.equal(store.get().run.status, "stopped");
});

test("the four-agent loop stops after the SOW draft and clear resets every output but keeps inputs", async () => {
  const { store, orchestrator } = setup();
  await orchestrator.rerunFourAgents();
  assert.equal(store.get().run.stages.sow_draft.status, "done");
  assert.equal(store.get().run.stages.architecture.status, "pending");
  assert.equal(calls.some((c) => c.name === "architecture.run"), false);

  orchestrator.clearOpportunities();
  const s = store.get();
  assert.equal(s.intake.opportunities.length, 0);
  assert.equal(s.liveAssist.brief, null);
  assert.equal(s.discovery.result, null);
  assert.equal(s.sow.draft, null);
  assert.equal(s.run.status, "idle");
  assert.equal(s.inputs.notesText, "Acme wants to move to AWS.", "inputs survive Clear so the agents can run again");
});

test("exit clears everything including inputs", async () => {
  const { store, orchestrator } = setup();
  await orchestrator.runFull();
  orchestrator.exit();
  const s = store.get();
  assert.equal(s.inputs.notesText, "");
  assert.equal(s.sow.combined, null);
  assert.equal(s.run.log.length, 0);
});

test("a standalone agent run does not need its upstream agents", async () => {
  const { store, orchestrator } = setup();
  store.update("inputs", { costText: "Two m5.large instances in us-east-1 running 24x7 with 100 GB of EBS storage." });
  await orchestrator.runStage("cost");
  assert.equal(store.get().run.status, "completed");
  assert.equal(calls.length, 1);
  assert.match(calls[0].payload.requirements, /m5\.large/);
});

test("missing input is reported as a clear message, not a crash", async () => {
  const { store, orchestrator } = setup();
  store.update("inputs", { notesText: "" });
  await orchestrator.runFull();
  assert.equal(store.get().run.status, "failed");
  assert.match(store.get().run.error, /Choose a source for the Intake agent/);
});

test("Dynamics 365 alone is enough for Intake and is sent with its limit", async () => {
  calls.length = 0;
  const { store, orchestrator } = setup();
  store.update("inputs", { useDynamics: true, dynamicsLimit: 10 });
  await orchestrator.runStage("intake");
  const start = calls.find((c) => c.name === "intake.start");
  assert.equal(start.payload.dynamicsLimit, 10);
  assert.equal(start.payload.outlookDays, null);
});

test("Outlook is refused until it is connected, then sent with its window", async () => {
  calls.length = 0;
  const { store, orchestrator } = setup();
  store.update("inputs", { useOutlook: true, outlookDays: 30 });
  await orchestrator.runStage("intake");
  assert.match(store.get().run.stages.intake.error, /Connect Outlook first/);
  store.update("outlook", { user: { email: "a@b.com" } });
  await orchestrator.runStage("intake");
  assert.equal(calls.find((c) => c.name === "intake.start").payload.outlookDays, 30);
});

test("exit empties the Outlook connection and every source", async () => {
  const { store, orchestrator } = setup();
  store.update("outlook", { user: { email: "a@b.com" } });
  store.update("inputs", { useDynamics: true });
  orchestrator.exit();
  assert.equal(store.get().outlook.user, null);
  assert.equal(store.get().inputs.useDynamics, false);
});
