import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import type { EngineApi } from "@/api";
import { PluginHost } from "./host";
import { PluginHostContext } from "./components";
import { PluginToolPart } from "./renderers";
import { PluginsSettings } from "./settings";

const entry = { id: "hello", name: "Hello", kind: "installed", enabled: true, state: "ok", shadowed: false,
  web: { entry: "web/dist/index.js", style: null, hash: "a" }, contributes: {} };
function phoneHost(plugin = entry) {
  const host = new PluginHost({ phoneLink: true, disabled: true, storage: { getItem: () => null, setItem: () => {}, removeItem: () => {}, clear: () => {}, key: () => null, length: 0 },
    json: async () => ({ schema: 1, plugins: [plugin] }), request: async () => new Response(new ReadableStream()),
    importer: vi.fn(async () => { throw new Error("must not import"); }), navigate: () => {}, openAgent: () => {}, copy: async () => {}, toast: () => {}, ui: {} });
  return host;
}

describe("PhoneLink plugin UI", () => {
  it("shows the read-only message and inventory, without failed or opt-out text", async () => {
    const host = phoneHost();
    await host.start();
    const html = renderToStaticMarkup(<PluginsSettings host={host} api={{} as EngineApi} canManage={false} />);
    expect(html).toContain("Open and manage plugins on this computer");
    expect(html).toContain("Hello");
    expect(html).not.toContain("Failed to Load");
    expect(html).not.toContain("?plugins=off");
    expect(html).not.toContain("Turn Off");
    host.close();
  });
  it("displays a server error without claiming the web part failed to load or offering Retry", async () => {
    const failed = { ...entry, error: "Server rejected this plugin" };
    const host = phoneHost(failed);
    await host.start();
    const html = renderToStaticMarkup(<PluginsSettings host={host} api={{} as EngineApi} canManage={false} />);
    expect(html).toContain("Server rejected this plugin");
    expect(html).not.toContain("Failed to Load");
    expect(html).not.toContain("Retry");
    host.close();
  });
  it("explains declared uses without implying plugin code is sandboxed", async () => {
    const plugin = { ...entry, uses: ["agents", "agents:unattended", "secret:KEY", "run:python3", "net:example.com", "files:/work", "future:thing"] };
    const host = phoneHost(plugin);
    await host.start();
    const html = renderToStaticMarkup(<PluginsSettings host={host} api={{} as EngineApi} canManage={false} />);
    expect(html).toContain("Through Kollab");
    expect(html).toContain("On Its Own");
    expect(html).toContain("Can start agent runs through Kollab.");
    expect(html).toContain("Can run agent tools without asking");
    expect(html).toContain("Can request the secret KEY through Kollab.");
    expect(html).toContain("Runs: python3");
    expect(html).not.toContain("Runs: python3.");
    expect(html).toContain("Talks to: example.com.");
    expect(html).toContain("Reads or writes: /work.");
    expect(html).toContain("future:thing");
    expect(html).toContain("Plugin code runs as you");
    host.close();
  });
  it("uses the normal renderer fallback", async () => {
    const host = phoneHost();
    await host.start();
    const part = { type: "tool-call", toolCallId: "1", toolName: "build_report", args: {}, argsText: "{}", status: { type: "complete" } } as ToolCallMessagePartProps;
    const html = renderToStaticMarkup(<PluginHostContext.Provider value={host}><PluginToolPart part={part} fallback={<span>Default tool</span>} /></PluginHostContext.Provider>);
    expect(html).toContain("Default tool");
    expect(html).not.toContain("Failed to Load");
    host.close();
  });
});


describe("empty plugin inventory", () => {
  it("tells managers how to add a plugin, without calling built-ins installed", () => {
    const snapshot = { phoneLink: false, disabled: false, loading: false, inventoryError: null,
      entries: [{ id: "mcp", name: "MCP", kind: "built-in", enabled: true, state: "ok", shadowed: false, source: "/Users/example/plugins/mcp.py" }],
      registry: { plugins: [], conflicts: [] }, errors: {}, skipped: [], renderFailed: [], failedRenderers: [] };
    const host = { subscribe: () => () => {}, getSnapshot: () => snapshot, getContext: () => undefined, engineTime: () => null } as unknown as PluginHost;
    const html = renderToStaticMarkup(<PluginsSettings host={host} api={{} as EngineApi} canManage />);
    expect(html).toContain("No plugins yet.");
    expect(html).toContain("Add Plugin");
    expect(html).not.toContain("/Users/example/plugins/mcp.py");
  });
  it("does not claim the inventory is empty when loading it failed", () => {
    const snapshot = { phoneLink: false, disabled: false, loading: false, inventoryError: "Inventory unavailable",
      entries: [], registry: { plugins: [], conflicts: [] }, errors: {}, skipped: [], renderFailed: [], failedRenderers: [] };
    const host = { subscribe: () => () => {}, getSnapshot: () => snapshot } as unknown as PluginHost;
    const html = renderToStaticMarkup(<PluginsSettings host={host} api={{} as EngineApi} canManage />);
    expect(html).toContain("Inventory unavailable");
    expect(html).not.toContain("No plugins yet.");
  });
});
