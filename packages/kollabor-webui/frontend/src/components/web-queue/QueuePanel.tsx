import { useCallback, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";

export type WebQueueItemStatus = "queued" | "dispatching" | "in_doubt";

export type WebQueueItemSnapshot = {
  request_id: string;
  status: WebQueueItemStatus;
  attempt_id: string | null;
  /** Bounded, text-only preview. Never pass provider payload bytes here. */
  text_preview?: string | null;
  /** Sanitized filename basenames only; never paths or stored attachment refs. */
  attachment_labels?: readonly string[];
};

export type WebQueueBatchSnapshot = {
  batch_id: string;
  sequence: number;
  status: WebQueueItemStatus;
  request_ids: readonly string[];
  claim_id: string | null;
  items: readonly WebQueueItemSnapshot[];
};

export type WebQueueSnapshot = {
  scope_id: string;
  incarnation: string;
  revision: number;
  batches: readonly WebQueueBatchSnapshot[];
};

export type QueuePanelProps = {
  snapshot: WebQueueSnapshot | null;
  dispatchAvailable: boolean;
  stale: boolean;
  refreshError: boolean;
  busy: boolean;
  onRefresh: () => void | Promise<void>;
  onRemoveItem: (requestId: string) => void | Promise<void>;
  onSendNow: (batchId: string) => void | Promise<void>;
};

type OrderedBatch = {
  batch: WebQueueBatchSnapshot;
  entries: Array<{ requestId: string; item: WebQueueItemSnapshot | null }>;
  items: WebQueueItemSnapshot[];
  missingCount: number;
  sourceIndex: number;
};

const MAX_PREVIEW_CODE_POINTS = 240;

function sanitizeAttachmentLabel(label: string): string {
  const basename = label
    .split(/[\\/]/)
    .filter(Boolean)
    .at(-1)
    ?.replace(/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g, "")
    .trim() ?? "";
  return basename === "." || basename === ".." ? "" : basename;
}

function previewText(text: string | null | undefined): string {
  if (!text) return "";
  const points = Array.from(text);
  if (points.length <= MAX_PREVIEW_CODE_POINTS) return text;
  return `${points.slice(0, MAX_PREVIEW_CODE_POINTS).join("")}…`;
}

function statusLabel(status: WebQueueItemStatus): string {
  switch (status) {
    case "dispatching":
      return "Sending";
    case "in_doubt":
      return "Delivery status unknown";
    default:
      return "Queued";
  }
}

function statusDotClass(status: WebQueueItemStatus): string {
  switch (status) {
    case "dispatching":
      return "animate-pulse bg-highlight";
    case "in_doubt":
      return "bg-destructive";
    default:
      return "bg-highlight";
  }
}

function orderedBatches(snapshot: WebQueueSnapshot | null): OrderedBatch[] {
  if (!snapshot) return [];
  return snapshot.batches
    .map((batch, sourceIndex) => {
      const byId = new Map(batch.items.map((item) => [item.request_id, item]));
      const entries = batch.request_ids.map((requestId) => ({
        requestId,
        item: byId.get(requestId) ?? null,
      }));
      const items = entries.flatMap(({ item }) => item ? [item] : []);
      return { batch, entries, items, missingCount: entries.length - items.length, sourceIndex };
    })
    .filter(({ batch }) => batch.request_ids.length > 0)
    .sort((left, right) => left.batch.sequence - right.batch.sequence || left.sourceIndex - right.sourceIndex);
}

/** A daemon-backed view of the current session's durable queued messages. */
export function QueuePanel({
  snapshot,
  dispatchAvailable,
  stale,
  refreshError,
  busy,
  onRefresh,
  onRemoveItem,
  onSendNow,
}: QueuePanelProps) {
  const batches = useMemo(() => orderedBatches(snapshot), [snapshot]);
  const totalItems = batches.reduce((sum, { batch }) => sum + batch.request_ids.length, 0);
  const missingItems = batches.reduce((sum, { missingCount }) => sum + missingCount, 0);
  const unknownCount = batches.reduce(
    (sum, { items }) => sum + items.filter((item) => item.status === "in_doubt").length,
    0,
  );
  const sendingCount = batches.reduce(
    (sum, { items }) => sum + items.filter((item) => item.status === "dispatching").length,
    0,
  );
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const pendingRef = useRef<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const scopeKey = `${snapshot?.scope_id ?? ""}:${snapshot?.incarnation ?? ""}`;

  const perform = useCallback(async (key: string, action: () => void | Promise<void>) => {
    if (pendingRef.current !== null) return;
    pendingRef.current = key;
    setPendingAction(key);
    setActionError(null);
    try {
      await action();
    } catch {
      // Do not echo backend detail that could contain filenames or stored refs.
      setActionError("Could not update the queue. Try again.");
    } finally {
      if (pendingRef.current === key) {
        pendingRef.current = null;
        setPendingAction(null);
      }
    }
  }, []);

  if (totalItems === 0 && !stale) return null;

  if (!snapshot && stale) {
    return (
      <section
        className="rounded-2xl bg-card/80 px-3 py-3 sm:px-3.5"
        data-slot="web-queue-panel"
        data-stale="true"
      >
        <div className="flex items-center justify-between gap-3">
          <p role="alert" className="text-destructive text-xs leading-4">
            {refreshError ? "Could not load queued messages." : "Queue status is out of date."}
          </p>
          <Button type="button" size="sm" variant="outline" onClick={() => void onRefresh()}>
            Retry
          </Button>
        </div>
      </section>
    );
  }

  const queueSummary = stale
    ? "Queue status is out of date. Showing last known messages; refresh before changing the queue."
    : missingItems > 0
    ? "Some queued message details are unavailable. Refresh before changing the queue."
    : unknownCount > 0
    ? `Delivery status unknown for ${unknownCount} ${unknownCount === 1 ? "message" : "messages"}. They are held to avoid sending twice.`
    : sendingCount > 0
      ? `${sendingCount} ${sendingCount === 1 ? "message is" : "messages are"} being sent.`
        : !dispatchAvailable
          ? "Queue delivery is unavailable right now."
          : busy
            ? "Waiting for this turn to finish."
            : "Queued for the next turn.";

  const earliest = batches[0];
  const earliestCount = earliest?.batch.request_ids.length ?? 0;
  const earliestSendKey = earliest ? `send:${scopeKey}:${earliest.batch.batch_id}` : "send:none";
  const earliestBatchActionable = !!earliest && earliest.batch.status === "queued" &&
    earliest.items.length === earliestCount && earliest.items.every((item) => item.status === "queued");
  const sendNowBlockedReason = stale
    ? "queue status is out of date"
    : !dispatchAvailable
    ? "queue delivery is unavailable"
    : earliest?.items.some((item) => item.status === "in_doubt") || earliest?.batch.status === "in_doubt"
      ? "earlier message delivery status is unknown"
      : earliest?.items.some((item) => item.status === "dispatching") || earliest?.batch.status === "dispatching"
        ? "earlier message is already being sent"
        : missingItems > 0
          ? "refresh the queue before sending"
          : pendingAction !== null
            ? "another queue action is in progress"
            : !earliestBatchActionable
              ? "earliest queued messages are not ready"
              : null;
  const sendNowDisabled = !busy || sendNowBlockedReason !== null;
  const sendNowAccessibleName = sendNowDisabled
    ? `Send Now unavailable: ${sendNowBlockedReason ?? "queue is idle"}`
    : `Send ${earliestCount} queued ${earliestCount === 1 ? "message" : "messages"} now`;
  const sendNowTitle = sendNowBlockedReason ?? "Adds the earliest queued batch at the next agent step";
  let nextOrdinal = 0;
  const queueRows = batches.flatMap(({ batch, entries }, batchIndex) => entries.map((entry) => ({
    batch,
    batchIndex,
    entry,
    ordinal: ++nextOrdinal,
  })));
  return (
    <section
      aria-labelledby="web-queue-title"
      aria-live="polite"
      aria-relevant="all"
      aria-atomic="false"
      aria-busy={pendingAction !== null}
      className="rounded-2xl bg-card/80 px-3 py-3 sm:px-3.5"
      data-slot="web-queue-panel"
      data-revision={snapshot?.revision}
      data-stale={stale ? "true" : "false"}
    >
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1">
          <h2 id="web-queue-title" className="text-sm font-semibold tracking-tight">
            Queued messages <span className="text-muted-foreground font-normal">· {totalItems}</span>
          </h2>
          <p
            className="mt-0.5 text-muted-foreground text-xs leading-4"
            role={stale ? "alert" : "status"}
            aria-live={stale ? "assertive" : "polite"}
          >
            {queueSummary}
          </p>
        </div>
        {busy ? (
          <Button
            type="button"
            size="sm"
            className="h-8 shrink-0 rounded-lg bg-highlight px-3 text-highlight-foreground hover:bg-highlight/90"
            aria-label={sendNowAccessibleName}
            data-slot="web-queue-send-now"
            title={sendNowTitle}
            disabled={sendNowDisabled}
            onClick={() => earliest && void perform(earliestSendKey, () => onSendNow(earliest.batch.batch_id))}
          >
            {pendingAction === earliestSendKey ? "Sending…" : "Send Now"}
          </Button>
        ) : null}
        {stale ? (
          <Button type="button" size="sm" variant="outline" onClick={() => void onRefresh()}>
            Retry
          </Button>
        ) : null}
      </div>

      <ol aria-label="Queued messages" className="mt-2.5 grid gap-1">
        {queueRows.map(({ batch, batchIndex, entry, ordinal }) => {
          const item = entry.item;
          const removeKey = `remove:${scopeKey}:${entry.requestId}`;
          const removing = pendingAction === removeKey;
          const canRemove = !!item && batch.status === "queued" && item.status === "queued" &&
            pendingAction === null && missingItems === 0 && !stale;

          if (!item) {
            return (
              <li
                key={`${batchIndex}:${entry.requestId}`}
                className="grid min-w-0 grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-2 rounded-lg bg-background/35 px-2 py-2"
                data-slot="web-queue-item"
                data-status="missing"
              >
                <span aria-hidden="true" className="pt-px text-muted-foreground text-xs tabular-nums">
                  {ordinal}
                </span>
                <div className="min-w-0">
                  <p className="text-muted-foreground text-[0.6875rem] font-medium leading-4">
                    Message details unavailable
                  </p>
                  <p className="mt-0.5 text-muted-foreground text-sm leading-5">Queued message is unavailable.</p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 self-center px-1.5 text-muted-foreground text-xs underline underline-offset-2 hover:text-foreground"
                  aria-label={`Remove queued message ${ordinal}`}
                  disabled
                >
                  Remove
                </Button>
              </li>
            );
          }

          const attachments = (item.attachment_labels ?? [])
            .map(sanitizeAttachmentLabel)
            .filter(Boolean);
          const preview = previewText(item.text_preview);
          return (
            <li
              key={`${batchIndex}:${entry.requestId}`}
              className="grid min-w-0 grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-2 rounded-lg bg-background/35 px-2 py-2"
              data-slot="web-queue-item"
              data-status={item.status}
            >
              <span aria-hidden="true" className="pt-px text-muted-foreground text-xs tabular-nums">
                {ordinal}
              </span>
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 text-muted-foreground text-[0.6875rem] font-medium leading-4">
                  <span
                    aria-hidden="true"
                    className={`inline-block size-1.5 shrink-0 rounded-full ${statusDotClass(item.status)}`}
                    data-slot="web-queue-status-dot"
                    data-status={item.status}
                  />
                  {statusLabel(item.status)}
                </p>
                {preview ? (
                  <p className="mt-0.5 line-clamp-2 break-words text-sm leading-5">{preview}</p>
                ) : attachments.length === 0 ? (
                  <p className="mt-0.5 text-muted-foreground text-sm leading-5">Queued message</p>
                ) : null}
                {attachments.length > 0 ? (
                  <p className="mt-1 line-clamp-1 break-all text-muted-foreground text-xs" title={attachments.join(", ")}>
                    Attachment{attachments.length === 1 ? "" : "s"} · {attachments.join(", ")}
                  </p>
                ) : null}
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 self-center px-1.5 text-muted-foreground text-xs underline underline-offset-2 hover:text-foreground"
                aria-label={`Remove queued message ${ordinal}`}
                disabled={!canRemove}
                onClick={() => void perform(removeKey, () => onRemoveItem(item.request_id))}
              >
                {removing ? "Removing…" : "Remove"}
              </Button>
            </li>
          );
        })}
      </ol>

      {missingItems > 0 ? (
        <p className="mt-2 text-muted-foreground text-xs" role="status">
          {missingItems} {missingItems === 1 ? "message is" : "messages are"} unavailable.
        </p>
      ) : null}

      {actionError ? (
        <p role="alert" className="mt-2 text-destructive text-xs leading-4">
          {actionError}
        </p>
      ) : null}
    </section>
  );
}
