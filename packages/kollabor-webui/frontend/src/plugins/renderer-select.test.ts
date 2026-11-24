import { describe, expect, it } from "vitest";
import { EMPTY_CONTRIBUTIONS, type PluginRecord } from "./registry";
import type { PluginApi, RendererSpec } from "./sdk";
import { findTagRenderer, findToolRenderer, tagRendererNames } from "./renderer-select";

const Render = (() => null) as unknown as RendererSpec["render"];
const k = {} as PluginApi;

function record(id: string, renderers: RendererSpec[], withK = true, enableOrder?: number): PluginRecord {
  return {
    id,
    name: id,
    entry: (enableOrder === undefined ? {} : { enable_order: enableOrder }) as PluginRecord["entry"],
    status: "ready",
    error: null,
    retries: 0,
    k: withK ? k : null,
    contributions: { ...EMPTY_CONTRIBUTIONS, renderers },
  };
}

describe("findToolRenderer", () => {
  it("returns the renderer a loaded plugin declares for the tool, with its k", () => {
    const spec: RendererSpec = { tool: "shell", render: Render };
    const match = findToolRenderer([record("hello", [spec])], "shell");
    expect(match?.spec).toBe(spec);
    expect(match?.record.id).toBe("hello");
    expect(match?.k).toBe(k);
  });

  it("returns null when no plugin claims the tool", () => {
    expect(findToolRenderer([record("hello", [{ tool: "shell", render: Render }])], "read")).toBeNull();
  });

  it("keeps the host permission prompt out of plugin tool renderers", () => {
    const plugin = record("alpha", [{ tool: "request_permission", render: Render }]);
    expect(findToolRenderer([plugin], "request_permission")).toBeNull();
  });

  it("ignores tag renderers and plugins whose code has not loaded", () => {
    const plugins = [
      record("alpha", [{ tag: "shell", render: Render }]),
      record("beta", [{ tool: "shell", render: Render }], false),
    ];
    expect(findToolRenderer(plugins, "shell")).toBeNull();
  });

  it("the first plugin in the list wins when two claim one tool", () => {
    const first: RendererSpec = { tool: "shell", render: Render };
    const second: RendererSpec = { tool: "shell", render: Render };
    const match = findToolRenderer([record("alpha", [first]), record("beta", [second])], "shell");
    expect(match?.spec).toBe(first);
  });

  it("the first enabled plugin owns both tool and tag renderers", () => {
    const alphaTool: RendererSpec = { tool: "shell", render: Render };
    const zuluTool: RendererSpec = { tool: "shell", render: Render };
    const alphaTag: RendererSpec = { tag: "status-card", render: Render };
    const zuluTag: RendererSpec = { tag: "status-card", render: Render };
    const plugins = [
      record("alpha", [alphaTool, alphaTag], true, 2),
      record("zulu", [zuluTool, zuluTag], true, 1),
    ];

    expect(findToolRenderer(plugins, "shell")?.spec).toBe(zuluTool);
    expect(findTagRenderer(plugins, "status-card")?.spec).toBe(zuluTag);
  });
});

describe("findTagRenderer and tagRendererNames", () => {
  it("finds a tag renderer by tag name and never returns a tool renderer", () => {
    const tag: RendererSpec = { tag: "status-card", render: Render };
    const plugins = [record("hello", [{ tool: "shell", render: Render }, tag])];
    expect(findTagRenderer(plugins, "status-card")?.spec).toBe(tag);
    expect(findTagRenderer(plugins, "shell")).toBeNull();
  });

  it("lists each registered tag name once, in order, from loaded plugins only", () => {
    const plugins = [
      record("alpha", [{ tag: "status-card", render: Render }, { tool: "shell", render: Render }]),
      record("beta", [{ tag: "status-card", render: Render }, { tag: "note", render: Render }]),
      record("gamma", [{ tag: "hidden", render: Render }], false),
    ];
    expect(tagRendererNames(plugins)).toEqual(["status-card", "note"]);
  });
});
