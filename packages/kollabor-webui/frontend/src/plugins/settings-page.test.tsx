import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PluginsSettings } from "./settings";
import { createPluginLoader } from "./loader";
import { pluginRegistry } from "./registry";
import type { PluginEntry } from "./sdk";

const source = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "settings.tsx"), "utf8");

const plugin = (settings: unknown[]): PluginEntry => ({
  id: "hello",
  name: "Hello",
  kind: "installed",
  enabled: false,
  shadowed: false,
  state: "off",
  error: null,
  web: null,
  contributes: {},
  services: [],
  settings,
} as PluginEntry);

async function markupFor(entries: PluginEntry[]): Promise<string> {
  const loader = createPluginLoader(pluginRegistry, {
    list: async () => ({ plugins: entries }),
    importModule: async () => ({}),
    fetchText: async () => "",
    loadCode: false,
    api: { request: async () => new Response(), stream: async () => {} },
  });
  await loader.refresh();
  vi.stubGlobal("window", { location: { pathname: "/settings" } });
  return renderToStaticMarkup(<PluginsSettings api={{} as never} />);
}

afterEach(() => vi.unstubAllGlobals());

describe("plugin settings page", () => {
  it("renders the engine-listed settings fields and a save action", async () => {
    const markup = await markupFor([plugin([
      {
        type: "spinbox", label: "Daily Token Cap", path: "plugin-values.hello.max_tokens_per_day",
        value: 2_000_000, editable: true,
      },
      {
        type: "spinbox", label: "Max Agent Runs At Once", path: "plugin-values.hello.max_concurrent_runs",
        value: 4, editable: true,
      },
    ])]);

    expect(markup).toContain("Daily Token Cap");
    expect(markup).toContain("Max Agent Runs At Once");
    expect(markup).toContain("Save");
  });

  it("saves plugin fields through the plugin settings endpoint", () => {
    expect(source).toMatch(/api\.json\([^\n]*plugins\/\$\{encodeURIComponent\(entry\.id\)\}\/settings/);
  });

  it("uses Title Case for backend service states", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      services: [
        { name: "starting-worker", state: "starting" },
        { name: "retrying-worker", state: "backoff" },
        { name: "failed-worker", state: "error" },
        { name: "unknown-worker", state: "unknown" },
      ],
    }]);

    for (const label of ["Starting", "Restarting", "Failed to Start", "Unknown"])
      expect(markup).toContain(label);
    for (const state of ["starting", "backoff", "error", "unknown"])
      expect(markup).not.toContain(`>${state}<`);
  });

  it("uses Failed to Load in the plugin row and detail", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      enabled: true,
      state: "error",
      error: "Plugin metadata could not be loaded",
    }]);

    expect(markup.match(/Failed to Load/g)).toHaveLength(2);
    expect(markup).not.toContain(">Failed</span>");
  });

  it("uses a Title Case Development Folder label", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      id: "hello@dev",
      name: "Hello@dev",
      kind: "dev",
      enabled: true,
      source: "/tmp/hello",
    }]);

    expect(markup).toContain("Development Folder:");
    expect(markup).not.toContain("Development folder:");
  });
});
