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
import { pluginsByEnableOrder } from "./plugin-order";
import { logPluginFailure, redactPluginError } from "./plugin-errors";
import * as pluginErrors from "./plugin-errors";
import { declaredIds } from "./plugin-declarations";
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
      views: [],
      pages: [{ id: "main", title: "Main" }],
      modals: [],
      slash: [],
      renderers: [],
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

const inventoryResponseCases: readonly [string, () => Promise<unknown>, boolean][] = [
  ["null", async () => null, false],
  ["undefined", async () => undefined, false],
  ["non-object", async () => "not an inventory", false],
  ["top-level array", async () => [], false],
  ["missing plugins", async () => ({ schema: 1 }), false],
  ["non-array plugins", async () => ({ plugins: null }), false],
  ["error body", async () => ({ detail: "Inventory unavailable" }), false],
  ["only invalid rows", async () => ({ plugins: [null, {}] }), false],
  ["partially invalid rows", async () => ({ plugins: [entry("replacement"), {}] }), false],
  ["thrown JSON parse", async () => { throw new SyntaxError("Invalid JSON"); }, false],
  ["non-2xx response", async () => { throw new Error("HTTP 503 Service Unavailable"); }, false],
  ["valid empty inventory", async () => ({ plugins: [] }), true],
];

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
    ["private key assignment", "private_key=private-secret"],
    ["secret key assignment", "secret-key=secret-secret"],
    ["signing key assignment", "signing_key=signing-secret"],
    ["bearer credential", "Bearer abc123XYZ987"],
    ["API key shape", "sk-live-abcdefghijklmnop"],
  ])("rejects %s", (_label, value) => {
    expect(normalizePluginEntry({ ...entry("hello"), name: value })?.name).toBe("hello");
  });

  it("redacts private-key assignments from browser plugin errors", () => {
    const secret = "private_key=private-secret";
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});

    logPluginFailure("hello", "command failed", new Error(secret));

    const output = logged.mock.calls.flat().join(" ");
    expect(output).toContain("[redacted]");
    expect(output).not.toContain("private-secret");
  });

  it("drops every URL query and fragment from logged errors and stacks", () => {
    const messageUrl = "https://store.example/object?opaque=message-secret#private-fragment";
    const stackUrl = "https://cdn.example/plugin.js?nonce=stack-secret#source-fragment";
    const error = new Error(`request failed: ${messageUrl}`);
    error.stack = `Error: request failed: ${messageUrl}\n    at render (${stackUrl})`;
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});

    logPluginFailure("hello", "renderer failed", error, `    at App (${stackUrl})`);

    const output = logged.mock.calls.flat().join(" ");
    expect(output).toContain("https://store.example/object");
    expect(output).toContain("https://cdn.example/plugin.js");
    expect(output).not.toContain("opaque=");
    expect(output).not.toContain("nonce=");
    expect(output).not.toContain("message-secret");
    expect(output).not.toContain("stack-secret");
    expect(output).not.toContain("private-fragment");
    expect(output).not.toContain("source-fragment");
  });

  it.each([
    ["POSIX", "/Users/marco/.ssh/config"],
    ["Windows", "C:\\Users\\marco\\AppData\\Roaming\\config.json"],
  ])("redacts absolute %s filesystem paths without changing relative or URL paths", (_platform, path) => {
    const error = redactPluginError(`Cannot read ${path}; also src/plugins/renderer.js and https://cdn.example/plugins/renderer.js?token=canary`);

    expect(error).not.toContain(path);
    expect(error).toContain("[path]");
    expect(error).toContain("src/plugins/renderer.js");
    expect(error).toContain("https://cdn.example/plugins/renderer.js");
    expect(error).not.toContain("canary");
  });

  it("redacts plugin and action text in the log prefix", () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});

    logPluginFailure(
      "hello token=plugin-prefix-canary",
      "listener https://service.example/events?token=action-prefix-canary",
      new Error("safe failure"),
    );

    const output = logged.mock.calls.flat().join(" ");
    expect(output).not.toContain("plugin-prefix-canary");
    expect(output).not.toContain("action-prefix-canary");
    expect(output).toContain("https://service.example/events");
  });

  it.each(["onCaughtError", "onUncaughtError", "onRecoverableError"] as const)(
    "reports non-plugin %s errors through redacted logging",
    (option) => {
      type RootErrorHandler = (error: unknown, info: { componentStack: string }) => void;
      const options = (pluginErrors as unknown as {
        REDACTED_ROOT_ERROR_OPTIONS?: Record<string, RootErrorHandler>;
      }).REDACTED_ROOT_ERROR_OPTIONS;
      const handler = options?.[option];
      expect(handler).toBeTypeOf("function");
      if (!handler) return;

      const error = new Error("unrelated failure https://app.example/path?private=value#fragment");
      error.stack = "Error: unrelated failure https://app.example/path?stack=value#source";
      const logged = vi.spyOn(console, "error").mockImplementation(() => {});

      handler(error, { componentStack: " at App (https://ui.example/view?tree=secret#component)" });

      const output = logged.mock.calls.flat().join(" ");
      expect(output).toContain("Kollab");
      expect(output).toContain("unrelated failure");
      expect(output).toContain("https://app.example/path");
      expect(output).toContain("https://ui.example/view");
      expect(output).not.toMatch(/[?#](?:private|stack|tree)=/);
      expect(output).not.toContain("#fragment");
      expect(output).not.toContain("#source");
      expect(output).not.toContain("#component");
    },
  );
});

describe("loading", () => {
  it.each([
    ["missing", undefined],
    ["tied", 1],
  ])("keeps API order through the registry's name sort when enable order is %s", async (_case, rank) => {
    const extra = rank === undefined ? { web: null } : { web: null, enable_order: rank };
    const { loader, registry } = harness([[entry("beta", extra), entry("alpha", extra)]], {});
    await loader.refresh();

    expect(pluginsByEnableOrder(registry.getSnapshot().plugins).map(({ id }) => id)).toEqual(["beta", "alpha"]);
  });

  it.each(inventoryResponseCases)("handles %s plugin inventory without losing the last good page", async (_label, nextList, isValidEmpty) => {
    const existing = entry("hello");
    const setup = harness([[existing]], { [assetUrl(existing, "entry")!]: module(page) });
    await setup.loader.refresh();
    await setup.loader.idle();
    expect(setup.plugin("hello")?.status).toBe("ready");

    setup.deps.list.mockImplementationOnce(nextList as never);
    await setup.loader.refresh();

    if (isValidEmpty) {
      expect(setup.plugin("hello")).toBeUndefined();
      expect(listedPluginEntries()).toEqual([]);
      expect(setup.registry.getSnapshot().listError).toBeNull();
      expect(setup.removed).toEqual(["hello"]);
    } else {
      expect(setup.plugin("hello")).toMatchObject({ status: "ready", contributions: { pages: [{ id: "main" }] } });
      expect(listedPluginEntries().map((item) => item.id)).toEqual(["hello"]);
      expect(setup.registry.getSnapshot().listError).toBeTruthy();
      expect(setup.removed).toEqual([]);
    }
  });

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

  it("keeps setup errors out of plugin UI and logs only redacted details", async () => {
    const secret = "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz";
    const original = new Error(secret);
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
    expect(logged).toHaveBeenCalledWith("hello web code load failed", expect.stringContaining("[redacted]"));
    expect(logged.mock.calls.flat().join(" ")).not.toContain(secret);
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

  it("re-registers contributions when declarations change without a new web hash", async () => {
    const declaration = (slash: string, tool: string) => ({
      menu: [],
      views: [],
      pages: [],
      modals: [],
      slash: [slash],
      renderers: [{ tool }],
    });
    const oldEntry = entry("hello", { contributes: declaration("publish", "shell") });
    const newEntry = entry("hello", { contributes: declaration("deploy", "git") });
    const setup: Setup = (k) => {
      k.command({ name: "publish", description: "Old", run: () => {} });
      k.command({ name: "deploy", description: "New", run: () => {} });
      k.renderer({ tool: "shell", render: Tab });
      k.renderer({ tool: "git", render: Tab });
    };
    const { loader, plugin, deps } = harness(
      [[oldEntry], [newEntry]],
      { "/plugins/hello/web/index.js?v=abc": module(setup) },
    );

    await loader.refresh();
    await loader.idle();
    expect(plugin("hello")?.contributions.commands.map(({ name }) => name)).toEqual(["publish"]);
    expect(plugin("hello")?.contributions.renderers.map((renderer) => renderer.tool)).toEqual(["shell"]);

    await loader.refresh();
    await loader.idle();

    expect(deps.importModule).toHaveBeenCalledTimes(2);
    expect(plugin("hello")?.entry.contributes.slash).toEqual(["deploy"]);
    expect(plugin("hello")?.contributions.commands.map(({ name }) => name)).toEqual(["deploy"]);
    expect(plugin("hello")?.contributions.renderers.map((renderer) => renderer.tool)).toEqual(["git"]);
  });

  it("retains the last good contribution declarations when a refresh row omits them", async () => {
    const declared = entry("hello", {
      contributes: {
        menu: [],
        views: [],
        pages: [],
        modals: [],
        slash: ["publish"],
        renderers: [{ tool: "shell" }],
      },
    });
    const partial = { ...declared } as Partial<PluginEntry>;
    delete partial.contributes;
    const { loader, plugin, deps } = harness(
      [[declared], [partial as PluginEntry]],
      {
        "/plugins/hello/web/index.js?v=abc": module((k) => {
          k.command({ name: "publish", description: "Publish", run: () => {} });
          k.renderer({ tool: "shell", render: Tab });
        }),
      },
    );

    await loader.refresh();
    await loader.idle();
    await loader.refresh();
    await loader.idle();

    expect(plugin("hello")?.entry.contributes.slash).toEqual(["publish"]);
    expect(plugin("hello")?.contributions.commands.map(({ name }) => name)).toEqual(["publish"]);
    expect(plugin("hello")?.contributions.renderers.map((renderer) => renderer.tool)).toEqual(["shell"]);
    expect(deps.importModule).toHaveBeenCalledTimes(1);
  });

  it("retains last-good declarations when the backend reports omitted contribution rows", async () => {
    const contributes: PluginEntry["contributes"] = {
      menu: [],
      views: [],
      pages: [],
      modals: [],
      slash: ["publish"],
      renderers: [{ tool: "shell" }],
    };
    const declared = entry("hello", { contributes });
    const partial = entry("hello", {
      contributes: { ...contributes, renderers: [] },
      omitted: { services: 0, uses: 0, granted: 0, settings: 0, setting_options: 0, option_labels: 0, contributions: 1 },
    });
    const { loader, plugin, deps } = harness(
      [[declared], [partial]],
      {
        "/plugins/hello/web/index.js?v=abc": module((k) => {
          k.command({ name: "publish", description: "Publish", run: () => {} });
          k.renderer({ tool: "shell", render: Tab });
        }),
      },
    );

    await loader.refresh();
    await loader.idle();
    await loader.refresh();
    await loader.idle();

    expect(plugin("hello")?.entry.contributes).toEqual(contributes);
    expect(plugin("hello")?.contributions.renderers.map(({ tool }) => tool)).toEqual(["shell"]);
    expect(deps.importModule).toHaveBeenCalledTimes(1);
  });

  it("rejects contribution rows with multiple identity fields", () => {
    const normalized = normalizePluginEntry(entry("hello", {
      contributes: {
        menu: [],
        views: [],
        pages: [],
        modals: [],
        slash: [{ id: "deploy", name: "review" }],
        renderers: [{ tool: "shell", tag: "card" }],
      },
    }));

    expect(normalized?.contributes.slash).toEqual([]);
    expect(normalized?.contributes.renderers).toEqual([]);
    expect(normalized?.omitted.contributions).toBe(2);
    expect(declaredIds([{ id: "deploy", name: "review" }, { tool: "shell", tag: "card" }])).toEqual(new Set());
  });

  it("drops duplicate contribution identities and counts them as omissions", () => {
    const contributes = {
      menu: [
        { id: "home", label: "Home", open: { page: "main" } },
        { id: "home", label: "Home again", open: { page: "main" } },
      ],
      views: [{ id: "peek" }, { id: "peek" }],
      pages: [{ id: "main" }, { id: "main" }],
      modals: [{ id: "confirm" }, { id: "confirm" }],
      slash: ["deploy", { name: "DEPLOY" }],
      renderers: [{ tool: "shell" }, { tool: "shell" }, { tag: "shell" }],
    } as PluginEntry["contributes"];
    const normalized = normalizePluginEntry(entry("hello", { contributes }));

    expect(normalized?.contributes.menu).toHaveLength(1);
    expect(normalized?.contributes.views).toHaveLength(1);
    expect(normalized?.contributes.pages).toHaveLength(1);
    expect(normalized?.contributes.modals).toHaveLength(1);
    expect(normalized?.contributes.slash).toEqual(["deploy"]);
    expect(normalized?.contributes.renderers).toEqual([{ tool: "shell" }, { tag: "shell" }]);
    expect(normalized?.omitted.contributions).toBe(6);
  });

  it("keeps documented underscores in renderer names through inventory normalization", () => {
    const normalized = normalizePluginEntry(entry("hello", {
      contributes: {
        menu: [],
        views: [],
        pages: [],
        modals: [],
        slash: [],
        renderers: [{ tool: "todo_write" }, { tag: "status_card" }],
      },
    }));

    expect(normalized?.contributes.renderers).toEqual([{ tool: "todo_write" }, { tag: "status_card" }]);
    expect(normalized?.omitted.contributions).toBe(0);
  });

  it("marks an inventory row with no contributes field incomplete", () => {
    const normalized = normalizePluginEntry({ id: "hello" });

    expect(normalized?.omitted.contributions).toBeGreaterThan(0);
  });

  it("retains the last-good declarations when rows over the limit are cut locally", () => {
    const contributes: PluginEntry["contributes"] = {
      menu: [], views: [], pages: [], modals: [], slash: ["publish"], renderers: [],
    };
    const previous = entry("hello", { contributes });
    const slash = [...Array.from({ length: 256 }, (_, index) => `cmd${index}`), "publish"];

    const normalized = normalizePluginEntry({ ...previous, contributes: { ...contributes, slash } }, previous);

    expect(normalized?.contributes).toEqual(contributes);
    expect(normalized?.omitted.contributions).toBeGreaterThan(0);
  });

  it("retains the last-good declarations when an inventory omits one contribution kind", () => {
    const contributes: PluginEntry["contributes"] = {
      menu: [],
      views: [],
      pages: [],
      modals: [],
      slash: ["publish"],
      renderers: [{ tool: "shell" }],
    };
    const previous = entry("hello", { contributes });
    const partial = {
      ...previous,
      contributes: {
        menu: [],
        views: [],
        pages: [],
        modals: [],
        slash: ["publish"],
      },
    };

    const normalized = normalizePluginEntry(partial, previous);

    expect(normalized?.contributes).toEqual(contributes);
    expect(normalized?.omitted.contributions).toBe(1);
  });

  it.each(["menu", "views", "pages", "modals", "slash", "renderers"] as const)(
    "retains last-good declarations when the %s contribution is malformed",
    (kind) => {
      const contributes: PluginEntry["contributes"] = {
        menu: [{ id: "main-menu", label: "Main", open: { page: "main" } }],
        views: ["peek"],
        pages: ["main"],
        modals: ["about"],
        slash: ["publish"],
        renderers: [{ tool: "shell" }],
      };
      const previous = entry("hello", { contributes });

      for (const malformedValue of ["not-an-array", [null]]) {
        const malformed = {
          ...previous,
          contributes: { ...contributes, [kind]: malformedValue },
        } as unknown as PluginEntry;
        const normalized = normalizePluginEntry(malformed, previous);

        expect(normalized?.contributes, `${kind}: ${JSON.stringify(malformedValue)}`).toEqual(contributes);
        expect(normalized?.omitted.contributions).toBe(1);
      }
    },
  );

  it("does not reload a plugin when malformed-present declarations follow a valid inventory row", async () => {
    const contributes: PluginEntry["contributes"] = {
      menu: [{ id: "main-menu", label: "Main", open: { page: "main" } }],
      views: ["peek"],
      pages: ["main"],
      modals: ["about"],
      slash: ["publish"],
      renderers: [{ tool: "shell" }],
    };
    const declared = entry("hello", { contributes });
    const malformed = {
      ...declared,
      contributes: { ...contributes, slash: "publish" },
    } as unknown as PluginEntry;
    const { loader, plugin, deps } = harness(
      [[declared], [malformed]],
      {
        "/plugins/hello/web/index.js?v=abc": module((k) => {
          k.command({ name: "publish", description: "Publish", run: () => {} });
          k.renderer({ tool: "shell", render: Tab });
        }),
      },
    );

    await loader.refresh();
    await loader.idle();
    await loader.refresh();
    await loader.idle();

    expect(plugin("hello")?.entry.contributes).toEqual(contributes);
    expect(plugin("hello")?.contributions.commands.map(({ name }) => name)).toEqual(["publish"]);
    expect(plugin("hello")?.contributions.renderers.map(({ tool }) => tool)).toEqual(["shell"]);
    expect(plugin("hello")?.entry.omitted.contributions).toBe(1);
    expect(deps.importModule).toHaveBeenCalledTimes(1);
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
