import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PluginFailure, PluginRecoveryNotices } from "./components";

describe("plugin recovery surfaces", () => {
  it("offers actionable load and render failure controls", () => {
    const markup = renderToStaticMarkup(<PluginFailure name="Hello" kind="render" error="broken" onRetry={() => {}} onTurnOff={() => {}} />);
    expect(markup).toContain("Hello");
    expect(markup).toContain("Failed to Render");
    expect(markup).toContain("Retry");
    expect(markup).toContain("View Error");
    expect(markup).toContain("Turn Off");
  });
  it("explains the page-wide opt-out and names a skipped plugin", () => {
    const markup = renderToStaticMarkup(<PluginRecoveryNotices disabled skipped={["hello"]} onTurnOnAgain={() => {}} />);
    expect(markup).toContain("Plugins are off for this page");
    expect(markup).toContain("hello was skipped: it froze the page last time.");
    expect(markup).toContain("Turn On Again");
  });
});
