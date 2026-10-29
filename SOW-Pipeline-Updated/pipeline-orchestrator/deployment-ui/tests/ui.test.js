/* Headless test of the pipeline page: node tests/ui.test.js   (needs: npm i jsdom)
 *
 * Loads the real index.html and scripts into jsdom, serves the static sample files from disk and the API from a
 * small in-memory fake, then drives the page like a user: Use sample, Start, a blocked run, a finished run, export.
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM, VirtualConsole } = require("jsdom");

const UI = path.resolve(__dirname, "..", "ui");
const API = "https://api.test/prod";
const calls = [];
const errors = [];
let runState;

const read = (f) => fs.readFileSync(path.join(UI, f), "utf8");
const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });

function fakeFetch(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  if (!url.startsWith(API)) {                       // a static file next to index.html
    const file = path.join(UI, url);
    if (!fs.existsSync(file)) return Promise.resolve({ ok: false, status: 404, text: async () => "" });
    return Promise.resolve({ ok: true, status: 200, text: async () => fs.readFileSync(file, "utf8") });
  }
  const route = url.slice(API.length);
  calls.push({ method, route, body: options.body ? JSON.parse(options.body) : null });
  if (route === "/session") return Promise.resolve(json({ runs_today: 2, runs_per_day: 20, stages: [
    { stage: "sow_draft", label: "SOW draft", agent: "sow" }, { stage: "sa_review", label: "Solutions Architect review", agent: "architect" },
    { stage: "wa_review", label: "Well-Architected review", agent: "sow" },
    { stage: "architecture", label: "Architecture diagram", agent: "architecture" }, { stage: "cost", label: "Cost estimate", agent: "cost" },
    { stage: "combined_review", label: "Final SOW review", agent: "sow" }] }));
  if (route === "/runs" && method === "GET") return Promise.resolve(json({ runs: [{ run_id: "a".repeat(32), title: "Fabrikam", status: "blocked", stage: "wa_review", progress: 20, created_at: "2026-10-27T10:00:00Z" }] }));
  if (route === "/runs" && method === "POST") return Promise.resolve(json({ run_id: "b".repeat(32) }, 202));
  if (/^\/runs\/[a-f0-9]{32}$/.test(route)) return Promise.resolve(json(runState));
  if (route.endsWith("/sow")) return Promise.resolve(json({ markdown: "# Pricing\nAWS Pricing Calculator estimate: https://calculator.aws/#/estimate?id=abc\n\n![Architecture Diagram](arch:1)\n" }));
  if (route.endsWith("/diagram")) return Promise.resolve(json({ png_base64: "UE5H" }));
  if (route.endsWith("/architecture")) return Promise.resolve(json({
    title: "Claims FNOL Assistant", region: "us-east-2", zones: ["us-east-2a", "us-east-2b"], context: "",
    components: [{ id: "C-02", name: "<img src=x onerror=window.__xss2=1>", service: "Amazon Bedrock", role: "Classifies claims", sku_summary: "Claude Sonnet", quantity: 1 },
      { id: "C-03", name: "Document Store", service: "Amazon S3", purpose: "Attachments", sku_summary: "S3 Standard", quantity: 2 }],
    flows: [{ from: "Triage Agent", to: "Document Store", label: "stores attachments", protocol: "HTTPS" }],
    decisions: [{ id: "ADR-001", title: "Use Bedrock", decision: "Claude Sonnet on demand." }],
    assumptions: ["18,000 claims a month."], security_operations: [], open_questions: [],
    adr_md: "# Architecture Decision Record\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n", usage_assumptions_md: "# Usage estimate", scope_warnings: ["Amazon Kinesis may be out of scope."] }));
  if (route.endsWith("/revise")) return Promise.resolve(json({ run_id: "b".repeat(32), target: options.body ? JSON.parse(options.body).target : "" }, 202));
  if (route.endsWith("/export")) return Promise.resolve(json({ download_url: "https://s3.test/x.docx", filename: "Fabrikam_SOW.docx", with_diagram: true }));
  if (route.endsWith("/continue")) return Promise.resolve(json({ run_id: "b".repeat(32) }, 202));
  return Promise.resolve(json({ error: "not found" }, 404));
}

const stages = (statuses, extra = {}) => ["sow_draft", "sa_review", "wa_review", "architecture", "cost", "combined_review"].map((s, i) => ({
  stage: s, label: s, agent: "x", status: statuses[i] || "pending", detail: "", error: "", seconds: 1, ...(extra[s] || {}) }));

async function main() {
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => { if (!/Not implemented: navigation/.test(String(e.message))) errors.push(e.message); });
  const dom = new JSDOM(read("index.html").replace(/<script src="[^"]+"><\/script>/g, ""), { runScripts: "outside-only", url: "http://localhost:5173/", virtualConsole });
  const w = dom.window;
  w.fetch = fakeFetch;
  w.matchMedia = () => ({ matches: true, addEventListener() {} });
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.eval(`window.PIPELINE_CONFIG = { apiUrl: "${API}" };`);
  for (const f of ["templates.js", "samples.js", "markdown.js", "app.js"]) w.eval(read(f));
  const $ = (id) => w.document.getElementById(id);
  const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
  await tick();

  // ---- templates and samples
  const options = [...$("template").options].map((o) => o.value);
  assert.deepStrictEqual(options.sort(), ["aiml/ai-ml", "cva/full", "devops/devops-standard"]);
  const sampleItems = [...w.document.querySelectorAll("#samples li.sample")];
  assert.strictEqual(sampleItems.length, 4, "four samples are listed");
  const buttonTexts = sampleItems[0].querySelectorAll("button");
  assert.deepStrictEqual([...buttonTexts].map((b) => b.textContent), ["Use sample", "Use with example SOW", "View example"]);
  assert.match($("quota").textContent, /2 of 20 runs/);

  // ---- Use sample (draft from scratch)
  const click = async (node) => { node.click(); await tick(60); };
  await click(sampleItems[0].querySelector("button"));
  assert.strictEqual($("customer").value, "Fabrikam Insurance");
  assert.strictEqual($("template").value, "aiml/ai-ml");
  assert.ok($("details").value.length > 200, "project details loaded");
  const up = JSON.parse($("upstream").value);
  assert.ok(!("architecture" in up) && !("cost" in up), "the pipeline's own blocks are stripped from the sample");
  assert.strictEqual($("sow").value, "", "plain Use sample leaves the SOW empty so it is drafted");
  assert.match($("form-status").textContent, /sample loaded/);

  // ---- Use with example SOW (skips drafting)
  await click(sampleItems[3].querySelectorAll("button")[1]);
  assert.strictEqual($("template").value, "cva/full");
  assert.strictEqual($("customer").value, "Woodgrove Bank");
  assert.ok($("sow").value.includes("[cost calculator link]"), "the example SOW is loaded, with its placeholder");
  assert.strictEqual($("sow-panel").open, true);

  // ---- Start: the request the API receives
  calls.length = 0;
  runState = { run: { run_id: "b".repeat(32), title: "Woodgrove Bank", status: "blocked", stage: "wa_review", progress: 20, template_id: "cva/full", created_at: "2026-10-27T10:00:00Z" },
    state: { status: "blocked", has_sow: true, scope: { in_scope: ["Server inventory"], out_of_scope: ["Migration"] },
      stages: stages(["skipped", "completed", "blocked"], { wa_review: { detail: "1 high Well-Architected finding(s) against the SOW: Workload scoped to a single Availability Zone.",
        summary: { pillars: [{ pillar: "reliability", label: "Reliability", findings: 1 }] },
        findings: [{ id: "x", severity: "high", pillar: "reliability", title: "<img src=x onerror=window.__xss=1>", detail: "d", suggestion: "s", evidence: "e" }] } }) },
    log: [{ at: "2026-10-27T10:00:01Z", stage: "wa_review", level: "warning", message: "stopped for review" }] };
  $("run-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await tick(80);
  const post = calls.find((c) => c.method === "POST" && c.route === "/runs");
  assert.ok(post, "POST /runs was sent");
  assert.strictEqual(post.body.template_id, "cva/full");
  assert.strictEqual(post.body.upstream.intake.customer_name, "Woodgrove Bank");
  assert.strictEqual(post.body.upstream.meta.template_id, "cva/full");
  assert.ok(!("architecture" in post.body.upstream) && !("cost" in post.body.upstream));
  assert.ok(post.body.sow_markdown.includes("Woodgrove Bank"));
  assert.strictEqual(post.body.stop_on_blocked, true);

  // ---- a blocked run: stages, banner, findings, nothing executed from the finding text
  assert.strictEqual($("run").hidden, false);
  assert.strictEqual(w.document.querySelectorAll("#stages li.stage").length, 6);
  assert.ok(w.document.querySelector("#stages li.stage.blocked"), "the blocked stage is marked");
  assert.strictEqual($("blocked").hidden, false);
  assert.match($("blocked-detail").textContent, /single Availability Zone/);
  assert.strictEqual($("downloads").hidden, false);
  assert.strictEqual($("export").disabled, true, "export waits for the cost stage");
  assert.strictEqual(w.__xss, undefined, "finding text is never executed");
  assert.ok(!$("review-view").querySelector("img"), "finding text is rendered as text, not HTML");
  assert.match($("review-view").textContent, /<img src=x/);
  assert.strictEqual($("review-count").hidden, false);

  // ---- a blocked run puts the "Change this run with AI" panel in front of the reader, aimed at the SOW
  assert.strictEqual($("revise-panel").open, true, "the panel opens on a blocked run");
  assert.strictEqual($("revise-target").value, "sow");
  w.document.querySelector("#review-view .finding button").click();
  assert.match($("revise-instruction").value, /^Resolve this Well-Architected finding: <img src=x/);
  assert.strictEqual($("revise-target").value, "sow");
  assert.strictEqual($("revise-send").disabled, false, "a blocked (settled) run can be changed");

  // ---- continue past the block
  calls.length = 0;
  await click($("continue"));
  assert.ok(calls.some((c) => c.method === "POST" && c.route.endsWith("/continue")));

  // ---- a finished run: link, diagram, export
  runState.run = { ...runState.run, status: "completed", progress: 100, calculator_url: "https://calculator.aws/#/estimate?id=abc" };
  runState.state = { status: "completed", has_sow: true, scope: runState.state.scope,
    stages: stages(["skipped", "completed", "completed", "completed", "completed", "completed"], { cost: { calculator_url: "https://calculator.aws/#/estimate?id=abc" } }) };
  await w.eval("document.querySelector('#history button').click()");   // open a run from the history
  await tick(150);
  assert.strictEqual($("export").disabled, false, "export is available once the cost stage is complete");
  assert.strictEqual($("calc-link").hidden, false);
  assert.strictEqual($("calc-link").getAttribute("href"), "https://calculator.aws/#/estimate?id=abc");
  assert.strictEqual($("blocked").hidden, true);
  assert.ok($("sow-view").textContent.includes("AWS Pricing Calculator estimate"), "the SOW is shown");
  assert.ok($("sow-view").querySelector("img[src^='data:image/png;base64,UE5H']"), "the diagram is placed where the SOW says arch:1");
  w.document.querySelector('[data-tab="arch"]').click();
  assert.ok($("arch-view").querySelector(".diagram-frame img"), "the Architecture tab shows the diagram");
  assert.strictEqual($("tab-arch").hidden, false);
  assert.strictEqual($("tab-sow").hidden, true);

  // ---- the design the architecture agent prepared is shown under the diagram, as text
  await tick(100);
  const archText = $("arch-view").textContent;
  assert.match(archText, /Claims FNOL Assistant/);
  assert.match(archText, /Region us-east-2 · 2 Availability Zones: us-east-2a, us-east-2b/);
  assert.strictEqual($("arch-view").querySelectorAll(".design > .md table tbody tr").length, 2, "one row per component");
  assert.match(archText, /Amazon Bedrock/);
  assert.match(archText, /Triage Agent → Document Store: stores attachments \(HTTPS\)/);
  assert.match(archText, /ADR-001: Use Bedrock/);
  assert.match(archText, /Amazon Kinesis may be out of scope/);
  assert.match([...$("arch-view").querySelectorAll("details summary")].map((s) => s.textContent).join("|"), /Architecture Decision Record \(ADR\)\|Usage assumptions/);
  assert.strictEqual(w.__xss2, undefined, "component names are never executed");
  assert.ok(!$("arch-view").querySelector(".design table img"), "component names are rendered as text");
  assert.strictEqual($("revise-target").value, "architecture", "opening the Architecture tab aims a change at it");
  assert.ok($("arch-view").querySelector("details .md table"), "the ADR is rendered as markdown, tables included");

  // ---- asking the AI for a change
  calls.length = 0;
  $("revise-instruction").value = "hi";
  $("revise-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await tick();
  assert.match($("revise-status").textContent, /Say what to change/);
  assert.ok(!calls.some((c) => c.route.endsWith("/revise")), "a too-short instruction is not sent");

  $("revise-instruction").value = "Put AWS WAF in front of the API";
  $("revise-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await tick(120);
  const revise = calls.find((c) => c.method === "POST" && c.route.endsWith("/revise"));
  assert.ok(revise, "POST /revise was sent");
  assert.deepStrictEqual(revise.body, { target: "architecture", instruction: "Put AWS WAF in front of the API" });
  assert.strictEqual($("revise-instruction").value, "", "the box is cleared once the change is queued");

  // ---- a change in progress: the form is locked and the pending request is listed first
  runState.run = { ...runState.run, status: "running", progress: 60 };
  runState.state = { ...runState.state, status: "running",
    revision: { target: "architecture", instruction: "Put AWS WAF in front of the API", requested_at: "t" }, revisions: [] };
  await w.eval("document.querySelector('#history button').click()");
  await tick(150);
  assert.strictEqual($("revise-send").disabled, true, "no second change while one is running");
  assert.match($("revise-status").textContent, /Applying your change to the Architecture design/);
  assert.match($("revisions").querySelector("li").textContent, /in progress[\s\S]*Put AWS WAF/);

  // ---- the history of changes, including one that was not applied
  runState.run = { ...runState.run, status: "completed", progress: 100 };
  runState.state = { ...runState.state, status: "completed", revision: null, revisions: [
    { target: "architecture", instruction: "Put AWS WAF in front of the API", status: "applied", summary: "Design revised and the diagram redrawn: 1 component(s) added, 0 removed.",
      details: ["Added: Web Firewall (AWS WAF)"], finished_at: "2026-10-27T10:05:00Z" },
    { target: "cost", instruction: "Assume <b>reserved</b> pricing", status: "failed", error: "the cost agent returned no calculator_url", details: [], finished_at: "2026-10-27T10:09:00Z" }] };
  await w.eval("document.querySelector('#history button').click()");
  await tick(150);
  const items = [...w.document.querySelectorAll("#revisions > li")];
  assert.strictEqual(items.length, 2);
  assert.match(items[0].textContent, /Cost estimate[\s\S]*not applied[\s\S]*Assume <b>reserved<\/b> pricing[\s\S]*no calculator_url/, "newest first, shown as text");
  assert.ok(!items[0].querySelector("b"), "instruction text is never parsed as HTML");
  assert.ok(items[0].classList.contains("lvl-error"));
  assert.match(items[1].textContent, /Architecture design[\s\S]*applied[\s\S]*Added: Web Firewall \(AWS WAF\)/);
  assert.strictEqual($("revise-send").disabled, false);
  runState.state.revisions = [];

  // ---- a link that is not the calculator is never made clickable
  runState.run.calculator_url = "javascript:alert(1)";
  runState.state.stages[4].calculator_url = "javascript:alert(1)";   // cost is the fifth stage now
  await w.eval("document.querySelector('#history button').click()");
  await tick(150);
  assert.strictEqual($("calc-link").hidden, true, "a non-calculator link is not offered");

  calls.length = 0;
  await click($("export"));
  assert.ok(calls.some((c) => c.method === "POST" && c.route.endsWith("/export")), "export was requested");
  assert.match($("export-status").textContent, /Fabrikam_SOW\.docx/);

  // ---- viewing an example does not disturb the form
  await click(sampleItems[1].querySelectorAll("button")[2]);
  assert.strictEqual($("viewer").hidden, false);
  assert.ok($("viewer-body").textContent.length > 500);
  await click($("viewer-close"));
  assert.strictEqual($("viewer").hidden, true);

  // ---- validation
  $("run-form").reset(); $("upstream").value = "{not json"; $("customer").value = "Acme";
  $("run-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await tick();
  assert.match($("form-status").textContent, /not valid JSON/);
  $("upstream").value = ""; $("customer").value = "";
  $("run-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await tick();
  assert.match($("form-status").textContent, /customer name/);

  assert.deepStrictEqual(errors, [], "no script errors");
  console.log("ui.test.js: all assertions passed");
  w.close();
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
