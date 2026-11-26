import { useCallback, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/chat-store";
import { queuedInputs, type QueuedInput } from "@/queue-state";

const MAX_PREVIEW_CODE_POINTS = 240;

/** Bounds a preview to MAX_PREVIEW_CODE_POINTS code points, without splitting one. */
function previewText(text: string): string {
  const points = Array.from(text);
  if (points.length <= MAX_PREVIEW_CODE_POINTS) return text;
  return `${points.slice(0, MAX_PREVIEW_CODE_POINTS).join("")}…`;
}

/**
 * The chat's queued messages (#1451): each input the daemon queued while a turn runs, read from the chat
 * store. Remove cancels one input. Send Now starts the earliest one in the running turn.
 */
export function QueuePanel({ canSendNow = true }: { canSendNow?: boolean }) {
  const chat = useChatStore();
  const queued = useMemo(() => queuedInputs(chat.rows, chat.inputs), [chat.rows, chat.inputs]);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const pendingRef = useRef<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const perform = useCallback(async (key: string, action: () => void | Promise<void>) => {
    if (pendingRef.current !== null) return;
    pendingRef.current = key;
    setPendingAction(key);
    setActionError(null);
    try {
      await action();
    } catch {
      // Do not echo backend detail that could contain message text.
      setActionError("Could not update the queue. Try again.");
    } finally {
      if (pendingRef.current === key) {
        pendingRef.current = null;
        setPendingAction(null);
      }
    }
  }, []);

  // The stream is the queue's only source. A connection that drops and recovers shows nothing here. One that
  // cannot recover leaves the list out of date, so the panel says so. Delivery needs a live stream.
  const stale = chat.connection === "failed";
  const dispatchAvailable = chat.connection === "live";
  if (queued.length === 0) {
    if (!stale) return null;
    return (
      <section className="rounded-2xl bg-card/80 px-3 py-3 sm:px-3.5" data-slot="web-queue-panel" data-stale="true">
        <div className="flex items-center justify-between gap-3">
          <p role="alert" className="text-destructive text-xs leading-4">
            Could not load queued messages.
          </p>
          <Button type="button" size="sm" variant="outline" onClick={() => void chat.retry()}>
            Retry
          </Button>
        </div>
      </section>
    );
  }

  const busy = chat.running;
  const earliest: QueuedInput | undefined = queued[0];
  const sendNowKey = `send:${earliest?.id ?? "none"}`;
  const summary = stale
    ? "Queue status is out of date. Showing last known messages; refresh before changing the queue."
    : !dispatchAvailable
      ? "Queue delivery is unavailable right now."
      : busy
        ? "Waiting for this turn to finish."
        : "Queued for the next turn.";
  const sendNowBlockedReason = stale
    ? "queue status is out of date"
    : !dispatchAvailable
      ? "queue delivery is unavailable"
      : pendingAction !== null
        ? "another queue action is in progress"
        : null;
  const sendNowDisabled = !busy || sendNowBlockedReason !== null;
  const sendNowAccessibleName = sendNowDisabled
    ? `Send Now unavailable: ${sendNowBlockedReason ?? "queue is idle"}`
    : "Send 1 queued message now";
  const sendNowTitle = sendNowBlockedReason ?? "Adds the earliest queued batch at the next agent step";

  return (
    <section
      aria-labelledby="web-queue-title"
      aria-live="polite"
      aria-relevant="all"
      aria-atomic="false"
      aria-busy={pendingAction !== null}
      className="rounded-2xl bg-card/80 px-3 py-3 sm:px-3.5"
      data-slot="web-queue-panel"
      data-stale={stale ? "true" : "false"}
    >
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1">
          <h2 id="web-queue-title" className="text-sm font-semibold tracking-tight">
            Queued messages <span className="text-muted-foreground font-normal">· {queued.length}</span>
          </h2>
          <p
            className="mt-0.5 text-muted-foreground text-xs leading-4"
            role={stale ? "alert" : "status"}
            aria-live={stale ? "assertive" : "polite"}
          >
            {summary}
          </p>
        </div>
        {busy && canSendNow ? (
          <Button
            type="button"
            size="sm"
            className="h-8 shrink-0 rounded-lg bg-highlight px-3 text-highlight-foreground hover:bg-highlight/90"
            aria-label={sendNowAccessibleName}
            data-slot="web-queue-send-now"
            title={sendNowTitle}
            disabled={sendNowDisabled}
            onClick={() => earliest && void perform(sendNowKey, () => chat.sendNow(earliest.id))}
          >
            {pendingAction === sendNowKey ? "Sending…" : "Send Now"}
          </Button>
        ) : null}
        {stale ? (
          <Button type="button" size="sm" variant="outline" onClick={() => void chat.retry()}>
            Retry
          </Button>
        ) : null}
      </div>

      <ol aria-label="Queued messages" className="mt-2.5 grid gap-1">
        {queued.map((input, index) => {
          const ordinal = index + 1;
          const removeKey = `remove:${input.id}`;
          const removing = pendingAction === removeKey;
          const canRemove = pendingAction === null && !stale;
          const preview = previewText(input.text);
          return (
            <li
              key={input.id}
              className="grid min-w-0 grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-2 rounded-lg bg-background/35 px-2 py-2"
              data-slot="web-queue-item"
              data-status="queued"
            >
              <span aria-hidden="true" className="pt-px text-muted-foreground text-xs tabular-nums">
                {ordinal}
              </span>
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 text-muted-foreground text-[0.6875rem] font-medium leading-4">
                  <span
                    aria-hidden="true"
                    className="inline-block size-1.5 shrink-0 rounded-full bg-highlight"
                    data-slot="web-queue-status-dot"
                    data-status="queued"
                  />
                  Queued
                </p>
                {preview ? (
                  <p className="mt-0.5 line-clamp-2 break-words text-sm leading-5">{preview}</p>
                ) : (
                  <p className="mt-0.5 text-muted-foreground text-sm leading-5">Queued message</p>
                )}
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 self-center px-1.5 text-muted-foreground text-xs underline underline-offset-2 hover:text-foreground"
                aria-label={`Remove queued message ${ordinal}`}
                disabled={!canRemove}
                onClick={() => void perform(removeKey, () => chat.cancel(input.id))}
              >
                {removing ? "Removing…" : "Remove"}
              </Button>
            </li>
          );
        })}
      </ol>

      {actionError ? (
        <p role="alert" className="mt-2 text-destructive text-xs leading-4">
          {actionError}
        </p>
      ) : null}
    </section>
  );
}
