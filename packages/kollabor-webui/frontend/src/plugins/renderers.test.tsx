import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { PluginHostContext } from "./components";
import type { PluginHost } from "./host";
import { PluginRegistry } from "./registry";
import { PluginToolPart } from "./renderers";

function hostWithTool() {
  const registry = new PluginRegistry();
  const draft = registry.stage("example");
  draft.renderer({ tool: "build_report", render: ({ toolCall }) => <div>Plugin result: {String(toolCall?.result)}</div> });
  registry.commit(draft);
  const snapshot = { disabled: false, loading: false, inventoryError: null, entries: [], registry: registry.getSnapshot(), errors: {}, skipped: [], renderFailed: [] };
  return { subscribe: () => () => {}, getSnapshot: () => snapshot, getContext: () => ({}), reportRenderError: () => {}, retry: async () => {} } as unknown as PluginHost;
}
const part = { type: "tool-call", toolCallId: "1", toolName: "build_report", args: {}, argsText: "{}", result: "safe output", status: { type: "complete" } } as ToolCallMessagePartProps;

describe("plugin tool renderer", () => {
  it("renders a matching plugin tool with the actual result", () => {
    const html = renderToStaticMarkup(<PluginHostContext.Provider value={hostWithTool()}><PluginToolPart part={part} fallback={<span>Default tool</span>} /></PluginHostContext.Provider>);
    expect(html).toContain("Plugin result: safe output");
    expect(html).not.toContain("Default tool");
  });
  it("uses the original tool UI when no plugin claims the tool", () => {
    const html = renderToStaticMarkup(<PluginHostContext.Provider value={hostWithTool()}><PluginToolPart part={{ ...part, toolName: "other" }} fallback={<span>Default tool</span>} /></PluginHostContext.Provider>);
    expect(html).toContain("Default tool");
  });
});
