import type { ComponentType } from "react";
import type { Session } from "../api";

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
