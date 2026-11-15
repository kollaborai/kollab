import { describe, expect, it, vi } from "vitest";
import { PluginRegistry } from "./registry";

const component = () => null;

describe("plugin contribution registry", () => {
  it("publishes all of a plugin's staged contributions together and removes them together", () => {
    const registry = new PluginRegistry();
    const notify = vi.fn();
    registry.subscribe(notify);
    const draft = registry.stage("example");
    draft.page({ id: "board", title: "Board", tabs: [{ id: "one", label: "One", render: component }] });
    draft.view({ id: "board", label: "Board", render: component });
    draft.notice({ text: "Hello" });
    expect(registry.getSnapshot().plugins).toEqual([]);
    registry.commit(draft);
    const snapshot = registry.getSnapshot();
    expect(snapshot.plugins[0].id).toBe("example");
    expect(snapshot.plugins[0].pages[0].id).toBe("board");
    expect(snapshot.plugins[0].views[0].id).toBe("board");
    expect(snapshot.plugins[0].notices[0].text).toBe("Hello");
    expect(notify).toHaveBeenCalledTimes(1);
    expect(registry.getSnapshot()).toBe(snapshot);
    registry.remove("example");
    expect(registry.getSnapshot().plugins).toEqual([]);
    expect(notify).toHaveBeenCalledTimes(2);
  });

  it("keeps built-in and earlier plugin commands and reports the losing plugin", () => {
    const registry = new PluginRegistry(["help"]);
    const first = registry.stage("first");
    first.command({ name: "deploy", description: "Deploy", run: () => {} });
    registry.commit(first);
    const second = registry.stage("second");
    second.command({ name: "deploy", description: "Other", run: () => {} });
    second.command({ name: "help", description: "Builtin", run: () => {} });
    registry.commit(second);
    const { plugins, conflicts } = registry.getSnapshot();
    expect(plugins.find(p => p.id === "first")?.commands.map(c => c.name)).toEqual(["deploy"]);
    expect(plugins.find(p => p.id === "second")?.commands).toEqual([]);
    expect(conflicts).toEqual([
      { pluginId: "second", command: "deploy", owner: "first" },
      { pluginId: "second", command: "help", owner: "built-in" },
    ]);
  });

  it("replaces a plugin atomically and releases its old command", () => {
    const registry = new PluginRegistry();
    const first = registry.stage("example");
    first.command({ name: "old", description: "Old", run: () => {} });
    registry.commit(first);
    const replacement = registry.stage("example");
    replacement.command({ name: "new", description: "New", run: () => {} });
    registry.commit(replacement);
    const other = registry.stage("other");
    other.command({ name: "old", description: "Old", run: () => {} });
    registry.commit(other);
    expect(registry.getSnapshot().plugins.map(p => p.commands.map(c => c.name))).toEqual([["new"], ["old"]]);
    expect(registry.getSnapshot().conflicts).toEqual([]);
  });
});
