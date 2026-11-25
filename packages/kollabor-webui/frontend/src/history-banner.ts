// The line the chat shows over its messages while older history loads or after a
// load failed (#1451). The chat store's olderStatus drives it; history-status.ts
// is the retired copy (deleted in lane 4h).

import { useState } from "react";
import type { ChatStore } from "./chat-stream";

export type OlderStatus = ChatStore["olderStatus"];

export type HistoryBanner = { kind: "loading" | "error"; message: string };

export const OLDER_LOADING = "Loading older messages…";
export const OLDER_LOAD_FAILED = "Couldn't load older messages. Scroll up or retry to try again.";

// Module-level, so a banner that is already shown keeps its identity.
const LOADING: HistoryBanner = { kind: "loading", message: OLDER_LOADING };
const FAILED: HistoryBanner = { kind: "error", message: OLDER_LOAD_FAILED };

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
