// The web UI's chat stream (#1451). applyChatStreamEvent applies one stream event
// to the state the chat shows; it is pure: no React, no fetch, and it never
// mutates its input. LiveChatStore, at the end of this file, runs the stream loop
// and the sends on top of it. Spec: docs/specs/webui-chat-single-stream.md.
// Shapes: ./chat-stream.ts.

import type { EngineApi } from "./api";
import {
  isChatStreamEvent,
  type ChatPart,
  type ChatRow,
  type ChatStore,
  type ChatStreamEvent,
  type InputInfo,
  type InputRequest,
  type TurnInfo,
} from "./chat-stream";

export type ConnectionState = "connecting" | "live" | "reconnecting" | "failed";

export type OlderStatus = ChatStore["olderStatus"];

export type ApplyOutcome =
  | { kind: "applied" }
  | { kind: "duplicate" }
  | { kind: "resync"; reason: string };

export type StreamState = {
  conversationUid: string | null;
  lastSeq: number | null;
  rows: readonly ChatRow[];
  inputs: Readonly<Record<string, InputInfo>>;
  turns: Readonly<Record<string, TurnInfo>>;
  olderCursor: string | null;
};

export const initialStreamState: StreamState = {
  conversationUid: null,
  lastSeq: null,
  rows: [],
  inputs: {},
  turns: {},
  olderCursor: null,
};

export function turnsRunning(turns: Readonly<Record<string, TurnInfo>>): boolean {
  return Object.values(turns).some((turn) => turn.state === "running");
}

type ApplyResult = { state: StreamState; outcome: ApplyOutcome };
type SnapshotEvent = Extract<ChatStreamEvent, { type: "snapshot" }>;
type LiveEvent = Exclude<ChatStreamEvent, SnapshotEvent>;

/**
 * Applies one event. The rules run in order and the first match wins.
 *
 * A resync keeps rows, inputs and turns on screen, and clears `conversationUid`
 * and `lastSeq`. So the next connect sends no `after`, and the next snapshot
 * replaces the window. Until that snapshot arrives, live events resync again.
 */
export function applyChatStreamEvent(
  state: StreamState,
  event: unknown,
): { state: StreamState; outcome: ApplyOutcome } {
  if (!isChatStreamEvent(event)) return resync(state, "invalid event shape");
  if (event.type === "snapshot") return { state: fromSnapshot(event), outcome: { kind: "applied" } };
  if (state.lastSeq === null) return resync(state, "event before snapshot");
  if (event.conversation_uid !== state.conversationUid) return resync(state, "conversation changed");
  if (event.seq === state.lastSeq) return { state, outcome: { kind: "duplicate" } };
  if (event.seq < state.lastSeq) return resync(state, "seq went backwards");
  if (event.seq > state.lastSeq + 1) return resync(state, "seq gap");
  const next = applyLive(state, event);
  if (typeof next === "string") return resync(state, next);
  return { state: { ...next, lastSeq: event.seq }, outcome: { kind: "applied" } };
}

function resync(state: StreamState, reason: string): ApplyResult {
  return {
    state: { ...state, conversationUid: null, lastSeq: null },
    outcome: { kind: "resync", reason },
  };
}

function fromSnapshot(event: SnapshotEvent): StreamState {
  const { data } = event;
  return {
    conversationUid: event.conversation_uid,
    lastSeq: data.next_seq - 1,
    rows: [...data.rows],
    inputs: Object.fromEntries(data.inputs.map((input) => [input.id, input] as const)),
    turns: Object.fromEntries(data.turns.map((turn) => [turn.turn_id, turn] as const)),
    olderCursor: data.older,
  };
}

/** Returns the new state, or the reason to resync. */
function applyLive(state: StreamState, event: LiveEvent): StreamState | string {
  switch (event.type) {
    case "row.upsert":
      return { ...state, rows: upsertRow(state.rows, event.data) };
    case "row.append":
      return appendText(state, event.data.id, event.data.part, event.data.text);
    case "row.delete":
      if (!state.rows.some((row) => row.id === event.data.id)) return "delete of unknown row";
      return { ...state, rows: state.rows.filter((row) => row.id !== event.data.id) };
    case "replace":
      return { ...state, rows: [...event.data.rows] };
    case "input.state": {
      const previous: InputInfo | undefined = state.inputs[event.data.id];
      if (previous?.state === "ended") return "input changed after it ended";
      return { ...state, inputs: { ...state.inputs, [event.data.id]: event.data } };
    }
    case "turn.state":
      return { ...state, turns: { ...state.turns, [event.data.turn_id]: event.data } };
  }
}

/** Replaces the row with the same id in place, or appends it when no row has that id. */
function upsertRow(rows: readonly ChatRow[], row: ChatRow): ChatRow[] {
  return rows.some((candidate) => candidate.id === row.id)
    ? rows.map((candidate) => (candidate.id === row.id ? row : candidate))
    : [...rows, row];
}

function appendText(state: StreamState, id: string, index: number, text: string): StreamState | string {
  const row = state.rows.find((candidate) => candidate.id === id);
  if (row === undefined) return "append to unknown row";
  const part = row.parts[index];
  if (!part || (part.type !== "text" && part.type !== "reasoning")) {
    return "append to a part that is not text";
  }
  const grown: ChatPart = { ...part, text: part.text + text };
  const parts = row.parts.map((candidate, at) => (at === index ? grown : candidate));
  return {
    ...state,
    rows: state.rows.map((candidate) => (candidate.id === id ? { ...candidate, parts } : candidate)),
  };
}

/** The transport the store uses: the engine's request and its SSE reader. */
export type ChatTransport = Pick<EngineApi, "request" | "streamSse">;

/** What the chat renders. A new object is made only when something changed. */
export type ChatSnapshot = {
  readonly rows: readonly ChatRow[];
  readonly inputs: Readonly<Record<string, InputInfo>>;
  readonly running: boolean;
  readonly connection: ConnectionState;
  readonly hasEarlier: boolean;
  readonly olderStatus: OlderStatus;
};

/** The store the chat renders from. LiveChatStore is the implementation. */
export interface LiveStore extends ChatStore {
  readonly connection: ConnectionState;
  readonly hasEarlier: boolean;
  subscribe(listener: () => void): () => void;
  getSnapshot(): ChatSnapshot;
  start(): void;
  retry(): void;
  dispose(): void;
}

/** The client prefix for the input ids the web UI picks: newInputId(WEB_INPUT_PREFIX). */
export const WEB_INPUT_PREFIX = "web";

const MAX_BACKOFF_MS = 10_000;

type HistoryPage = { rows: ChatRow[]; older: string | null };

/** The HTTP status carried by an error the transport threw, if it has one. */
function errorStatus(error: unknown): number | undefined {
  const status = (error as { status?: unknown } | null | undefined)?.status;
  return typeof status === "number" ? status : undefined;
}

/**
 * Follows one session's stream and sends its inputs. Rows stay on screen across
 * a dropped connection; a seq gap or a conflict resyncs from a new snapshot.
 */
export class LiveChatStore implements LiveStore {
  private state: StreamState = initialStreamState;
  // Not named `connection`: that name is the public getter below.
  private connectionState: ConnectionState = "connecting";
  // Not named `olderStatus`: that name is the public getter below.
  private paging: OlderStatus = "idle";
  private attempts = 0;
  private started = false;
  private disposed = false;
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private wake: (() => void) | null = null;
  private readonly listeners = new Set<() => void>();
  private readonly transport: ChatTransport;
  private readonly enc: string;
  private readonly retryBaseMs: number;
  private readonly maxAttempts: number;
  private snapshot: ChatSnapshot;

  constructor(
    transport: ChatTransport,
    sessionId: string,
    options: { retryBaseMs?: number; maxAttempts?: number } = {},
  ) {
    this.transport = transport;
    this.enc = encodeURIComponent(sessionId);
    this.retryBaseMs = options.retryBaseMs ?? 500;
    this.maxAttempts = options.maxAttempts ?? 8;
    this.snapshot = this.snapshotOf();
  }

  // Arrow properties, so these references stay the same for useSyncExternalStore.
  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getSnapshot = (): ChatSnapshot => this.snapshot;

  get rows(): readonly ChatRow[] {
    return this.snapshot.rows;
  }

  get inputs(): Readonly<Record<string, InputInfo>> {
    return this.snapshot.inputs;
  }

  get running(): boolean {
    return this.snapshot.running;
  }

  get connection(): ConnectionState {
    return this.snapshot.connection;
  }

  get hasEarlier(): boolean {
    return this.snapshot.hasEarlier;
  }

  get olderStatus(): OlderStatus {
    return this.snapshot.olderStatus;
  }

  start(): void {
    if (this.started || this.disposed) return;
    this.started = true;
    void this.run();
  }

  /** Connects again after the connection has failed for good. */
  retry(): void {
    if (this.disposed || this.connectionState !== "failed") return;
    this.attempts = 0;
    this.started = true;
    void this.run();
  }

  dispose(): void {
    this.disposed = true;
    this.controller?.abort();
    clearTimeout(this.timer);
    const wake = this.wake;
    this.wake = null;
    wake?.();
    this.listeners.clear();
  }

  async send(input: InputRequest): Promise<void> {
    const response = await this.transport.request(`/sessions/${this.enc}/inputs`, {
      method: "POST",
      body: JSON.stringify(input),
    });
    if (!response.ok) throw new Error(`send failed: ${response.status}`);
  }

  /** Starts a queued input now. Nothing local changes: the daemon answers with input.state on the stream. */
  async sendNow(inputId: string): Promise<void> {
    const path = `/sessions/${this.enc}/inputs/${encodeURIComponent(inputId)}/send-now`;
    const response = await this.transport.request(path, { method: "POST", body: JSON.stringify({}) });
    if (!response.ok) throw new Error(`send-now failed: ${response.status}`);
  }

  async cancel(inputId: string): Promise<void> {
    const path = `/sessions/${this.enc}/inputs/${encodeURIComponent(inputId)}/cancel`;
    const response = await this.transport.request(path, { method: "POST" });
    if (!response.ok) throw new Error(`cancel failed: ${response.status}`);
  }

  async stop(): Promise<void> {
    const response = await this.transport.request(`/sessions/${this.enc}/cancel`, { method: "POST" });
    if (!response.ok) throw new Error(`stop failed: ${response.status}`);
  }

  async loadOlder(): Promise<void> {
    const cursor = this.state.olderCursor;
    if (cursor === null) return;
    this.setPaging("loading");
    const path = `/sessions/${this.enc}/history?before=${encodeURIComponent(cursor)}`;
    let page: HistoryPage;
    try {
      const response = await this.requestHistory(path);
      if (response === null) {
        // A stale cursor: resync, and the next snapshot brings a fresh one.
        this.setPaging("idle");
        this.resync();
        return;
      }
      page = (await response.json()) as HistoryPage;
      if (!Array.isArray(page.rows)) throw new Error("history failed: unexpected page");
    } catch (error) {
      this.setPaging("failed");
      throw error;
    }
    const held = new Set(this.state.rows.map((row) => row.id));
    const older = page.rows.filter((row) => !held.has(row.id));
    this.state = { ...this.state, rows: [...older, ...this.state.rows], olderCursor: page.older ?? null };
    this.paging = this.state.olderCursor === null ? "complete" : "idle";
    this.publish();
  }

  /** The history answer, or null when the cursor is stale (409). Any other failure throws. */
  private async requestHistory(path: string): Promise<Response | null> {
    let response: Response;
    try {
      response = await this.transport.request(path);
    } catch (error) {
      // EngineApi.request throws ApiError on any non-2xx answer, so a 409 can arrive here.
      if (errorStatus(error) === 409) return null;
      throw error;
    }
    if (response.status === 409) return null;
    if (!response.ok) throw new Error(`history failed: ${response.status}`);
    return response;
  }

  private snapshotOf(): ChatSnapshot {
    return {
      rows: this.state.rows,
      inputs: this.state.inputs,
      running: turnsRunning(this.state.turns),
      connection: this.connectionState,
      hasEarlier: this.state.olderCursor !== null,
      olderStatus: this.paging,
    };
  }

  /** Builds the next snapshot and tells every listener once. */
  private publish(): void {
    this.snapshot = this.snapshotOf();
    for (const listener of [...this.listeners]) listener();
  }

  /** Sets the paging state and tells the listeners, unless the state is unchanged. */
  private setPaging(status: OlderStatus): void {
    if (this.paging === status) return;
    this.paging = status;
    this.publish();
  }

  /** Connects, applies events, and reconnects after a drop until the attempts run out. */
  private async run(): Promise<void> {
    while (!this.disposed) {
      const controller = new AbortController();
      this.controller = controller;
      const after = this.state.lastSeq;
      const path = `/sessions/${this.enc}/stream` + (after === null ? "" : `?after=${after}`);
      this.connectionState = "connecting";
      this.publish();
      try {
        await this.transport.streamSse<ChatStreamEvent>(
          path,
          controller.signal,
          (event) => this.onEvent(controller, event),
          () => this.onOpen(controller),
        );
      } catch {
        // A refused, failed or aborted connection counts as a drop below.
      }
      this.controller = null;
      if (this.disposed) return;
      this.attempts += 1;
      if (this.attempts >= this.maxAttempts) {
        this.connectionState = "failed";
        this.publish();
        return;
      }
      this.connectionState = "reconnecting";
      this.publish();
      await this.sleep(Math.min(MAX_BACKOFF_MS, this.retryBaseMs * 2 ** (this.attempts - 1)));
    }
  }

  private onOpen(controller: AbortController): void {
    if (this.disposed || this.controller !== controller || controller.signal.aborted) return;
    this.connectionState = "live";
    this.publish();
  }

  private onEvent(controller: AbortController, event: ChatStreamEvent): void {
    // An event from a superseded or aborted connection is stale: ignore it.
    if (this.disposed || this.controller !== controller || controller.signal.aborted) return;
    const result = applyChatStreamEvent(this.state, event);
    if (result.outcome.kind === "resync") {
      this.resync();
      return;
    }
    if (result.outcome.kind === "duplicate") return;
    this.state = result.state;
    // A snapshot brings a new window with its own cursor, so the paging state follows it.
    if (event.type === "snapshot") this.paging = this.state.olderCursor === null ? "complete" : "idle";
    this.attempts = 0;
    this.publish();
  }

  /** Keeps rows on screen, forgets the cursor, and ends the connection so the loop reconnects without `after`. */
  private resync(): void {
    this.state = { ...this.state, conversationUid: null, lastSeq: null };
    this.controller?.abort();
  }

  private sleep(ms: number): Promise<void> {
    return new Promise<void>((resolve) => {
      this.wake = () => resolve();
      this.timer = setTimeout(() => {
        this.wake = null;
        resolve();
      }, ms);
    });
  }
}
