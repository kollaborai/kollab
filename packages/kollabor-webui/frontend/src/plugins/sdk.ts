import { useCallback, useEffect, useState, type ComponentType } from "react";
import type { Session } from "../api";
import type { PluginDraft } from "./registry";

export type PluginOpen = { page: string } | { modal: string } | { view: string } | { url: string };
export type PluginRenderProps = { k: PluginContext; session?: Session };
export type PluginRender = ComponentType<PluginRenderProps>;
export type PluginTab = { id: string; label: string; render: PluginRender };
export type PluginMenu = { id: string; count?: { path: string; refreshOn?: string } };
export type PluginView = { id: string; label: string; icon?: string; render: PluginRender; needs_folder?: boolean };
export type PluginPage = { id: string; title: string; tabs: PluginTab[] };
export type PluginModal = PluginPage;
export type PluginSettings = { render: PluginRender };
export type PluginCommand = { name: string; description: string; run: (args: string) => unknown };
export type PluginRenderer = { tool?: string; tag?: string; render: PluginRender };
export type PluginNotice = { text: string; button?: { label: string; open: PluginOpen } };
export type PluginApiState<T> = { data: T | null; error: string | null; loading: boolean; refresh: () => void };

export interface PluginContributions {
  menu(id: string, options?: Omit<PluginMenu, "id">): void;
  view(view: PluginView): void;
  page(page: PluginPage): void;
  modal(modal: PluginModal): void;
  settings: ((settings: PluginSettings) => void) & {
    get(): Promise<Record<string, unknown>>;
    save(values: Record<string, unknown>): Promise<unknown>;
  };
  command(command: PluginCommand): void;
  renderer(renderer: PluginRenderer): void;
  notice(notice: PluginNotice): void;
}

export interface PluginContext extends PluginContributions {
  api: {
    get<T = unknown>(path: string): Promise<T>;
    post<T = unknown>(path: string, body?: unknown): Promise<T>;
    put<T = unknown>(path: string, body?: unknown): Promise<T>;
    delete<T = unknown>(path: string): Promise<T>;
  };
  useApi<T = unknown>(path: string, options?: { refreshOn?: string; everyMs?: number }): PluginApiState<T>;
  events: { on(topic: string, callback: (data: unknown) => void): () => void };
  agents: { open(sessionId: string): void };
  navigate(target: PluginOpen): void;
  copy(text: string): Promise<void>;
  toast(text: string): void;
  ui: Record<string, unknown>;
}

/** Type-check a plugin's activation while leaving the function available as the default export. */
export function definePlugin(activate: (k: PluginContext) => void | Promise<void>) {
  return activate;
}

export interface PluginContextOptions {
  id: string;
  draft: PluginDraft;
  json: (path: string, init?: RequestInit) => Promise<unknown>;
  events: { on(topic: string, callback: (data: unknown) => void): () => void };
  navigate: (target: PluginOpen) => void;
  openAgent: (sessionId: string) => void;
  copy: (text: string) => Promise<void>;
  toast: (text: string) => void;
  ui: Record<string, unknown>;
}

function scopedApiPath(id: string, path: string): string {
  if (!path || path.startsWith("//") || path.includes("\\") || path.includes("#") || /^[a-z][a-z\d+.-]*:/i.test(path)) {
    throw new Error("Invalid plugin API path");
  }
  const [pathname] = path.split("?", 1);
  const parts = pathname.replace(/^\//, "").split("/");
  try {
    if (parts.some(part => {
      const decoded = decodeURIComponent(part);
      return !decoded || decoded === "." || decoded === ".." || decoded.includes("/") || decoded.includes("\\");
    })) throw new Error("Invalid plugin API path");
  } catch {
    throw new Error("Invalid plugin API path");
  }
  return `/plugins/${encodeURIComponent(id)}/api/${path.replace(/^\//, "")}`;
}

/** Create the per-plugin SDK. The host must dispose it when the plugin is removed. */
export function createPluginContext(options: PluginContextOptions): PluginContext & { dispose(): void } {
  const { id, draft, json } = options;
  const prefix = `/plugins/${encodeURIComponent(id)}`;
  const disposers = new Set<() => void>();
  let disposed = false;
  const on = (topic: string, callback: (data: unknown) => void) => {
    if (disposed) return () => {};
    const off = options.events.on(topic, callback);
    let active = true;
    const unsubscribe = () => {
      if (!active) return;
      active = false;
      disposers.delete(unsubscribe);
      off();
    };
    disposers.add(unsubscribe);
    return unsubscribe;
  };
  const request = async <T,>(path: string, init?: RequestInit): Promise<T> =>
    await json(scopedApiPath(id, path), init) as T;
  const api: PluginContext["api"] = {
    get: <T,>(path: string) => request<T>(path),
    post: <T,>(path: string, body?: unknown) => request<T>(path, { method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) }) }),
    put: <T,>(path: string, body?: unknown) => request<T>(path, { method: "PUT", ...(body === undefined ? {} : { body: JSON.stringify(body) }) }),
    delete: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
  };
  const settings = Object.assign((value: PluginSettings) => draft.settings(value), {
    get: async () => await json(`${prefix}/settings`, undefined) as Record<string, unknown>,
    save: async (values: Record<string, unknown>) => await json(`${prefix}/settings`, { method: "PUT", body: JSON.stringify(values) }),
  });
  function useApi<T>(path: string, config: { refreshOn?: string; everyMs?: number } = {}): PluginApiState<T> {
    const [state, setState] = useState<Omit<PluginApiState<T>, "refresh">>({ data: null, error: null, loading: true });
    const [revision, setRevision] = useState(0);
    const refresh = useCallback(() => setRevision(value => value + 1), []);
    useEffect(() => {
      const controller = new AbortController();
      setState(previous => ({ ...previous, loading: true, error: null }));
      void request<T>(path, { signal: controller.signal }).then(
        data => { if (!controller.signal.aborted) setState({ data, error: null, loading: false }); },
        error => { if (!controller.signal.aborted) setState(previous => ({ ...previous, error: error instanceof Error ? error.message : String(error), loading: false })); },
      );
      return () => controller.abort();
    }, [path, revision]);
    useEffect(() => config.refreshOn ? on(config.refreshOn, refresh) : undefined, [config.refreshOn, refresh]);
    useEffect(() => {
      if (!config.everyMs || config.everyMs <= 0) return;
      const timer = setInterval(refresh, config.everyMs);
      return () => clearInterval(timer);
    }, [config.everyMs, refresh]);
    return { ...state, refresh };
  }
  return {
    menu: draft.menu.bind(draft), view: draft.view.bind(draft), page: draft.page.bind(draft),
    modal: draft.modal.bind(draft), settings, command: draft.command.bind(draft),
    renderer: draft.renderer.bind(draft), notice: draft.notice.bind(draft),
    api, useApi, events: { on }, agents: { open: options.openAgent },
    navigate: options.navigate, copy: options.copy, toast: options.toast, ui: options.ui,
    dispose: () => { disposed = true; for (const dispose of [...disposers]) dispose(); },
  };
}
