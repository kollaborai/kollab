import { describe, expect, it } from "vitest";
import { describePluginAdds } from "./install";

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
