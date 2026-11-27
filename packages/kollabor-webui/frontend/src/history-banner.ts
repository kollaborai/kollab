// The line the chat shows over its messages while older history loads or after a
// load failed (#1451). The chat store's olderStatus drives it; history-status.ts
// is the retired copy (deleted in lane 4h).

import { useState } from "react";
import type { ChatStore } from "./chat-stream";
import type { ConnectionState } from "./chat-store";

export type OlderStatus = ChatStore["olderStatus"];

export type HistoryBanner = {
  kind: "loading" | "error";
  message: string;
  /** What Retry does when it is not a page of older rows: a failed connection reconnects. Absent: it loads older rows. */
  retry?: "reconnect";
};

export const OLDER_LOADING = "Loading older messages…";
export const OLDER_LOAD_FAILED = "Couldn't load older messages. Scroll up or retry to try again.";
// The copy the runtime's history status showed while a conversation loaded, and when it did not (#1451).
export const INITIAL_LOADING = "Loading conversation history…";
export const CONVERSATION_LOAD_FAILED = "Couldn't load conversation history. Check the connection and retry.";

export const SAVED_CONVERSATION_SHOWN = "Couldn't refresh history; showing the saved conversation. Retry to update.";
export const CONNECTION_LOST = "Connection lost. Showing the last messages; retry to reconnect now.";

// Module-level, so a banner that is already shown keeps its identity.
const LOADING: HistoryBanner = { kind: "loading", message: OLDER_LOADING };
const FAILED: HistoryBanner = { kind: "error", message: OLDER_LOAD_FAILED };
const INITIAL: HistoryBanner = { kind: "loading", message: INITIAL_LOADING };
const CONNECT_FAILED: HistoryBanner = { kind: "error", message: CONVERSATION_LOAD_FAILED, retry: "reconnect" };

const SAVED_SHOWN: HistoryBanner = { kind: "error", message: SAVED_CONVERSATION_SHOWN, retry: "reconnect" };
const LOST: HistoryBanner = { kind: "error", message: CONNECTION_LOST, retry: "reconnect" };

/**
 * The line for a chat with nothing on screen yet: while its stream connects, the load line; once the stream
 * has failed, the load failure with a retry that reconnects. Rows on screen show no line while the stream is up
 * or reconnecting (#1451). Once it has failed, rows on screen are the last ones it had: the browser's saved rows
 * and the live rows each say so, with the same retry (the store also keeps checking on its own).
 */
export function initialHistoryBanner(connection: ConnectionState, rowCount: number, saved = false): HistoryBanner | null {
  if (saved && connection === "failed") return SAVED_SHOWN;
  if (rowCount > 0) return connection === "failed" ? LOST : null;
  if (connection === "connecting") return INITIAL;
  return connection === "failed" ? CONNECT_FAILED : null;
}

/**
 * The banner for the store's olderStatus, given the banner already shown. A failed
 * load stays on screen while a scroll tries again: swapping the failure for the
 * loading line and back on every try makes the red strip blink. Returns `shown`
 * itself whenever nothing changes, so a caller can compare by identity.
 */
export function nextOlderBanner(shown: HistoryBanner | null, status: OlderStatus): HistoryBanner | null {
  switch (status) {
    case "loading":
      return shown?.kind === "loading" || shown?.kind === "error" ? shown : LOADING;
    case "failed":
      return shown?.kind === "error" ? shown : FAILED;
    default:
      return null;
  }
}

/** The banner to show now. Kept in state, so a banner that is shown does not flicker while a retry runs. */
export function useOlderBanner(status: OlderStatus): HistoryBanner | null {
  const [shown, setShown] = useState<HistoryBanner | null>(() => nextOlderBanner(null, status));
  const next = nextOlderBanner(shown, status);
  if (next !== shown) setShown(next);
  return next;
}
