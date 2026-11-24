/**
 * The web plugin API, `@kollab/plugin` (docs/specs/plugins.md, "Web plugin API").
 *
 * A plugin's `web/dist/index.js` default-exports `definePlugin((k) => { ... })`. Kollab imports it, calls the
 * function with `k` and shows what it registered once the function returns (loader.ts). Every contribution binds
 * to an id the plugin's manifest declares under `contributes`; an undeclared id is ignored with a warning, so the
 * rows kollab draws from the manifest and the code that fills them always agree. Plugin text is drawn as text,
 * never as HTML, and plugin controls are the app's own components (`k.ui`).
 */
import { useCallback, useEffect, useState, type ComponentType } from "react";
import type { PanelField } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { HoverHelp } from "@/components/ui/hover-help";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { FieldRenderer } from "@/components/panels/FieldRenderer";
import { GemAvatar } from "@/components/gems/GemAvatar";
import { copyText } from "@/phonelink/copy-link";
import { nextNoticeKey, type Contributions } from "./registry";

/** Where a menu row, a notice button or `k.navigate` goes. */
export type OpenTarget = { page: string } | { modal: string } | { view: string } | { url: string };

/** A right-hand menu row as the manifest declares it (`contributes.menu`). */
export type DeclaredRow = { id: string; label: string; icon?: string; open: OpenTarget };

/** A declared view, page, modal, slash command or renderer: its id (or tool or tag), alone or in an object. */
export type Declared = string | { id?: string; name?: string; tool?: string; tag?: string; label?: string; title?: string };

export type PluginService = {
  name: string;
  state: "starting" | "running" | "backoff" | "stopping" | "stop-failed" | "exited" | "stopped" | "crash-stopped" | "error" | "unknown" | "failed";
  desired?: "running" | "stopped";
  pid?: number | null;
  since?: number | null;
  restarts?: number;
  secrets?: string[];
  heartbeat_s?: number | null;
  heartbeat?: number | null;
  heartbeat_monitor_since?: number | null;
  stop_deadline?: number | null;
  error?: string | null;
};

/** One row of `GET /plugins` (`kollabor_plugins.registry.plugin_entries()`). */
export type PluginEntry = {
  id: string;
  name: string;
  version?: string | null;
  kind: "built-in" | "installed" | "dev";
  source?: string | null;
  commit?: string | null;
  enabled: boolean;
  enable_order?: number | null;
  shadowed: boolean;
  state: "ok" | "error" | "off" | "stopping" | "stop-failed" | "changed-on-disk" | "skipped";
  error: string | null;
  web: { entry: string; style?: string | null; hash: string } | null;
  contributes: {
    menu?: DeclaredRow[];
    views?: Declared[];
    pages?: Declared[];
    modals?: Declared[];
    slash?: Declared[];
    renderers?: Declared[];
  };
  services: PluginService[];
  settings?: PanelField[];
  settings_section?: { title?: string } | null;
  uses?: string[];
  granted?: string[];
};

/** `GET /plugins` response. `server_time` is Unix time in seconds. */
export type PluginInventoryResponse = {
  plugins?: unknown;
  server_time?: unknown;
};

/** The open chat, as a plugin view receives it. */
export type PluginSession = { session_id: string; workspace: string; name?: string; identity?: string };

/** What every plugin `render` component receives (`session` only inside a view). */
export type PluginRenderProps = { k: PluginApi; session?: PluginSession };
export type PluginComponent = ComponentType<PluginRenderProps>;
export type PluginTab = { id: string; label: string; render: PluginComponent };

export type CountSource = { path: string; refreshOn?: string; everyMs?: number };
export type MenuOptions = { count?: CountSource };
export type ViewSpec = {
  id: string;
  label: string;
  icon?: string | ComponentType<{ className?: string }>;
  render: PluginComponent;
  needs_folder?: boolean;
};
export type PageSpec = { id: string; title: string; tabs: PluginTab[] };
export type ModalSpec = { id: string; title: string; tabs: PluginTab[] };
export type SettingsSpec = { render: PluginComponent };
export type CommandSpec = { name: string; description: string; run: (args: string) => unknown };
export type RendererSpec = { tool?: string; tag?: string; render: ComponentType<{ k: PluginApi; data: unknown }> };
export type NoticeButton = { label: string; open?: OpenTarget; run?: () => unknown };
export type NoticeSpec = { text: string; button?: NoticeButton };
export type ApiOptions = { refreshOn?: string; everyMs?: number };
export type ApiState<T> = { data: T | undefined; error: string | null; loading: boolean; refresh: () => void };

/** The app's own components, so a plugin looks like kollab. */
export const ui = {
  Button,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  Switch,
  Input,
  Label,
  Skeleton,
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  HoverHelp,
  ScrollArea,
  FieldRenderer,
  GemAvatar,
};

/** `k`: a plugin's handle on the app. */
export type PluginApi = {
  readonly id: string;
  /** A live count on a declared right-hand menu row; `count.path` answers `{text, hint}`. */
  menu(id: string, options?: MenuOptions): void;
  /** A main-area tab beside Chat and Trajectory for the open chat. */
  view(spec: ViewSpec): void;
  /** The whole main area with the plugin's own tab bar. */
  page(spec: PageSpec): void;
  /** A dialog with tabs, the shape of Settings. */
  modal(spec: ModalSpec): void;
  /** Its section in Settings → Plugins; `get` and `save` read and write the same store that page edits. */
  settings: ((spec: SettingsSpec) => void) & {
    get(): Promise<unknown>;
    save(values: Record<string, unknown>): Promise<unknown>;
  };
  /** A composer slash command. */
  command(spec: CommandSpec): void;
  /** A chat card for a tool or a message tag it owns. */
  renderer(spec: RendererSpec): void;
  /** One NoticeBar line with at most one button; returns its dismissal. */
  notice(spec: NoticeSpec): () => void;
  /** Its own engine routes under `/plugins/{id}/api/`, with the page's auth, through `/engine` when remote. */
  api: {
    get<T = unknown>(path: string): Promise<T>;
    post<T = unknown>(path: string, body?: unknown): Promise<T>;
    put<T = unknown>(path: string, body?: unknown): Promise<T>;
    delete<T = unknown>(path: string): Promise<T>;
  };
  /** A React hook that reads one of its routes and reads it again on an event topic or a timer. */
  useApi<T = unknown>(path: string, options?: ApiOptions): ApiState<T>;
  /** Its own event stream (`GET /plugins/{id}/events`); returns the unsubscribe. */
  events: { on(topic: string, listener: (data: unknown) => void): () => void };
  agents: { open(sessionId: string): void };
  navigate(target: OpenTarget): void;
  copy(text: string): Promise<boolean>;
  /** A notice that leaves by itself after a few seconds. */
  toast(text: string): void;
  ui: typeof ui;
};

export type PluginSetup = (k: PluginApi) => void | Promise<void>;

/** What `web/dist/index.js` default-exports. */
export function definePlugin(setup: PluginSetup): PluginSetup {
  return setup;
}

/** What the loader gives each plugin's `k`. */
export type PluginApiDeps = {
  /** The app's authenticated engine request (`EngineApi.request`). */
  request(path: string, init?: RequestInit): Promise<Response>;
  /** The app's SSE reader (`EngineApi.streamSse`); resolves when the stream ends. */
  stream(path: string, signal: AbortSignal, onEvent: (event: unknown) => void, onOpen?: () => void): Promise<void>;
  /** Applies a change to this plugin's contributions (held back until its function returns). */
  contribute(change: (current: Contributions) => Contributions): void;
  open(target: OpenTarget): void;
  openSession(sessionId: string): void;
};

const TOAST_MS = 5_000;

const isText = (value: unknown): value is string => typeof value === "string" && value.trim() !== "";
const isLine = (value: unknown): value is string => isText(value) && !/[\r\n]/.test(value);
const isComponent = (value: unknown) =>
  typeof value === "function" || (typeof value === "object" && value !== null && "$$typeof" in value);
const isTabs = (tabs: unknown): tabs is PluginTab[] =>
  Array.isArray(tabs) &&
  tabs.length > 0 &&
  tabs.every((tab) => tab && isText(tab.id) && isLine(tab.label) && isComponent(tab.render));

export function isOpenTarget(value: unknown): value is OpenTarget {
  if (!value || typeof value !== "object") return false;
  const keys = Object.keys(value);
  return (
    keys.length === 1 &&
    ["page", "modal", "view", "url"].includes(keys[0]) &&
    isText((value as Record<string, unknown>)[keys[0]])
  );
}

function declaredIds(list: readonly unknown[] | undefined): Set<string> {
  const ids = new Set<string>();
  for (const item of list ?? []) {
    if (typeof item === "string") ids.add(item);
    else if (item && typeof item === "object")
      for (const key of ["id", "name", "tool", "tag"] as const) {
        const value = (item as Record<string, unknown>)[key];
        if (typeof value === "string") ids.add(value);
      }
  }
  return ids;
}

/** Waits `ms`, or less when `signal` aborts. */
function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

/**
 * Follows an engine SSE stream until `signal` aborts: a stream that ends or fails (engine restarted, idle
 * timeout) is opened again after 1 s, 2 s, 4 s ... up to 30 s; `onOpen` runs on every (re)open.
 */
export async function followEvents(
  stream: PluginApiDeps["stream"],
  path: string,
  signal: AbortSignal,
  onEvent: (event: unknown) => void,
  onOpen?: () => void,
): Promise<void> {
  let delay = 1_000;
  while (!signal.aborted) {
    try {
      await stream(path, signal, onEvent, () => {
        delay = 1_000;
        onOpen?.();
      });
    } catch {
      // Closed, refused or engine down: try again below.
    }
    if (signal.aborted) return;
    await pause(delay, signal);
    delay = Math.min(delay * 2, 30_000);
  }
}

/** What a remote read holds, for the plugin and path (`key`) it was read for. */
export type RemoteState<T> = { key: string | null; data: T | undefined; error: string | null; loading: boolean };

/** The read as shown for `key` (null: nothing to read): never data read for another plugin or path. */
export function remoteView<T>(state: RemoteState<T>, key: string | null): Omit<ApiState<T>, "refresh"> {
  if (key === null) return { data: undefined, error: null, loading: false };
  if (state.key !== key) return { data: undefined, error: null, loading: true };
  return { data: state.data, error: state.error, loading: state.loading };
}

/**
 * Reads `path` from a plugin's routes and again on `refreshOn` events or every `everyMs`. `k` or `path` null
 * reads nothing (a menu row whose plugin has no count). Used by `k.useApi` and the menu row counts.
 */
export function useRemote<T>(k: PluginApi | null, path: string | null | undefined, options: ApiOptions = {}): ApiState<T> {
  const key = k && path ? `${k.id}|${path}` : null;
  const [state, setState] = useState<RemoteState<T>>({ key: null, data: undefined, error: null, loading: false });
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((value) => value + 1), []);
  useEffect(() => {
    if (!k || !path || !key) return;
    let live = true;
    setState((current) => (current.key === key ? { ...current, loading: true } : { key, data: undefined, error: null, loading: true }));
    k.api.get<T>(path).then(
      (data) => {
        if (live) setState({ key, data, error: null, loading: false });
      },
      (error: unknown) => {
        if (live)
          setState((current) => ({
            key,
            data: current.key === key ? current.data : undefined,
            error: error instanceof Error ? error.message : String(error),
            loading: false,
          }));
      },
    );
    return () => {
      live = false;
    };
  }, [k, path, key, tick]);
  const { refreshOn, everyMs } = options;
  useEffect(() => (k && path && refreshOn ? k.events.on(refreshOn, refresh) : undefined), [k, path, refreshOn, refresh]);
  useEffect(() => {
    if (!k || !path || !everyMs || everyMs <= 0) return;
    const timer = setInterval(refresh, Math.max(1_000, everyMs));
    return () => clearInterval(timer);
  }, [k, path, everyMs, refresh]);
  return { ...remoteView(state, key), refresh };
}

/** Builds one plugin's `k`. `dispose` closes its event stream and turns every later call into a no-op. */
export function createPluginApi(entry: PluginEntry, deps: PluginApiDeps): { k: PluginApi; dispose: () => void } {
  const id = entry.id;
  const declared = entry.contributes ?? {};
  let disposed = false;
  const contribute = (change: (current: Contributions) => Contributions) => {
    if (!disposed) deps.contribute(change);
  };
  const bound = (kind: string, ids: Set<string>, name: unknown): name is string => {
    if (typeof name === "string" && ids.has(name)) return true;
    console.warn(`${id} did not declare ${kind} ${String(name)}; ignored. Declare it under contributes in kollab-plugin.json.`);
    return false;
  };
  const invalid = (kind: string, why: string) => {
    console.warn(`${id}: ${kind} ignored: ${why}`);
  };

  const routePath = (path: unknown): string => {
    const clean = String(path ?? "").replace(/^\/+/, "");
    const segments = clean.split(/[?#]/, 1)[0].replace(/%2e/gi, ".").split(/[/\\]/);
    if (segments.some((segment) => segment === "." || segment === ".."))
      throw new Error(`${String(path)}: a k.api path stays under /plugins/${id}/api/`);
    return `/plugins/${id}/api/${clean}`;
  };
  const read = async (response: Response) =>
    (response.headers.get("content-type") ?? "").includes("json") ? response.json() : response.text();
  // A plugin turned off, failed or replaced keeps no access through its old `k`.
  const live = () => {
    if (disposed) throw new Error(`${id} no longer runs on this page`);
  };
  const call = async (method: string, path: string, body?: unknown) => {
    live();
    return read(await deps.request(routePath(path), body === undefined ? { method } : { method, body: JSON.stringify(body) }));
  };

  const listeners = new Map<string, Set<(data: unknown) => void>>();
  let events: AbortController | null = null;
  const dispatch = (event: unknown) => {
    if (!event || typeof event !== "object") return;
    const { topic, data } = event as { topic?: unknown; data?: unknown };
    if (typeof topic !== "string") return;
    for (const listener of [...(listeners.get(topic) ?? [])]) {
      try {
        listener(data);
      } catch (error) {
        console.error(`${id}: event listener for ${topic} failed`, error);
      }
    }
  };

  const notice = (spec: NoticeSpec): (() => void) => {
    const button = spec?.button;
    if (!isLine(spec?.text)) {
      invalid("notice", "text must be one line");
      return () => {};
    }
    if (button !== undefined && !(isLine(button?.label) && (isOpenTarget(button.open) || typeof button.run === "function"))) {
      invalid("notice", "a button needs a label and open or run");
      return () => {};
    }
    const key = nextNoticeKey();
    const text = spec.text;
    contribute((current) => ({
      ...current,
      notices: [...current.notices, button ? { key, text, button } : { key, text }],
    }));
    return () => contribute((current) => ({ ...current, notices: current.notices.filter((item) => item.key !== key) }));
  };

  const settings = Object.assign(
    (spec: SettingsSpec) => {
      if (!isComponent(spec?.render)) return invalid("settings", "render must be a component");
      contribute((current) => ({ ...current, settings: { render: spec.render } }));
    },
    {
      get: async () => {
        live();
        return read(await deps.request(`/plugins/${id}/settings`, { method: "GET" }));
      },
      save: async (values: Record<string, unknown>) => {
        live();
        return read(await deps.request(`/plugins/${id}/settings`, { method: "PUT", body: JSON.stringify(values) }));
      },
    },
  );

  const k: PluginApi = {
    id,
    menu(rowId, options = {}) {
      if (!bound("menu row", declaredIds(declared.menu), rowId)) return;
      const count = options?.count;
      if (count !== undefined && !isText(count?.path)) return invalid("menu", "count.path must be a route path");
      contribute((current) => ({
        ...current,
        menus: {
          ...current.menus,
          [rowId]: count ? { count: { path: count.path, refreshOn: count.refreshOn, everyMs: count.everyMs } } : {},
        },
      }));
    },
    view(spec) {
      if (!bound("view", declaredIds(declared.views), spec?.id)) return;
      if (!isLine(spec.label) || !isComponent(spec.render)) return invalid("view", "it needs a label and a render component");
      const view: ViewSpec = { id: spec.id, label: spec.label, icon: spec.icon, render: spec.render, needs_folder: Boolean(spec.needs_folder) };
      contribute((current) => ({ ...current, views: [...current.views.filter((item) => item.id !== view.id), view] }));
    },
    page(spec) {
      if (!bound("page", declaredIds(declared.pages), spec?.id)) return;
      if (!isLine(spec.title) || !isTabs(spec.tabs)) return invalid("page", "it needs a title and at least one tab");
      const page: PageSpec = { id: spec.id, title: spec.title, tabs: [...spec.tabs] };
      contribute((current) => ({ ...current, pages: [...current.pages.filter((item) => item.id !== page.id), page] }));
    },
    modal(spec) {
      if (!bound("modal", declaredIds(declared.modals), spec?.id)) return;
      if (!isLine(spec.title) || !isTabs(spec.tabs)) return invalid("modal", "it needs a title and at least one tab");
      const modal: ModalSpec = { id: spec.id, title: spec.title, tabs: [...spec.tabs] };
      contribute((current) => ({ ...current, modals: [...current.modals.filter((item) => item.id !== modal.id), modal] }));
    },
    settings,
    command(spec) {
      if (!bound("slash command", declaredIds(declared.slash), spec?.name)) return;
      if (typeof spec.run !== "function") return invalid("command", "run must be a function");
      const command: CommandSpec = { name: spec.name, description: String(spec.description ?? ""), run: spec.run };
      contribute((current) => ({
        ...current,
        commands: [...current.commands.filter((item) => item.name !== command.name), command],
      }));
    },
    renderer(spec) {
      const name = spec?.tool ?? spec?.tag;
      if (!bound("renderer", declaredIds(declared.renderers), name)) return;
      if (!isComponent(spec.render)) return invalid("renderer", "render must be a component");
      const renderer: RendererSpec = spec.tool ? { tool: spec.tool, render: spec.render } : { tag: spec.tag, render: spec.render };
      contribute((current) => ({
        ...current,
        renderers: [...current.renderers.filter((item) => (item.tool ?? item.tag) !== name), renderer],
      }));
    },
    notice,
    api: {
      get: (path) => call("GET", path),
      post: (path, body) => call("POST", path, body),
      put: (path, body) => call("PUT", path, body),
      delete: (path) => call("DELETE", path),
    },
    useApi: (path, options) => useRemote(k, path, options),
    events: {
      on(topic, listener) {
        if (disposed) return () => {};
        const set = listeners.get(topic) ?? new Set();
        set.add(listener);
        listeners.set(topic, set);
        if (!events) {
          events = new AbortController();
          void followEvents(deps.stream, `/plugins/${id}/events`, events.signal, dispatch);
        }
        return () => {
          set.delete(listener);
          if (!set.size) listeners.delete(topic);
          if (!listeners.size && events) {
            events.abort();
            events = null;
          }
        };
      },
    },
    agents: {
      open: (sessionId) => {
        if (!disposed) deps.openSession(String(sessionId));
      },
    },
    navigate(target) {
      if (disposed) return;
      if (isOpenTarget(target)) deps.open(target);
      else invalid("navigate", "the target is one of {page}, {modal}, {view} or {url}");
    },
    copy: async (text) => (disposed ? false : copyText(globalThis.navigator?.clipboard, String(text))),
    toast(text) {
      const dismiss = notice({ text });
      setTimeout(dismiss, TOAST_MS);
    },
    ui,
  };

  return {
    k,
    dispose() {
      disposed = true;
      events?.abort();
      events = null;
      listeners.clear();
    },
  };
}
