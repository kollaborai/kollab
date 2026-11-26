import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
// @ts-expect-error jsdom is a test runtime dependency without bundled declarations.
import { JSDOM } from "jsdom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ToolFallback,
  ToolFallbackContent,
  ToolFallbackArgs,
  ToolFallbackResult,
  ToolFallbackRoot,
  fetchHistoryOutput,
  toolErrorLine,
} from "./tool-fallback";
import * as toolFallback from "./tool-fallback";
import type { ChatStreamToolOutputPreviewMarker } from "../../chat-stream";
import {
  ChatStreamStreamingContent,
  ChatStreamContentPreview,
  fetchChatStreamContent,
  type ChatStreamContentPreviewMarker,
} from "../../chat-stream-content";

const mocks = vi.hoisted(() => ({
  getHistoryOutput: vi.fn(),
  getChatStreamToolOutput: vi.fn(),
  getChatStreamContent: vi.fn(),
}));

vi.mock("@/api", () => ({
  CHAT_STREAM_TOOL_OUTPUT_CHUNK_SIZE: 32768,
  EngineApi: class {
    getHistoryOutput = mocks.getHistoryOutput;
    getChatStreamToolOutput = mocks.getChatStreamToolOutput;
    getChatStreamContent = mocks.getChatStreamContent;
  },
}));

const errorMessage = "Full tool output could not be loaded. Check the connection and retry.";

function previewResult(sessionId: string, preview: string) {
  return {
    kind: "history-output-preview",
    sessionId,
    reference: "opaque-output-ref",
    preview,
  };
}

function chatStreamPreviewResult(
  sessionId: string,
  preview: string,
  outputRevision = "a".repeat(64),
  outputChars = 40_000,
): ChatStreamToolOutputPreviewMarker {
  return {
    kind: "chat-stream-tool-output-preview",
    session_id: sessionId,
    row_id: "row-1",
    tool_call_id: "tool-call-1",
    output_revision: outputRevision,
    output_bytes: outputChars,
    output_chars: outputChars,
    output_type: "text",
    preview,
  };
}

function fetchChatStreamToolOutput() {
  return (toolFallback as unknown as {
    fetchChatStreamToolOutput?: (
      sessionId: string,
      rowId: string,
      toolCallId: string,
      outputRevision: string,
      outputChars: number,
      outputType: "text" | "json",
    ) => Promise<string>;
  }).fetchChatStreamToolOutput;
}

/** The card the thread renders for a paged tool output: closed, it mounts nothing. */
function cardMarkup(open: boolean, result: unknown, resultPreview?: ReturnType<typeof chatStreamPreviewResult>) {
  return renderToStaticMarkup(
    <ToolFallbackRoot open={open}>
      <ToolFallbackContent>
        <ToolFallbackResult result={result} resultPreview={resultPreview} />
      </ToolFallbackContent>
    </ToolFallbackRoot>,
  );
}

describe("expanding a tool loads its full output", () => {
  beforeEach(() => {
    mocks.getHistoryOutput.mockReset();
  });

  it("keeps a closed card from requesting the full output", () => {
    const markup = cardMarkup(false, previewResult("session-closed", "preview lines of a long output"));

    expect(markup).not.toContain("history-output-preview");
    expect(markup).not.toContain("preview lines of a long output");
    expect(mocks.getHistoryOutput).not.toHaveBeenCalled();
  });

  it("shows the full output under Output: after one getHistoryOutput call", async () => {
    mocks.getHistoryOutput.mockResolvedValueOnce({
      session_id: "session-open",
      content: "the complete long tool output",
    });

    const before = cardMarkup(true, previewResult("session-open", "preview lines of a long output"));
    expect(before).toContain("Output preview:");
    expect(before).toContain("preview lines of a long output");
    expect(before).not.toContain("Show more");

    // Expanding mounts the card content; its load path runs exactly once.
    await fetchHistoryOutput("session-open", "opaque-output-ref");
    expect(mocks.getHistoryOutput).toHaveBeenCalledTimes(1);

    const after = cardMarkup(true, previewResult("session-open", "preview lines of a long output"));
    expect(after).toContain("Output:");
    expect(after).not.toContain("Output preview:");
    expect(after).toContain("the complete long tool output");
  });

  it("does not fetch again when the card is collapsed and expanded", async () => {
    mocks.getHistoryOutput.mockResolvedValueOnce({
      session_id: "session-reopen",
      content: "cached full output",
    });
    await fetchHistoryOutput("session-reopen", "opaque-output-ref");

    // Collapsed: the content unmounts and nothing is requested.
    expect(cardMarkup(false, previewResult("session-reopen", "preview lines"))).not.toContain("cached full output");
    // Expanded again: the fresh mount reads the cache instead of refetching.
    expect(cardMarkup(true, previewResult("session-reopen", "preview lines"))).toContain("cached full output");
    expect(cardMarkup(true, previewResult("session-reopen", "preview lines"))).toContain("Output:");
    expect(mocks.getHistoryOutput).toHaveBeenCalledTimes(1);
  });

  it("shows the error and a Retry button after a failed load, and Retry fetches again", async () => {
    mocks.getHistoryOutput.mockRejectedValueOnce(new Error("offline"));
    await expect(fetchHistoryOutput("session-retry", "opaque-output-ref")).rejects.toThrow("offline");

    const failed = cardMarkup(true, previewResult("session-retry", "preview lines"));
    expect(failed).toContain(errorMessage);
    expect(failed).toContain("Retry");
    expect(failed).not.toContain("Show more");
    expect(failed).toContain("Output preview:");

    // Pressing Retry runs the load again; this attempt succeeds.
    mocks.getHistoryOutput.mockResolvedValueOnce({
      session_id: "session-retry",
      content: "output after retry",
    });
    await fetchHistoryOutput("session-retry", "opaque-output-ref");
    expect(mocks.getHistoryOutput).toHaveBeenCalledTimes(2);

    const recovered = cardMarkup(true, previewResult("session-retry", "preview lines"));
    expect(recovered).toContain("Output:");
    expect(recovered).toContain("output after retry");
    expect(recovered).not.toContain(errorMessage);
  });
});

describe("collapsed failed tool previews", () => {
  it("shows the first useful line from a bounded output marker", () => {
    expect(toolErrorLine(chatStreamPreviewResult(
      "session-error-preview",
      "Error: permission denied while reading file\nremaining stack details",
    ))).toBe("permission denied while reading file");
  });

  it("keeps the failure indicator when projection externalizes the result", () => {
    const resultPreview = chatStreamPreviewResult(
      "session-failed-preview",
      "Error: permission denied while reading file",
    );
    const markup = renderToStaticMarkup(
      <ToolFallback
        toolCallId="tool-call-1"
        toolName="read_file"
        args={{}}
        argsText="{}"
        providerMetadata={{ kollab: { chat_stream_result_preview: resultPreview } }}
        result={undefined}
        isError={true}
        status={{ type: "complete" }}
        addResult={() => undefined}
        resume={() => undefined}
        respondToApproval={() => undefined}
      />,
    );

    expect(markup).toContain('data-slot="tool-fallback-trigger-error"');
    expect(markup).toContain("permission denied while reading file");
  });
});

describe("live streamed text previews", () => {
  it("hydrates a joining snapshot, keeps later deltas visible, and loads the final revision", async () => {
    const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>");
    const rootElement = dom.window.document.getElementById("root");
    if (!rootElement) throw new Error("test root element is missing");
    vi.stubGlobal("window", dom.window);
    vi.stubGlobal("document", dom.window.document);
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const root = createRoot(rootElement);
    const snapshotText = `snapshot body ${"older content ".repeat(100)}`;
    const tail = "delta arrived after snapshot";
    const finalText = snapshotText + tail;
    const snapshotMarker: ChatStreamContentPreviewMarker = {
      kind: "chat-stream-content-preview",
      row_id: "row-dom-live",
      session_id: "session-dom-live",
      part_index: 0,
      field: "text",
      output_type: "text",
      output_revision: "c".repeat(64),
      output_chars: Array.from(snapshotText).length,
      output_bytes: new TextEncoder().encode(snapshotText).length,
      preview: snapshotText.slice(0, 1024),
    };
    const finalMarker: ChatStreamContentPreviewMarker = {
      ...snapshotMarker,
      output_revision: "d".repeat(64),
      output_chars: Array.from(finalText).length,
      output_bytes: new TextEncoder().encode(finalText).length,
    };
    mocks.getChatStreamContent
      .mockResolvedValueOnce({
        row_id: snapshotMarker.row_id, part_index: 0, field: "text",
        output_revision: snapshotMarker.output_revision, offset: 0,
        next_offset: snapshotMarker.output_chars, total_chars: snapshotMarker.output_chars,
        output_type: "text", content: snapshotText, done: true,
      })
      .mockResolvedValueOnce({
        row_id: finalMarker.row_id, part_index: 0, field: "text",
        output_revision: finalMarker.output_revision, offset: 0,
        next_offset: finalMarker.output_chars, total_chars: finalMarker.output_chars,
        output_type: "text", content: finalText, done: true,
      });

    const render = (marker: ChatStreamContentPreviewMarker, text: string) => (
      <ChatStreamStreamingContent marker={marker} text={text}>
        {(content) => <span data-testid="streamed-text">{content}</span>}
      </ChatStreamStreamingContent>
    );
    try {
      await act(async () => {
        root.render(render(snapshotMarker, snapshotMarker.preview));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(rootElement.textContent).toContain(snapshotText);

      await act(async () => {
        root.render(render(snapshotMarker, snapshotMarker.preview + tail));
      });
      expect(rootElement.textContent).toContain(finalText);

      await act(async () => {
        root.render(render(finalMarker, finalMarker.preview));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(rootElement.textContent).toContain(finalText);
      expect(mocks.getChatStreamContent).toHaveBeenCalledTimes(2);
    } finally {
      await act(async () => root.unmount());
      vi.unstubAllGlobals();
      dom.window.close();
    }
  });
});

describe("recovering bounded chat stream content", () => {
  beforeEach(() => {
    mocks.getChatStreamContent.mockReset();
  });

  const revision = "c".repeat(64);
  const contentMarker: ChatStreamContentPreviewMarker = {
    kind: "chat-stream-content-preview",
    session_id: "session-1",
    row_id: "row-1",
    part_index: 0,
    field: "text",
    output_bytes: 40_000,
    output_chars: 3,
    output_type: "text",
    output_revision: revision,
    preview: "short",
  };

  it("fetches the exact row part in code-point chunks and assembles full text", async () => {
    mocks.getChatStreamContent
      .mockResolvedValueOnce({
        row_id: "row-1", part_index: 0, field: "text", output_revision: revision,
        offset: 0, next_offset: 2, total_chars: 3, output_type: "text", content: "🦊a", done: false,
      })
      .mockResolvedValueOnce({
        row_id: "row-1", part_index: 0, field: "text", output_revision: revision,
        offset: 2, next_offset: 3, total_chars: 3, output_type: "text", content: "b", done: true,
      });

    await expect(fetchChatStreamContent({ ...contentMarker, output_bytes: 6 })).resolves.toBe("🦊ab");
    expect(mocks.getChatStreamContent).toHaveBeenNthCalledWith(
      1, "session-1", "row-1", 0, "text", revision, { offset: 0, limit: 32768 },
    );
    expect(mocks.getChatStreamContent).toHaveBeenNthCalledWith(
      2, "session-1", "row-1", 0, "text", revision, { offset: 2, limit: 32768 },
    );
  });

  it("rejects a revision change rather than mixing text from two row versions", async () => {
    mocks.getChatStreamContent
      .mockResolvedValueOnce({
        row_id: "row-mixed", part_index: 0, field: "text", output_revision: revision,
        offset: 0, next_offset: 2, total_chars: 4, output_type: "text", content: "ab", done: false,
      })
      .mockResolvedValueOnce({
        row_id: "row-mixed", part_index: 0, field: "text", output_revision: "d".repeat(64),
        offset: 2, next_offset: 4, total_chars: 4, output_type: "text", content: "cd", done: true,
      });

    await expect(fetchChatStreamContent({ ...contentMarker, row_id: "row-mixed", output_chars: 4 })).rejects.toThrow(
      "Chat stream content chunk does not match the requested part.",
    );
  });

  it("replaces the clipped text part with recovered content when mounted", async () => {
    const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>");
    const rootElement = dom.window.document.getElementById("root");
    if (!rootElement) throw new Error("test root element is missing");
    vi.stubGlobal("window", dom.window);
    vi.stubGlobal("document", dom.window.document);
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const root = createRoot(rootElement);
    mocks.getChatStreamContent.mockResolvedValueOnce({
      row_id: "row-complete", part_index: 0, field: "text", output_revision: revision,
        offset: 0, next_offset: 18, total_chars: 18, output_type: "text", content: "complete long text", done: true,
    });

    try {
      await act(async () => {
        root.render(
          <ChatStreamContentPreview marker={{ ...contentMarker, row_id: "row-complete", output_chars: 18 }}>
            {(text) => <span data-testid="recovered-text">{text}</span>}
          </ChatStreamContentPreview>,
        );
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(rootElement.textContent).toContain("complete long text");
      expect(rootElement.textContent).not.toContain("short");
    } finally {
      await act(async () => root.unmount());
      vi.unstubAllGlobals();
      dom.window.close();
    }
  });

  it("renders and recovers oversized tool arguments when the call is expanded", async () => {
    const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>");
    const rootElement = dom.window.document.getElementById("root");
    if (!rootElement) throw new Error("test root element is missing");
    vi.stubGlobal("window", dom.window);
    vi.stubGlobal("document", dom.window.document);
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const root = createRoot(rootElement);
    const argsRevision = "e".repeat(64);
    const argsMarker: ChatStreamContentPreviewMarker = {
      ...contentMarker,
      row_id: "row-args",
      part_index: 1,
      field: "args",
      output_type: "json",
      output_revision: argsRevision,
      output_chars: 28,
      preview: '{"command":"echo clipped"}',
    };
    mocks.getChatStreamContent.mockResolvedValueOnce({
      row_id: "row-args", part_index: 1, field: "args", output_revision: argsRevision,
      offset: 0, next_offset: 28, total_chars: 28, output_type: "json",
      content: '{"command":"echo recovered"}', done: true,
    });

    try {
      await act(async () => {
        root.render(<ToolFallbackArgs argsText="" preview={argsMarker} />);
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(rootElement.textContent).toContain("echo recovered");
      expect(rootElement.textContent).not.toContain("echo clipped");
      expect(mocks.getChatStreamContent).toHaveBeenCalledWith(
        "session-1", "row-args", 1, "args", argsRevision, { offset: 0, limit: 32768 },
      );
    } finally {
      await act(async () => root.unmount());
      vi.unstubAllGlobals();
      dom.window.close();
    }
  });
});

describe("history output cache", () => {
  beforeEach(() => {
    mocks.getHistoryOutput.mockReset();
  });

  it("shares one request between two cards loading the same output", async () => {
    let finish: (value: { session_id: string; content: string }) => void = () => {};
    mocks.getHistoryOutput.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    const first = fetchHistoryOutput("session-shared", "opaque-output-ref");
    const second = fetchHistoryOutput("session-shared", "opaque-output-ref");
    finish({ session_id: "session-shared", content: "one full output" });
    await expect(Promise.all([first, second])).resolves.toEqual(["one full output", "one full output"]);
    expect(mocks.getHistoryOutput).toHaveBeenCalledTimes(1);
  });

  it("keeps the 20 most recently loaded outputs and drops the oldest", async () => {
    mocks.getHistoryOutput.mockImplementation(async (sessionId: string, reference: string) => ({
      session_id: sessionId,
      content: `full ${reference}`,
    }));
    for (let i = 0; i < 21; i += 1) await fetchHistoryOutput("session-bound", `ref-${i}`);
    expect(mocks.getHistoryOutput).toHaveBeenCalledTimes(21);
    await fetchHistoryOutput("session-bound", "ref-20");
    expect(mocks.getHistoryOutput).toHaveBeenCalledTimes(21);
    await fetchHistoryOutput("session-bound", "ref-0");
    expect(mocks.getHistoryOutput).toHaveBeenCalledTimes(22);
  });
});

describe("expanding a chat stream tool output preview", () => {
  beforeEach(() => {
    mocks.getChatStreamToolOutput.mockReset();
  });

  it("renders the preview without exposing the marker as generic tool output", () => {
    const markup = cardMarkup(true, undefined, chatStreamPreviewResult("stream-session", "first lines of output"));

    expect(markup).toContain("Output preview:");
    expect(markup).toContain("first lines of output");
    expect(markup).not.toContain("&quot;kind&quot;: &quot;chat-stream-tool-output-preview&quot;");
    expect(markup).not.toContain("output_bytes");
    expect(markup).not.toContain("tool_call_id");
    expect(mocks.getChatStreamToolOutput).not.toHaveBeenCalled();
  });

  it("leaves marker-shaped nested result data opaque", () => {
    const markerShapedUserResult = chatStreamPreviewResult("user-data", "application payload");
    const markup = cardMarkup(true, markerShapedUserResult);

    expect(markup).toContain("application payload");
    expect(markup).toContain("chat-stream-tool-output-preview");
    expect(mocks.getChatStreamToolOutput).not.toHaveBeenCalled();
  });

  it("loads every bounded chunk in order and renders the complete output", async () => {
    const fetchOutput = fetchChatStreamToolOutput();
    expect(fetchOutput).toBeTypeOf("function");
    if (!fetchOutput) return;

    mocks.getChatStreamToolOutput
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: "a".repeat(64), offset: 0,
        next_offset: 5, total_chars: 8, output_type: "text", content: "first", done: false,
      })
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: "a".repeat(64), offset: 5,
        next_offset: 8, total_chars: 8, output_type: "text", content: "end", done: true,
      });

    await expect(fetchOutput("stream-chunks", "row-1", "tool-call-1", "a".repeat(64), 8, "text"))
      .resolves.toBe("firstend");
    expect(mocks.getChatStreamToolOutput).toHaveBeenNthCalledWith(
      1, "stream-chunks", "row-1", "tool-call-1", "a".repeat(64), { offset: 0, limit: 32768 },
    );
    expect(mocks.getChatStreamToolOutput).toHaveBeenNthCalledWith(
      2, "stream-chunks", "row-1", "tool-call-1", "a".repeat(64), { offset: 5, limit: 32768 },
    );

    const markup = cardMarkup(true, undefined, chatStreamPreviewResult("stream-chunks", "first lines of output"));
    expect(markup).toContain("Output:");
    expect(markup).toContain("firstend");
    expect(markup).not.toContain("Output preview:");
  });

  it("counts chunk offsets in Unicode characters", async () => {
    const fetchOutput = fetchChatStreamToolOutput();
    expect(fetchOutput).toBeTypeOf("function");
    if (!fetchOutput) return;
    mocks.getChatStreamToolOutput
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: "a".repeat(64), offset: 0,
        next_offset: 2, total_chars: 3, output_type: "text", content: "🙂a", done: false,
      })
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: "a".repeat(64), offset: 2,
        next_offset: 3, total_chars: 3, output_type: "text", content: "é", done: true,
      });

    await expect(fetchOutput("stream-unicode", "row-1", "tool-call-1", "a".repeat(64), 3, "text"))
      .resolves.toBe("🙂aé");
    expect(mocks.getChatStreamToolOutput).toHaveBeenNthCalledWith(
      2, "stream-unicode", "row-1", "tool-call-1", "a".repeat(64), { offset: 2, limit: 32768 },
    );
  });

  it("does not reuse cached output when ACP updates a tool row in place", async () => {
    const fetchOutput = fetchChatStreamToolOutput();
    expect(fetchOutput).toBeTypeOf("function");
    if (!fetchOutput) return;
    const oldRevision = "a".repeat(64);
    const newRevision = "b".repeat(64);
    mocks.getChatStreamToolOutput
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: oldRevision,
        offset: 0, next_offset: 10, total_chars: 10, output_type: "text", content: "old result", done: true,
      })
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: newRevision,
        offset: 0, next_offset: 10, total_chars: 10, output_type: "text", content: "new result", done: true,
      });

    await expect(fetchOutput("stream-revision", "row-1", "tool-call-1", oldRevision, 10, "text"))
      .resolves.toBe("old result");
    await expect(fetchOutput("stream-revision", "row-1", "tool-call-1", newRevision, 10, "text"))
      .resolves.toBe("new result");
    expect(mocks.getChatStreamToolOutput).toHaveBeenCalledTimes(2);

    const markup = cardMarkup(
      true,
      chatStreamPreviewResult("stream-revision", "preview for the new result", newRevision, 10),
    );
    expect(markup).toContain("new result");
    expect(markup).not.toContain("old result");
  });

  it("does not share an old in-flight result with a newer revision", async () => {
    const fetchOutput = fetchChatStreamToolOutput();
    expect(fetchOutput).toBeTypeOf("function");
    if (!fetchOutput) return;
    const oldRevision = "c".repeat(64);
    const newRevision = "d".repeat(64);
    let finishOld: (chunk: unknown) => void = () => {};
    mocks.getChatStreamToolOutput
      .mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve; }))
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: newRevision,
        offset: 0, next_offset: 13, total_chars: 13, output_type: "text", content: "current value", done: true,
      });

    const oldRequest = fetchOutput("stream-inflight", "row-1", "tool-call-1", oldRevision, 11, "text");
    void oldRequest.catch(() => undefined);
    const newRequest = fetchOutput("stream-inflight", "row-1", "tool-call-1", newRevision, 13, "text");
    await expect(newRequest).resolves.toBe("current value");
    finishOld({
      row_id: "row-1", tool_call_id: "tool-call-1", output_revision: oldRevision,
      offset: 0, next_offset: 11, total_chars: 11, output_type: "text", content: "old version", done: true,
    });
    await expect(oldRequest).resolves.toBe("old version");
    await expect(fetchOutput("stream-inflight", "row-1", "tool-call-1", newRevision, 13, "text"))
      .resolves.toBe("current value");
    expect(mocks.getChatStreamToolOutput).toHaveBeenCalledTimes(2);
  });

  it("rejects a mixed revision if the tool result changes between chunks", async () => {
    const fetchOutput = fetchChatStreamToolOutput();
    expect(fetchOutput).toBeTypeOf("function");
    if (!fetchOutput) return;
    const requestedRevision = "1".repeat(64);
    const updatedRevision = "2".repeat(64);
    mocks.getChatStreamToolOutput
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: requestedRevision,
        offset: 0, next_offset: 5, total_chars: 8, output_type: "text", content: "first", done: false,
      })
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: updatedRevision,
        offset: 5, next_offset: 8, total_chars: 8, output_type: "text", content: "end", done: true,
      });

    await expect(fetchOutput("stream-mixed", "row-1", "tool-call-1", requestedRevision, 8, "text"))
      .rejects.toThrow("Tool output chunk does not match the requested result.");
    expect(mocks.getChatStreamToolOutput).toHaveBeenNthCalledWith(
      1, "stream-mixed", "row-1", "tool-call-1", requestedRevision, { offset: 0, limit: 32768 },
    );
    expect(mocks.getChatStreamToolOutput).toHaveBeenNthCalledWith(
      2, "stream-mixed", "row-1", "tool-call-1", requestedRevision, { offset: 5, limit: 32768 },
    );
  });

  it("remounts the result card when the same tool row gets a new revision", async () => {
    const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>");
    const rootElement = dom.window.document.getElementById("root");
    if (!rootElement) throw new Error("test root element is missing");
    vi.stubGlobal("window", dom.window);
    vi.stubGlobal("document", dom.window.document);
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const root = createRoot(rootElement);
    const oldRevision = "e".repeat(64);
    const newRevision = "f".repeat(64);
    let finishOld: (chunk: unknown) => void = () => {};
    mocks.getChatStreamToolOutput
      .mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve; }))
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: newRevision,
        offset: 0, next_offset: 13, total_chars: 13, output_type: "text", content: "current value", done: true,
      });

    try {
      await act(async () => {
        root.render(<ToolFallbackResult resultPreview={chatStreamPreviewResult("stream-card", "old preview", oldRevision, 11)} />);
      });
      await act(async () => {
        root.render(<ToolFallbackResult resultPreview={chatStreamPreviewResult("stream-card", "new preview", newRevision, 13)} />);
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(rootElement.textContent).toContain("current value");

      await act(async () => {
        finishOld({
          row_id: "row-1", tool_call_id: "tool-call-1", output_revision: oldRevision,
          offset: 0, next_offset: 11, total_chars: 11, output_type: "text", content: "old version", done: true,
        });
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(rootElement.textContent).toContain("current value");
      expect(rootElement.textContent).not.toContain("old version");
    } finally {
      await act(async () => root.unmount());
      vi.unstubAllGlobals();
      dom.window.close();
    }
  });

  it("rejects a chunk that belongs to a different row", async () => {
    const fetchOutput = fetchChatStreamToolOutput();
    expect(fetchOutput).toBeTypeOf("function");
    if (!fetchOutput) return;
    mocks.getChatStreamToolOutput.mockResolvedValueOnce({
      row_id: "another-row", tool_call_id: "tool-call-1", output_revision: "a".repeat(64), offset: 0,
      next_offset: 4, total_chars: 4, output_type: "text", content: "nope", done: true,
    });
    await expect(fetchOutput("stream-wrong-row", "row-1", "tool-call-1", "a".repeat(64), 4, "text"))
      .rejects.toThrow();
  });

  it("rejects a chunk that makes no progress", async () => {
    const fetchOutput = fetchChatStreamToolOutput();
    expect(fetchOutput).toBeTypeOf("function");
    if (!fetchOutput) return;
    mocks.getChatStreamToolOutput.mockResolvedValueOnce({
      row_id: "row-1", tool_call_id: "tool-call-1", output_revision: "a".repeat(64), offset: 0,
      next_offset: 0, total_chars: 4, output_type: "text", content: "", done: false,
    });
    await expect(fetchOutput("stream-no-progress", "row-1", "tool-call-1", "a".repeat(64), 4, "text"))
      .rejects.toThrow();
  });
});
