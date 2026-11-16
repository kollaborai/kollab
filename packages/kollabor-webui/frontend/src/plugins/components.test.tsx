import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PluginFailure, PluginNotices, PluginRecoveryNotices } from "./components";
import type { PluginHost } from "./host";

describe("plugin recovery surfaces", () => {
  it("shows a dismiss control and identifies the source of plugin notices", () => {
    const snapshot = { disabled: false, loading: false, inventoryError: null, skipped: [], errors: {}, renderFailed: [],
      entries: [{ id: "hello", name: "Hello Plugin" }],
      registry: { plugins: [{ id: "hello", notices: [{ text: "Hello there" }] }], conflicts: [] } };
    const host = { subscribe: () => () => {}, getSnapshot: () => snapshot } as unknown as PluginHost;
    const markup = renderToStaticMarkup(<PluginNotices host={host} />);
    expect(markup).toContain("Hello Plugin");
    expect(markup).toContain("<span>Hello there</span>");
    expect(markup).toContain('aria-label="Dismiss"');
  });


  it("shows which command was skipped and which plugin or built-in owns it", () => {
    const snapshot = { disabled: false, loading: false, inventoryError: null, skipped: [], errors: {}, renderFailed: [],
      entries: [{ id: "later", name: "Later Plugin" }, { id: "first", name: "First Plugin" }],
      registry: { plugins: [], conflicts: [
        { pluginId: "later", command: "shared", owner: "first" },
        { pluginId: "later", command: "help", owner: "built-in" },
      ] } };
    const host = { subscribe: () => () => {}, getSnapshot: () => snapshot } as unknown as PluginHost;
    const markup = renderToStaticMarkup(<PluginNotices host={host} />);
    expect(markup).toContain("Later Plugin could not register /shared: already used by First Plugin.");
    expect(markup).toContain("Later Plugin could not register /help: already used by a built-in command.");
  });

  it("offers actionable load and render failure controls", () => {
    const markup = renderToStaticMarkup(<PluginFailure name="Hello" kind="render" error="broken" onRetry={() => {}} onTurnOff={() => {}} />);
    expect(markup).toContain("Hello");
    expect(markup).toContain("Failed to Render");
    expect(markup).toContain("Hello could not draw this page.");
    expect(markup).toContain("Retry");
    expect(markup).toContain("View Error");
    expect(markup).toContain("Turn Off");
    expect(markup.indexOf("Retry")).toBeLessThan(markup.indexOf("View Error"));
    expect(markup.indexOf("View Error")).toBeLessThan(markup.indexOf("Turn Off"));
  });
  it("explains the page-wide opt-out and names a skipped plugin", () => {
    const markup = renderToStaticMarkup(<PluginRecoveryNotices disabled skipped={["hello"]} names={{ hello: "Hello Plugin" }} onTurnOnAgain={() => {}} />);
    expect(markup).toContain("Plugins are off for this page");
    expect(markup).toContain("Hello Plugin was skipped: it froze the page last time.");
    expect(markup).not.toContain("hello was skipped");
    expect(markup).toContain("Turn On Again");
  });
});
