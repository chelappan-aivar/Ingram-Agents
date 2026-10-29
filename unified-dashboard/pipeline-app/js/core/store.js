/**
 * In-memory state only. Nothing is written to localStorage/sessionStorage/cookies, so closing or
 * leaving the page, or pressing "Exit pipeline", leaves no run data behind.
 */
export function createStore(initialFactory) {
  let state = initialFactory();
  const listeners = new Set();
  const emit = () => listeners.forEach((fn) => fn(state));
  return {
    get: () => state,
    /** Shallow-merge a patch (object) or apply an updater (state => patch). */
    set(patch) {
      state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
      emit();
    },
    /** Update one top-level slice: update("sow", s => ({...})). */
    update(key, patch) {
      const current = state[key];
      state = { ...state, [key]: { ...current, ...(typeof patch === "function" ? patch(current) : patch) } };
      emit();
    },
    reset(keepKeys = []) {
      const fresh = initialFactory();
      for (const k of keepKeys) fresh[k] = state[k];
      state = fresh;
      emit();
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}
