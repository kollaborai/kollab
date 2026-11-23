import { describe, expect, it } from "vitest";
import {
  createPluginRegistry,
  EMPTY_CONTRIBUTIONS,
  parsePluginViewKey,
  pluginViewKey,
  type PluginRecord,
} from "./registry";
import type { PluginEntry } from "./sdk";

const entry = (id: string, name: string): PluginEntry => ({
  id,
  name,
  kind: "installed",
  enabled: true,
  shadowed: false,
  state: "ok",
  error: null,
  web: null,
  contributes: {},
  services: [],
});

const record = (id: string, name: string, extra: Partial<PluginRecord> = {}): PluginRecord => ({
  id,
  name,
  entry: entry(id, name),
  status: "ready",
  error: null,
  retries: 0,
  k: null,
  contributions: EMPTY_CONTRIBUTIONS,
  ...extra,
});

const Tab = () => null;

describe("plugin registry", () => {
  it("keeps one snapshot object between changes and notifies subscribers on each change", () => {
    const registry = createPluginRegistry();
    const first = registry.getSnapshot();
    expect(registry.getSnapshot()).toBe(first);
    let calls = 0;
    const stop = registry.subscribe(() => calls++);
    registry.put(record("hello", "Hello"));
    expect(calls).toBe(1);
    const second = registry.getSnapshot();
    expect(second).not.toBe(first);
    expect(registry.getSnapshot()).toBe(second);
    stop();
    registry.put(record("other", "Other"));
    expect(calls).toBe(1);
  });

  it("starts with no plugins, not listed, and plugins on", () => {
    const snapshot = createPluginRegistry().getSnapshot();
    expect(snapshot).toMatchObject({ off: false, listed: false, listError: null, plugins: [], modal: null });
  });

  it("sorts plugins by name, then id", () => {
    const registry = createPluginRegistry();
    registry.put(record("zeta", "PR Chain"));
    registry.put(record("alpha", "Zoo"));
    registry.put(record("beta", "Hello"));
    expect(registry.getSnapshot().plugins.map((item) => item.id)).toEqual(["beta", "zeta", "alpha"]);
  });

  it("removing a plugin drops all its rows, tabs, pages, modals and notices at once", () => {
    const registry = createPluginRegistry();
    registry.put(
      record("hello", "Hello", {
        contributions: {
          ...EMPTY_CONTRIBUTIONS,
          menus: { hello: {} },
          pages: [{ id: "main", title: "Hello", tabs: [{ id: "one", label: "One", render: Tab }] }],
          views: [{ id: "peek", label: "Peek", render: Tab }],
          notices: [{ key: 1, text: "Hi" }],
        },
      }),
    );
    registry.put(record("other", "Other"));
    registry.openModal("hello", "about");
    registry.remove("hello");
    const snapshot = registry.getSnapshot();
    expect(snapshot.plugins.map((item) => item.id)).toEqual(["other"]);
    expect(snapshot.modal).toBeNull();
    // The name stays known so an open page can say "Hello is off".
    expect(snapshot.names.hello).toBe("Hello");
  });

  it("contribute changes only the named plugin and ignores an unknown id", () => {
    const registry = createPluginRegistry();
    registry.put(record("hello", "Hello"));
    registry.put(record("other", "Other"));
    registry.contribute("hello", (current) => ({ ...current, notices: [{ key: 7, text: "Saved" }] }));
    registry.contribute("missing", (current) => ({ ...current, notices: [{ key: 8, text: "Lost" }] }));
    const [hello, other] = registry.getSnapshot().plugins;
    expect(hello.contributions.notices).toEqual([{ key: 7, text: "Saved" }]);
    expect(other.contributions.notices).toEqual([]);
  });

  it("records the plugins=off switch and the listing outcome", () => {
    const registry = createPluginRegistry();
    registry.setListed("Kollab Engine is unavailable");
    expect(registry.getSnapshot()).toMatchObject({ listed: true, listError: "Kollab Engine is unavailable" });
    registry.setListed(null);
    expect(registry.getSnapshot().listError).toBeNull();
    registry.setOff();
    expect(registry.getSnapshot().off).toBe(true);
  });

  it("a modal for a plugin that is not loaded is not opened", () => {
    const registry = createPluginRegistry();
    registry.openModal("missing", "about");
    expect(registry.getSnapshot().modal).toBeNull();
    registry.put(record("hello", "Hello"));
    registry.openModal("hello", "about");
    expect(registry.getSnapshot().modal).toEqual({ plugin: "hello", modal: "about" });
    registry.closeModal();
    expect(registry.getSnapshot().modal).toBeNull();
  });
});

describe("plugin view keys", () => {
  it("round-trip a plugin id (including @dev) and a view id", () => {
    expect(pluginViewKey("pr-chain", "board")).toBe("plugin:pr-chain:board");
    expect(parsePluginViewKey("plugin:pr-chain:board")).toEqual({ pluginId: "pr-chain", viewId: "board" });
    expect(parsePluginViewKey("plugin:hello@dev:peek")).toEqual({ pluginId: "hello@dev", viewId: "peek" });
  });

  it("are never confused with the built-in views", () => {
    expect(parsePluginViewKey("chat")).toBeNull();
    expect(parsePluginViewKey("plugin:")).toBeNull();
    expect(parsePluginViewKey("plugin:hello")).toBeNull();
  });
});
