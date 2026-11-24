import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
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

afterEach(() => pluginRegistry.reset());

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
      services: [{ name: "loop", state: "crash-stopped" }],
    });
    expect(rowCount(crashed, { text: "4", hint: "4 chains running" }).text).toBe("Stopped");
    const stopping = put("hello", "Hello", {}, { state: "stopping" });
    expect(rowCount(stopping, undefined).text).toBe("Stopping");
    expect(rows()).toContain("Stopping");
    const stopFailed = put("hello", "Hello", {}, {
      state: "stop-failed",
      services: [{ name: "loop", state: "stop-failed", error: "signal failed" }],
    });
    expect(rowCount(stopFailed, undefined)).toEqual({ text: "Stop Failed", hint: "signal failed" });
    expect(rows()).toContain("Stop Failed");
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
});
