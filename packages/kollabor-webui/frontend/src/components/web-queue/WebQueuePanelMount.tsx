import { useEffect, useMemo, useState } from "react";
import type { EngineApi, WebMessageQueueSnapshot } from "@/api";
import { WEB_QUEUE_UPDATED_EVENT } from "./submit";
import { QueuePanel, type WebQueueSnapshot } from "./QueuePanel";

export type WebQueuePanelState = {
  snapshot: WebMessageQueueSnapshot | null;
  dispatchAvailable: boolean;
  stale: boolean;
  refreshError: boolean;
};

type QueueIdentity = {
  sessionId: string;
  conversationUid: string;
  key: string;
};

type QueueTimer = ReturnType<typeof globalThis.setInterval>;
type QueueSetTimer = (handler: TimerHandler, timeout?: number) => QueueTimer;
type QueueClearTimer = (timer: QueueTimer) => void;

const EMPTY_STATE: WebQueuePanelState = {
  snapshot: null,
  dispatchAvailable: false,
  stale: false,
  refreshError: false,
};
const REFRESH_INTERVAL_MS = 2_000;

/** Owns one authenticated session/conversation snapshot and its bounded polling lifecycle. */
export class WebQueuePanelController {
  private identity: QueueIdentity | null = null;
  private state: WebQueuePanelState = EMPTY_STATE;
  private timer: QueueTimer | null = null;
  private refreshRequest: Promise<WebMessageQueueSnapshot | null> | null = null;
  private generation = 0;
  private readSequence = 0;
  private listeners = new Set<(state: WebQueuePanelState) => void>();

  constructor(
    private readonly api: EngineApi,
    private readonly setTimer: QueueSetTimer = (handler, timeout) => globalThis.setInterval(handler, timeout),
    private readonly clearTimer: QueueClearTimer = (timer) => globalThis.clearInterval(timer),
  ) {}

  getState(): WebQueuePanelState {
    return this.state;
  }

  subscribe(listener: (state: WebQueuePanelState) => void): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => {
      this.listeners.delete(listener);
    };
  }

  setIdentity(sessionId: string | null, conversationUid: string | null | undefined): string | null {
    const next = sessionId && conversationUid
      ? {
          sessionId,
          conversationUid,
          key: JSON.stringify([sessionId, conversationUid]),
        }
      : null;
    if (next?.key === this.identity?.key) return next?.key ?? null;

    this.generation += 1;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.identity = next;
    this.publish(EMPTY_STATE);
    if (!next) return null;

    void this.refresh(true);
    this.timer = this.setTimer(() => void this.refresh(), REFRESH_INTERVAL_MS);
    return next.key;
  }

  clearIdentity(expectedKey: string | null): void {
    if (expectedKey && this.identity?.key === expectedKey) this.setIdentity(null, null);
  }

  refresh(force = false): Promise<WebMessageQueueSnapshot | null> {
    const identity = this.identity;
    if (!identity) return Promise.resolve(null);
    if (!force && this.refreshRequest) return this.refreshRequest;

    const generation = this.generation;
    const sequence = ++this.readSequence;
    const request = this.api.getWebMessageQueue(identity.sessionId, identity.conversationUid)
      .then((snapshot) => {
        if (this.isCurrent(identity, generation) && sequence === this.readSequence) {
          this.publishSnapshot(snapshot);
        }
        return snapshot;
      })
      .catch(() => {
        if (this.isCurrent(identity, generation) && sequence === this.readSequence) {
          this.publish({ ...this.state, stale: true, refreshError: true });
        }
        return null;
      })
      .finally(() => {
        if (this.refreshRequest === request) this.refreshRequest = null;
      });
    this.refreshRequest = request;
    return request;
  }

  async removeItem(requestId: string): Promise<void> {
    const identity = this.identity;
    const observed = this.state.snapshot;
    if (!identity || !observed || this.state.stale || !this.hasQueuedItem(observed, requestId)) {
      throw new Error("The queued message is no longer available.");
    }
    const generation = this.generation;

    try {
      const ids = await this.api.allocateWebMessageQueueIds(
        identity.sessionId,
        identity.conversationUid,
        1,
        1,
        0,
      );
      if (!this.isCurrent(identity, generation)) throw new Error("The queue changed sessions.");
      const current = this.state.snapshot;
      if (!current || current.scope_id !== observed.scope_id || current.incarnation !== observed.incarnation ||
          ids.scope_id !== observed.scope_id || ids.incarnation !== observed.incarnation ||
          this.state.stale || !this.hasQueuedItem(current, requestId)) {
        throw new Error("The queue changed. Refresh before removing a message.");
      }
      const operationId = ids.operation_ids[0];
      if (!operationId) throw new Error("The daemon did not allocate a queue operation.");
      await this.api.removeWebMessageQueueItem(
        identity.sessionId,
        identity.conversationUid,
        operationId,
        requestId,
      );
    } finally {
      if (this.isCurrent(identity, generation)) await this.refresh(true);
    }
  }

  async sendNow(batchId: string): Promise<void> {
    const identity = this.identity;
    const observed = this.state.snapshot;
    if (!identity || !observed || !observed.dispatch_available || !this.hasQueuedBatch(observed, batchId)) {
      throw new Error("The queued batch is no longer available.");
    }
    const generation = this.generation;
    let current: WebMessageQueueSnapshot;
    try {
      current = await this.api.getWebMessageQueue(identity.sessionId, identity.conversationUid);
    } catch (error) {
      if (this.isCurrent(identity, generation)) {
        this.publish({ ...this.state, stale: true, refreshError: true });
      }
      throw error;
    }
    if (!this.isCurrent(identity, generation)) throw new Error("The queue changed sessions.");
    if (current.scope_id !== observed.scope_id || current.incarnation !== observed.incarnation) {
      this.publishSnapshot(current);
      throw new Error("The queue changed. Refresh before sending.");
    }
    this.publishSnapshot(current);
    if (this.state.stale) throw new Error("Queue status is out of date. Refresh before sending.");
    const latest = this.state.snapshot;
    const verified = latest && latest.scope_id === current.scope_id &&
      latest.incarnation === current.incarnation && latest.revision > current.revision
      ? latest
      : current;
    if (!verified.dispatch_available || !this.hasQueuedBatch(verified, batchId)) {
      throw new Error("The queued batch is no longer ready to send.");
    }

    let accepted = false;
    try {
      const result = await this.api.sendWebMessageQueueBatchNow(
        identity.sessionId,
        identity.conversationUid,
        batchId,
      );
      accepted = result.accepted === true;
    } finally {
      if (this.isCurrent(identity, generation)) await this.refresh(true);
    }
    if (!accepted) throw new Error("The daemon did not accept the queued batch.");
  }

  private hasQueuedItem(snapshot: WebMessageQueueSnapshot, requestId: string): boolean {
    return snapshot.batches.some((batch) => batch.status === "queued" &&
      batch.request_ids.includes(requestId) &&
      batch.items.some((item) => item.request_id === requestId && item.status === "queued"));
  }

  private hasQueuedBatch(snapshot: WebMessageQueueSnapshot, batchId: string): boolean {
    const earliest = [...snapshot.batches].sort((left, right) => left.sequence - right.sequence)[0];
    return earliest?.batch_id === batchId && earliest.status === "queued" &&
      earliest.request_ids.length > 0 && earliest.request_ids.every((requestId) =>
        earliest.items.some((item) => item.request_id === requestId && item.status === "queued"));
  }

  private isCurrent(identity: QueueIdentity, generation: number): boolean {
    return this.identity === identity && this.generation === generation;
  }

  private publishSnapshot(snapshot: WebMessageQueueSnapshot): void {
    const prior = this.state.snapshot;
    if (prior && prior.scope_id === snapshot.scope_id && prior.incarnation === snapshot.incarnation &&
        snapshot.revision < prior.revision) return;
    this.publish({
      snapshot,
      dispatchAvailable: snapshot.dispatch_available === true,
      stale: false,
      refreshError: false,
    });
  }

  private publish(state: WebQueuePanelState): void {
    this.state = state;
    for (const listener of this.listeners) listener(state);
  }
}

export function WebQueuePanelMount({
  api,
  sessionId,
  conversationUid,
  busy,
}: {
  api: EngineApi;
  sessionId: string;
  conversationUid: string | null | undefined;
  busy: boolean;
}) {
  const controller = useMemo(() => new WebQueuePanelController(api), [api]);
  const [state, setState] = useState(controller.getState());

  useEffect(() => controller.subscribe(setState), [controller]);
  useEffect(() => {
    const identityKey = controller.setIdentity(sessionId, conversationUid);
    return () => controller.clearIdentity(identityKey);
  }, [controller, sessionId, conversationUid]);
  useEffect(() => {
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void controller.refresh(true);
    };
    window.addEventListener("focus", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.removeEventListener("focus", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [controller]);
  useEffect(() => {
    const refreshAfterLocalAdmission = () => void controller.refresh(true);
    window.addEventListener(WEB_QUEUE_UPDATED_EVENT, refreshAfterLocalAdmission);
    return () => window.removeEventListener(WEB_QUEUE_UPDATED_EVENT, refreshAfterLocalAdmission);
  }, [controller]);

  return (
    <QueuePanel
      snapshot={state.snapshot as WebQueueSnapshot | null}
      dispatchAvailable={state.dispatchAvailable}
      stale={state.stale}
      refreshError={state.refreshError}
      busy={busy}
      onRefresh={() => { void controller.refresh(true); }}
      onRemoveItem={(requestId) => controller.removeItem(requestId)}
      onSendNow={(batchId) => controller.sendNow(batchId)}
    />
  );
}
