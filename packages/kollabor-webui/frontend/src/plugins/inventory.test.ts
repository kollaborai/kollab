import { describe, expect, it } from "vitest";
import { createPluginLoader, listedPluginEntries, type LoaderDeps } from "./loader";
import { createPluginRegistry } from "./registry";
import type { PluginEntry } from "./sdk";

const entry = (id: string, enabled: boolean, web: PluginEntry["web"] = null): PluginEntry => ({
  id,
  name: id,
  kind: "installed",
  enabled,
  shadowed: false,
  state: "ok",
  error: null,
  web,
  contributes: web ? { views: ["panel"] } : {},
  services: [],
});

const deps = (list: LoaderDeps["list"], extra: Partial<LoaderDeps> = {}): LoaderDeps =>
  ({
    list,
    importModule: async () => ({}),
    fetchText: async () => "",
    api: { request: async () => new Response(), stream: async () => {} },
    ...extra,
  }) as unknown as LoaderDeps;

describe("plugin inventory", () => {
  it("keeps every listed plugin, including ones that are off", async () => {
    const list = async () => ({ plugins: [entry("alpha", true), entry("beta", false)] });
    await createPluginLoader(createPluginRegistry(), deps(list)).refresh();
    expect(listedPluginEntries().map((item) => [item.id, item.enabled])).toEqual([
      ["alpha", true],
      ["beta", false],
    ]);
  });

  it("keeps the last good list when a later read fails", async () => {
    let fail = false;
    const list = async () => {
      if (fail) throw new Error("engine down");
      return { plugins: [entry("gamma", true)] };
    };
    const loader = createPluginLoader(createPluginRegistry(), deps(list));
    await loader.refresh();
    fail = true;
    await loader.refresh();
    expect(listedPluginEntries().map((item) => item.id)).toEqual(["gamma"]);
  });
});
