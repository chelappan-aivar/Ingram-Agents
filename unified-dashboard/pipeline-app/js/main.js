import { createArchitectureAgent } from "./agents/architecture.js";
import { createCostAgent } from "./agents/cost.js";
import { createDiscoveryAgent } from "./agents/discovery.js";
import { createIntakeAgent } from "./agents/intake.js";
import { createLiveAssistAgent } from "./agents/live-assist.js";
import { createSowAgent } from "./agents/sow.js";
import { createLiveAssistHttp, requireSignIn } from "./core/dashboard-session.js";
import { createStore } from "./core/store.js";
import { createOrchestrator } from "./pipeline/orchestrator.js";
import { AGENT_TABS, initialState } from "./pipeline/state.js";
import { renderBanner, renderLog, renderResult } from "./ui/chrome.js";
import { button, h, replace } from "./ui/dom.js";
import { createInputsCard } from "./ui/inputs.js";
import { renderStepper } from "./ui/stepper.js";
import { renderTabBar, renderTabPanel } from "./ui/tabs.js";

const $ = (id) => document.getElementById(id);

function applyTheme() {
  let pref = null;
  try { pref = localStorage.getItem("ingram.theme"); } catch { /* storage blocked: follow the system */ }
  const dark = pref === "dark" || ((pref === "system" || pref === null) && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

async function boot() {
  applyTheme();
  const user = await requireSignIn();
  const name = user?.name || user?.email || user?.user?.email || "";
  $("avatar").textContent = (name.split(/[\s.@]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("") || "U");

  const store = createStore(initialState);
  const agents = {
    intake: createIntakeAgent(),
    liveAssist: createLiveAssistAgent(createLiveAssistHttp()),
    discovery: createDiscoveryAgent(),
    sow: createSowAgent(),
    architecture: createArchitectureAgent(),
    cost: createCostAgent(),
  };
  const orchestrator = createOrchestrator({ store, agents });
  const requested = new URLSearchParams(location.search).get("agent");
  let activeTab = AGENT_TABS.some((t) => t.id === requested) ? requested : "intake";

  const actions = {
    runStage: (key) => orchestrator.runStage(key),
    select: (key) => orchestrator.select(key),
    choose: (d) => orchestrator.choose(d),
    resume: () => orchestrator.resume(),
    exit: () => { orchestrator.exit(); activeTab = "intake"; },
    clear: () => orchestrator.clearOpportunities(),
    rerunFour: () => orchestrator.rerunFourAgents(),
    refresh: () => render(store.get()),
  };

  const connectOutlook = async () => {
    const set = (patch) => store.update("outlook", patch);
    set({ busy: true, code: null, uri: null, message: "Getting a sign-in code…" });
    try {
      const user = await agents.intake.connectOutlook({ onCode: (l) => set({ code: l.user_code, uri: l.verification_uri, message: "Waiting for you to finish signing in…" }) });
      set({ user, busy: false, code: null, uri: null, message: "" });
      store.update("inputs", { useOutlook: true });
    } catch (err) {
      set({ busy: false, code: null, uri: null, message: err.message });
    }
  };
  const disconnectOutlook = async () => {
    try { await agents.intake.disconnectOutlook(); } catch { /* the session expires on its own */ }
    store.update("outlook", { user: null, message: "" });
    store.update("inputs", { useOutlook: false });
  };
  const inputs = createInputsCard(store, { connectOutlook, disconnectOutlook });
  const controls = h("div", { class: "toolbar-actions" });
  const slots = { banner: h("div"), stepper: h("div"), result: h("div"), tabs: h("div"), panel: h("div"), log: h("div") };
  replace($("inputs-slot"), inputs.node);
  replace($("outputs"), slots.banner, slots.result, h("div", { class: "card" }, slots.tabs, slots.panel), slots.log);
  replace($("stepper-slot"), slots.stepper);
  replace($("controls-slot"), controls);

  function render(state) {
    const active = ["running", "paused"].includes(state.run.status);
    inputs.sync(state);
    replace(controls,
      button("Run pipeline", { variant: "primary", disabled: active, onClick: () => orchestrator.runFull() }),
      active ? button("Stop", { onClick: () => orchestrator.stop() }) : null,
      button("Exit pipeline", { onClick: actions.exit, title: "Stop, clear every result and empty the inputs" }));
    replace(slots.banner, renderBanner(state, actions));
    replace(slots.stepper, renderStepper(state));
    replace(slots.result, renderResult(state));
    replace(slots.tabs, renderTabBar(state, activeTab, (id) => { activeTab = id; render(store.get()); }));
    replace(slots.panel, renderTabPanel(state, activeTab, actions));
    replace(slots.log, renderLog(state));
    document.title = `${active ? "● " : ""}Pipeline Runs · Ingram Micro Agents Dashboard`;
  }

  let queued = false;
  store.subscribe(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; render(store.get()); });
  });
  setInterval(() => { if (store.get().run.status === "running") replace(slots.stepper, renderStepper(store.get())); }, 1000);

  // Leaving the page ends the run; warn before losing work that is still in progress.
  addEventListener("beforeunload", (e) => {
    if (["running", "paused"].includes(store.get().run.status) || store.get().run.stages.final_review.status === "done") { e.preventDefault(); e.returnValue = ""; }
  });

  render(store.get());
  agents.sow.templates().then((templates) => store.set({ templates })).catch((err) => {
    store.update("run", (run) => ({ log: [...run.log, { at: Date.now(), level: "warn", message: `Could not load SOW templates: ${err.message}` }] }));
  });
}

boot().catch((err) => {
  const box = $("outputs") ?? document.body;
  box.replaceChildren(h("div", { class: "banner banner-danger", role: "alert" }, h("strong", {}, "The Pipeline Runs page could not start."), h("p", {}, err.message)));
});
