// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import { readFileSync } from "node:fs";
import { cloneElement, type ComponentType, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Puzzle } from "lucide-react";
import { PluginBoundary, PluginFailure, PluginMount, PluginTabs, pluginIcon } from "./mount";
import { EMPTY_CONTRIBUTIONS, type PluginRecord } from "./registry";
import type { PluginApi, PluginEntry } from "./sdk";

const XSS = '"><img src=x onerror=alert(1)>';

const k = { id: "hello" } as unknown as PluginApi;

const record = (extra: Partial<PluginRecord> = {}): PluginRecord => ({
  id: "hello",
  name: "Hello",
  entry: { id: "hello", name: "Hello" } as PluginEntry,
  status: "ready",
  error: null,
  retries: 0,
  k,
  contributions: EMPTY_CONTRIBUTIONS,
  ...extra,
});

afterEach(() => vi.restoreAllMocks());

describe("PluginMount", () => {
  it("mounts plugin UI inside <div data-plugin> so its CSS can scope to it", () => {
    const html = renderToStaticMarkup(
      <PluginMount record={record()}>
        <p>plugin body</p>
      </PluginMount>,
    );
    expect(html).toMatch(
      /^<div data-plugin="hello"[^>]*><p>plugin body<\/p><div data-plugin-portal-root=""[^>]*><\/div><\/div>$/,
    );
  });
});

describe("plugin portal scope", () => {
  it.each([
    ["dialog.tsx", "dialogs"],
    ["sheet.tsx", "sheets"],
    ["select.tsx", "selects"],
    ["tooltip.tsx", "tooltips"],
  ])("mounts %s inside the plugin portal container", (file, label) => {
    const source = readFileSync(new URL("../components/ui/" + file, import.meta.url), "utf8");
    expect(source, label + " must target the active plugin mount's portal container").toMatch(/usePluginPortalScope/);
    expect(source, label + " must pass the scoped container to Radix").toContain("container={portalContainer}");
  });
});

describe("PluginBoundary", () => {
  it("gives each owner and renderer a distinct boundary identity", async () => {
    const { pluginRendererBoundaryKey } = (await import("./mount")) as unknown as {
      pluginRendererBoundaryKey: (ownerPluginId: string, renderer: string) => string;
    };
    expect(pluginRendererBoundaryKey("owner-a", "tool:deploy")).not.toBe(
      pluginRendererBoundaryKey("owner-b", "tool:deploy"),
    );
    expect(pluginRendererBoundaryKey("owner-a", "tool:deploy")).not.toBe(
      pluginRendererBoundaryKey("owner-a", "tag:deploy"),
    );
    expect(pluginRendererBoundaryKey("owner-a", "tool:deploy")).toBe(
      pluginRendererBoundaryKey("owner-a", "tool:deploy"),
    );
  });

  it("keeps a secret-bearing render error out of the expanded error DOM", () => {
    const secret = "secret-token-123";
    const error = new Error(secret);
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const boundary = new PluginBoundary({ name: "Hello", children: null });
    boundary.state = { ...boundary.state, ...PluginBoundary.getDerivedStateFromError(error) };
    boundary.componentDidCatch(error, { componentStack: "at SettingsView" });

    const failure = boundary.render() as ReactElement<{ initiallyOpen?: boolean }>;
    const html = renderToStaticMarkup(cloneElement(failure, { initiallyOpen: true }));

    expect(html).toContain("Hello Failed to Render");
    expect(html).not.toContain(secret);
    expect(html).toContain("Check the browser console for details.");
    expect(log).toHaveBeenCalledWith("Hello failed to render", error, "at SettingsView");
  });

  it("turns an error thrown while drawing into Failed to Render, not a blank app", () => {
    const state = PluginBoundary.getDerivedStateFromError(new Error("bad render"));
    expect(state).toEqual({ error: expect.any(Error) });
    const boundary = new PluginBoundary({ name: "Hello", children: null });
    boundary.state = { ...boundary.state, ...state };
    const html = renderToStaticMarkup(boundary.render());
    expect(html).toContain("Hello Failed to Render");
    expect(html).toMatch(/<button[^>]*>Retry<\/button>/);
    expect(html).toMatch(/<button[^>]*aria-expanded="false"[^>]*>View Error<\/button>/);
    expect(html).not.toContain("bad render");
  });

  it("Retry clears the error so the plugin draws again", () => {
    const boundary = new PluginBoundary({ name: "Hello", children: null });
    const setState = vi.spyOn(boundary, "setState").mockImplementation(() => {});
    boundary.state = { error: new Error("bad render") };
    boundary.retry();
    expect(setState).toHaveBeenCalledWith({ error: null });
  });

  it("clears a render error when its renderer input changes", () => {
    const boundary = new PluginBoundary({ name: "Hello", children: null });
    const setState = vi.spyOn(boundary, "setState").mockImplementation(() => {});
    boundary.state = { error: new Error("result is not ready") };
    const previousProps = { ...boundary.props, resetKey: "running" };
    const updated = boundary as unknown as {
      props: { resetKey?: unknown };
      componentDidUpdate?: (props: { resetKey?: unknown }) => void;
    };
    updated.props = { ...boundary.props, resetKey: "complete" };

    updated.componentDidUpdate?.(previousProps);

    expect(setState).toHaveBeenCalledWith({ error: null });
  });

  it("shows a supplied host fallback after a renderer fails", () => {
    const fallback = <p>native tool output</p>;
    const boundary = new PluginBoundary({ name: "Hello", children: null, errorFallback: fallback });
    boundary.state = { error: new Error("bad plugin render") };

    expect(boundary.render()).toBe(fallback);
  });

  it("logs what failed for the developer", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const boundary = new PluginBoundary({ name: "Hello", children: null });
    boundary.componentDidCatch(new Error("bad render"), { componentStack: "at Tab" });
    expect(String(error.mock.calls[0][0])).toContain("Hello failed to render");
  });
});

describe("PluginFailure", () => {
  it("is kollab-drawn: a title, Retry and View Error, the error shown as text", () => {
    const html = renderToStaticMarkup(
      <PluginFailure title="Hello Failed to Load" error={XSS} onRetry={() => {}} initiallyOpen />,
    );
    expect(html).toContain('role="alert"');
    expect(html).toContain("Hello Failed to Load");
    expect(html).toMatch(/aria-expanded="true"[^>]*>View Error<\/button>/);
    expect(html).toContain("&quot;&gt;&lt;img src=x onerror=alert(1)&gt;");
    expect(html).not.toContain("<img");
  });

  it("keeps an inline plugin failure in phrasing markup", () => {
    const InlineFailure = PluginFailure as unknown as ComponentType<{
      title?: string;
      error: string;
      onRetry: () => void;
      inline: boolean;
    }>;
    const html = renderToStaticMarkup(
      <InlineFailure title="Card Failed" error="bad render" onRetry={() => {}} inline />,
    );
    expect(html).toContain('<span role="alert"');
    expect(html).not.toContain("<div");
  });
});

describe("PluginTabs", () => {
  it("draws the plugin's tab bar with real tabs and the first tab mounted in data-plugin", () => {
    const First = ({ k: api }: { k: PluginApi }) => <p>first for {api.id}</p>;
    const Second = () => <p>second</p>;
    const html = renderToStaticMarkup(
      <PluginTabs
        record={record()}
        tabs={[
          { id: "board", label: "Board", render: First },
          { id: "prs", label: XSS, render: Second },
        ]}
      />,
    );
    expect(html).toContain('role="tablist"');
    expect(html.match(/role="tab"/g)).toHaveLength(2);
    expect(html).toContain(">Board</button>");
    expect(html).toContain("&quot;&gt;&lt;img src=x onerror=alert(1)&gt;");
    expect(html).toContain('<div data-plugin="hello"');
    expect(html).toContain("first for hello");
    expect(html).not.toContain("second");
    expect(html).not.toContain("<img");
  });
});

describe("pluginIcon", () => {
  it("maps the manifest's icon names and falls back to a puzzle piece", () => {
    expect(pluginIcon("git-pull-request")).not.toBe(Puzzle);
    expect(pluginIcon("not-an-icon")).toBe(Puzzle);
    expect(pluginIcon(undefined)).toBe(Puzzle);
  });
});
