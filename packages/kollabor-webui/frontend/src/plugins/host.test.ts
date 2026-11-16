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
