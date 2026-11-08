import { useCallback, useSyncExternalStore } from "react";
import type { EngineApi, ModelChoice } from "@/api";

/**
 * The model picker's list, kept for the whole page instead of one popover. The shell asks for it when it
 * loads (and the engine has been learning it since it started), so the first click shows it complete. Every
 * opening asks again, quietly: the list already held stays up until the new answer replaces it.
 */
export type ChoicesView = {
  /** The rows the engine last sent; null until its first answer. */
  choices: ModelChoice[] | null;
  /** Harnesses the engine is still learning; their rows can grow. */
  discovering: string[];
  /** Harnesses whose options did not (fully) load; Retry asks again. */
  incomplete: string[];
  /** The request failed and there is nothing to show. */
  failed: boolean;
};

type Discovery = Awaited<ReturnType<EngineApi["listModelChoices"]>>["discovery"];

/** How often a page asks again while the engine reports a harness still loading. */
export const POLL_MS = 1500;
/** The most asks in a row: 80 at 1.5 s is two minutes, long past a cold start of every harness. */
export const MAX_POLLS = 80;

const EMPTY: ChoicesView = { choices: null, discovering: [], incomplete: [], failed: false };

type Store = {
  view: ChoicesView;
  listeners: Set<() => void>;
  running: Promise<void> | null;
  /** A Retry pressed while a load runs rides on that load's next ask. */
  retryNext: boolean;
};

const stores = new WeakMap<EngineApi, Store>();

function storeOf(api: EngineApi): Store {
  let store = stores.get(api);
  if (!store) {
    store = { view: EMPTY, listeners: new Set(), running: null, retryNext: false };
    stores.set(api, store);
  }
  return store;
}

function publish(store: Store, view: ChoicesView) {
  store.view = view;
  store.listeners.forEach((listener) => listener());
}

export function getModelChoices(api: EngineApi): ChoicesView {
  return storeOf(api).view;
}

export function subscribeModelChoices(api: EngineApi, listener: () => void): () => void {
  const { listeners } = storeOf(api);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * While a harness is still being learned, an answer with fewer rows for it than the page already holds (an
 * engine restarted with the page open knows nothing yet) does not replace them. The final answer always does.
 */
export function mergeChoices(
  previous: ModelChoice[] | null,
  next: ModelChoice[],
  discovery: Discovery,
): ModelChoice[] {
  if (!previous || !discovery) return next;
  const held = new Map<string, ModelChoice[]>();
  for (const [name, info] of Object.entries(discovery)) {
    if (!info.more) continue;
    const before = previous.filter((choice) => choice.profile === name);
    if (before.length > next.filter((choice) => choice.profile === name).length) held.set(name, before);
  }
  if (!held.size) return next;
  const merged: ModelChoice[] = [];
  const placed = new Set<string>();
  for (const choice of next) {
    const keep = held.get(choice.profile);
    if (!keep) merged.push(choice);
    else if (!placed.has(choice.profile)) {
      placed.add(choice.profile);
      merged.push(...keep);
    }
  }
  return merged;
}

/** Rows of a profile the user deleted never show. With no profile list yet, every row shows. */
export function rowsForProfiles(rows: ModelChoice[], profiles: readonly { name: string }[]): ModelChoice[] {
  if (!profiles.length) return rows;
  const known = new Set(profiles.map((profile) => profile.name));
  return rows.filter((choice) => known.has(choice.profile));
}

async function poll(api: EngineApi, store: Store): Promise<void> {
  for (let polls = 1; ; polls += 1) {
    const retry = store.retryNext;
    store.retryNext = false;
    let answer: Awaited<ReturnType<EngineApi["listModelChoices"]>>;
    try {
      answer = await (retry ? api.listModelChoices(true) : api.listModelChoices());
    } catch {
      const { view } = store;
      publish(store, {
        choices: view.choices,
        discovering: [],
        incomplete: [...new Set([...view.incomplete, ...view.discovering])],
        failed: view.choices === null,
      });
      return;
    }
    const statuses = Object.entries(answer.discovery ?? {});
    const advancing = statuses.filter(([, info]) => info.more).map(([name]) => name);
    const stalled = statuses.filter(([, info]) => info.status !== "ready" && !info.more).map(([name]) => name);
    const again = advancing.length > 0 && polls < MAX_POLLS;
    publish(store, {
      choices: mergeChoices(store.view.choices, answer.choices, answer.discovery),
      discovering: again ? advancing : [],
      incomplete: again ? stalled : [...stalled, ...advancing],
      failed: false,
    });
    if (!again) return;
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

/**
 * Ask the engine for the list and keep asking while it reports a harness still loading. Joins a load that is
 * already running instead of starting a second. `retry` makes the engine probe the incomplete harnesses again.
 */
export function loadModelChoices(api: EngineApi, options: { retry?: boolean } = {}): Promise<void> {
  const store = storeOf(api);
  if (options.retry) store.retryNext = true;
  if (store.running) return store.running;
  const run = poll(api, store).finally(() => {
    store.running = null;
  });
  store.running = run;
  return run;
}

export function useModelChoices(api: EngineApi): ChoicesView {
  const subscribe = useCallback((listener: () => void) => subscribeModelChoices(api, listener), [api]);
  const snapshot = useCallback(() => getModelChoices(api), [api]);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
