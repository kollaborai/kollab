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
    expect(html).toMatch(/^<div data-plugin="hello"[^>]*><p>plugin body<\/p><\/div>$/);
  });
});

describe("PluginBoundary", () => {
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
