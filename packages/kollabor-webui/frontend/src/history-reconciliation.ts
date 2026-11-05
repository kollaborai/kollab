export type HistoryReconciliationOptions = {
  /** Each delay follows one unsuccessful refresh; the first refresh is immediate. */
  delaysMs?: readonly number[];
  isCurrent: () => boolean;
  refresh: () => Promise<boolean>;
  onExhausted: () => void;
};

const DEFAULT_DELAYS_MS = [250, 500, 1_000, 2_000, 3_000, 5_000] as const;

/**
 * Retry a nonblocking history read while the caller still owns the session.
 * A newer history event can expedite a retry without overlapping reads.
 */
export function createHistoryReconciliation({
  delaysMs = DEFAULT_DELAYS_MS,
  isCurrent,
  refresh,
  onExhausted,
}: HistoryReconciliationOptions) {
  let cancelled = false;
  let exhausted = false;
  let started = false;
  let running = false;
  let rerun = false;
  let attempts = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const clearTimer = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };

  const run = async () => {
    if (cancelled || !isCurrent()) {
      cancel();
      return;
    }
    if (running) {
      rerun = true;
      return;
    }
    running = true;
    attempts += 1;
    let found = false;
    try {
      found = await refresh();
    } catch {
      // A transient fetch failure consumes this bounded attempt; the retry
      // path may recover while the same session remains open.
    }
    running = false;
    if (cancelled || !isCurrent()) {
      cancel();
      return;
    }
    if (found) {
      cancel();
      return;
    }
    // A burst of history events may coalesce one immediate rerun, but it may
    // never extend the configured attempt budget.
    const delay = delaysMs[attempts - 1];
    if (delay === undefined) {
      cancelled = true;
      exhausted = true;
      onExhausted();
      return;
    }
    if (rerun) {
      rerun = false;
      void run();
      return;
    }
    timer = setTimeout(() => {
      timer = undefined;
      void run();
    }, delay);
  };

  const cancel = () => {
    cancelled = true;
    clearTimer();
  };

  return {
    start() {
      if (started || cancelled) return;
      started = true;
      void run();
    },
    refreshNow(): boolean {
      if (cancelled) return false;
      clearTimer();
      if (running) {
        rerun = true;
        return true;
      }
      void run();
      return true;
    },
    isExhausted: () => exhausted,
    cancel,
  };
}
