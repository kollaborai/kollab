import { describe, expect, it, vi } from "vitest";
import { PluginHost, type PluginHostOptions } from "./host";

function storage(): Storage {
  const items = new Map<string, string>();
  return {
    get length() { return items.size; }, clear: () => items.clear(),
    getItem: key => items.get(key) ?? null, key: index => [...items.keys()][index] ?? null,
    removeItem: key => { items.delete(key); }, setItem: (key, value) => { items.set(key, value); },
  };
}
const entry = (id: string, enabled = true) => ({
  id, name: id, kind: "installed", enabled, shadowed: false, state: enabled ? "ok" : "off",
  web: { entry: "web/dist/index.js", style: null, hash: "a" }, contributes: {},
});
function setup(options: Partial<PluginHostOptions> = {}) {
  let inventory: (ReturnType<typeof entry> & { services?: unknown[] })[] = [entry("hello")];
  const json = vi.fn(async () => ({ schema: 1, plugins: inventory }));
  const request = vi.fn(async () => new Response(new ReadableStream<Uint8Array>()));
  const importer = vi.fn(async () => ({ default: (k: { notice(value: { text: string }): void }) => k.notice({ text: "Hello" }) }));
  const session = storage();
  const host = new PluginHost({ json, request, importer, storage: session, disabled: false,
    navigate: vi.fn(), openAgent: vi.fn(), copy: vi.fn(async () => {}), toast: vi.fn(), ui: {}, ...options });
  return { host, json, importer, session, setInventory: (next: typeof inventory) => { inventory = next; } };
}

describe("plugin host lifecycle", () => {

  it("waits for the winning inventory when a refresh is superseded", async () => {
    type Inventory = { schema: number; plugins: ReturnType<typeof entry>[] };
    let resolveFirst!: (value: Inventory) => void;
    let resolveSecond!: (value: Inventory) => void;
    const first = new Promise<Inventory>(resolve => { resolveFirst = resolve; });
    const second = new Promise<Inventory>(resolve => { resolveSecond = resolve; });
    const { host, json } = setup();
    json.mockImplementationOnce(() => first).mockImplementationOnce(() => second);
    let firstDone = false;
    const stale = host.refresh({ waitForActivation: false }).then(() => { firstDone = true; });
    const current = host.refresh({ waitForActivation: false });
    resolveFirst({ schema: 1, plugins: [entry("one")] });
    await Promise.resolve();
    await Promise.resolve();
    expect(firstDone).toBe(false);
    resolveSecond({ schema: 1, plugins: [{ ...entry("one"), enabled: false }] });
    await Promise.all([stale, current]);
    expect(host.getSnapshot().entries[0].enabled).toBe(false);
    host.close();
  });


  it("keeps the earlier-enabled command when imports complete in reverse order", async () => {
    let finishFirst!: (module: unknown) => void;
    const firstImport = new Promise<unknown>(resolve => { finishFirst = resolve; });
    const activate = (id: string) => ({ default: (k: { command(value: { name: string; description: string; run: () => void }): void }) =>
      k.command({ name: "shared", description: id, run: () => {} }) });
    const importer = vi.fn((url: string) => url.includes("/first/") ? firstImport : Promise.resolve(activate("second")));
    const first = { ...entry("first"), enable_order: 1, contributes: { slash: ["shared"] } };
    const second = { ...entry("second"), enable_order: 2, contributes: { slash: ["shared"] } };
    const { host, setInventory } = setup({ importer });
    setInventory([first, second]);
    const start = host.start();
    await vi.waitFor(() => expect(importer).toHaveBeenCalledTimes(2));
    finishFirst(activate("first"));
    await start;
    const { plugins, conflicts } = host.getSnapshot().registry;
    expect(plugins.find(plugin => plugin.id === "first")?.commands.map(command => command.name)).toEqual(["shared"]);
    expect(plugins.find(plugin => plugin.id === "second")?.commands).toEqual([]);
    expect(conflicts).toEqual([{ pluginId: "second", command: "shared", owner: "first" }]);
    host.close();
  });

  it("does not fetch or import when plugins are off for the page", async () => {
    const { host, json, importer } = setup({ disabled: true });
    await host.start();
    expect(json).not.toHaveBeenCalled();
    expect(importer).not.toHaveBeenCalled();
    expect(host.getSnapshot().disabled).toBe(true);
    host.close();
  });

  it("loads enabled plugins and removes contributions after inventory changes", async () => {
    const { host, importer, setInventory } = setup();
    await host.start();
    expect(importer).toHaveBeenCalledOnce();
    expect(host.getSnapshot().registry.plugins[0].notices[0].text).toBe("Hello");
    setInventory([entry("hello", false)]);
    await host.refresh();
    expect(host.getSnapshot().registry.plugins).toEqual([]);
    host.close();
  });

  it("observes service starts, crashes and backoff without an inventory event", async () => {
    vi.useFakeTimers();
    const onServiceCrash = vi.fn();
    const { host, json, setInventory } = setup({ onServiceCrash });
    const withState = (state: string) => ({ ...entry("hello"), services: [{ name: "worker", state }] });
    try {
      setInventory([withState("starting")]);
      await host.start();
      for (const [state, delay] of [["running", 5000], ["backoff", 30_000], ["crash-stopped", 5000]] as const) {
        setInventory([withState(state)]);
        await vi.advanceTimersByTimeAsync(delay);
        expect(host.getSnapshot().entries[0].services?.[0]).toMatchObject({ state });
      }
      expect(onServiceCrash).toHaveBeenCalledOnce();
      expect(onServiceCrash).toHaveBeenCalledWith([{ pluginId: "hello", pluginName: "hello", serviceName: "worker" }]);
      const calls = json.mock.calls.length;
      await vi.advanceTimersByTimeAsync(10_000);
      expect(json).toHaveBeenCalledTimes(calls);
    } finally { host.close(); vi.useRealTimers(); }
  });

  it("does not repeatedly scan the full inventory for a stable running service", async () => {
    vi.useFakeTimers();
    const active = { ...entry("hello"), services: [{ name: "worker", state: "running" }] };
    const { host, json, setInventory } = setup();
    try {
      setInventory([active]);
      await host.start();
      await vi.advanceTimersByTimeAsync(29_000);
      expect(json).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1000);
      expect(json).toHaveBeenCalledTimes(2);
    } finally { host.close(); vi.useRealTimers(); }
  });

  it("does not poll terminal service states even when their desired state remains running", async () => {
    vi.useFakeTimers();
    try {
      for (const state of ["exited", "crash-stopped", "error"]) {
        const { host, json, setInventory } = setup();
        setInventory([{ ...entry("hello"), services: [{ name: "worker", state, desired: "running" }] }]);
        await host.start();
        await vi.advanceTimersByTimeAsync(60_000);
        expect(json, `${state} is terminal`).toHaveBeenCalledTimes(1);
        host.close();
      }
    } finally { vi.useRealTimers(); }
  });

  it("does not overlap service polls when inventory is slow", async () => {
    vi.useFakeTimers();
    const active = { ...entry("hello"), services: [{ name: "worker", state: "running" }] };
    let finish!: (value: unknown) => void;
    const slow = new Promise(resolve => { finish = resolve; });
    const json = vi.fn().mockResolvedValueOnce({ schema: 1, plugins: [active] }).mockReturnValue(slow);
    const { host } = setup({ json });
    try {
      await host.start();
      await vi.advanceTimersByTimeAsync(90_000);
      expect(json).toHaveBeenCalledTimes(2);
      finish({ schema: 1, plugins: [active] });
      await vi.advanceTimersByTimeAsync(0);
    } finally { host.close(); vi.useRealTimers(); }
  });

  it("refreshes promised heartbeats and stops polling when closed", async () => {
    vi.useFakeTimers();
    const { host, json, setInventory } = setup();
    try {
      const initial = { ...entry("hello"), services: [{ name: "worker", state: "running", heartbeat_s: 60, heartbeat: 100 }] };
      setInventory([initial]);
      await host.start();
      expect(json).toHaveBeenCalledTimes(1);
      setInventory([{ ...initial, services: [{ ...initial.services[0], heartbeat: 200 }] }]);
      await vi.advanceTimersByTimeAsync(30_000);
      expect(json).toHaveBeenCalledTimes(2);
      expect(host.getSnapshot().entries[0].services?.[0]).toMatchObject({ heartbeat: 200 });
      setInventory([entry("hello")]);
      await host.refresh();
      expect(json).toHaveBeenCalledTimes(3);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(json).toHaveBeenCalledTimes(3);
      host.close();
      await vi.advanceTimersByTimeAsync(60_000);
      expect(json).toHaveBeenCalledTimes(3);
    } finally { host.close(); vi.useRealTimers(); }
  });

  it("exposes engine time using a monotonic inventory sample and clears invalid samples", async () => {
    vi.useFakeTimers();
    const json = vi.fn().mockResolvedValueOnce({ schema: 1, plugins: [entry("hello")], server_time: 1000 })
      .mockResolvedValueOnce({ schema: 1, plugins: [entry("hello")] });
    const { host } = setup({ json });
    try {
      await host.start();
      expect(host.engineTime()).toBe(1000);
      await vi.advanceTimersByTimeAsync(5000);
      expect(host.engineTime()).toBe(1005);
      await host.refresh();
      expect(host.engineTime()).toBeNull();
    } finally { host.close(); vi.useRealTimers(); }
  });


  it("keeps a disabled plugin mounted while stopping and polls until it is off", async () => {
    vi.useFakeTimers();
    const active = entry("hello");
    const stopping = { ...active, enabled: false, state: "stopping", services: [{ name: "worker", state: "stopping", stop_deadline: 1100 }] };
    const off = { ...active, enabled: false, state: "off" };
    const json = vi.fn().mockResolvedValueOnce({ schema: 1, server_time: 1000, plugins: [active] })
      .mockResolvedValueOnce({ schema: 1, server_time: 1001, plugins: [stopping] })
      .mockResolvedValue({ schema: 1, server_time: 1003, plugins: [off] });
    const { host, importer } = setup({ json });
    try {
      await host.start();
      expect(host.getSnapshot().registry.plugins).toHaveLength(1);
      const context = host.getContext("hello");
      await host.refresh();
      expect(host.getSnapshot().registry.plugins).toHaveLength(1);
      expect(importer).toHaveBeenCalledTimes(1);
      expect(host.getContext("hello")).toBe(context);
      await vi.advanceTimersByTimeAsync(2000);
      expect(host.getSnapshot().registry.plugins).toHaveLength(0);
      expect(host.getContext("hello")).toBeUndefined();
    } finally { host.close(); vi.useRealTimers(); }
  });


  it("does not overlap stopping polls when inventory is slow", async () => {
    vi.useFakeTimers();
    const stopping = { ...entry("hello"), state: "stopping" };
    let finish!: (value: unknown) => void;
    const slow = new Promise(resolve => { finish = resolve; });
    const json = vi.fn().mockResolvedValueOnce({ schema: 1, plugins: [stopping] }).mockReturnValue(slow);
    const { host } = setup({ json });
    try {
      await host.start();
      await vi.advanceTimersByTimeAsync(6000);
      expect(json).toHaveBeenCalledTimes(2);
      finish({ schema: 1, plugins: [stopping] });
      await vi.advanceTimersByTimeAsync(0);
    } finally { host.close(); vi.useRealTimers(); }
  });


  it("notifies once when a service reaches its crash limit and again after recovery", async () => {
    const active = { ...entry("hello"), services: [{ name: "worker", state: "running" }] };
    const crashed = { ...active, services: [{ name: "worker", state: "crash-stopped" }] };
    const json = vi.fn().mockResolvedValueOnce({ schema: 1, plugins: [active] })
      .mockResolvedValueOnce({ schema: 1, plugins: [crashed] })
      .mockResolvedValueOnce({ schema: 1, plugins: [crashed] })
      .mockResolvedValueOnce({ schema: 1, plugins: [active] })
      .mockResolvedValueOnce({ schema: 1, plugins: [crashed] });
    const toast = vi.fn();
    const { host } = setup({ json, toast });
    try {
      await host.start();
      expect(toast).not.toHaveBeenCalled();
      await host.refresh();
      expect(toast).toHaveBeenCalledWith("hello: worker stopped after 5 crashes. Open Settings → Plugins to view its log.");
      await host.refresh();
      expect(toast).toHaveBeenCalledTimes(1);
      await host.refresh();
      await host.refresh();
      expect(toast).toHaveBeenCalledTimes(2);
    } finally { host.close(); }
  });


  it("delivers actionable crash identities without also showing a text toast", async () => {
    const crashed = { ...entry("hello"), services: [{ name: "worker", state: "crash-stopped" }] };
    const json = vi.fn().mockResolvedValue({ schema: 1, plugins: [crashed] });
    const toast = vi.fn();
    const onServiceCrash = vi.fn();
    const { host } = setup({ json, toast, onServiceCrash });
    try {
      await host.start();
      expect(onServiceCrash).toHaveBeenCalledWith([{ pluginId: "hello", pluginName: "hello", serviceName: "worker" }]);
      await host.refresh();
      expect(onServiceCrash).toHaveBeenCalledTimes(1);
      expect(toast).not.toHaveBeenCalled();
    } finally { host.close(); }
  });


  it("refreshes declared menu labels when the manifest changes without a bundle change", async () => {
    const importer = vi.fn(async () => ({ default: (k: { menu(id: string): void }) => k.menu("board") }));
    const { host, setInventory } = setup({ importer });
    const before = { ...entry("hello"), contributes: { menu: [{ id: "board", label: "Old label" }] } };
    setInventory([before]);
    await host.start();
    expect(host.getSnapshot().registry.plugins[0].menus[0].label).toBe("Old label");
    const after = { ...before, contributes: { menu: [{ id: "board", label: "New label" }] } };
    setInventory([after]);
    await host.refresh();
    expect(host.getSnapshot().registry.plugins[0].menus[0].label).toBe("New label");
    expect(importer).toHaveBeenCalledTimes(2);
    host.close();
  });

  it("skips an import left in progress by a frozen page until the user retries", async () => {
    const session = storage();
    session.setItem("kollab:plugin:loading:hello", "1");
    const { host, importer } = setup({ storage: session });
    await host.start();
    expect(importer).not.toHaveBeenCalled();
    expect(host.getSnapshot().skipped).toEqual(["hello"]);
    await host.turnOnAgain("hello");
    expect(importer).toHaveBeenCalledOnce();
    expect(host.getSnapshot().registry.plugins[0].id).toBe("hello");
    host.close();
  });

  it("keeps the freeze marker until asynchronous activation finishes", async () => {
    let finish!: () => void;
    const activation = new Promise<void>(resolve => { finish = resolve; });
    const { host, session } = setup({ importer: async () => ({ default: () => activation }) });
    const starting = host.start();
    await vi.waitFor(() => expect(session.getItem("kollab:plugin:loading:hello")).not.toBeNull());
    // A new page sees the unfinished boot and skips it instead of activating it again.
    const recovered = setup({ storage: session });
    await recovered.host.start();
    expect(recovered.host.getSnapshot().skipped).toEqual(["hello"]);
    expect(recovered.importer).not.toHaveBeenCalled();
    recovered.host.close();
    finish();
    await starting;
    expect(session.getItem("kollab:plugin:loading:hello")).toBeNull();
    host.close();
  });

  it("keeps the freeze marker while publishing the activated plugin", async () => {
    const { host, session } = setup();
    const markersDuringPublication: (string | null)[] = [];
    host.subscribe(() => {
      if (host.getSnapshot().registry.plugins.length) {
        markersDuringPublication.push(session.getItem("kollab:plugin:loading:hello"));
      }
    });
    await host.start();
    expect(markersDuringPublication.length).toBeGreaterThan(0);
    expect(markersDuringPublication[0]).not.toBeNull();
    expect(session.getItem("kollab:plugin:loading:hello")).toBeNull();
    host.close();
  });

  it("does not clear a newer import's freeze marker when an old import finishes", async () => {
    const completions: ((module: unknown) => void)[] = [];
    const importer = vi.fn(() => new Promise<unknown>(resolve => completions.push(resolve)));
    const { host, session, setInventory } = setup({ importer });
    const first = host.start();
    await vi.waitFor(() => expect(completions).toHaveLength(1));
    const updated = entry("hello");
    updated.web.hash = "b";
    setInventory([updated]);
    const second = host.refresh();
    await vi.waitFor(() => expect(completions).toHaveLength(2));
    completions[0]({ default: () => {} });
    await first;
    expect(session.getItem("kollab:plugin:loading:hello")).not.toBeNull();
    completions[1]({ default: () => {} });
    await second;
    host.close();
  });

  it("retains failed renderer identities without retaining plugin functions", async () => {
    const importer = vi.fn().mockResolvedValue({ default: (k: any) => {
      k.renderer({ tool: "build_report", render: () => null });
      k.renderer({ tag: "report_tag", render: () => null });
    } });
    const configured = entry("hello");
    configured.contributes = { renderers: [{ tool: "build_report" }, { tag: "report_tag" }] };
    const { host, setInventory } = setup({ importer });
    setInventory([configured]);
    await host.start();
    host.reportRenderError("hello", new Error("boom"));
    expect(host.getSnapshot().failedRenderers).toEqual([
      { pluginId: "hello", tool: "build_report" }, { pluginId: "hello", tag: "report_tag" },
    ]);
    await host.retry("hello");
    expect(host.getSnapshot().failedRenderers).toEqual([]);
    host.close();
  });

  it("isolates a render failure and retries that plugin", async () => {
    const { host, importer } = setup();
    await host.start();
    expect(host.getContext("hello")).toBeDefined();
    host.reportRenderError("hello", new Error("render broke"));
    expect(host.getSnapshot().errors.hello).toContain("render broke");
    expect(host.getSnapshot().registry.plugins).toEqual([]);
    expect(host.getContext("hello")).toBeUndefined();
    await host.retry("hello");
    expect(importer).toHaveBeenCalledTimes(2);
    expect(host.getSnapshot().errors.hello).toBeUndefined();
    expect(host.getSnapshot().registry.plugins[0].id).toBe("hello");
    host.close();
  });

  it("forgets a render failure when disabled and reports a later load failure correctly", async () => {
    const importer = vi.fn()
      .mockResolvedValueOnce({ default: () => {} })
      .mockRejectedValueOnce(new Error("new load failure"));
    const { host, setInventory } = setup({ importer });
    await host.start();
    host.reportRenderError("hello", new Error("render failure"));
    expect(host.getSnapshot().renderFailed).toContain("hello");
    setInventory([{ ...entry("hello"), enabled: false }]);
    await host.refresh();
    expect(host.getSnapshot().renderFailed).not.toContain("hello");
    setInventory([entry("hello")]);
    await host.refresh();
    expect(host.getSnapshot().errors.hello).toBe("new load failure");
    expect(host.getSnapshot().renderFailed).not.toContain("hello");
    host.close();
  });

  it("retains a load error while the plugin is stopping", async () => {
    const importer = vi.fn(async () => { throw new Error("bad module"); });
    const { host, setInventory } = setup({ importer });
    await host.start();
    const stopping = entry("hello");
    stopping.state = "stopping";
    setInventory([stopping]);
    await host.refresh();
    expect(host.getSnapshot().errors.hello).toContain("bad module");
    expect(importer).toHaveBeenCalledTimes(1);
    host.close();
  });

  it("refreshes inventory after the event stream reconnects", async () => {
    let streamCalls = 0;
    const request = vi.fn(async () => {
      streamCalls++;
      return new Response(new ReadableStream<Uint8Array>({
        start(controller) { if (streamCalls === 1) controller.close(); },
      }));
    });
    const { host, json } = setup({ request });
    await host.start();
    const initialReads = json.mock.calls.length;
    await vi.waitFor(() => expect(json.mock.calls.length).toBeGreaterThan(initialReads), { timeout: 2500 });
    expect(streamCalls).toBeGreaterThanOrEqual(2);
    host.close();
  });

  it("keeps an error visible when a plugin cannot load", async () => {
    const { host } = setup({ importer: async () => { throw new Error("bad module"); } });
    await host.start();
    expect(host.getSnapshot().errors.hello).toContain("bad module");
    host.close();
  });
});
