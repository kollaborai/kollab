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
function phoneHost() {
  const host = new PluginHost({ phoneLink: true, disabled: true, storage: { getItem: () => null, setItem: () => {}, removeItem: () => {}, clear: () => {}, key: () => null, length: 0 },
    json: async () => ({ schema: 1, plugins: [entry] }), request: async () => new Response(new ReadableStream()),
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
