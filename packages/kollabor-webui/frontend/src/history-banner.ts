// The line the chat shows over its messages while older history loads or after a
// load failed (#1451). The chat store's olderStatus drives it; history-status.ts
// is the retired copy (deleted in lane 4h).

import { useState } from "react";
import type { ChatStore } from "./chat-stream";
import type { ConnectionState } from "./chat-store";

export type OlderStatus = ChatStore["olderStatus"];

export type HistoryBanner = { kind: "loading" | "error"; message: string };

export const OLDER_LOADING = "Loading older messages…";
export const OLDER_LOAD_FAILED = "Couldn't load older messages. Scroll up or retry to try again.";
// The copy the runtime's history status showed while a conversation loaded (#1451).
export const INITIAL_LOADING = "Loading conversation history…";

// Module-level, so a banner that is already shown keeps its identity.
const LOADING: HistoryBanner = { kind: "loading", message: OLDER_LOADING };
const FAILED: HistoryBanner = { kind: "error", message: OLDER_LOAD_FAILED };
const INITIAL: HistoryBanner = { kind: "loading", message: INITIAL_LOADING };

/**
 * The line for a chat whose first snapshot is still on its way: it is connecting and nothing is on
 * screen. Once rows arrive, or the stream opens, the line is gone (#1451).
 */
export function initialHistoryBanner(connection: ConnectionState, rowCount: number): HistoryBanner | null {
  return connection === "connecting" && rowCount === 0 ? INITIAL : null;
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
