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
  let inventory = [entry("hello")];
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

  it("keeps an error visible when a plugin cannot load", async () => {
    const { host } = setup({ importer: async () => { throw new Error("bad module"); } });
    await host.start();
    expect(host.getSnapshot().errors.hello).toContain("bad module");
    host.close();
  });
});
