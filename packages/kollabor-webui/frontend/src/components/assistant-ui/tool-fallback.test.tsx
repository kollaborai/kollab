import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ToolFallbackContent,
  ToolFallbackResult,
  ToolFallbackRoot,
  fetchHistoryOutput,
} from "./tool-fallback";

const mocks = vi.hoisted(() => ({ getHistoryOutput: vi.fn() }));

vi.mock("@/api", () => ({
  EngineApi: class {
    getHistoryOutput = mocks.getHistoryOutput;
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
