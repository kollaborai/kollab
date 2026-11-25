import { expect, test } from "vitest";

import { initialHistoryBanner, nextOlderBanner, OLDER_LOAD_FAILED, OLDER_LOADING } from "../src/history-banner.ts";

// The banner over the chat comes from the store's olderStatus (#1451). Loading
// shows the loading line; failed shows the failure with a way to retry.

test("a loading load of older messages shows the loading line", () => {
  expect(nextOlderBanner(null, "loading")).toEqual({ kind: "loading", message: OLDER_LOADING });
  expect(OLDER_LOADING).toBe("Loading older messages…");
});

test("a failed load of older messages shows the failure line", () => {
  expect(nextOlderBanner(null, "failed")).toEqual({ kind: "error", message: OLDER_LOAD_FAILED });
  expect(OLDER_LOAD_FAILED).toMatch(/^Couldn't load older messages\./);
});

test("idle and complete show no banner", () => {
  expect(nextOlderBanner(null, "idle")).toBeNull();
  expect(nextOlderBanner(null, "complete")).toBeNull();
});

test("a failed load stays on screen while a scroll tries again", () => {
  // Each scroll to the top tries again. Swapping the failure for the loading
  // line and back on every try makes the red strip blink.
  const failed = nextOlderBanner(null, "failed");
  expect(nextOlderBanner(failed, "loading")).toBe(failed);
  expect(nextOlderBanner(failed, "failed")).toBe(failed);
});

test("a successful retry clears the failure", () => {
  const failed = nextOlderBanner(null, "failed");
  expect(nextOlderBanner(failed, "idle")).toBeNull();
  expect(nextOlderBanner(failed, "complete")).toBeNull();
});

// The first load of a chat shows the load line the old history status showed, until its
// rows arrive or its stream is open (#1451). Nothing shows once rows are on screen.
test("connecting with nothing on screen shows the load line", () => {
  expect(initialHistoryBanner("connecting", 0)).toEqual({ kind: "loading", message: "Loading conversation history…" });
});

test("rows on screen, or a stream that is open, show no load line", () => {
  expect(initialHistoryBanner("connecting", 2)).toBeNull();
  expect(initialHistoryBanner("live", 0)).toBeNull();
});

// A stream that fails before its first rows arrive means the conversation did not load: the old
// history status showed this line with a Retry, which now reconnects the store (#1451).
test("a failed stream with nothing on screen shows the old load failure with a retry", () => {
  expect(initialHistoryBanner("failed", 0)).toEqual({
    kind: "error",
    message: "Couldn't load conversation history. Check the connection and retry.",
    retry: "reconnect",
  });
});

test("a failed stream with rows on screen shows no load failure", () => {
  expect(initialHistoryBanner("failed", 3)).toBeNull();
});
