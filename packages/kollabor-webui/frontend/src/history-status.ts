/** The line the chat shows over its messages while history loads or after a load failed. */
export type HistoryStatus = {
  sessionId: string;
  kind: "loading" | "loading-older" | "error" | "empty";
  message: string;
} | null;

const OLDER_LOAD_FAILED = "Couldn't load older messages. Scroll up or retry to try again.";

const olderLoadFailure = (status: HistoryStatus, sessionId: string) =>
  status?.sessionId === sessionId && status.kind === "error" && status.message === OLDER_LOAD_FAILED;

/**
 * The status while a load of older messages runs. A failed load stays on
 * screen while a scroll tries again: each scroll to the top is a new try, and
 * swapping the failure for the loading line and back makes the strip blink.
 */
export const olderLoadRunning = (status: HistoryStatus, sessionId: string): HistoryStatus =>
  olderLoadFailure(status, sessionId) ? status
    : { sessionId, kind: "loading-older", message: "Loading older messages…" };

/** The status after a load of older messages failed: the one already shown, when it is that failure. */
export const olderLoadFailed = (status: HistoryStatus, sessionId: string): HistoryStatus =>
  olderLoadFailure(status, sessionId) ? status : { sessionId, kind: "error", message: OLDER_LOAD_FAILED };
