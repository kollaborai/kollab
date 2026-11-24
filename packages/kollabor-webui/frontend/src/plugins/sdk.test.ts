import { afterEach, describe, expect, it, vi } from "vitest";
import { EMPTY_CONTRIBUTIONS, type Contributions } from "./registry";
import { createPluginApi, definePlugin, remoteView, type PluginApiDeps, type PluginEntry } from "./sdk";

const Tab = () => null;

const entry = (id = "hello", extra: Partial<PluginEntry> = {}): PluginEntry => ({
  id,
  name: "Hello",
  kind: "installed",
  enabled: true,
  shadowed: false,
  state: "ok",
  error: null,
  web: { entry: "web/dist/index.js", style: null, hash: "abc" },
  contributes: {
    menu: [{ id: "hello", label: "Hello", icon: "hand", open: { page: "main" } }],
    pages: [{ id: "main", title: "Hello" }],
    views: [{ id: "peek", label: "Peek" }],
    modals: ["about"],
    slash: ["hello"],
    renderers: ["hello_tool"],
  },
  services: [],
  ...extra,
});

function harness(id = "hello", declaredSlash = ["hello"]) {
  let contributions: Contributions = EMPTY_CONTRIBUTIONS;
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
  const deps = {
    request: vi.fn(async (_path: string, _init?: RequestInit) => json({ ok: true })),
    stream: vi.fn(
      async (_path: string, _signal: AbortSignal, _onEvent: (event: unknown) => void, _onOpen?: () => void) =>
        new Promise<void>(() => {}),
    ),
    contribute: vi.fn((change: (current: Contributions) => Contributions) => {
      contributions = change(contributions);
    }),
    open: vi.fn(),
    openSession: vi.fn(),
  } satisfies PluginApiDeps;
  const pluginEntry = entry(id);
  pluginEntry.contributes.slash = declaredSlash;
  const { k, dispose } = createPluginApi(pluginEntry, deps);
  return { k, dispose, deps, current: () => contributions };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("definePlugin", () => {
  it("returns the plugin function unchanged", () => {
    const setup = () => {};
    expect(definePlugin(setup)).toBe(setup);
  });
});

describe("contribution points", () => {
  it("bind to the ids the manifest declares", () => {
    const { k, current } = harness();
    k.menu("hello", { count: { path: "/badge", refreshOn: "chain" } });
    k.page({ id: "main", title: "Hello", tabs: [{ id: "one", label: "One", render: Tab }] });
    k.view({ id: "peek", label: "Peek", render: Tab });
    k.modal({ id: "about", title: "About", tabs: [{ id: "one", label: "One", render: Tab }] });
    k.command({ name: "hello", description: "Say hello", run: () => {} });
    k.renderer({ tool: "hello_tool", render: Tab });
    k.settings({ render: Tab });
    const result = current();
    expect(result.menus.hello).toEqual({ count: { path: "/badge", refreshOn: "chain" } });
    expect(result.pages.map((page) => page.id)).toEqual(["main"]);
    expect(result.views.map((view) => view.id)).toEqual(["peek"]);
    expect(result.modals.map((modal) => modal.id)).toEqual(["about"]);
    expect(result.commands.map((command) => command.name)).toEqual(["hello"]);
    expect(result.renderers.map((renderer) => renderer.tool)).toEqual(["hello_tool"]);
    expect(result.settings?.render).toBe(Tab);
  });

  it("ignores an undeclared id with a warning instead of adding it", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { k, current } = harness();
    k.menu("other-row", {});
    k.page({ id: "secret", title: "Secret", tabs: [{ id: "one", label: "One", render: Tab }] });
    k.view({ id: "nope", label: "Nope", render: Tab });
    k.modal({ id: "nope", title: "Nope", tabs: [{ id: "one", label: "One", render: Tab }] });
    k.command({ name: "deploy", description: "", run: () => {} });
    k.renderer({ tag: "nope", render: Tab });
    expect(current()).toEqual(EMPTY_CONTRIBUTIONS);
    expect(warn).toHaveBeenCalledTimes(6);
    expect(String(warn.mock.calls[1][0])).toContain("hello did not declare page secret");
  });

  it("rejects command names that are not one slash token with a warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const names = ["two words", "/leading", "nested/name"];
    const { k, current } = harness("hello", names);
    for (const name of names) k.command({ name, description: "", run: () => {} });
    expect(current()).toEqual(EMPTY_CONTRIBUTIONS);
    expect(warn).toHaveBeenCalledTimes(names.length);
    for (const [message] of warn.mock.calls) {
      expect(String(message)).toContain("command ignored: name must be a single slash token");
    }
  });

  it("ignores malformed specs instead of storing something that cannot draw", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { k, current } = harness();
    k.page({ id: "main", title: "Hello", tabs: [] });
    k.page({ id: "main", title: "Hello", tabs: [{ id: "one", label: "One", render: "x" as never }] });
    k.view({ id: "peek", label: "", render: Tab });
    k.notice({ text: "" });
    k.notice({ text: "two\nlines" });
    expect(current()).toEqual(EMPTY_CONTRIBUTIONS);
    expect(warn).toHaveBeenCalledTimes(5);
  });

  it("a second registration of the same id replaces the first", () => {
    const { k, current } = harness();
    k.page({ id: "main", title: "Old", tabs: [{ id: "one", label: "One", render: Tab }] });
    k.page({ id: "main", title: "New", tabs: [{ id: "one", label: "One", render: Tab }] });
    expect(current().pages.map((page) => page.title)).toEqual(["New"]);
  });

  it("notice adds one line with one button and returns its dismissal", () => {
    const { k, current } = harness();
    const dismiss = k.notice({ text: "A pull request is waiting for your review.", button: { label: "Open", open: { page: "main" } } });
    expect(current().notices).toEqual([
      expect.objectContaining({ text: "A pull request is waiting for your review.", button: { label: "Open", open: { page: "main" } } }),
    ]);
    dismiss();
    expect(current().notices).toEqual([]);
  });

  it("toast is a notice that leaves on its own", () => {
    vi.useFakeTimers();
    try {
      const { k, current } = harness();
      k.toast("Copied");
      expect(current().notices.map((notice) => notice.text)).toEqual(["Copied"]);
      vi.advanceTimersByTime(5_000);
      expect(current().notices).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("k.api", () => {
  it("calls the plugin's own routes under /plugins/{id}/api/ through the app's engine client", async () => {
    const { k, deps } = harness();
    await expect(k.api.get("/badge")).resolves.toEqual({ ok: true });
    await k.api.post("chains/start", { pr: 970 });
    await k.api.put("/config", { threads: 2 });
    await k.api.delete("/chains/970");
    expect(deps.request.mock.calls).toEqual([
      ["/plugins/hello/api/badge", { method: "GET" }],
      ["/plugins/hello/api/chains/start", { method: "POST", body: JSON.stringify({ pr: 970 }) }],
      ["/plugins/hello/api/config", { method: "PUT", body: JSON.stringify({ threads: 2 }) }],
      ["/plugins/hello/api/chains/970", { method: "DELETE" }],
    ]);
  });

  it("keeps a development plugin's @dev id in the path", async () => {
    const { k, deps } = harness("hello@dev");
    await k.api.get("badge");
    expect(deps.request).toHaveBeenCalledWith("/plugins/hello@dev/api/badge", { method: "GET" });
  });

  it("refuses a path that climbs out of the plugin's routes", async () => {
    const { k, deps } = harness();
    await expect(k.api.get("../../sessions")).rejects.toThrow("stays under");
    expect(deps.request).not.toHaveBeenCalled();
  });

  it("returns text for a response that is not JSON", async () => {
    const { k, deps } = harness();
    deps.request.mockResolvedValueOnce(new Response("plain", { headers: { "content-type": "text/plain" } }));
    await expect(k.api.get("/log")).resolves.toBe("plain");
  });

  it("reads and saves its settings through the plugin settings routes", async () => {
    const { k, deps } = harness();
    await k.settings.get();
    await k.settings.save({ "loop.threads": 2 });
    expect(deps.request.mock.calls).toEqual([
      ["/plugins/hello/settings", { method: "GET" }],
      ["/plugins/hello/settings", { method: "PUT", body: JSON.stringify({ "loop.threads": 2 }) }],
    ]);
  });
});

describe("k.events", () => {
  it("shares one stream per plugin, delivers only the named topic and stops with the last listener", async () => {
    const { k, deps } = harness();
    const chain = vi.fn();
    const other = vi.fn();
    const stopChain = k.events.on("chain", chain);
    const stopOther = k.events.on("other", other);
    expect(deps.stream).toHaveBeenCalledTimes(1);
    const [path, signal, onEvent] = deps.stream.mock.calls[0];
    expect(path).toBe("/plugins/hello/events");
    onEvent({ topic: "chain", data: { pr: 970 }, ts: 1 });
    expect(chain).toHaveBeenCalledWith({ pr: 970 });
    expect(other).not.toHaveBeenCalled();
    stopChain();
    expect(signal.aborted).toBe(false);
    stopOther();
    expect(signal.aborted).toBe(true);
  });

  it("a listener that throws does not stop the others", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const { k, deps } = harness();
    const second = vi.fn();
    k.events.on("chain", () => {
      throw new Error("plugin bug");
    });
    k.events.on("chain", second);
    deps.stream.mock.calls[0][2]({ topic: "chain", data: 1 });
    expect(second).toHaveBeenCalledWith(1);
    expect(error).toHaveBeenCalled();
  });

  it("after dispose its routes and settings refuse without sending anything", async () => {
    const { k, deps, dispose } = harness();
    dispose();
    await expect(k.api.get("/badge")).rejects.toThrow("hello no longer runs on this page");
    await expect(k.api.post("/x", {})).rejects.toThrow("no longer");
    await expect(k.settings.get()).rejects.toThrow("no longer");
    await expect(k.settings.save({})).rejects.toThrow("no longer");
    expect(deps.request).not.toHaveBeenCalled();
  });

  it("after dispose it cannot write the clipboard", async () => {
    const writeText = vi.fn(async (_text: string) => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    try {
      const { k, dispose } = harness();
      await expect(k.copy("before")).resolves.toBe(true);
      dispose();
      await expect(k.copy("after")).resolves.toBe(false);
      expect(writeText.mock.calls).toEqual([["before"]]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("dispose closes the stream and turns every later call into a no-op", () => {
    const { k, deps, dispose, current } = harness();
    k.events.on("chain", () => {});
    const signal = deps.stream.mock.calls[0][1];
    dispose();
    expect(signal.aborted).toBe(true);
    k.notice({ text: "Late" });
    k.page({ id: "main", title: "Late", tabs: [{ id: "one", label: "One", render: Tab }] });
    expect(current()).toEqual(EMPTY_CONTRIBUTIONS);
  });
});

describe("navigation and helpers", () => {
  it("navigate, agents.open and copy go through the app", async () => {
    const { k, deps } = harness();
    k.navigate({ page: "main" });
    k.agents.open("session-1");
    expect(deps.open).toHaveBeenCalledWith({ page: "main" });
    expect(deps.openSession).toHaveBeenCalledWith("session-1");
    await expect(k.copy("text")).resolves.toBe(false);
  });

  it("k.ui is the app's own components", async () => {
    const { k } = harness();
    const { Button } = await import("@/components/ui/button");
    const { Tabs } = await import("@/components/ui/tabs");
    expect(k.ui.Button).toBe(Button);
    expect(k.ui.Tabs).toBe(Tabs);
    for (const name of ["Dialog", "Sheet", "Select", "Switch", "Input", "Label", "Skeleton", "Tooltip", "HoverHelp", "ScrollArea", "FieldRenderer", "GemAvatar"]) {
      expect(k.ui, name).toHaveProperty(name);
    }
  });
});

describe("remote state", () => {
  const loaded = { key: "hello|/badge", data: { text: "4" }, error: null, loading: false };

  it("shows what was read for the same plugin and path", () => {
    expect(remoteView(loaded, "hello|/badge")).toEqual({ data: { text: "4" }, error: null, loading: false });
  });

  it("never shows another path's data: an update that drops or moves the count starts empty", () => {
    expect(remoteView(loaded, null)).toEqual({ data: undefined, error: null, loading: false });
    expect(remoteView(loaded, "hello|/count")).toEqual({ data: undefined, error: null, loading: true });
    expect(remoteView({ ...loaded, error: "boom" }, "hello@dev|/badge")).toEqual({ data: undefined, error: null, loading: true });
  });
});
