// @vitest-environment jsdom
import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";
import { TextMessagePartProvider } from "@assistant-ui/core/react";
import type { ChatModelAdapter } from "@assistant-ui/core";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EMPTY_CONTRIBUTIONS, pluginRegistry, type PluginRecord } from "./registry";
import { PluginTaggedText } from "./renderers";
import type { PluginApi, PluginRendererProps, RendererSpec } from "./sdk";

const chatModel: ChatModelAdapter = { run: async () => ({ content: [] }) };
const pluginApi = {} as PluginApi;

function TagView({ tag }: PluginRendererProps) {
  return <span data-plugin-tag-view="">{tag?.content}</span>;
}

function registerTagRenderer() {
  const renderer: RendererSpec = { tag: "card", render: TagView };
  const record: PluginRecord = {
    id: "streaming-renderer-test",
    name: "Streaming Renderer Test",
    entry: {
      web: { entry: "web/dist/index.js", style: null, hash: "test" },
      contributes: { renderers: [{ tag: "card" }] },
    } as PluginRecord["entry"],
    status: "ready",
    error: null,
    retries: 0,
    k: pluginApi,
    contributions: { ...EMPTY_CONTRIBUTIONS, renderers: [renderer] },
  };
  pluginRegistry.put(record);
}

function RunningTextMessagePart({ text }: { text: string }) {
  const runtime = useLocalRuntime(chatModel);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <TextMessagePartProvider text={text} isRunning>
        <PluginTaggedText text={text} />
      </TextMessagePartProvider>
    </AssistantRuntimeProvider>
  );
}

afterEach(() => {
  pluginRegistry.reset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("streaming plugin tag renderer", () => {
  it("never reveals its internal placeholder host for a running text part", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const frames: FrameRequestCallback[] = [];
    let currentTime = 0;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {});
    vi.spyOn(performance, "now").mockImplementation(() => currentTime);

    registerTagRenderer();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const text = "Before <card>x</card> after.";
    const revealSteps: string[] = [];

    try {
      await act(async () => root.render(<RunningTextMessagePart text={text} />));
      expect(host.querySelector(".aui-md")?.getAttribute("data-status")).toBe("running");
      revealSteps.push(host.innerHTML);

      for (let step = 0; frames.length > 0 && step < 100; step += 1) {
        const frame = frames.shift();
        if (!frame) break;
        currentTime += 16;
        await act(async () => frame(currentTime));
        revealSteps.push(host.innerHTML);
      }

      expect(revealSteps.length).toBeGreaterThan(1);
      expect(host.querySelector("[data-plugin-tag-view]")).not.toBeNull();
      for (const [step, html] of revealSteps.entries()) {
        expect(html, `placeholder leaked at reveal step ${step}`).not.toContain(
          "plugin-tag.kollab.invalid",
        );
      }
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
