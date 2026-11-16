import { describe, expect, it, vi } from "vitest";
import { PluginLoader, type WebPluginEntry } from "./loader";

const entry = (id: string, overrides: Partial<WebPluginEntry> = {}): WebPluginEntry => ({
  id, enabled: true, shadowed: false, state: "ok", web: { entry: "web/dist/index.js", style: null, hash: "abc" }, ...overrides,
});

function setup(importer: (url: string) => Promise<unknown>, timeoutMs = 5000) {
  const active = new Map<string, string[]>();
  const errors = new Map<string, string>();
  const loader = new PluginLoader({
    importer, timeoutMs,
    createContext: () => { const rows: string[] = []; return { api: { add: (s: string) => rows.push(s) }, value: rows }; },
    commit: (id, value) => { active.set(id, value); errors.delete(id); },
    remove: id => active.delete(id),
    fail: (id, error) => { active.delete(id); errors.set(id, error); },
  });
  return { loader, active, errors };
}

describe("web plugin loader", () => {
  it("loads enabled entries in parallel and gives each its own context", async () => {
    const importer = vi.fn(async (_url: string) => ({ default: async (k: { add(s: string): void }) => k.add("ready") }));
    const { loader, active } = setup(importer);
    await loader.reconcile([entry("a"), entry("b"), entry("off", { enabled: false }), entry("shadow", { shadowed: true })]);
    expect([...active]).toEqual([["a", ["ready"]], ["b", ["ready"]]]);
    expect(importer).toHaveBeenCalledTimes(2);
    expect(importer.mock.calls[0][0]).toBe("/plugins/a/web/index.js?v=abc");
  });

  it("removes disabled contributions and ignores a late import", async () => {
    let finish!: (value: unknown) => void;
    const { loader, active } = setup(() => new Promise(resolve => { finish = resolve; }));
    const first = loader.reconcile([entry("a")]);
    await loader.reconcile([entry("a", { enabled: false })]);
    finish({ default: (k: { add(s: string): void }) => k.add("late") });
    await first;
    expect(active.size).toBe(0);
  });

  it("times out and ignores late activation, then retries with a cache buster", async () => {
    vi.useFakeTimers();
    try {
      let finish!: (value: unknown) => void;
      const importer = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
        .mockResolvedValueOnce({ default: (k: { add(s: string): void }) => k.add("recovered") });
      const { loader, active, errors } = setup(importer, 50);
      const pending = loader.reconcile([entry("a")]);
      await vi.advanceTimersByTimeAsync(51);
      await pending;
      expect(errors.get("a")).toContain("timed out");
      const lateActivation = vi.fn((k: { add(s: string): void }) => k.add("late"));
      finish({ default: lateActivation });
      await Promise.resolve();
      expect(lateActivation).not.toHaveBeenCalled();
      expect(active.size).toBe(0);
      await loader.retry("a");
      expect(active.get("a")).toEqual(["recovered"]);
      expect(importer.mock.calls[1][0]).toBe("/plugins/a/web/index.js?v=abc&retry=1");
    } finally { vi.useRealTimers(); }
  });

  it("reconciles a changed declared stylesheet even when dist files are unchanged", async () => {
    const importer = vi.fn(async () => ({ default: (k: { add(s: string): void }) => k.add("ready") }));
    const { loader, active } = setup(importer);
    await loader.reconcile([entry("a")]);
    await loader.reconcile([entry("a")]);
    expect(importer).toHaveBeenCalledTimes(1);
    await loader.reconcile([entry("a", { web: { entry: "web/dist/index.js", style: "web/dist/index.css", hash: "abc" } })]);
    expect(importer).toHaveBeenCalledTimes(2);
    expect(active.get("a")).toEqual(["ready"]);
  });

  it("contains context creation failures and still loads other plugins", async () => {
    const registry = new PluginRegistry<{ add(value: string): void }>();
    const active = new Map<string, string[]>();
    const fail = vi.fn();
    const loader = new PluginLoader({
      registry,
      importer: vi.fn(async () => ({ default: (k: { add(value: string): void }) => k.add("ready") })),
      createContext: (item, draft) => {
        if (item.id === "bad") throw new Error("invalid stylesheet");
        return { context: draft.context };
      },
      commit: (id, records) => { active.set(id, records); },
      remove: id => { active.delete(id); },
      fail,
    });
    await expect(loader.reconcile([entry("bad"), entry("good")])).resolves.toBeUndefined();
    expect(fail).toHaveBeenCalledWith("bad", expect.objectContaining({ message: "invalid stylesheet" }));
    expect(active.get("good")).toEqual(["ready"]);
  });

  it("contains activation errors and leaves other plugins working", async () => {
    const { loader, active, errors } = setup(async url => ({ default: (k: { add(s: string): void }) => {
      if (url.includes("/bad/")) throw new Error("broken");
      k.add("ok");
    } }));
    await loader.reconcile([entry("bad"), entry("good")]);
    expect(errors.get("bad")).toContain("broken");
    expect(active.get("good")).toEqual(["ok"]);
  });
});
