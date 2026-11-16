import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { PluginHostContext } from "./components";
import type { PluginHost } from "./host";
import { PluginRegistry } from "./registry";
import { PluginTaggedText, PluginToolPart } from "./renderers";

function hostWithTool() {
  const registry = new PluginRegistry();
  const draft = registry.stage("example");
  draft.renderer({ tool: "build_report", render: ({ toolCall }) => <div>Plugin result: {String(toolCall?.result)}</div> });
  registry.commit(draft);
  const snapshot = { disabled: false, loading: false, inventoryError: null, entries: [], registry: registry.getSnapshot(), errors: {}, skipped: [], renderFailed: [], failedRenderers: [] };
  return { subscribe: () => () => {}, getSnapshot: () => snapshot, getContext: () => ({}), reportRenderError: () => {}, retry: async () => {} } as unknown as PluginHost;
}
const part = { type: "tool-call", toolCallId: "1", toolName: "build_report", args: {}, argsText: "{}", result: "safe output", status: { type: "complete" } } as ToolCallMessagePartProps;

describe("plugin tool renderer", () => {
  it("renders a matching plugin tool with the actual result", () => {
    const html = renderToStaticMarkup(<PluginHostContext.Provider value={hostWithTool()}><PluginToolPart part={part} fallback={<span>Default tool</span>} /></PluginHostContext.Provider>);
    expect(html).toContain("Plugin result: safe output");
    expect(html).not.toContain("Default tool");
  });
  it("keeps a failure panel for a tool after its plugin is quarantined", () => {
    const snapshot = { disabled: false, loading: false, inventoryError: null, entries: [],
      registry: { plugins: [], conflicts: [] }, errors: { example: "renderer crashed" }, skipped: [], renderFailed: ["example"],
      failedRenderers: [{ pluginId: "example", tool: "build_report" }] };
    const host = { subscribe: () => () => {}, getSnapshot: () => snapshot, retry: async () => {} } as unknown as PluginHost;
    const html = renderToStaticMarkup(<PluginHostContext.Provider value={host}><PluginToolPart part={part} fallback={<span>Default tool</span>} /></PluginHostContext.Provider>);
    expect(html).toContain("Failed to Render");
    expect(html).toContain("Retry");
    expect(html).not.toContain("Default tool");
  });
  it("keeps a failure panel for a tag after its plugin is quarantined", () => {
    const snapshot = { disabled: false, loading: false, inventoryError: null, entries: [],
      registry: { plugins: [], conflicts: [] }, errors: { example: "renderer crashed" }, skipped: [], renderFailed: ["example"],
      failedRenderers: [{ pluginId: "example", tag: "report_tag" }] };
    const host = { subscribe: () => () => {}, getSnapshot: () => snapshot, retry: async () => {} } as unknown as PluginHost;
    const html = renderToStaticMarkup(<PluginHostContext.Provider value={host}><PluginTaggedText text="<report_tag>safe</report_tag>" /></PluginHostContext.Provider>);
    expect(html).toContain("Failed to Render");
    expect(html).toContain("Retry");
  });
  it("uses the original tool UI when no plugin claims the tool", () => {
    const html = renderToStaticMarkup(<PluginHostContext.Provider value={hostWithTool()}><PluginToolPart part={{ ...part, toolName: "other" }} fallback={<span>Default tool</span>} /></PluginHostContext.Provider>);
    expect(html).toContain("Default tool");
  });
});
