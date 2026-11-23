import { describe, expect, it } from "vitest";
import { describePluginAdds, reviewFormState } from "./install";

describe("plugin review additions", () => {
  it("lists only additions as lowercase fragments", () => {
    expect(describePluginAdds({ python: true, engine: true, web: true, agents: true,
      hooks: false, mcp: false, contributes: { views: ["hello"] }, services: {} })).toEqual([
      "1 view", "code that runs in every agent", "engine routes", "browser code", "agent definitions",
    ]);
  });
  it("includes hooks, MCP and background processes when declared", () => {
    expect(describePluginAdds({ hooks: true, mcp: true, services: { server: {} } })).toEqual([
      "hooks", "MCP servers", "1 background process",
    ]);
  });
});

describe("plugin review form state", () => {
  it("keeps the source form and Review open after a failed review", () => {
    expect(reviewFormState("install", { status: "error" })).toEqual({ source: true, review: true });
  });
  it("opens the source form before any review starts", () => {
    expect(reviewFormState("install", null)).toEqual({ source: true, review: true });
  });
  it("hides the source form while a review is staging or ready", () => {
    expect(reviewFormState("install", { status: "staging" })).toEqual({ source: false, review: false });
    expect(reviewFormState("install", { status: "ready" })).toEqual({ source: false, review: false });
  });
  it("never shows the source form for an update", () => {
    expect(reviewFormState("update", null)).toEqual({ source: false, review: false });
    expect(reviewFormState("update", { status: "error" })).toEqual({ source: false, review: false });
  });
});
