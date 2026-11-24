// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import { readFileSync } from "node:fs";
// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import path from "node:path";
// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PluginsSettings, pluginRowControls } from "./settings";
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

async function markupFor(entries: PluginEntry[], pathname = "/settings"): Promise<string> {
  const loader = createPluginLoader(pluginRegistry, {
    list: async () => ({ plugins: entries }),
    importModule: async () => ({}),
    fetchText: async () => "",
    loadCode: false,
    api: { request: async () => new Response(), stream: async () => {} },
  });
  await loader.refresh();
  vi.stubGlobal("window", { location: { pathname } });
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

  it("mounts custom plugin settings with the scoped portal root", () => {
    const settingsView = source.slice(source.indexOf("{SettingsView && record?.k ?"));
    expect(settingsView).toMatch(/<PluginMount record=\{record\}>\s*<SettingsView/);
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

  it("shows when a running service misses its declared heartbeat", async () => {
    const now = Date.now() / 1000;
    const markup = await markupFor([{
      ...plugin([]),
      enabled: true,
      services: [{
        name: "worker",
        state: "running",
        since: now - 7200,
        heartbeat: now - 6 * 60,
        heartbeat_s: 60,
      } as PluginEntry["services"][number]],
    }]);

    expect(markup).toContain("Running · No Heartbeat for 6 min");
  });

  it("starts the missed-heartbeat window at the later heartbeat monitor or heartbeat time", async () => {
    const now = Date.now() / 1000;
    const markup = await markupFor([{
      ...plugin([]),
      enabled: true,
      services: [
        {
          name: "restarted-worker",
          state: "running",
          since: now - 7200,
          heartbeat: null,
          heartbeat_monitor_since: now - 30,
          heartbeat_s: 60,
        },
        {
          name: "late-heartbeat-worker",
          state: "running",
          since: now - 7200,
          heartbeat: now - 180,
          heartbeat_monitor_since: now - 30,
          heartbeat_s: 60,
        },
        {
          name: "late-monitor-worker",
          state: "running",
          since: now - 7200,
          heartbeat: now - 30,
          heartbeat_monitor_since: now - 180,
          heartbeat_s: 60,
        },
      ] as PluginEntry["services"],
    }]);

    expect(markup).not.toContain("No Heartbeat");
  });

  it("shows which declared secrets a background process receives", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      enabled: true,
      services: [{ name: "worker", state: "stopped", secrets: ["API_TOKEN"] }],
    }]);

    expect(markup).toContain("Receives secrets: API_TOKEN");
  });

  it("ignores a non-array service secrets value from the inventory", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      enabled: true,
      services: [{ name: "worker", state: "stopped", secrets: "API_TOKEN" as unknown as string[] }],
    }]);

    expect(markup).not.toContain("API_TOKEN");
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
      version: "0.8.1",
    }]);

    expect(markup).toContain("Development Folder:");
    expect(markup).toContain("Version: 0.8.1");
    expect(markup).not.toContain("Development folder:");
  });

  it("shows an installed plugin version and the shadowing development folder", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      enabled: true,
      shadowed: true,
      state: "ok",
      version: "2.3.4",
    }]);

    expect(markup).toContain("Version: 2.3.4");
    expect(markup.match(/Shadowed by a Dev Folder/g)).toHaveLength(2);
  });

  it("does not offer an enable switch for an always-on development plugin", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      id: "hello@dev",
      name: "Hello@dev",
      kind: "dev",
      enabled: true,
      source: "/tmp/hello",
    }]);

    expect(markup).not.toContain('aria-label="Hello@dev enabled"');
  });

  it("does not offer service controls for a plugin changed on disk", () => {
    expect(pluginRowControls({ kind: "installed", shadowed: false, state: "changed-on-disk" })).toEqual({
      managed: true,
      switchEnabled: false,
      services: false,
      updateRemove: true,
    });
  });

  it("allows a narrow plugin row to wrap long service statuses", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      name: "Phone Bridge",
      enabled: true,
      services: [{ name: "phone-bridge-worker", state: "crash-stopped" }],
    }]);

    expect(markup).toContain("phone-bridge-worker");
    expect(markup).toContain("Stopped After 5 Crashes");
    expect(markup).toMatch(/<button[^>]*class="[^"]*whitespace-normal/);
  });

  it("explains the different blockers for turning off and removing a plugin", () => {
    expect(source).toMatch(/confirmation === "remove" \? "Background processes will stop\. Cancel active plugin agent runs first; plugin requests must finish before removal\." : "Background processes will stop\. Cancel active plugin agent runs before turning it off\."/);
  });

  it("names each service action for the process it controls", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      enabled: true,
      services: [
        { name: "worker-a", state: "running" },
        { name: "worker-b", state: "running" },
        { name: "retry-worker", state: "stop-failed" },
        { name: "stopped-worker", state: "stopped" },
        { name: "starting-worker", state: "starting" },
        { name: "retrying-worker", state: "backoff" },
        { name: "failed-worker", state: "error" },
        { name: "unknown-worker", state: "unknown" },
      ],
    }]);

    for (const label of [
      "View Log worker-a", "Restart worker-a", "Stop worker-a",
      "View Log worker-b", "Restart worker-b", "Stop worker-b",
      "View Log retry-worker", "Retry Stop retry-worker",
      "View Log stopped-worker", "Start stopped-worker",
    ]) expect(markup).toContain(`aria-label="${label}"`);
    for (const name of ["starting-worker", "retrying-worker", "failed-worker", "unknown-worker"])
      expect(markup).not.toContain(`aria-label="Start ${name}"`);
  });

  it("clears old service errors before the next service or confirmation action", () => {
    expect(source).toContain("onError(null)");
    expect(source).toMatch(/setError\(null\);\s*setConfirmation\("disable"\)/);
    expect(source).toMatch(/setError\(null\);\s*setConfirmation\("remove"\)/);
  });

  it("locks service controls and plugin navigation during a plugin action", () => {
    const serviceRow = source.slice(source.indexOf("function ServiceRow"), source.indexOf("function PluginDetail"));
    for (const action of ["Restart", "Stop", "Start"])
      expect(serviceRow).toMatch(new RegExp(`aria-label=\\{\`${action} \\$\\{service\\.name\\}\`\\} disabled=\\{busy \\|\\| disabled \\|\\| operationBusy\\}`));
    expect(source).toMatch(/disabled=\{busyPluginKey !== null\}/);
  });

  it("renders PhoneLink as read-only through the plugin page controls", async () => {
    const markup = await markupFor([{
      ...plugin([{
        type: "spinbox", label: "Daily Token Cap", path: "plugin-values.hello.max_tokens_per_day",
        value: 2_000_000, editable: true,
      }]),
      enabled: true,
      state: "ok",
      services: [{ name: "worker", state: "stopped" }],
    }], "/relay/v1/phone");

    expect(markup).toContain("Manage plugins on this computer");
    expect(markup).not.toContain("Add Plugin");
    expect(markup).not.toContain(">Update</button>");
    expect(markup).not.toContain(">Remove</button>");
    expect(markup).not.toContain(">Start</button>");
    expect(markup).toContain('data-disabled="" disabled=""');
    expect(markup).not.toContain(">Save</button>");
  });
});
