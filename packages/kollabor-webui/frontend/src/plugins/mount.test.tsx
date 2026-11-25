// @vitest-environment jsdom
// @ts-expect-error Vitest runs in Node; the app tsconfig intentionally omits Node ambient types.
import { readFileSync } from "node:fs";
import { act, cloneElement, type ComponentType, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Puzzle } from "lucide-react";
import { PluginBoundary, PluginFailure, PluginMount, PluginTabs, pluginIcon } from "./mount";
import { EMPTY_CONTRIBUTIONS, pluginRegistry, type PluginRecord } from "./registry";
import type { PluginApi, PluginEntry, PluginRendererProps, RendererSpec } from "./sdk";
import { PluginToolPart } from "./renderers";

const XSS = '"><img src=x onerror=alert(1)>';

const k = { id: "hello" } as unknown as PluginApi;

const record = (extra: Partial<PluginRecord> = {}): PluginRecord => {
  const contributions = extra.contributions ?? EMPTY_CONTRIBUTIONS;
  return {
    id: "hello",
    name: "Hello",
    entry: extra.entry ?? ({
      id: "hello",
      name: "Hello",
      web: { entry: "web/dist/index.js", style: null, hash: "test" },
      contributes: {
        renderers: contributions.renderers.map((renderer) =>
          "tool" in renderer ? { tool: renderer.tool } : { tag: renderer.tag },
        ),
      },
    } as PluginEntry),
    status: "ready",
    error: null,
    retries: 0,
    k,
    contributions,
    ...extra,
  };
};

afterEach(() => {
  pluginRegistry.reset();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

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

  it.each([
    ["tool renderer error", "tool:deploy", false, "tool-error"],
    ["tool renderer suspension", "tool:deploy", false, "tool-suspend"],
  ] as const)("shows Failed to Render with Retry for a failing or suspended tool renderer", async (_surface, renderer, inline, kind) => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const Suspends = () => { throw new Promise<void>(() => {}); };
    const Throws = () => { throw new Error("GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz"); };
    const Render = kind === "tool-error" ? Throws : Suspends;
    const element = kind.startsWith("tool") ? (
      <PluginToolPart
        part={{ toolName: "deploy", args: {}, status: "complete" }}
        fallback={<p>Native tool card</p>}
      />
    ) : (
      <PluginMount record={record()} renderer={renderer} inline={inline}>
        <Render />
      </PluginMount>
    );
    if (kind.startsWith("tool")) {
      pluginRegistry.put(record({
        contributions: {
          ...EMPTY_CONTRIBUTIONS,
          renderers: [{ tool: "deploy", render: Render }],
        },
      }));
    }

    try {
      await act(async () => root.render(element));
      if (kind.endsWith("suspend")) {
        expect(host.textContent).toContain("Rendering plugin view");
        await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
      }
      expect(host.textContent).toContain("Hello Failed to Render");
      expect(host.textContent).toContain("Retry");
      expect(host.textContent).not.toContain("Native tool card");

      await act(async () => {
        host.querySelector("button")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      if (kind.endsWith("suspend")) expect(host.textContent).toContain("Rendering plugin view");
      else expect(host.textContent).toContain("Hello Failed to Render");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });

  it("F3 keeps the first suspension deadline through resetKey updates and resets on Retry", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const never = new Promise<void>(() => {});
    const Suspends = () => { throw never; };
    const view = (resetKey: string) => (
      <PluginMount record={record()} renderer="tag:status" resetKey={resetKey}>
        <Suspends />
      </PluginMount>
    );
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    try {
      await act(async () => root.render(view("chunk-1")));
      expect(host.textContent).toContain("Rendering plugin view");
      await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
      await act(async () => root.render(view("chunk-2")));
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
      expect(host.textContent).toContain("Hello Failed to Render");

      await act(async () => root.render(view("chunk-3")));
      expect(host.textContent).toContain("Hello Failed to Render");
      await act(async () => host.querySelector("button")?.click());
      expect(host.textContent).toContain("Rendering plugin view");
      await act(async () => { await vi.advanceTimersByTimeAsync(4_999); });
      expect(host.textContent).toContain("Rendering plugin view");
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(host.textContent).toContain("Hello Failed to Render");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });

  it("F3 restarts the suspension deadline after content commits", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const never = new Promise<void>(() => {});
    const Toggle = ({ suspended }: { suspended: boolean }) => {
      if (suspended) throw never;
      return <p>ready</p>;
    };
    const view = (suspended: boolean, resetKey: string) => (
      <PluginMount record={record()} renderer="view:peek" resetKey={resetKey}>
        <Toggle suspended={suspended} />
      </PluginMount>
    );
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    try {
      await act(async () => root.render(view(true, "first")));
      await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
      await act(async () => root.render(view(false, "committed")));
      expect(host.textContent).toContain("ready");
      await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
      expect(host.textContent).toContain("ready");

      await act(async () => root.render(view(true, "second")));
      await act(async () => { await vi.advanceTimersByTimeAsync(4_999); });
      expect(host.textContent).toContain("Rendering plugin view");
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(host.textContent).toContain("Hello Failed to Render");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});

describe("PluginToolPart recovery", () => {
  it("clears a renderer failure when streamed tool args change", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.spyOn(console, "error").mockImplementation(() => {});
    const Render = ({ toolCall }: PluginRendererProps) => {
      const args = toolCall?.args as { target?: string };
      if (!args?.target) throw new Error("target is not ready");
      return <p>{args.target}</p>;
    };
    const renderer: RendererSpec = { tool: "deploy", render: Render };
    pluginRegistry.put(record({
      contributions: { ...EMPTY_CONTRIBUTIONS, renderers: [renderer] },
    }));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const part = (args: unknown) => (
      <PluginToolPart
        part={{ toolName: "deploy", args, status: "running" }}
        fallback={<p>Native tool card</p>}
      />
    );

    try {
      await act(async () => root.render(part({})));
      expect(host.textContent).toContain("Hello Failed to Render");

      await act(async () => root.render(part({ target: "ready" })));

      expect(host.textContent).toContain("ready");
      expect(host.textContent).not.toContain("Failed to Render");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
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
    expect(log).toHaveBeenCalledWith("Hello failed to render", expect.stringContaining("[redacted]"), "at SettingsView");
    expect(log.mock.calls.flat().join(" ")).not.toContain(secret);
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

  it("contains a non-Error throw whose coercion or prototype access throws", () => {
    const hostile = new Proxy(Object.create(null) as object, {
      getPrototypeOf() { throw new Error("prototype trap"); },
      get() { throw new Error("property trap"); },
    });

    expect(() => PluginBoundary.getDerivedStateFromError(Object.create(null))).not.toThrow();
    expect(PluginBoundary.getDerivedStateFromError(Object.create(null)).error.message).toBe("Unprintable plugin error");
    expect(PluginBoundary.getDerivedStateFromError(hostile).error.message).toBe("Unprintable plugin error");
  });

  it("describes renderer failures with the surface that actually failed", async () => {
    const module = (await import("./plugin-errors")) as unknown as {
      pluginRenderFailureMessage?: (renderer: string) => string;
    };
    expect(module.pluginRenderFailureMessage).toBeTypeOf("function");
    if (!module.pluginRenderFailureMessage) return;

    expect(module.pluginRenderFailureMessage("tool:deploy")).toContain("tool card renderer");
    expect(module.pluginRenderFailureMessage("tag:status")).toContain("inline tag renderer");
    expect(module.pluginRenderFailureMessage("view:peek")).toContain("chat view");
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
    expect(html).not.toContain("Turn Off");
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

  it.each(["page-tab", "modal-tab"] as const)(
    "resets a failed %s boundary when a sibling surface reuses its local tab id",
    async (rendererPrefix) => {
      vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
      vi.spyOn(console, "error").mockImplementation(() => {});
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);
      const Broken = () => { throw new Error("sibling renderer failed"); };
      const Healthy = () => <p>healthy sibling surface</p>;
      const view = (surfaceId: string, render: ComponentType<PluginRendererProps>) => (
        <PluginTabs
          {...({
            record: record(),
            rendererPrefix,
            surfaceId,
            tabs: [{ id: "overview", label: "Overview", render }],
          } as Parameters<typeof PluginTabs>[0])}
        />
      );

      try {
        await act(async () => root.render(view("first-surface", Broken)));
        expect(host.textContent).toContain("Failed to Render");

        await act(async () => root.render(view("second-surface", Healthy)));

        expect(host.textContent).toContain("healthy sibling surface");
        expect(host.textContent).not.toContain("Failed to Render");
      } finally {
        await act(async () => root.unmount());
        host.remove();
      }
    },
  );
});

describe("pluginIcon", () => {
  it("maps the manifest's icon names and falls back to a puzzle piece", () => {
    expect(pluginIcon("git-pull-request")).not.toBe(Puzzle);
    expect(pluginIcon("not-an-icon")).toBe(Puzzle);
    expect(pluginIcon(undefined)).toBe(Puzzle);
  });
});
