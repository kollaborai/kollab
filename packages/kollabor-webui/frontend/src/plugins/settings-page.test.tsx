// @vitest-environment jsdom
// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import { readFileSync } from "node:fs";
// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import path from "node:path";
// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PluginsSettings, pluginRowControls } from "./settings";
import { createPluginLoader, listedPluginEntries } from "./loader";
import { EMPTY_CONTRIBUTIONS, pluginRegistry, type PluginRecord } from "./registry";
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
  omitted: { services: 0, uses: 0, granted: 0, settings: 0, setting_options: 0, option_labels: 0, contributions: 0 },
  settings,
} as PluginEntry);

const CANARY = "secret-token-canary-1397";

async function markupFor(
  entries: unknown[],
  pathname = "/settings",
  serverTime?: number,
  runtimeRecords: PluginRecord[] = [],
): Promise<string> {
  const loader = createPluginLoader(pluginRegistry, {
    list: async () => ({ plugins: entries, server_time: serverTime }),
    importModule: async () => ({}),
    fetchText: async () => "",
    loadCode: false,
    api: { request: async () => new Response(), stream: async () => {} },
  });
  await loader.refresh();
  for (const record of runtimeRecords) pluginRegistry.put(record);
  vi.stubGlobal("window", { location: { pathname } });
  return renderToStaticMarkup(<PluginsSettings api={{} as never} />);
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("plugin settings page", () => {
  it.each([
    ["Built In", "built-in", false],
    ["Installed", "installed", true],
  ] as const)("associates custom settings with the matching kind when %s is selected", async (_label, selectedKind, showsPackageSettings) => {
    const builtIn = { ...plugin([]), id: "voice", name: "Voice Built In", kind: "built-in", state: "ok", enabled: true } as PluginEntry;
    const installed = { ...plugin([]), id: "voice", name: "Voice Package", kind: "installed", state: "ok", enabled: true } as PluginEntry;
    const selected = selectedKind === "built-in" ? builtIn : installed;
    const other = selectedKind === "built-in" ? installed : builtIn;
    const record: PluginRecord = {
      id: "voice",
      name: installed.name,
      entry: installed,
      status: "ready",
      error: null,
      retries: 0,
      k: {} as never,
      contributions: {
        ...EMPTY_CONTRIBUTIONS,
        settings: { render: () => <p>Package Custom Settings</p> },
      },
    };

    const markup = await markupFor([selected, other], "/settings", undefined, [record]);

    expect(markup.includes("Package Custom Settings")).toBe(showsPackageSettings);
  });

  it("shows counts for data omitted from the installed inventory", async () => {
    const entry = {
      ...plugin([]),
      omitted: { services: 1, uses: 1, granted: 1, settings: 1, setting_options: 1, option_labels: 1, contributions: 1 },
      services: [{ name: "worker", state: "stopped", secrets: [], secrets_omitted: 1 }],
    } as unknown as PluginEntry;
    const markup = await markupFor([entry]);

    expect(markup).toContain(
      "Inventory omitted: 1 service, 1 use declaration, 1 granted capability, 1 setting, 1 setting option, 1 option label, 1 contribution declaration, 1 service secret.",
    );
  });

  it.each([
    ["entry id", (entry: Record<string, unknown>) => ({ ...entry, id: { token: CANARY } })],
    ["entry name", (entry: Record<string, unknown>) => ({ ...entry, name: CANARY })],
    ["entry version", (entry: Record<string, unknown>) => ({ ...entry, version: { token: CANARY } })],
    ["entry kind", (entry: Record<string, unknown>) => ({ ...entry, kind: { token: CANARY } })],
    ["entry source", (entry: Record<string, unknown>) => ({ ...entry, source: `https://example.invalid/?token=${CANARY}` })],
    ["entry commit", (entry: Record<string, unknown>) => ({ ...entry, commit: { token: CANARY } })],
    ["entry enabled", (entry: Record<string, unknown>) => ({ ...entry, enabled: "true" })],
    ["entry state", (entry: Record<string, unknown>) => ({ ...entry, state: { token: CANARY } })],
    ["entry error", (entry: Record<string, unknown>) => ({ ...entry, state: "error", error: `Loader failed: ${CANARY}` })],
    ["entry web", (entry: Record<string, unknown>) => ({ ...entry, web: { entry: { token: CANARY }, style: [CANARY], hash: CANARY } })],
    ["entry contributes", (entry: Record<string, unknown>) => ({ ...entry, contributes: { menu: [{ id: CANARY, label: CANARY, open: { view: CANARY } }] } })],
    ["entry enable order", (entry: Record<string, unknown>) => ({ ...entry, enable_order: { token: CANARY } })],
    ["entry shadowed", (entry: Record<string, unknown>) => ({ ...entry, shadowed: { token: CANARY } })],
    ["entry services", (entry: Record<string, unknown>) => ({ ...entry, services: { name: CANARY } })],
    ["entry settings", (entry: Record<string, unknown>) => ({ ...entry, settings: { label: CANARY } })],
    ["entry settings section", (entry: Record<string, unknown>) => ({ ...entry, settings_section: { title: CANARY } })],
    ["entry uses", (entry: Record<string, unknown>) => ({ ...entry, uses: [`custom:${CANARY}`] })],
    ["entry grants", (entry: Record<string, unknown>) => ({ ...entry, granted: { token: CANARY } })],
    ["service name", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: CANARY, state: "stopped" }] })],
    ["service state", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: CANARY }] })],
    ["service desired", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "stopped", desired: CANARY }] })],
    ["service pid", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "stopped", pid: { token: CANARY } }] })],
    ["service since", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "stopped", since: { token: CANARY } }] })],
    ["service restarts", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "stopped", restarts: CANARY }] })],
    ["service heartbeat", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "stopped", heartbeat: { token: CANARY } }] })],
    ["service heartbeat interval", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "stopped", heartbeat_s: CANARY }] })],
    ["service heartbeat monitor time", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "stopped", heartbeat_monitor_since: { token: CANARY } }] })],
    ["service stop deadline", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "stopped", stop_deadline: { token: CANARY } }] })],
    ["service error", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "error", error: `Could not start /run/${CANARY}: No such file or directory` }] })],
    ["service secrets", (entry: Record<string, unknown>) => ({ ...entry, services: [{ name: "worker", state: "stopped", secrets: [CANARY] }] })],
  ] as const)("normalizes malformed %s inventory data before Settings renders it", async (_field, mutate) => {
    const base = {
      ...plugin([{ type: "label", path: "plugin-values.hello.label", label: "Label", editable: false }]),
      enabled: true,
      services: [{
        name: "worker", state: "stopped", desired: "stopped", pid: null, since: null, restarts: 0,
        secrets: [], heartbeat: null, heartbeat_s: null, heartbeat_monitor_since: null, stop_deadline: null, error: null,
      }],
    } as unknown as Record<string, unknown>;
    const raw = mutate(base);
    const markup = await markupFor([raw]);
    expect(markup).not.toContain(CANARY);

    const entries = listedPluginEntries();
    if (_field === "entry id") {
      expect(entries).toHaveLength(0);
      return;
    }
    expect(entries).toHaveLength(1);
    const normalized = entries[0] as unknown as Record<string, unknown>;
    expect(JSON.stringify(normalized)).not.toContain(CANARY);
    expect(typeof normalized.id).toBe("string");
    expect(typeof normalized.name).toBe("string");
    expect(normalized.version === null || typeof normalized.version === "string").toBe(true);
    expect(["built-in", "installed", "dev"]).toContain(normalized.kind);
    expect(normalized.source === null || typeof normalized.source === "string").toBe(true);
    expect(normalized.commit === null || typeof normalized.commit === "string").toBe(true);
    expect(typeof normalized.enabled).toBe("boolean");
    expect(typeof normalized.shadowed).toBe("boolean");
    expect(["ok", "error", "off", "stopping", "stop-failed", "changed-on-disk", "skipped"]).toContain(normalized.state);
    expect(normalized.error === null || typeof normalized.error === "string").toBe(true);
    expect(normalized.web === null || typeof normalized.web === "object").toBe(true);
    expect(normalized.contributes && typeof normalized.contributes === "object").toBe(true);
    expect(Array.isArray(normalized.services)).toBe(true);
    expect(Array.isArray(normalized.settings)).toBe(true);
    expect(normalized.settings_section === null || typeof normalized.settings_section === "object").toBe(true);
    expect(Array.isArray(normalized.uses)).toBe(true);
    expect(Array.isArray(normalized.granted)).toBe(true);
    expect(normalized.enable_order === null || typeof normalized.enable_order === "number").toBe(true);
    for (const service of normalized.services as Record<string, unknown>[]) {
      expect(typeof service.name).toBe("string");
      expect(["starting", "running", "backoff", "stopping", "stop-failed", "exited", "stopped", "crash-stopped", "error", "unknown", "failed"]).toContain(service.state);
      expect(["running", "stopped"]).toContain(service.desired);
      expect(service.pid === null || typeof service.pid === "number").toBe(true);
      expect(service.since === null || typeof service.since === "number").toBe(true);
      expect(typeof service.restarts).toBe("number");
      expect(service.heartbeat === null || typeof service.heartbeat === "number").toBe(true);
      expect(service.heartbeat_s === null || typeof service.heartbeat_s === "number").toBe(true);
      expect(service.heartbeat_monitor_since === null || typeof service.heartbeat_monitor_since === "number").toBe(true);
      expect(service.stop_deadline === null || typeof service.stop_deadline === "number").toBe(true);
      expect(service.error === null || typeof service.error === "string").toBe(true);
      expect(Array.isArray(service.secrets)).toBe(true);
    }
  });

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

  it("uses engine time for heartbeat and uptime when the browser clock is ahead", async () => {
    const engineNow = 1_800_000_000;
    vi.useFakeTimers();
    vi.setSystemTime(new Date((engineNow + 10 * 60) * 1000));
    const markup = await markupFor([{
      ...plugin([]),
      enabled: true,
      services: [{
        name: "worker",
        state: "running",
        since: engineNow - 7200,
        heartbeat: engineNow - 30,
        heartbeat_s: 60,
      } as PluginEntry["services"][number]],
    }], "/settings", engineNow);

    expect(markup).not.toContain("No Heartbeat");
    expect(markup).toContain("Running · 2 h");
  });

  it.each([
    ["omitted", {}],
    ["malformed", { server_time: { token: CANARY } }],
  ] as const)("retains the last engine clock offset when server_time is %s", async (_kind, secondResponse) => {
    const engineNow = 1_800_000_000;
    vi.useFakeTimers();
    vi.setSystemTime(new Date((engineNow + 10 * 60) * 1000));
    const entry = {
      ...plugin([]),
      enabled: true,
      services: [{ name: "worker", state: "running", since: engineNow - 7200, heartbeat: engineNow - 30, heartbeat_s: 60 }],
    };
    let reads = 0;
    const loader = createPluginLoader(pluginRegistry, {
      list: async () => {
        reads += 1;
        return (reads === 1
          ? { plugins: [entry], server_time: engineNow }
          : { plugins: [entry], ...secondResponse }) as never;
      },
      importModule: async () => ({}),
      fetchText: async () => "",
      loadCode: false,
      api: { request: async () => new Response(), stream: async () => {} },
    });
    await loader.refresh();
    await loader.refresh();
    vi.stubGlobal("window", { location: { pathname: "/settings" } });
    const markup = renderToStaticMarkup(<PluginsSettings api={{} as never} />);

    expect(markup).not.toContain("No Heartbeat");
    expect(markup).toContain("Running · 2 h");
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

    expect(markup).toContain("Receives Secrets: API_TOKEN");
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

  it("shows failed-stop state in the plugin row and detail", async () => {
    const markup = await markupFor([{
      ...plugin([]),
      state: "stop-failed",
      services: [{ name: "worker", state: "stop-failed", error: "signal failed" }],
    }]);

    expect(markup.match(/>Failed to Stop</g)).toHaveLength(4);
    expect(markup).toContain('class="text-xs font-medium text-destructive">Failed to Stop</span>');
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

  it("moves focus to the remaining plugin after the removed detail unmounts", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const voice = {
      ...plugin([]), id: "voice", name: "Voice", kind: "built-in", enabled: true, state: "ok",
    } as PluginEntry;
    const entries = [plugin([]), voice];
    const loader = createPluginLoader(pluginRegistry, {
      list: async () => ({ plugins: entries, server_time: undefined }),
      importModule: async () => ({}),
      fetchText: async () => "",
      loadCode: false,
      api: { request: async () => new Response(), stream: async () => {} },
    });
    await loader.refresh();

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(<PluginsSettings api={{
        json: async () => {
          entries.splice(0, entries.length, voice);
          await loader.refresh();
          return {};
        },
      } as never} />));

      const remove = [...document.querySelectorAll("button")]
        .find((button) => button.textContent?.trim() === "Remove");
      expect(remove).toBeDefined();
      await act(async () => remove?.dispatchEvent(new MouseEvent("click", { bubbles: true })));

      const dialog = document.querySelector('[role="dialog"]');
      const confirmRemove = [...(dialog?.querySelectorAll("button") ?? [])]
        .find((button) => button.textContent?.trim() === "Remove");
      expect(confirmRemove).toBeDefined();
      expect(confirmRemove?.getAttribute("data-variant")).toBe("destructive");
      await act(async () => {
        confirmRemove?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(document.activeElement?.getAttribute("aria-label")).toBe("Voice details");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });

  it("includes plugin kind when enabling a built-in plugin", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const voice = {
      ...plugin([]), id: "voice", name: "Voice", kind: "built-in", enabled: false, state: "off",
    } as PluginEntry;
    const loader = createPluginLoader(pluginRegistry, {
      list: async () => ({ plugins: [voice], server_time: undefined }),
      importModule: async () => ({}),
      fetchText: async () => "",
      loadCode: false,
      api: { request: async () => new Response(), stream: async () => {} },
    });
    await loader.refresh();

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const requests: string[] = [];
    try {
      await act(async () => root.render(<PluginsSettings api={{
        json: async (path: string) => {
          requests.push(path);
          return {};
        },
      } as never} />));

      const toggle = host.querySelector('[role="switch"]');
      expect(toggle).toBeDefined();
      await act(async () => {
        toggle?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(requests).toEqual(["/plugins/voice/enable?kind=built-in"]);
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
