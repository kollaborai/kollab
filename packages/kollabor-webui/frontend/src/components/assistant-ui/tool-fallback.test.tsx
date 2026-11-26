import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ToolFallbackContent,
  ToolFallbackResult,
  ToolFallbackRoot,
  fetchHistoryOutput,
} from "./tool-fallback";
import * as toolFallback from "./tool-fallback";

const mocks = vi.hoisted(() => ({
  getHistoryOutput: vi.fn(),
  getChatStreamToolOutput: vi.fn(),
}));

vi.mock("@/api", () => ({
  CHAT_STREAM_TOOL_OUTPUT_CHUNK_SIZE: 32768,
  EngineApi: class {
    getHistoryOutput = mocks.getHistoryOutput;
    getChatStreamToolOutput = mocks.getChatStreamToolOutput;
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
) {
  return {
    kind: "chat-stream-tool-output-preview",
    session_id: sessionId,
    row_id: "row-1",
    tool_call_id: "tool-call-1",
    output_revision: outputRevision,
    output_bytes: 40_000,
    output_chars: 40_000,
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
function cardMarkup(open: boolean, result: unknown) {
  return renderToStaticMarkup(
    <ToolFallbackRoot open={open}>
      <ToolFallbackContent>
        <ToolFallbackResult result={result} />
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
    const markup = cardMarkup(true, chatStreamPreviewResult("stream-session", "first lines of output"));

    expect(markup).toContain("Output preview:");
    expect(markup).toContain("first lines of output");
    expect(markup).not.toContain("&quot;kind&quot;: &quot;chat-stream-tool-output-preview&quot;");
    expect(markup).not.toContain("output_bytes");
    expect(markup).not.toContain("tool_call_id");
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

    const markup = cardMarkup(true, chatStreamPreviewResult("stream-chunks", "first lines of output"));
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
        offset: 0, next_offset: 9, total_chars: 9, output_type: "text", content: "old result", done: true,
      })
      .mockResolvedValueOnce({
        row_id: "row-1", tool_call_id: "tool-call-1", output_revision: newRevision,
        offset: 0, next_offset: 9, total_chars: 9, output_type: "text", content: "new result", done: true,
      });

    await expect(fetchOutput("stream-revision", "row-1", "tool-call-1", oldRevision, 9, "text"))
      .resolves.toBe("old result");
    await expect(fetchOutput("stream-revision", "row-1", "tool-call-1", newRevision, 9, "text"))
      .resolves.toBe("new result");
    expect(mocks.getChatStreamToolOutput).toHaveBeenCalledTimes(2);

    const markup = cardMarkup(
      true,
      chatStreamPreviewResult("stream-revision", "preview for the new result", newRevision),
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
        offset: 0, next_offset: 11, total_chars: 11, output_type: "text", content: "current value", done: true,
      });

    const oldRequest = fetchOutput("stream-inflight", "row-1", "tool-call-1", oldRevision, 10, "text");
    void oldRequest.catch(() => undefined);
    const newRequest = fetchOutput("stream-inflight", "row-1", "tool-call-1", newRevision, 11, "text");
    await expect(newRequest).resolves.toBe("current value");
    finishOld({
      row_id: "row-1", tool_call_id: "tool-call-1", output_revision: oldRevision,
      offset: 0, next_offset: 10, total_chars: 10, output_type: "text", content: "old version", done: true,
    });
    await expect(oldRequest).resolves.toBe("old version");
    await expect(fetchOutput("stream-inflight", "row-1", "tool-call-1", newRevision, 11, "text"))
      .resolves.toBe("current value");
    expect(mocks.getChatStreamToolOutput).toHaveBeenCalledTimes(2);
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
        offset: 0, next_offset: 11, total_chars: 11, output_type: "text", content: "current value", done: true,
      });

    try {
      await act(async () => {
        root.render(<ToolFallbackResult result={chatStreamPreviewResult("stream-card", "old preview", oldRevision)} />);
      });
      await act(async () => {
        root.render(<ToolFallbackResult result={chatStreamPreviewResult("stream-card", "new preview", newRevision)} />);
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(rootElement.textContent).toContain("current value");

      await act(async () => {
        finishOld({
          row_id: "row-1", tool_call_id: "tool-call-1", output_revision: oldRevision,
          offset: 0, next_offset: 10, total_chars: 10, output_type: "text", content: "old version", done: true,
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
