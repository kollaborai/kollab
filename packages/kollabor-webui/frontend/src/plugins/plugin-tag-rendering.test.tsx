import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const messagePartText = vi.hoisted(() => ({ value: "" }));

vi.mock("@assistant-ui/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@assistant-ui/react")>();
  return {
    ...actual,
    useMessagePartText: () => ({ text: messagePartText.value }),
  };
});

import { EMPTY_CONTRIBUTIONS, pluginRegistry, type PluginRecord } from "./registry";
import { PluginTaggedText } from "./renderers";
import type { PluginApi, PluginRendererProps, RendererSpec } from "./sdk";

const k = {} as PluginApi;

function TagView({ tag }: PluginRendererProps) {
  return <span data-plugin-tag-view="">{tag?.content}</span>;
}

function addTagRenderer() {
  const renderer: RendererSpec = { tag: "card", render: TagView };
  const record: PluginRecord = {
    id: "renderer-test",
    name: "Renderer Test",
    entry: {} as PluginRecord["entry"],
    status: "ready",
    error: null,
    retries: 0,
    k,
    contributions: { ...EMPTY_CONTRIBUTIONS, renderers: [renderer] },
  };
  pluginRegistry.put(record);
}

function render(text: string) {
  messagePartText.value = text;
  addTagRenderer();
  return renderToStaticMarkup(<PluginTaggedText text={text} />);
}

beforeEach(() => pluginRegistry.reset());
afterEach(() => pluginRegistry.reset());

describe("plugin tag message markdown", () => {
  it("keeps Markdown syntax continuous across an inline plugin view", () => {
    const html = render("**before <card>x</card> after**");
    const start = html.indexOf("<strong");
    const end = html.indexOf("</strong>", start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    expect(html.slice(start, end)).toContain("data-plugin-tag-view");
    expect(html.slice(start, end)).toContain("after");
  });

  it("keeps native Copy headers on code blocks around a plugin view", () => {
    const html = render("```ts\nconst before = 1;\n```\n\n<card>x</card>\n\n```ts\nconst after = 2;\n```");

    expect((html.match(/aui-code-header-root/g) ?? []).length).toBe(2);
    expect((html.match(/>Copy</g) ?? []).length).toBe(2);
  });
});
