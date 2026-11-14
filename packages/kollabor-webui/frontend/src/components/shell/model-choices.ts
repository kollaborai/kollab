import { useCallback, useSyncExternalStore } from "react";
import type { EngineApi, ModelChoice, Profile } from "@/api";

/**
 * The model picker's list, kept for the whole page instead of one popover. The shell asks for it when it
 * loads (and the engine has been learning it since it started), so the first click shows it complete. Every
 * opening asks again, quietly: the list already held stays up until the new answer replaces it. One list is
 * kept per workspace (the folder whose own profiles a picker lists; empty is the engine's folder), so a picker
 * never shows, and is never overwritten by, the rows another folder's answer carries.
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
  /** The folder this list is for, trimmed; empty is the engine's own. */
  workspace: string;
  view: ChoicesView;
  listeners: Set<() => void>;
  running: Promise<void> | null;
  /** A Retry pressed while a load runs rides on that load's next ask. */
  retryNext: boolean;
  /** Last launch identity per profile, used to discard rows from an older ACP context. */
  profileSignatures: Map<string, string> | null;
};

const stores = new WeakMap<EngineApi, Map<string, Store>>();

/** Profile changes that can alter the engine's model catalog or ACP warm result. */
export function profileChoicesKey(
  profiles: readonly Pick<Profile, "name" | "provider" | "choices_fingerprint">[],
): string {
  return profiles
    .map(({ name, provider, choices_fingerprint }) =>
      `${name}\u0000${provider ?? ""}\u0000${choices_fingerprint ?? ""}`,
    )
    .join("\n");
}

function storeOf(api: EngineApi, workspace = ""): Store {
  const folder = workspace.trim();
  let byFolder = stores.get(api);
  if (!byFolder) {
    byFolder = new Map();
    stores.set(api, byFolder);
  }
  let store = byFolder.get(folder);
  if (!store) {
    store = {
      workspace: folder,
      view: EMPTY,
      listeners: new Set(),
      running: null,
      retryNext: false,
      profileSignatures: null,
    };
    byFolder.set(folder, store);
  }
  return store;
}

function publish(store: Store, view: ChoicesView) {
  store.view = view;
  store.listeners.forEach((listener) => listener());
}

export function getModelChoices(api: EngineApi, workspace = ""): ChoicesView {
  return storeOf(api, workspace).view;
}

export function subscribeModelChoices(api: EngineApi, listener: () => void, workspace = ""): () => void {
  const { listeners } = storeOf(api, workspace);
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
    const requestSignatures = store.profileSignatures;
    let answer: Awaited<ReturnType<EngineApi["listModelChoices"]>>;
    try {
      answer = await (
        retry ? api.listModelChoices(store.workspace, true) : api.listModelChoices(store.workspace)
      );
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
    const changedDuringRequest = new Set<string>();
    const requestedSignatures = requestSignatures ?? new Map<string, string>();
    const currentSignatures = store.profileSignatures ?? new Map<string, string>();
    const comparedProfiles = new Set([...requestedSignatures.keys(), ...currentSignatures.keys()]);
    for (const name of comparedProfiles) {
      if (requestedSignatures.get(name) !== currentSignatures.get(name)) changedDuringRequest.add(name);
    }
    const choices = answer.choices.filter((choice) => !changedDuringRequest.has(choice.profile));
    const discovery = answer.discovery
      ? Object.fromEntries(Object.entries(answer.discovery).filter(([name]) => !changedDuringRequest.has(name)))
      : answer.discovery;
    const statuses = Object.entries(discovery ?? {});
    const advancing = [
      ...new Set([...statuses.filter(([, info]) => info.more).map(([name]) => name), ...changedDuringRequest]),
    ];
    const stalled = statuses.filter(([, info]) => info.status !== "ready" && !info.more).map(([name]) => name);
    const retryQueued = store.retryNext;
    const again = retryQueued || (advancing.length > 0 && polls < MAX_POLLS);
    publish(store, {
      choices: mergeChoices(store.view.choices, choices, discovery),
      discovering: again ? [...new Set([...advancing, ...(retryQueued ? stalled : [])])] : [],
      incomplete: again ? stalled : [...stalled, ...advancing],
      failed: false,
    });
    if (!again) return;
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

/**
 * Ask the engine for the list and keep asking while it reports a harness still loading. Joins a load that is
 * already running for the same `workspace` instead of starting a second. `retry` makes the engine probe the
 * incomplete harnesses again.
 */
export function loadModelChoices(
  api: EngineApi,
  options: {
    retry?: boolean;
    profiles?: readonly Pick<Profile, "name" | "provider" | "choices_fingerprint">[];
    workspace?: string;
  } = {},
): Promise<void> {
  const store = storeOf(api, options.workspace);
  if (options.profiles) {
    const nextSignatures = new Map(
      options.profiles.map(({ name, provider, choices_fingerprint }) => [
        name,
        `${provider ?? ""}\u0000${choices_fingerprint ?? ""}`,
      ]),
    );
    const previousSignatures = store.profileSignatures;
    if (previousSignatures) {
      const changed = new Set([...previousSignatures.keys(), ...nextSignatures.keys()]);
      for (const name of changed) {
        if (previousSignatures.get(name) === nextSignatures.get(name)) changed.delete(name);
      }
      if (changed.size && store.view.choices) {
        publish(store, {
          ...store.view,
          choices: store.view.choices.filter((choice) => !changed.has(choice.profile)),
          discovering: store.view.discovering.filter((name) => !changed.has(name)),
          incomplete: store.view.incomplete.filter((name) => !changed.has(name)),
        });
      }
    }
    store.profileSignatures = nextSignatures;
  }
  if (options.retry) store.retryNext = true;
  if (store.running) return store.running;
  const run = poll(api, store).finally(() => {
    store.running = null;
  });
  store.running = run;
  return run;
}

export function useModelChoices(api: EngineApi, workspace = ""): ChoicesView {
  const subscribe = useCallback(
    (listener: () => void) => subscribeModelChoices(api, listener, workspace),
    [api, workspace],
  );
  const snapshot = useCallback(() => getModelChoices(api, workspace), [api, workspace]);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
