/**
 * Runs stages in order, one at a time, each on the previous stage's output. Owns run status, timing,
 * the activity log, review gates, stop, resume and reset. No server state: leaving the page ends the run.
 */
import { AbortedError } from "../core/http.js";
import { FOUR_AGENT_KEYS, STAGES, STAGE_KEYS, STAGE_OUTPUTS, blankStages, freshOutputs, initialState } from "./state.js";
import { UserError, createStages } from "./stages.js";

const QUOTA_AGENTS = { discovery: "discovery", sow_draft: "sow", sow_combined: "sow", architecture: "architecture", cost: "cost" };
const MAX_LOG = 300;

export function createOrchestrator({ store, agents }) {
  let controller = null;
  let gateResolver = null;
  let currentKey = null;

  const signal = () => controller?.signal;
  const setStage = (key, patch) =>
    store.update("run", (run) => ({ stages: { ...run.stages, [key]: { ...run.stages[key], ...patch } } }));
  const log = (message, level = "info") =>
    store.update("run", (run) => ({ log: [...run.log, { at: Date.now(), level, key: currentKey, message }].slice(-MAX_LOG) }));
  const detail = (text) => currentKey && setStage(currentKey, { detail: text });

  function gate({ kind, title, message }) {
    store.update("run", { status: "paused", gate: { kind, title, message, stageKey: currentKey } });
    log(`Paused: ${title}`, "warn");
    return new Promise((resolve) => { gateResolver = resolve; });
  }

  function resolveGate(decision) {
    if (!gateResolver) return;
    const resolve = gateResolver;
    gateResolver = null;
    store.update("run", { status: "running", gate: null });
    resolve(decision);
  }

  const stages = createStages({ store, agents, signal, detail, log, gate });

  /** Abort the active run and mark it so its late callbacks cannot write into the reset state. */
  function discardRun() {
    if (controller) { controller.discarded = true; controller.abort(); }
    resolveGate("stop");
    agents.intake.reset();
  }

  /** Drop the outputs of `key` and every later stage so nothing stale survives a re-run. */
  function invalidateFrom(key) {
    const from = STAGE_KEYS.indexOf(key);
    store.set((state) => {
      const next = { run: { ...state.run, stages: { ...state.run.stages } } };
      for (const k of STAGE_KEYS.slice(from)) {
        next.run.stages[k] = blankStages()[k];
        for (const path of STAGE_OUTPUTS[k]) {
          const [slice, field] = path;
          const blank = freshOutputs[slice]();
          next[slice] = field ? { ...(next[slice] ?? state[slice]), [field]: blank[field] } : blank;
        }
      }
      return next;
    });
  }

  async function checkQuotas(keys) {
    const wanted = [...new Set(keys.map((k) => QUOTA_AGENTS[k]).filter(Boolean))];
    await Promise.all(wanted.map(async (name) => {
      try {
        const q = await agents[name].quota(signal());
        const left = q.runs_per_day - q.runs_today;
        log(`${name}: ${q.runs_today}/${q.runs_per_day} runs used today (limit ${q.runs_per_ip_hour} per hour per IP)`);
        if (left <= 0) throw new UserError(`The daily limit for the ${name} agent is used up (${q.runs_per_day}). Try again tomorrow.`);
      } catch (err) {
        if (err instanceof UserError || err instanceof AbortedError) throw err;
        log(`Could not read the ${name} quota: ${err.message}`, "warn");
      }
    }));
  }

  async function execute(key, mode) {
    const mine = controller;
    currentKey = key;
    setStage(key, { status: "running", startedAt: Date.now(), endedAt: null, error: "", detail: "Starting…" });
    log(`${STAGES.find((s) => s.key === key).label}: started`);
    try {
      await stages[key](mode);
      setStage(key, { status: "done", endedAt: Date.now() });
      log(`${STAGES.find((s) => s.key === key).label}: done`);
    } catch (err) {
      const stopped = err instanceof AbortedError;
      // After clear/exit the state has been reset; do not write a stale "stopped" back into it.
      if (!mine?.discarded) setStage(key, { status: stopped ? "stopped" : "failed", endedAt: Date.now(), error: stopped ? "Stopped." : err.message });
      throw err;
    } finally {
      currentKey = null;
    }
  }

  /** Runs `keys` in order. Stages already done are skipped when `resume` is set. */
  async function runPlan(keys, mode, { resume = false } = {}) {
    if (["running", "paused"].includes(store.get().run.status)) return;
    const mine = new AbortController();
    controller = mine;
    store.update("run", { status: "running", mode, startedAt: store.get().run.startedAt ?? Date.now(), finishedAt: null, error: null, gate: null });
    try {
      const todo = keys.filter((k) => !(resume && store.get().run.stages[k].status === "done"));
      await checkQuotas(todo);
      for (const key of todo) {
        invalidateFrom(key);
        await execute(key, mode);
      }
      store.update("run", { status: "completed", finishedAt: Date.now() });
      log("Run complete");
    } catch (err) {
      if (mine.discarded) return;
      const stopped = err instanceof AbortedError;
      store.update("run", { status: stopped ? "stopped" : "failed", finishedAt: Date.now(), error: stopped ? null : err.message });
      log(stopped ? "Stopped by user" : `Run stopped: ${err.message}`, stopped ? "warn" : "error");
    } finally {
      if (controller === mine) { controller = null; gateResolver = null; }
    }
  }

  return {
    stages: STAGES,
    /** The full nine-stage pipeline. */
    runFull: () => runPlan(STAGE_KEYS, "pipeline"),
    /** Clear, then Intake → Live Assist → Discovery → Live Assist follow-up → SOW draft. */
    rerunFourAgents: () => runPlan(FOUR_AGENT_KEYS, "four"),
    /** Run one agent on whatever its upstream agents have already produced. */
    runStage: (key) => runPlan([key], "single"),
    /** Pick up after a failure: skip stages that finished, continue the full pipeline. */
    resume: () => runPlan(STAGE_KEYS, "pipeline", { resume: true }),

    choose: (decision) => resolveGate(decision),

    /** Choose which opportunity the other agents use. Anything built on the previous choice is discarded. */
    select(key) {
      if (store.get().intake.selectedKey === key) return;
      store.update("intake", { selectedKey: key });
      if (store.get().run.gate?.kind !== "select" && store.get().run.stages.live_assist.status !== "pending") invalidateFrom("live_assist");
    },
    stop() {
      controller?.abort();
      resolveGate("stop");
    },

    /** Discard every agent output (inputs are kept) so the four agents can run again from scratch. */
    clearOpportunities() {
      discardRun();
      const { inputs, templates } = store.get();
      store.reset();
      store.set({ inputs, templates });
    },

    /** Leave the pipeline: stop everything and return to the empty default state. */
    exit() {
      discardRun();
      const { templates } = store.get();
      store.reset();
      store.set({ templates });
    },
  };
}

export { initialState };
