/**
 * The web plugin loader (docs/specs/plugins.md, "Web plugin API" > Loading).
 *
 * At boot the page reads `GET /plugins` and imports every enabled plugin's `web/dist` entry in parallel from
 * `/plugins/{id}/web/<file>?v=<hash>`, then awaits its plugin function. A 5 s limit covers the import and that
 * await; a plugin that throws or runs out of time shows Failed to Load and nothing else changes. Its
 * contributions show only once its function returns, so a timed-out import that resolves later adds nothing.
 * Retry imports `<file>?v=<hash>&retry=<n>` so the browser does not hand back the failed module. Every
 * `plugins-changed` event reads `GET /plugins` again and adds or removes plugins without a reload, and
 * `?plugins=off` in the page URL loads no plugin code at all.
 */
import { useSyncExternalStore } from "react";
import type { EngineApi } from "@/api";
import { inPhoneApp, navigateToPluginPage } from "@/utils/page-route";
import {
  EMPTY_CONTRIBUTIONS,
  nextNoticeKey,
  pluginRegistry,
  pluginViewKey,
  type Contributions,
  type PluginRegistry,
  type PluginViewKey,
} from "./registry";
import { createPluginApi, followEvents, isOpenTarget, type OpenTarget, type PluginApiDeps, type PluginEntry } from "./sdk";

export const LOAD_TIMEOUT_MS = 5_000;

const ID = /^[a-z0-9][a-z0-9-]{1,39}(?:@dev)?$/;
// `stopping`: its background processes finish their step and its page stays until they exit.
const LOADABLE = new Set<PluginEntry["state"]>(["ok", "error", "stopping"]);
// The export React itself assigns. Code that only reads it (a renderer library) never assigns it.
const OWN_REACT =
  /__(?:CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE|SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED)\s*=(?!=)/;

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

export type PluginInventory = { entries: readonly PluginEntry[]; error: string | null };

// Every listed plugin, including off, shadowed and failed ones. The registry holds only the loaded ones.
let inventory: PluginInventory = { entries: [], error: null };
const inventoryListeners = new Set<() => void>();

function setInventory(next: PluginInventory): void {
  inventory = next;
  for (const listener of inventoryListeners) listener();
}

/** Every listed plugin as the last successful read returned it. */
export const listedPluginEntries = (): readonly PluginEntry[] => inventory.entries;

function subscribeInventory(listener: () => void): () => void {
  inventoryListeners.add(listener);
  return () => {
    inventoryListeners.delete(listener);
  };
}

/** The listed plugins and the error of the last read; a failed read keeps the last good list. */
export function usePluginInventory(): PluginInventory {
  return useSyncExternalStore(
    subscribeInventory,
    () => inventory,
    () => inventory,
  );
}

/** `?plugins=off`: the way back in when a plugin freezes the page. */
export function pluginsOff(search: string): boolean {
  return new URLSearchParams(search).get("plugins") === "off";
}

/** Whether a listed plugin runs on this page: on, not shadowed by a dev folder, unchanged, with something to show. */
export function isActive(entry: PluginEntry): boolean {
  const shows =
    Boolean(entry.web) || Object.values(entry.contributes ?? {}).some((list) => Array.isArray(list) && list.length > 0);
  return ID.test(entry.id) && entry.enabled === true && !entry.shadowed && LOADABLE.has(entry.state) && shows;
}

/** The web UI URL of a plugin's entry module or stylesheet; null for a path outside `web/dist/`. */
export function assetUrl(entry: PluginEntry, file: "entry" | "style", retry = 0): string | null {
  const web = entry.web;
  const path = file === "entry" ? web?.entry : web?.style;
  if (!web || typeof path !== "string" || !path.startsWith("web/dist/") || !ID.test(entry.id)) return null;
  const parts = path.slice("web/dist/".length).split("/");
  if (parts.some((part) => !part || part.startsWith("."))) return null;
  const query = `v=${encodeURIComponent(web.hash)}${retry > 0 ? `&retry=${retry}` : ""}`;
  return `/plugins/${entry.id}/web/${parts.map(encodeURIComponent).join("/")}?${query}`;
}

/** A bundle that carries its own copy of React (its hooks would not work with the app's React). */
export function bundlesOwnReact(source: string): boolean {
  return OWN_REACT.test(source);
}

const IMPORT_SPECIFIERS = [
  /\b(?:import|export)\s*(?:[^;'\"]*?\s*from\s*)?["']([^"']+)["']/g,
  /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
];

/** Check the entry and its local module graph; bundled React is often emitted as a shared chunk. */
async function bundleImportsOwnReact(
  pluginId: string,
  entryUrl: string,
  entrySource: string,
  fetchText: (url: string) => Promise<string>,
): Promise<boolean> {
  const origin = "https://kollab-plugin.invalid";
  const rootPath = `/plugins/${pluginId}/web/`;
  const visited = new Set<string>();

  async function inspect(url: string, source: string): Promise<boolean> {
    const resolved = new URL(url, origin);
    if (resolved.origin !== origin || !resolved.pathname.startsWith(rootPath)) return false;
    const key = `${resolved.pathname}${resolved.search}`;
    if (visited.has(key)) return false;
    visited.add(key);
    if (bundlesOwnReact(source)) return true;

    const specifiers = new Set<string>();
    for (const pattern of IMPORT_SPECIFIERS) {
      pattern.lastIndex = 0;
      for (let match = pattern.exec(source); match; match = pattern.exec(source)) specifiers.add(match[1]);
    }
    for (const specifier of specifiers) {
      if (!specifier.startsWith(".") && !specifier.startsWith("/")) continue;
      const dependency = new URL(specifier, resolved);
      if (dependency.origin !== origin || !dependency.pathname.startsWith(rootPath)) continue;
      // Keep the entry's cache key on fetched chunks so a retry checks the same version the browser imports.
      if (!dependency.search) {
        for (const keyName of ["v", "retry"]) {
          const value = resolved.searchParams.get(keyName);
          if (value !== null) dependency.searchParams.set(keyName, value);
        }
      }
      if (await inspect(`${dependency.pathname}${dependency.search}`, await fetchText(`${dependency.pathname}${dependency.search}`)))
        return true;
    }
    return false;
  }

  return inspect(entryUrl, entrySource);
}

export type LoaderDeps = {
  list(): Promise<{ plugins?: unknown }>;
  importModule(url: string): Promise<unknown>;
  fetchText(url: string): Promise<string>;
  addStyle?(id: string, href: string): () => void;
  api: Pick<PluginApiDeps, "request" | "stream">;
  timeoutMs?: number;
};

/** What the app lends plugins: switching the open chat's tab and opening a chat. */
export type PluginHost = { openView(key: PluginViewKey): void; openSession(sessionId: string): void };

const NO_HOST: PluginHost = { openView() {}, openSession() {} };
let host = NO_HOST;

/** App registers its view switch and chat opener; returns the unregister. */
export function setPluginHost(next: PluginHost): () => void {
  host = next;
  return () => {
    if (host === next) host = NO_HOST;
  };
}

/** Opens a menu row's, a notice's or `k.navigate`'s target. */
export function openTarget(pluginId: string, target: OpenTarget, registry: PluginRegistry = pluginRegistry): void {
  if ("page" in target) navigateToPluginPage(pluginId, target.page);
  else if ("modal" in target) registry.openModal(pluginId, target.modal);
  else if ("view" in target) host.openView(pluginViewKey(pluginId, target.view));
  else if (/^https?:\/\//i.test(target.url)) window.open(target.url, "_blank", "noopener,noreferrer");
}

type Attempt = { token: symbol; hash: string | null; dispose(): void };

export function createPluginLoader(registry: PluginRegistry, deps: LoaderDeps) {
  const timeoutMs = deps.timeoutMs ?? LOAD_TIMEOUT_MS;
  const attempts = new Map<string, Attempt>();
  const pending = new Set<Promise<void>>();
  let queue: Promise<void> = Promise.resolve();

  const current = (id: string, token: symbol) => attempts.get(id)?.token === token;
  const find = (id: string) => registry.getSnapshot().plugins.find((record) => record.id === id);

  function drop(id: string) {
    attempts.get(id)?.dispose();
    attempts.delete(id);
    registry.remove(id);
  }

  function load(entry: PluginEntry, retries: number): Promise<void> {
    attempts.get(entry.id)?.dispose();
    const token = Symbol(entry.id);
    let staged: Contributions = EMPTY_CONTRIBUTIONS;
    let live = false;
    // Timed out, failed, replaced or removed: nothing of this attempt runs any more.
    let abandoned = false;
    let unstyle = () => {};
    const { k, dispose } = createPluginApi(entry, {
      ...deps.api,
      contribute(change) {
        if (!current(entry.id, token)) return;
        if (live) registry.contribute(entry.id, change);
        else staged = change(staged);
      },
      open: (target) => openTarget(entry.id, target, registry),
      openSession: (sessionId) => host.openSession(sessionId),
    });
    attempts.set(entry.id, {
      token,
      hash: entry.web?.hash ?? null,
      dispose() {
        abandoned = true;
        dispose();
        unstyle();
      },
    });
    const web = entry.web;
    registry.put({
      id: entry.id,
      name: entry.name,
      entry,
      status: web ? "loading" : "ready",
      error: null,
      retries,
      k: web ? null : k,
      contributions: EMPTY_CONTRIBUTIONS,
    });
    if (!web) {
      live = true;
      return Promise.resolve();
    }
    const url = assetUrl(entry, "entry", retries);
    const work = (async () => {
      if (!url) throw new Error(`${web.entry} is not a file inside web/dist`);
      const source = await deps.fetchText(url);
      if (abandoned) return;
      if (await bundleImportsOwnReact(entry.id, url, source, deps.fetchText))
        throw new Error(`${web.entry} bundles its own React: mark react as external`);
      const module = await deps.importModule(url);
      // A module that arrives after the limit is never set up.
      if (abandoned) return;
      const setup = (module as { default?: unknown } | null)?.default;
      if (typeof setup !== "function") throw new Error(`${web.entry} does not default-export definePlugin(...).`);
      await setup(k);
    })();
    // A failure after the limit has nowhere to go.
    work.catch(() => {});
    let timer: ReturnType<typeof setTimeout> | undefined;
    const limit = new Promise<never>((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`${entry.name} did not finish loading within ${timeoutMs / 1000} s.`)),
        timeoutMs,
      );
    });
    const settled = Promise.race([work, limit])
      .then(
        () => {
          if (!current(entry.id, token)) return;
          live = true;
          const style = deps.addStyle ? assetUrl(entry, "style") : null;
          if (style) unstyle = deps.addStyle!(entry.id, style);
          registry.update(entry.id, (record) => ({ ...record, status: "ready", error: null, k, contributions: staged }));
        },
        (error: unknown) => {
          // Whatever it still does later reaches nothing.
          abandoned = true;
          dispose();
          if (!current(entry.id, token)) return;
          registry.update(entry.id, (record) => ({ ...record, status: "failed", error: message(error) }));
        },
      )
      .finally(() => {
        clearTimeout(timer);
        pending.delete(settled);
      });
    pending.add(settled);
    return settled;
  }

  async function reconcile(): Promise<void> {
    let entries: PluginEntry[];
    try {
      const listed = (await deps.list()).plugins;
      entries = (Array.isArray(listed) ? listed : [])
        .filter((item): item is PluginEntry => Boolean(item) && typeof item.id === "string" && typeof item.name === "string")
        .map((item) => ({ ...item, contributes: item.contributes ?? {}, services: Array.isArray(item.services) ? item.services : [] }));
    } catch (error) {
      setInventory({ entries: inventory.entries, error: message(error) });
      registry.setListed(message(error));
      return;
    }
    setInventory({ entries, error: null });
    const active = entries.filter(isActive);
    const keep = new Set(active.map((entry) => entry.id));
    for (const { id } of registry.getSnapshot().plugins) if (!keep.has(id)) drop(id);
    for (const entry of active) {
      // The same build stays as it is; its row reads the new state (Stopping, Failed to Load ...).
      if (find(entry.id) && attempts.get(entry.id)?.hash === (entry.web?.hash ?? null))
        registry.update(entry.id, (record) => ({ ...record, name: entry.name, entry }));
      else void load(entry, 0);
    }
    registry.setListed(null);
  }

  return {
    /** Reads `GET /plugins` and adds, replaces or removes plugins to match. */
    refresh(): Promise<void> {
      const run = queue.then(reconcile);
      queue = run.catch(() => {});
      return run;
    },
    /** Imports a plugin again, as `?v=<hash>&retry=<n>`. */
    retry(id: string): Promise<void> {
      const record = find(id);
      return record ? load(record.entry, record.retries + 1) : Promise.resolve();
    },
    /** An engine notice (`ctx.notify`) for a loaded plugin. */
    notice(data: unknown) {
      if (!data || typeof data !== "object") return;
      const { plugin_id: id, text, button } = data as Record<string, unknown>;
      if (typeof id !== "string" || !find(id) || typeof text !== "string" || !text.trim() || /[\r\n]/.test(text)) return;
      const action =
        button && typeof button === "object" && typeof (button as { label?: unknown }).label === "string" &&
        isOpenTarget((button as { open?: unknown }).open)
          ? { label: (button as { label: string }).label, open: (button as { open: OpenTarget }).open }
          : undefined;
      const key = nextNoticeKey();
      registry.contribute(id, (current) => ({
        ...current,
        notices: [...current.notices, action ? { key, text, button: action } : { key, text }],
      }));
    },
    /** Resolves once every import in flight has settled. */
    async idle() {
      while (pending.size) await Promise.all([...pending]);
    },
    stop() {
      for (const attempt of attempts.values()) attempt.dispose();
      attempts.clear();
    },
  };
}

export type PluginLoader = ReturnType<typeof createPluginLoader>;

let runtime: PluginLoader | null = null;

/** Retry from a Failed to Load or Failed to Render panel. */
export function retryPlugin(id: string): void {
  void runtime?.retry(id);
}

/** Reads `GET /plugins` again; resolves once the list has been updated. */
export function refreshPlugins(): Promise<void> {
  return runtime?.refresh() ?? Promise.resolve();
}

/**
 * Starts plugins for this page: lists them, loads them, and follows `GET /plugins/events` for
 * `plugins-changed` and engine notices. Returns the stop. PhoneLink's phone app loads no plugin code yet: it
 * cannot `import()` from the computer.
 */
export function startPluginRuntime(api: EngineApi, where: Pick<Location, "pathname" | "search"> = window.location): () => void {
  if (pluginsOff(where.search) || inPhoneApp(where.pathname)) {
    pluginRegistry.setOff();
    return () => {};
  }
  const loader = createPluginLoader(pluginRegistry, {
    list: () => api.json<{ plugins?: unknown }>("/plugins", { cache: "no-store" }),
    importModule: (url) => import(/* @vite-ignore */ url),
    async fetchText(url) {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Could not read ${url.split("?")[0]} (HTTP ${response.status}).`);
      return response.text();
    },
    addStyle(id, href) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      // Not `data-plugin`: that attribute marks only the mount plugin CSS scopes to.
      link.dataset.pluginStyle = id;
      document.head.append(link);
      return () => link.remove();
    },
    api: {
      request: (path, init) => api.request(path, init),
      stream: (path, signal, onEvent, onOpen) => api.streamSse(path, signal, onEvent, onOpen),
    },
  });
  runtime = loader;
  const controller = new AbortController();
  let listed = false;
  const refresh = () => {
    listed = true;
    void loader.refresh();
  };
  void followEvents(
    // The list is read when the stream opens (so no change falls between the two), or once anyway when an
    // engine without the stream refuses it.
    (path, signal, onEvent, onOpen) =>
      api.streamSse(path, signal, onEvent, onOpen).catch((error: unknown) => {
        if (!listed) refresh();
        throw error;
      }),
    "/plugins/events",
    controller.signal,
    (event) => {
      const { topic, data } = (event ?? {}) as { topic?: unknown; data?: unknown };
      if (topic === "plugins-changed") void loader.refresh();
      else if (topic === "notice") loader.notice(data);
    },
    refresh,
  );
  return () => {
    controller.abort();
    loader.stop();
    if (runtime === loader) runtime = null;
  };
}
