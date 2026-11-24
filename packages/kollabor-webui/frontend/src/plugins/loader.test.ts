import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import { readFileSync } from "node:fs";
import {
  assetUrl,
  bundlesOwnReact,
  createPluginLoader,
  isActive,
  listedPluginEntries,
  LOAD_TIMEOUT_MS,
  normalizePluginEntry,
  pluginsOff,
  type LoaderDeps,
} from "./loader";
import { createPluginRegistry } from "./registry";
import type { PluginApi, PluginEntry } from "./sdk";

const Tab = () => null;

function entry(id: string, extra: Partial<PluginEntry> = {}): PluginEntry {
  return {
    id,
    name: id === "pr-chain" ? "PR Chain" : "Hello",
    kind: "installed",
    enabled: true,
    shadowed: false,
    state: "ok",
    error: null,
    web: { entry: "web/dist/index.js", style: "web/dist/index.css", hash: "abc" },
    contributes: {
      menu: [{ id, label: "Row", open: { page: "main" } }],
      pages: [{ id: "main", title: "Main" }],
    },
    services: [],
    omitted: { services: 0, uses: 0, granted: 0, settings: 0, setting_options: 0, option_labels: 0, contributions: 0 },
    ...extra,
  };
}

type Setup = (k: PluginApi) => unknown;

/** A promise the test settles by hand. */
function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function harness(lists: PluginEntry[][], modules: Record<string, () => Promise<unknown>>) {
  const registry = createPluginRegistry();
  let listing = 0;
  const removed: string[] = [];
  const deps = {
    list: vi.fn(async () => ({ plugins: lists[Math.min(listing++, lists.length - 1)] })),
    importModule: vi.fn(async (url: string) => {
      const load = modules[url];
      if (!load) throw new Error(`no module at ${url}`);
      return load();
    }),
    fetchText: vi.fn(async (_url: string) => "export default function plugin() {}"),
    addStyle: vi.fn((id: string, _href: string) => () => removed.push(id)),
    api: {
      request: vi.fn(async () => new Response("{}", { headers: { "content-type": "application/json" } })),
      stream: vi.fn(async () => new Promise<void>(() => {})),
    },
  } satisfies LoaderDeps;
  const loader = createPluginLoader(registry, deps);
  const plugin = (id: string) => registry.getSnapshot().plugins.find((item) => item.id === id);
  return { registry, loader, deps, plugin, removed };
}

const module = (setup: Setup) => async () => ({ default: setup });
const page = (k: PluginApi) =>
  k.page({ id: "main", title: "Main", tabs: [{ id: "one", label: "One", render: Tab }] });

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("which plugins load", () => {
  it("?plugins=off turns plugin loading off for the page", () => {
    expect(pluginsOff("?plugins=off")).toBe(true);
    expect(pluginsOff("?topic=agents&plugins=off")).toBe(true);
    expect(pluginsOff("?plugins=on")).toBe(false);
    expect(pluginsOff("")).toBe(false);
  });

  it("only enabled, unshadowed plugins in a loadable state with something to show", () => {
    expect(isActive(entry("hello"))).toBe(true);
    expect(isActive(entry("hello", { state: "error", error: "engine part failed" }))).toBe(true);
    expect(isActive(entry("hello", { state: "stop-failed" }))).toBe(true);
    expect(isActive(entry("hello", { enabled: false, state: "stop-failed" }))).toBe(false);
    expect(isActive(entry("hello", { enabled: false, state: "off" }))).toBe(false);
    expect(isActive(entry("hello", { shadowed: true }))).toBe(false);
    expect(isActive(entry("hello", { state: "changed-on-disk" }))).toBe(false);
    expect(isActive(entry("hello", { state: "skipped" }))).toBe(false);
    expect(isActive(entry("hub", { kind: "built-in", web: null, contributes: {} }))).toBe(false);
    expect(isActive(entry("../x"))).toBe(false);
  });

  it("asset URLs name the file inside web/dist with its hash, and the retry count when retrying", () => {
    const hello = entry("hello");
    expect(assetUrl(hello, "entry")).toBe("/plugins/hello/web/index.js?v=abc");
    expect(assetUrl(hello, "entry", 2)).toBe("/plugins/hello/web/index.js?v=abc&retry=2");
    expect(assetUrl(hello, "style")).toBe("/plugins/hello/web/index.css?v=abc");
    expect(assetUrl(entry("hello@dev"), "entry")).toBe("/plugins/hello@dev/web/index.js?v=abc");
    expect(assetUrl(entry("hello", { web: { entry: "bin/run.js", hash: "abc" } }), "entry")).toBeNull();
    expect(assetUrl(entry("hello", { web: { entry: "web/dist/../../x.js", hash: "abc" } }), "entry")).toBeNull();
  });
});

describe("plugin inventory text redaction", () => {
  it("preserves safe labels and provenance while rejecting credential text", () => {
    const source = "https://github.com/acme/token-manager.git";
    const normalized = normalizePluginEntry({
      ...entry("hello"),
      name: "Token-Budget",
      source,
      settings: [{
        type: "text_input", path: "plugins.demo.password", label: "Password: required",
        help: "Password: required", value: null, editable: false,
      }],
    });
    expect(normalized?.name).toBe("Token-Budget");
    expect(normalized?.source).toBe(source);
    expect(normalized?.settings?.[0]?.label).toBe("Password: required");
    expect(normalized?.settings?.[0]?.help).toBe("Password: required");
  });

  it.each([
    ["underscore credential assignment", "GITHUB_TOKEN=ghp_fakecredentialvalue123"],
    ["bearer credential", "Bearer abc123XYZ987"],
    ["API key shape", "sk-live-abcdefghijklmnop"],
  ])("rejects %s", (_label, value) => {
    expect(normalizePluginEntry({ ...entry("hello"), name: value })?.name).toBe("hello");
  });
});

describe("loading", () => {
  it("imports every enabled entry in parallel and awaits each plugin function before showing it", async () => {
    const gates = { hello: deferred(), chain: deferred() };
    const { loader, deps, plugin } = harness([[entry("hello"), entry("pr-chain")]], {
      "/plugins/hello/web/index.js?v=abc": module(async (k) => {
        page(k);
        await gates.hello.promise;
      }),
      "/plugins/pr-chain/web/index.js?v=abc": module(async (k) => {
        page(k);
        await gates.chain.promise;
      }),
    });
    await loader.refresh();
    await vi.waitFor(() => expect(deps.importModule).toHaveBeenCalledTimes(2));
    // Both started before either finished: parallel, and nothing shows until its function returns.
    expect(plugin("hello")).toMatchObject({ status: "loading" });
    expect(plugin("hello")?.contributions.pages).toEqual([]);
    gates.hello.resolve();
    await vi.waitFor(() => expect(plugin("hello")?.status).toBe("ready"));
    expect(plugin("hello")?.contributions.pages.map((item) => item.id)).toEqual(["main"]);
    expect(plugin("pr-chain")?.status).toBe("loading");
    gates.chain.resolve();
    await loader.idle();
    expect(plugin("pr-chain")?.status).toBe("ready");
    expect(deps.addStyle).toHaveBeenCalledWith("hello", "/plugins/hello/web/index.css?v=abc");
  });

  it("a 5 s limit covers the import and the awaited function; the rest of the app keeps going", async () => {
    vi.useFakeTimers();
    const { loader, plugin } = harness([[entry("hello"), entry("pr-chain")]], {
      "/plugins/hello/web/index.js?v=abc": module(() => new Promise(() => {})),
      "/plugins/pr-chain/web/index.js?v=abc": () => new Promise(() => {}),
    });
    await loader.refresh();
    await vi.advanceTimersByTimeAsync(LOAD_TIMEOUT_MS - 1);
    expect(plugin("hello")?.status).toBe("loading");
    await vi.advanceTimersByTimeAsync(1);
    await loader.idle();
    expect(plugin("hello")).toMatchObject({ status: "failed", error: "This plugin's web code failed while loading. Check the browser console for details." });
    expect(plugin("pr-chain")).toMatchObject({ status: "failed", error: "This plugin's web code failed while loading. Check the browser console for details." });
  });

  it("a timed-out import that resolves later is ignored: its plugin function never runs", async () => {
    vi.useFakeTimers();
    const late = deferred<{ default: Setup }>();
    const setup = vi.fn(page);
    const { loader, plugin } = harness([[entry("hello")]], {
      "/plugins/hello/web/index.js?v=abc": () => late.promise,
    });
    await loader.refresh();
    await vi.advanceTimersByTimeAsync(LOAD_TIMEOUT_MS);
    await loader.idle();
    expect(plugin("hello")?.status).toBe("failed");
    late.resolve({ default: setup });
    await vi.advanceTimersByTimeAsync(10);
    expect(setup).not.toHaveBeenCalled();
    expect(plugin("hello")).toMatchObject({ status: "failed", k: null });
    expect(plugin("hello")?.contributions.pages).toEqual([]);
  });

  it("a plugin function still running at the limit can reach nothing afterwards", async () => {
    vi.useFakeTimers();
    let k: PluginApi | null = null;
    const gate = deferred();
    const { loader, plugin, deps } = harness([[entry("hello")]], {
      "/plugins/hello/web/index.js?v=abc": module(async (api) => {
        k = api;
        await gate.promise;
      }),
    });
    await loader.refresh();
    await vi.advanceTimersByTimeAsync(LOAD_TIMEOUT_MS);
    await loader.idle();
    expect(plugin("hello")?.status).toBe("failed");
    gate.resolve();
    k!.notice({ text: "Too late" });
    await expect(k!.api.get("/badge")).rejects.toThrow("no longer");
    expect(deps.api.request).not.toHaveBeenCalled();
    expect(plugin("hello")?.contributions.notices).toEqual([]);
  });

  it("keeps arbitrary setup errors out of plugin UI and logs the original exception", async () => {
    const original = new Error("secret returned by a plugin API");
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const { loader, plugin } = harness([[entry("hello"), entry("pr-chain")]], {
      "/plugins/hello/web/index.js?v=abc": module(() => {
        throw original;
      }),
      "/plugins/pr-chain/web/index.js?v=abc": module(page),
    });
    await loader.refresh();
    await loader.idle();
    expect(plugin("hello")).toMatchObject({
      status: "failed",
      error: "This plugin's web code failed while loading. Check the browser console for details.",
    });
    expect(logged).toHaveBeenCalledWith("Plugin hello web code failed while loading", original);
    expect(plugin("pr-chain")?.status).toBe("ready");
  });

  it("a module without a default plugin function fails with what to fix", async () => {
    const { loader, plugin } = harness([[entry("hello")]], {
      "/plugins/hello/web/index.js?v=abc": async () => ({ activate() {} }),
    });
    await loader.refresh();
    await loader.idle();
    expect(plugin("hello")).toMatchObject({
      status: "failed",
      error: "This plugin's web code failed while loading. Check the browser console for details.",
    });
  });

  it("refuses a bundle that carries its own React before running it", async () => {
    const { loader, deps, plugin } = harness([[entry("hello")]], {
      "/plugins/hello/web/index.js?v=abc": module(page),
    });
    deps.fetchText.mockResolvedValue("var e={};e.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE=t;");
    await loader.refresh();
    await loader.idle();
    expect(plugin("hello")).toMatchObject({
      status: "failed",
      error: "This plugin's web code failed while loading. Check the browser console for details.",
    });
    expect(deps.importModule).not.toHaveBeenCalled();
  });

  it("refuses React carried by an imported chunk before running the plugin", async () => {
    const { loader, deps, plugin } = harness([[entry("hello")]], {
      "/plugins/hello/web/index.js?v=abc": module(page),
    });
    deps.fetchText.mockImplementation(async (url) =>
      url === "/plugins/hello/web/index.js?v=abc"
        ? 'import "./vendor-react.js"; export default function setup() {}'
        : "var e={};e.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE=t;",
    );
    await loader.refresh();
    await loader.idle();
    expect(plugin("hello")).toMatchObject({
      status: "failed",
      error: "This plugin's web code failed while loading. Check the browser console for details.",
    });
    expect(deps.importModule).not.toHaveBeenCalled();
  });

  it.each([
    [
      "nested minified static imports",
      'import{jsx as e}from"./chunks/shared.js";',
      {
        "/plugins/hello/web/chunks/shared.js?v=abc": 'export{jsx as e}from"../vendor-react.js";',
        "/plugins/hello/web/vendor-react.js?v=abc":
          "var e={};e.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE=t;",
      },
    ],
    [
      "literal dynamic imports",
      'import("./chunks/lazy.js");',
      {
        "/plugins/hello/web/chunks/lazy.js?v=abc":
          "var e={};e.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE=t;",
      },
    ],
  ])("refuses React in %s", async (_case, entrySource, chunks) => {
    const { loader, deps, plugin } = harness([[entry("hello")]], {
      "/plugins/hello/web/index.js?v=abc": module(page),
    });
    deps.fetchText.mockImplementation(async (url) =>
      url === "/plugins/hello/web/index.js?v=abc" ? entrySource : chunks[url as keyof typeof chunks] ?? "",
    );
    await loader.refresh();
    await loader.idle();
    expect(plugin("hello")).toMatchObject({
      status: "failed",
      error: "This plugin's web code failed while loading. Check the browser console for details.",
    });
    expect(deps.importModule).not.toHaveBeenCalled();
  });

  it("Retry imports index.js?v=<hash>&retry=<n> so the browser does not hand back the failed module", async () => {
    let fail = true;
    const retried = vi.fn(page);
    const { loader, deps, plugin } = harness([[entry("hello")]], {
      "/plugins/hello/web/index.js?v=abc": module(() => {
        throw new Error("first try fails");
      }),
      "/plugins/hello/web/index.js?v=abc&retry=1": module((k) => {
        if (fail) throw new Error("second try fails");
      }),
      "/plugins/hello/web/index.js?v=abc&retry=2": module(retried),
    });
    await loader.refresh();
    await loader.idle();
    expect(plugin("hello")?.status).toBe("failed");
    await loader.retry("hello");
    expect(plugin("hello")).toMatchObject({
      status: "failed",
      error: "This plugin's web code failed while loading. Check the browser console for details.",
      retries: 1,
    });
    fail = false;
    await loader.retry("hello");
    expect(plugin("hello")).toMatchObject({ status: "ready", retries: 2, error: null });
    expect(retried).toHaveBeenCalledTimes(1);
    expect(deps.importModule.mock.calls.map(([url]) => url)).toEqual([
      "/plugins/hello/web/index.js?v=abc",
      "/plugins/hello/web/index.js?v=abc&retry=1",
      "/plugins/hello/web/index.js?v=abc&retry=2",
    ]);
  });

  it("a plugin with rows but no web code is shown without importing anything", async () => {
    const { loader, deps, plugin } = harness([[entry("links", { web: null })]], {});
    await loader.refresh();
    await loader.idle();
    expect(plugin("links")?.status).toBe("ready");
    expect(deps.importModule).not.toHaveBeenCalled();
  });

  it("records a listing failure without throwing", async () => {
    const { loader, deps, registry } = harness([[]], {});
    deps.list.mockRejectedValueOnce(new Error("Kollab Engine is unavailable; verify it is running."));
    await loader.refresh();
    expect(registry.getSnapshot()).toMatchObject({
      listed: true,
      listError: "Kollab Engine is unavailable; verify it is running.",
      plugins: [],
    });
  });
});

describe("plugins-changed", () => {
  it("re-reads GET /plugins and adds or removes rows, tabs and pages without a reload", async () => {
    const { loader, plugin, deps, removed } = harness(
      [[entry("hello")], [entry("pr-chain")], [entry("pr-chain")]],
      {
        "/plugins/hello/web/index.js?v=abc": module(page),
        "/plugins/pr-chain/web/index.js?v=abc": module(page),
      },
    );
    await loader.refresh();
    await loader.idle();
    expect(plugin("hello")?.status).toBe("ready");
    await loader.refresh();
    await loader.idle();
    expect(plugin("hello")).toBeUndefined();
    expect(removed).toEqual(["hello"]);
    expect(plugin("pr-chain")?.status).toBe("ready");
    // The same build again is kept as it is: no second import.
    await loader.refresh();
    await loader.idle();
    expect(deps.importModule).toHaveBeenCalledTimes(2);
  });

  it("an updated build (new hash) replaces the old contributions with the new ones", async () => {
    const { loader, plugin, deps } = harness(
      [[entry("hello")], [entry("hello", { web: { entry: "web/dist/index.js", hash: "def" } })]],
      {
        "/plugins/hello/web/index.js?v=abc": module((k) =>
          k.page({ id: "main", title: "Old", tabs: [{ id: "one", label: "One", render: Tab }] }),
        ),
        "/plugins/hello/web/index.js?v=def": module((k) =>
          k.page({ id: "main", title: "New", tabs: [{ id: "one", label: "One", render: Tab }] }),
        ),
      },
    );
    await loader.refresh();
    await loader.idle();
    await loader.refresh();
    await loader.idle();
    expect(deps.importModule).toHaveBeenCalledTimes(2);
    expect(plugin("hello")?.contributions.pages.map((item) => item.title)).toEqual(["New"]);
  });

  it("a plugin turned off while it loads never shows", async () => {
    const gate = deferred();
    const { loader, plugin } = harness([[entry("hello")], []], {
      "/plugins/hello/web/index.js?v=abc": module(async (k) => {
        page(k);
        await gate.promise;
      }),
    });
    await loader.refresh();
    await loader.refresh();
    gate.resolve();
    await loader.idle();
    expect(plugin("hello")).toBeUndefined();
  });

  it("does not load a listing that finishes after the loader stops", async () => {
    const gate = deferred<{ plugins: PluginEntry[] }>();
    const setup = vi.fn(page);
    const { loader, deps, plugin } = harness([[entry("hello")]], {
      "/plugins/hello/web/index.js?v=abc": module(setup),
    });
    deps.list.mockImplementationOnce(() => gate.promise);

    const refresh = loader.refresh();
    loader.stop();
    gate.resolve({ plugins: [entry("hello")] });
    await refresh;
    await loader.idle();

    expect(setup).not.toHaveBeenCalled();
    expect(plugin("hello")).toBeUndefined();
  });

  it("an engine notice for a loaded plugin becomes its notice; one for an unknown plugin is dropped", async () => {
    const { loader, plugin } = harness([[entry("hello")]], {
      "/plugins/hello/web/index.js?v=abc": module(page),
    });
    await loader.refresh();
    await loader.idle();
    loader.notice({ plugin_id: "hello", text: "A pull request is waiting for your review.", button: { label: "Open", open: { page: "main" } } });
    loader.notice({ plugin_id: "missing", text: "Lost" });
    loader.notice({ plugin_id: "hello", text: "" });
    expect(plugin("hello")?.contributions.notices).toEqual([
      expect.objectContaining({ text: "A pull request is waiting for your review.", button: { label: "Open", open: { page: "main" } } }),
    ]);
  });
});

describe("own React detection", () => {
  it("matches the export React assigns, React 18 and 19, not code that only reads it", () => {
    expect(bundlesOwnReact("var e={};e.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE=t;")).toBe(true);
    expect(bundlesOwnReact("t.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED=n")).toBe(true);
    expect(bundlesOwnReact('import{useState as e}from"react";export default function(){}')).toBe(false);
    expect(bundlesOwnReact("var s=r.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;s.H")).toBe(false);
    expect(bundlesOwnReact("if(r.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE===void 0)x()")).toBe(false);
  });
});

describe("PhoneLink lists plugins and loads no code", () => {
  it("fills the list, imports nothing and keeps the registry empty when loadCode is false", async () => {
    const runtimeSource = readFileSync(new URL("./loader.ts", import.meta.url), "utf8");
    const { registry, deps } = harness([[entry("hello"), entry("pr-chain")]], {});
    const phone = { ...deps, loadCode: false };
    const loader = createPluginLoader(registry, phone);
    await loader.refresh();
    await loader.idle();
    expect(deps.importModule).not.toHaveBeenCalled();
    expect(deps.fetchText).not.toHaveBeenCalled();
    expect(deps.addStyle).not.toHaveBeenCalled();
    expect(runtimeSource).toContain("loadCode: !inPhoneApp(where.pathname)");
    expect(listedPluginEntries().map((item) => item.id)).toEqual(["hello", "pr-chain"]);
    expect(registry.getSnapshot()).toMatchObject({ listed: true, plugins: [] });
  });
});

describe("fixed plugin inventory errors", () => {
  it("preserves the development manifest error", () => {
    const normalized = normalizePluginEntry(entry("hello@dev", {
      kind: "dev",
      state: "error",
      error: "Development plugin metadata could not be loaded",
    }));

    expect(normalized?.error).toBe("Development plugin metadata could not be loaded");
  });
});
