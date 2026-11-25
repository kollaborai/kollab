// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SidebarProvider } from "@/components/ui/sidebar";
import { EMPTY_CONTRIBUTIONS, pluginRegistry, pluginViewKey, type Contributions, type PluginRecord } from "./registry";
import type { PluginApi, PluginEntry } from "./sdk";
import {
  PluginMenuRows,
  PluginModalHost,
  PluginNotices,
  PluginPage,
  PluginViewHost,
  pluginViewAvailable,
  pluginViewGone,
  pluginViews,
  rowCount,
} from "./surfaces";

// Radix portals never mount in static rendering; the dialog parts become plain wrappers.
vi.mock("@/components/ui/dialog", async (importOriginal) => {
  const React = await import("react");
  const wrap = ({ children }: { children?: ReactNode }) => React.createElement("div", null, children);
  return {
    ...(await importOriginal<object>()),
    Dialog:({ open, children }: { open?: boolean; children?: ReactNode }) =>
      open ? React.createElement("div", { "data-dialog": "" }, children) : null,
    DialogContent: wrap,
    DialogHeader: wrap,
    DialogTitle: ({ children }: { children?: ReactNode }) => React.createElement("h2", null, children),
    DialogDescription: wrap,
  };
});

const XSS = '"><img src=x onerror=alert(1)>';
const ESCAPED = "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;";
const Body = ({ k, session }: { k: PluginApi; session?: { session_id: string; workspace: string } }) => (
  <p>
    body of {k.id}
    {session ? ` in ${session.workspace}` : ""}
  </p>
);
const Suspends = () => { throw new Promise<void>(() => {}); };

function put(id: string, name: string, extra: Partial<PluginRecord> = {}, entry: Partial<PluginEntry> = {}) {
  const record: PluginRecord = {
    id,
    name,
    entry: {
      id,
      name,
      kind: "installed",
      enabled: true,
      shadowed: false,
      state: "ok",
      error: null,
      web: { entry: "web/dist/index.js", hash: "abc" },
      contributes: { menu: [{ id, label: name, icon: "git-pull-request", open: { page: "main" } }] },
      services: [],
      omitted: { services: 0, uses: 0, granted: 0, settings: 0, setting_options: 0, option_labels: 0, contributions: 0 },
      ...entry,
    },
    status: "ready",
    error: null,
    retries: 0,
    k: { id } as unknown as PluginApi,
    contributions: EMPTY_CONTRIBUTIONS,
    ...extra,
  };
  pluginRegistry.put(record);
  return record;
}

const contributions = (extra: Partial<Contributions>): Contributions => ({ ...EMPTY_CONTRIBUTIONS, ...extra });

const rows = () => renderToStaticMarkup(<PluginMenuRows rowClassName="row" onOpen={() => {}} />);
const page = (pluginId = "hello", pageId = "main") =>
  renderToStaticMarkup(
    <SidebarProvider>
      <TooltipProvider>
        <PluginPage pluginId={pluginId} pageId={pageId} onExit={() => {}} />
      </TooltipProvider>
    </SidebarProvider>,
  );

afterEach(() => {
  pluginRegistry.reset();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("with no plugins", () => {
  it("draws nothing at all, so the app looks exactly as before", () => {
    pluginRegistry.setListed(null);
    expect(rows()).toBe("");
    expect(renderToStaticMarkup(<PluginNotices />)).toBe("");
    expect(renderToStaticMarkup(<PluginModalHost />)).toBe("");
    expect(pluginViews(pluginRegistry.getSnapshot(), true)).toEqual([]);
  });
});

describe("right-hand menu rows", () => {
  it("adds each declared row as a real button, sorted by plugin name", () => {
    put("pr-chain", "PR Chain");
    put("hello", "Hello");
    const html = rows();
    expect(html.match(/<button/g)).toHaveLength(2);
    expect(html.indexOf("Hello")).toBeLessThan(html.indexOf("PR Chain"));
    expect(html).toContain('type="button"');
    expect(html).toContain("pointer-coarse:min-h-11");
  });

  it("shows the row text as text, never as HTML", () => {
    put("hello", XSS);
    const html = rows();
    expect(html).toContain(ESCAPED);
    expect(html).not.toContain("<img");
  });

  it("kollab sets the count when it knows the plugin is unwell", () => {
    const failed = put("hello", "Hello", { status: "failed", error: "boom" });
    expect(rowCount(failed, undefined)).toEqual({ text: "Failed to Load", hint: "boom" });
    const engineError = put("hello", "Hello", {}, { state: "error", error: "engine part failed" });
    expect(rowCount(engineError, undefined).text).toBe("Failed to Load");
    const crashed = put("hello", "Hello", {}, {
      services: [{ name: "loop", state: "crash-stopped", secrets_omitted: 0 }],
    });
    expect(rowCount(crashed, { text: "4", hint: "4 chains running" }).text).toBe("Stopped");
    const stopping = put("hello", "Hello", {}, { state: "stopping" });
    expect(rowCount(stopping, undefined).text).toBe("Stopping");
    expect(rows()).toContain("Stopping");
    const stopFailed = put("hello", "Hello", {}, {
      state: "stop-failed",
      services: [{ name: "loop", state: "stop-failed", error: "signal failed", secrets_omitted: 0 }],
    });
    expect(rowCount(stopFailed, undefined)).toEqual({ text: "Failed to Stop", hint: "signal failed" });
    expect(rows()).toContain("Failed to Stop");
  });

  it("otherwise shows the plugin's own count and hover text", () => {
    const hello = put("hello", "Hello");
    expect(rowCount(hello, { text: "4 · 1 needs you", hint: "4 chains running, 1 chain needs you" })).toEqual({
      text: "4 · 1 needs you",
      hint: "4 chains running, 1 chain needs you",
    });
    expect(rowCount(hello, { text: 4, hint: ["x"] })).toEqual({ text: "", hint: "" });
    expect(rowCount(hello, undefined)).toEqual({ text: "", hint: "" });
  });

  it("a plugin that failed to load keeps its row", () => {
    put("hello", "Hello", { status: "failed", error: "boom", k: null });
    expect(rows()).toContain("Failed to Load");
  });
});

describe("plugin pages", () => {
  it("fill the main area with the plugin's title, tab bar and first tab inside data-plugin", () => {
    put("hello", "Hello", {
      contributions: contributions({
        pages: [{ id: "main", title: "PR Chain", tabs: [{ id: "board", label: "Board", render: Body }, { id: "prs", label: "Pull Requests", render: Body }] }],
      }),
    });
    pluginRegistry.setListed(null);
    const html = page();
    expect(html).toContain("<main");
    expect(html).toContain(">PR Chain</h1>");
    expect(html).toContain('role="tablist"');
    expect(html).toContain(">Pull Requests</button>");
    expect(html).toContain('<div data-plugin="hello"');
    expect(html).toContain("body of hello");
    expect(html).toContain(">Close PR Chain</span>");
  });

  it("say the plugin is off, with Back to Chat, once it is turned off", () => {
    put("hello", "Hello");
    pluginRegistry.setListed(null);
    pluginRegistry.remove("hello");
    const html = page();
    expect(html).toContain("Hello is off");
    expect(html).toMatch(/<button[^>]*>Back to Chat<\/button>/);
  });

  it("wait for the plugin list before deciding anything", () => {
    expect(page()).toContain("Loading");
    put("hello", "Hello", { status: "loading", k: null });
    pluginRegistry.setListed(null);
    expect(page()).toContain("Loading");
  });

  it("show Failed to Load with Retry for a plugin whose code failed", () => {
    put("hello", "Hello", { status: "failed", error: "boom", k: null });
    pluginRegistry.setListed(null);
    const html = page();
    expect(html).toContain("Hello Failed to Load");
    expect(html).toMatch(/<button[^>]*>Retry<\/button>/);
  });

  it("say why plugins are missing when the list could not be read", () => {
    pluginRegistry.setListed("Kollab Engine is unavailable; verify it is running.");
    expect(page()).toContain("Kollab Engine is unavailable; verify it is running.");
  });
});

describe("notices", () => {
  it("one line and one button per plugin notice, text shown as text", () => {
    put("hello", "Hello", {
      contributions: contributions({
        notices: [
          { key: 1, text: "A pull request is waiting for your review.", button: { label: "Open", open: { page: "main" } } },
          { key: 2, text: XSS },
        ],
      }),
    });
    const html = renderToStaticMarkup(<PluginNotices />);
    expect(html).toContain("A pull request is waiting for your review.");
    expect(html).toMatch(/<button[^>]*>Open<\/button>/);
    expect(html).toContain(ESCAPED);
    expect(html).not.toContain("<img");
    // Each notice can be dismissed; only the first has an action.
    expect(html.match(/aria-label="Dismiss"/g)).toHaveLength(2);
    expect(html.match(/>Open<\/button>/g)).toHaveLength(1);
  });

  it.each(["throws", "rejects"] as const)(
    "shows a fixed safe toast when a plugin notice action %s",
    async (failure) => {
      vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
      const secret = "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz";
      const toast = vi.fn();
      const run = () => {
        if (failure === "throws") throw new Error(secret);
        return Promise.reject(new Error(secret));
      };
      put("hello", "Hello", {
        k: { id: "hello", toast } as unknown as PluginApi,
        contributions: contributions({
          notices: [{ key: 3, text: "Action", button: { label: "Open", run } }],
        }),
      });
      const log = vi.spyOn(console, "error").mockImplementation(() => {});
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);
      try {
        await act(async () => root.render(<PluginNotices />));
        const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.trim() === "Open");
        expect(button).toBeDefined();
        await act(async () => {
          button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
          await new Promise((resolve) => setTimeout(resolve, 0));
        });

        expect(toast).toHaveBeenCalledWith(
          "The plugin action failed. Check the browser console for redacted details.",
        );
        expect(log.mock.calls.flat().join(" ")).toContain("[redacted]");
        expect(log.mock.calls.flat().join(" ")).not.toContain(secret);
      } finally {
        await act(async () => root.unmount());
        host.remove();
      }
    },
  );

  it("ignores a notice action rejection after its plugin unloads", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const secret = "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz";
    let rejectAction!: (reason: unknown) => void;
    const action = new Promise<void>((_resolve, reject) => { rejectAction = reject; });
    const toast = vi.fn();
    put("hello", "Hello", {
      k: { id: "hello", toast } as unknown as PluginApi,
      contributions: contributions({
        notices: [{ key: 4, text: "Action", button: { label: "Open", run: () => action } }],
      }),
    });
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(<PluginNotices />));
      const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.trim() === "Open");
      await act(async () => {
        button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await Promise.resolve();
      });
      await act(async () => { pluginRegistry.remove("hello"); });
      await act(async () => {
        rejectAction(new Error(secret));
        await action.catch(() => {});
      });

      expect(toast).not.toHaveBeenCalled();
      expect(log).not.toHaveBeenCalled();
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});

describe("main-area views", () => {
  it("add the plugin's tabs to the chat's views; a tab that needs a folder hides for a chat with none", () => {
    put("hello", "Hello", {
      contributions: contributions({
        views: [
          { id: "peek", label: "Peek", render: Body },
          { id: "files", label: "Files", render: Body, needs_folder: true },
        ],
      }),
    });
    const snapshot = pluginRegistry.getSnapshot();
    expect(pluginViews(snapshot, true).map(([key, label]) => [key, label])).toEqual([
      ["plugin:hello:peek", "Peek"],
      ["plugin:hello:files", "Files"],
    ]);
    expect(pluginViews(snapshot, false).map(([key]) => key)).toEqual(["plugin:hello:peek"]);
    expect(pluginViewAvailable(snapshot, pluginViewKey("hello", "files"), false)).toBe(false);
    expect(pluginViewAvailable(snapshot, pluginViewKey("hello", "peek"), false)).toBe(true);
  });

  it("a chat on a removed plugin's tab goes to Chat", () => {
    put("hello", "Hello", { contributions: contributions({ views: [{ id: "peek", label: "Peek", render: Body }] }) });
    const key = pluginViewKey("hello", "peek");
    expect(pluginViewAvailable(pluginRegistry.getSnapshot(), key, true)).toBe(true);
    pluginRegistry.remove("hello");
    expect(pluginViewAvailable(pluginRegistry.getSnapshot(), key, true)).toBe(false);
  });

  it("a tab that cannot come back sends the chat to Chat; one that is loading or needs a folder waits", () => {
    const key = pluginViewKey("hello", "files");
    const views = contributions({ views: [{ id: "files", label: "Files", render: Body, needs_folder: true }] });
    put("hello", "Hello", { contributions: views });
    expect(pluginViewGone(pluginRegistry.getSnapshot(), key)).toBe(false);
    put("hello", "Hello", { status: "loading", k: null, contributions: EMPTY_CONTRIBUTIONS });
    expect(pluginViewGone(pluginRegistry.getSnapshot(), key)).toBe(false);
    put("hello", "Hello", { status: "failed", k: null, contributions: EMPTY_CONTRIBUTIONS });
    expect(pluginViewGone(pluginRegistry.getSnapshot(), key)).toBe(true);
    put("hello", "Hello", { contributions: contributions({ views: [] }) });
    expect(pluginViewGone(pluginRegistry.getSnapshot(), key)).toBe(true);
    pluginRegistry.remove("hello");
    expect(pluginViewGone(pluginRegistry.getSnapshot(), key)).toBe(true);
    expect(pluginViewGone(pluginRegistry.getSnapshot(), "chat")).toBe(false);
  });

  it("the view gets the open chat's session and mounts inside data-plugin", () => {
    put("hello", "Hello", { contributions: contributions({ views: [{ id: "peek", label: "Peek", render: Body }] }) });
    const html = renderToStaticMarkup(
      <PluginViewHost viewKey={pluginViewKey("hello", "peek")} session={{ session_id: "s1", workspace: "/work" }} />,
    );
    expect(html).toContain('<div data-plugin="hello"');
    expect(html).toContain("body of hello in /work");
  });

  it("contains a suspended real chat view and names that surface in its failure panel", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    put("hello", "Hello", { contributions: contributions({ views: [{ id: "peek", label: "Peek", render: Suspends }] }) });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(
        <PluginViewHost viewKey={pluginViewKey("hello", "peek")} session={{ session_id: "s1" }} />,
      ));
      expect(host.textContent).toContain("Rendering plugin view");
      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
      expect(host.textContent).toContain("Hello Failed to Render");
      const viewError = [...host.querySelectorAll("button")].find((button) => button.textContent === "View Error");
      await act(async () => viewError?.click());
      expect(host.textContent).toContain("chat view renderer");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });

  it("contains a suspended real plugin page tab and names that surface in its failure panel", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }));
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    put("hello", "Hello", { contributions: contributions({
      pages: [{ id: "main", title: "Main", tabs: [{ id: "board", label: "Board", render: Suspends }] }],
    }) });
    pluginRegistry.setListed(null);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(
        <SidebarProvider><TooltipProvider><PluginPage pluginId="hello" pageId="main" onExit={() => {}} /></TooltipProvider></SidebarProvider>,
      ));
      expect(host.textContent).toContain("Rendering plugin view");
      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
      expect(host.textContent).toContain("Hello Failed to Render");
      const viewError = [...host.querySelectorAll("button")].find((button) => button.textContent === "View Error");
      await act(async () => viewError?.click());
      expect(host.textContent).toContain("page tab renderer");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});

describe("modals", () => {
  it("open with the plugin's title and tabs, and draw nothing when closed", () => {
    put("hello", "Hello", {
      contributions: contributions({
        modals: [{ id: "about", title: "About Hello", tabs: [{ id: "one", label: "One", render: Body }] }],
      }),
    });
    expect(renderToStaticMarkup(<PluginModalHost />)).toBe("");
    pluginRegistry.openModal("hello", "about");
    const html = renderToStaticMarkup(<PluginModalHost />);
    expect(html).toContain("About Hello");
    expect(html).toContain('role="tablist"');
    expect(html).toContain('<div data-plugin="hello"');
  });

  it("contains a suspended real plugin modal tab and names that surface in its failure panel", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    put("hello", "Hello", { contributions: contributions({
      modals: [{ id: "about", title: "About Hello", tabs: [{ id: "one", label: "One", render: Suspends }] }],
    }) });
    pluginRegistry.openModal("hello", "about");
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(<PluginModalHost />));
      expect(host.textContent).toContain("Rendering plugin view");
      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
      expect(host.textContent).toContain("Hello Failed to Render");
      const viewError = [...host.querySelectorAll("button")].find((button) => button.textContent === "View Error");
      await act(async () => viewError?.click());
      expect(host.textContent).toContain("modal tab renderer");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
