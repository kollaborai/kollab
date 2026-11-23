/**
 * The web plugin registry: one small store of every loaded plugin and what it adds (rows, views, pages,
 * modals, notices ...). Each contribution point reads it with `usePlugins()`; removing a plugin drops all of
 * its contributions in one change, so a plugin turned off leaves every surface at once, without a reload.
 */
import { useSyncExternalStore } from "react";
import type {
  CommandSpec,
  MenuOptions,
  ModalSpec,
  NoticeButton,
  PageSpec,
  PluginApi,
  PluginEntry,
  RendererSpec,
  SettingsSpec,
  ViewSpec,
} from "./sdk";

export type PluginNotice = { key: number; text: string; button?: NoticeButton };

export type Contributions = {
  /** `k.menu` options by declared row id. */
  menus: Readonly<Record<string, MenuOptions>>;
  views: readonly ViewSpec[];
  pages: readonly PageSpec[];
  modals: readonly ModalSpec[];
  commands: readonly CommandSpec[];
  renderers: readonly RendererSpec[];
  settings: SettingsSpec | null;
  notices: readonly PluginNotice[];
};

export const EMPTY_CONTRIBUTIONS: Contributions = Object.freeze({
  menus: {},
  views: [],
  pages: [],
  modals: [],
  commands: [],
  renderers: [],
  settings: null,
  notices: [],
});

/** `failed`: its code did not load (the row reads Failed to Load). */
export type PluginStatus = "loading" | "ready" | "failed";

export type PluginRecord = {
  id: string;
  name: string;
  entry: PluginEntry;
  status: PluginStatus;
  error: string | null;
  /** How many times Retry ran; the next import URL carries `&retry=<n>`. */
  retries: number;
  /** The plugin's `k`, once its code has loaded. */
  k: PluginApi | null;
  contributions: Contributions;
};

export type PluginsSnapshot = {
  /** `?plugins=off`, or a page that cannot load plugin code. */
  off: boolean;
  /** `GET /plugins` has answered (or failed) at least once. */
  listed: boolean;
  listError: string | null;
  /** Sorted by plugin name. */
  plugins: readonly PluginRecord[];
  /** Every plugin seen on this page, by id, so an open page can still say `PR Chain is off`. */
  names: Readonly<Record<string, string>>;
  modal: { plugin: string; modal: string } | null;
};

const INITIAL: PluginsSnapshot = { off: false, listed: false, listError: null, plugins: [], names: {}, modal: null };

let noticeKeys = 0;
/** A key unique on this page for a plugin notice. */
export const nextNoticeKey = () => ++noticeKeys;

const byName = (a: PluginRecord, b: PluginRecord) =>
  a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

export function createPluginRegistry() {
  let snapshot = INITIAL;
  const listeners = new Set<() => void>();
  const set = (next: PluginsSnapshot) => {
    snapshot = next;
    for (const listener of [...listeners]) listener();
  };
  const has = (id: string) => snapshot.plugins.some((record) => record.id === id);
  const update = (id: string, change: (record: PluginRecord) => PluginRecord) => {
    if (!has(id)) return;
    const plugins = snapshot.plugins.map((record) => (record.id === id ? change(record) : record)).sort(byName);
    const record = plugins.find((item) => item.id === id)!;
    set({ ...snapshot, plugins, names: { ...snapshot.names, [id]: record.name } });
  };
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => snapshot,
    setOff() {
      set({ ...snapshot, off: true });
    },
    setListed(error: string | null) {
      set({ ...snapshot, listed: true, listError: error });
    },
    /** Adds a plugin or replaces the one with its id. */
    put(record: PluginRecord) {
      const plugins = [...snapshot.plugins.filter((item) => item.id !== record.id), record].sort(byName);
      set({ ...snapshot, plugins, names: { ...snapshot.names, [record.id]: record.name } });
    },
    update,
    contribute(id: string, change: (current: Contributions) => Contributions) {
      update(id, (record) => ({ ...record, contributions: change(record.contributions) }));
    },
    /** Drops a plugin and everything it added. */
    remove(id: string) {
      if (!has(id)) return;
      set({
        ...snapshot,
        plugins: snapshot.plugins.filter((record) => record.id !== id),
        modal: snapshot.modal?.plugin === id ? null : snapshot.modal,
      });
    },
    openModal(plugin: string, modal: string) {
      if (has(plugin)) set({ ...snapshot, modal: { plugin, modal } });
    },
    closeModal() {
      if (snapshot.modal) set({ ...snapshot, modal: null });
    },
    /** Back to an empty page (tests). */
    reset() {
      set(INITIAL);
    },
  };
}

export type PluginRegistry = ReturnType<typeof createPluginRegistry>;

/** The page's registry. */
export const pluginRegistry = createPluginRegistry();

export function usePlugins(registry: PluginRegistry = pluginRegistry): PluginsSnapshot {
  return useSyncExternalStore(registry.subscribe, registry.getSnapshot, registry.getSnapshot);
}

/** A plugin view's key among the chat's views (`SessionView`): `plugin:<plugin id>:<view id>`. */
export type PluginViewKey = `plugin:${string}`;

export function pluginViewKey(pluginId: string, viewId: string): PluginViewKey {
  return `plugin:${pluginId}:${viewId}`;
}

export function parsePluginViewKey(key: string): { pluginId: string; viewId: string } | null {
  const match = /^plugin:([^:]+):(.+)$/.exec(key);
  return match ? { pluginId: match[1], viewId: match[2] } : null;
}
