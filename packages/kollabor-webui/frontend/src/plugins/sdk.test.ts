import { describe, expect, it, vi } from "vitest";
import { createPluginContext, definePlugin } from "./sdk";
import { PluginRegistry } from "./registry";

function setup() {
  const json = vi.fn(async (_path: string, _init?: RequestInit): Promise<unknown> => ({ ok: true }));
  const listeners = new Map<string, (data: unknown) => void>();
  const off = vi.fn();
  const events = { on: vi.fn((topic: string, fn: (data: unknown) => void) => { listeners.set(topic, fn); return off; }) };
  const navigate = vi.fn();
  const draft = new PluginRegistry().stage("hello@dev");
  const k = createPluginContext({ id: "hello@dev", draft, json, events, navigate, openAgent: vi.fn(), copy: vi.fn(), toast: vi.fn(), ui: {} });
  return { k, json, listeners, off, events, draft, navigate };
}

describe("plugin SDK", () => {
  it("defines the same activation function and stages the contribution calls", () => {
    const { k, draft } = setup();
    const activate = definePlugin(plugin => {
      plugin.menu("hello", { count: { path: "/badge", refreshOn: "change" } });
      plugin.notice({ text: "Hello" });
      plugin.command({ name: "hello", description: "Hello", run: () => {} });
    });
    expect(activate).toBeTypeOf("function");
    activate(k);
    expect(draft.records.menus[0]).toEqual({ id: "hello", count: { path: "/badge", refreshOn: "change" } });
    expect(draft.records.notices[0].text).toBe("Hello");
    expect(draft.records.commands[0].name).toBe("hello");
  });

  it("uses authenticated engine requests confined to this plugin and saves settings", async () => {
    const { k, json } = setup();
    await k.api.get("/badge?limit=2");
    await k.api.post("/items", { name: "one" });
    await k.api.put("/items/one", { enabled: true });
    await k.api.delete("/items/one");
    await k.settings.get();
    await k.settings.save({ enabled: true });
    expect(json.mock.calls).toEqual([
      ["/plugins/hello%40dev/api/badge?limit=2", undefined],
      ["/plugins/hello%40dev/api/items", { method: "POST", body: '{"name":"one"}' }],
      ["/plugins/hello%40dev/api/items/one", { method: "PUT", body: '{"enabled":true}' }],
      ["/plugins/hello%40dev/api/items/one", { method: "DELETE" }],
      ["/plugins/hello%40dev/settings", undefined],
      ["/plugins/hello%40dev/settings", { method: "PUT", body: '{"enabled":true}' }],
    ]);
  });

  it("revokes SDK actions and aborts outstanding requests after disposal", async () => {
    const { k, json, navigate } = setup();
    let requestSignal: AbortSignal | undefined;
    json.mockImplementationOnce((_path, init) => {
      requestSignal = init?.signal ?? undefined;
      return new Promise((_resolve, reject) => requestSignal?.addEventListener("abort", () => reject(new Error("aborted"))));
    });
    const inFlight = k.api.get("/slow");
    expect(requestSignal).toBeInstanceOf(AbortSignal);
    k.dispose();
    await expect(inFlight).rejects.toThrow("aborted");
    expect(requestSignal?.aborted).toBe(true);
    await expect(k.api.post("/late", { x: 1 })).rejects.toThrow("disposed");
    await expect(k.settings.save({ greeting: "late" })).rejects.toThrow("disposed");
    await expect(k.settings.get()).rejects.toThrow("disposed");
    k.navigate({ page: "board" });
    expect(navigate).not.toHaveBeenCalled();
    expect(json).toHaveBeenCalledTimes(1);
  });

  it("refuses traversal, encoded separators, or an absolute URL as an API path", async () => {
    const { k, json } = setup();
    for (const path of ["../other", "/%2e%2e/settings", "/a%2fb", "https://outside.test/a", "//outside.test/a", "/a\\b"]) {
      await expect(k.api.get(path)).rejects.toThrow("Invalid plugin API path");
    }
    expect(json).not.toHaveBeenCalled();
  });

  it("provides scoped event subscriptions and navigation", () => {
    const { k, listeners, off, navigate } = setup();
    const callback = vi.fn();
    const dispose = k.events.on("chain", callback);
    listeners.get("chain")?.({ count: 1 });
    expect(callback).toHaveBeenCalledWith({ count: 1 });
    dispose();
    expect(off).toHaveBeenCalledOnce();
    k.navigate({ page: "board" });
    expect(navigate).toHaveBeenCalledWith({ page: "board" });
  });
});
