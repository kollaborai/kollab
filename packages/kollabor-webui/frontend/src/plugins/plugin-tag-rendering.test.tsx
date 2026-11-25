// @vitest-environment jsdom
import { act, type ComponentProps, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { vi } from "vitest";

const primitiveText = vi.hoisted(() => ({ value: "" }));

vi.mock("@assistant-ui/react-markdown", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@assistant-ui/react-markdown")>();
  const React = await import("react");
  const ReactMarkdownModule = await import("react-markdown");

  function MarkdownTextPrimitive({
    className,
    components = {},
    preprocess,
    remarkPlugins,
  }: {
    className?: string;
    components?: import("@assistant-ui/react-markdown").MarkdownTextPrimitiveProps["components"];
    preprocess?: (text: string) => string;
    remarkPlugins?: import("react-markdown").Options["remarkPlugins"];
  }) {
    const native = { ...components } as unknown as Record<string, unknown>;
    const CodeHeader = native.CodeHeader as ComponentType<{ language: string; code: string }> | undefined;
    const NativePre = native.pre as ComponentType<ComponentProps<"pre">> | undefined;
    delete native.CodeHeader;
    delete native.SyntaxHighlighter;
    native.pre = (props: ComponentProps<"pre">) => {
      const child = React.Children.toArray(props.children).find(React.isValidElement);
      const childProps = child && React.isValidElement<{ className?: string; children?: ReactNode }>(child)
        ? child.props
        : {};
      const code = typeof childProps.children === "string" ? childProps.children : "";
      const language = /language-([^\s]+)/.exec(childProps.className ?? "")?.[1] ?? "";
      return React.createElement(
        React.Fragment,
        null,
        CodeHeader ? React.createElement(CodeHeader, { language, code }) : null,
        NativePre ? React.createElement(NativePre, props) : React.createElement("pre", props),
      );
    };
    const markdownOptions: import("react-markdown").Options = {
      children: preprocess ? preprocess(primitiveText.value) : primitiveText.value,
      components: native as unknown as import("react-markdown").Options["components"],
      remarkPlugins,
    };
    return React.createElement(
      "div",
      { className },
      React.createElement(ReactMarkdownModule.default, markdownOptions),
    );
  }

  return { ...actual, MarkdownTextPrimitive };
});

import { EMPTY_CONTRIBUTIONS, pluginRegistry, type PluginRecord } from "./registry";
import { PluginTaggedText } from "./renderers";
import type { PluginApi, PluginRendererProps, RendererSpec } from "./sdk";

const k = {} as PluginApi;

function TagView({ tag }: PluginRendererProps) {
  return <span data-plugin-tag-view="">{tag?.content}</span>;
}

function addTagRenderer(render: RendererSpec["render"] = TagView) {
  const renderer: RendererSpec = { tag: "card", render };
  const record: PluginRecord = {
    id: "renderer-test",
    name: "Renderer Test",
    entry: { contributes: { renderers: [{ tag: "card" }] } } as PluginRecord["entry"],
    status: "ready",
    error: null,
    retries: 0,
    k,
    contributions: { ...EMPTY_CONTRIBUTIONS, renderers: [renderer] },
  };
  pluginRegistry.put(record);
}

function render(text: string) {
  primitiveText.value = text;
  addTagRenderer();
  return renderToStaticMarkup(<PluginTaggedText text={text} />);
}

beforeEach(() => pluginRegistry.reset());
afterEach(() => {
  pluginRegistry.reset();
  primitiveText.value = "";
});

describe("plugin tag message markdown", () => {
  it("keeps Markdown syntax continuous across an inline plugin view", () => {
    const html = render("**before <card>x</card> after**");
    const start = html.indexOf("<strong");
    const end = html.indexOf("</strong>", start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    expect(html.slice(start, end)).toContain("data-plugin-tag-view");
    expect(html.slice(start, end)).toContain("after");
    const paragraph = html.match(/<p[^>]*>[\s\S]*?<\/p>/)?.[0] ?? "";
    expect(paragraph).toContain('<span data-plugin="renderer-test"');
    expect(paragraph).not.toContain('<div data-plugin="renderer-test"');
  });

  it("keeps inline code inline around a plugin tag", () => {
    const html = render("use `npm test` here, then <card>x</card>");
    const paragraph = html.match(/<p[^>]*>[\s\S]*?<\/p>/)?.[0] ?? "";

    expect(paragraph).toMatch(/use <code[^>]*aui-md-inline-code[^>]*>npm test<\/code> here, then/);
    expect(paragraph).toContain("data-plugin-tag-view");
    expect(paragraph).not.toContain("<pre");
  });

  it("keeps native Copy headers on code blocks around a plugin view", () => {
    const html = render("```ts\nconst before = 1;\n```\n\n<card>x</card>\n\n```ts\nconst after = 2;\n```");

    expect(html).toContain("data-plugin-tag-view");
    expect((html.match(/aui-code-header-root/g) ?? []).length).toBe(2);
    expect((html.match(/>Copy</g) ?? []).length).toBe(2);
  });

  it("contains a hostile non-Error throw through the real inline tag renderer", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.spyOn(console, "error").mockImplementation(() => {});
    const Throws = (() => { throw Object.create(null); }) as ComponentType<PluginRendererProps>;
    const renderer = Throws as RendererSpec["render"];
    primitiveText.value = "<card>x</card>";
    addTagRenderer(renderer);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    try {
      await act(async () => root.render(<PluginTaggedText text="<card>x</card>" />));
      expect(host.textContent).toContain("Renderer Test Failed to Render");
      const viewError = [...host.querySelectorAll("button")].find((button) => button.textContent === "View Error");
      expect(viewError).toBeDefined();
      await act(async () => viewError?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
      expect(host.textContent).toContain("inline tag renderer");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
