// The web UI's chat stream (#1451). applyChatStreamEvent applies one stream event
// to the state the chat shows; it is pure: no React, no fetch, and it never
// mutates its input. LiveChatStore, at the end of this file, runs the stream loop
// and the sends on top of it. Spec: docs/specs/webui-chat-single-stream.md.
// Shapes: ./chat-stream.ts.

import { createContext, createElement, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";
import type { EngineApi } from "./api";
import {
  isChatStreamEvent,
  type ChatPart,
  type ChatRow,
  type ChatStore,
  type ChatStreamCursor,
  type ChatStreamEvent,
  type InputInfo,
  type InputRequest,
  type TurnInfo,
} from "./chat-stream";

export type ConnectionState = "connecting" | "live" | "reconnecting" | "failed";

export type OlderStatus = ChatStore["olderStatus"];

/** Private goal output held only by this client's chat store, keyed by its input row id. */
export type LocalGoalReply =
  | { readonly state: "pending" }
  | { readonly state: "uncertain" }
  | { readonly state: "complete"; readonly success: boolean; readonly message: string }
  | { readonly state: "cancelled" };

export type ApplyOutcome =
  | { kind: "applied" }
  | { kind: "duplicate" }
  | { kind: "resync"; reason: string };

export type StreamState = {
  conversationUid: string | null;
  generation: string | null;
  lastSeq: number | null;
  rows: readonly ChatRow[];
  inputs: Readonly<Record<string, InputInfo>>;
  turns: Readonly<Record<string, TurnInfo>>;
  olderCursor: string | null;
};

export const initialStreamState: StreamState = {
  conversationUid: null,
  generation: null,
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
 * A resync keeps rows, inputs and turns on screen, and clears the stream cursor.
 * The next connect has no cursor, and the next snapshot replaces the window.
 * Until that snapshot arrives, live events resync again.
 */
export function applyChatStreamEvent(
  state: StreamState,
  event: unknown,
): { state: StreamState; outcome: ApplyOutcome } {
  if (!isChatStreamEvent(event)) return resync(state, "invalid event shape");
  if (event.type === "snapshot") return { state: fromSnapshot(event), outcome: { kind: "applied" } };
  if (state.lastSeq === null) return resync(state, "event before snapshot");
  if (event.conversation_uid !== state.conversationUid) return resync(state, "conversation changed");
  if (event.generation !== state.generation) return resync(state, "generation changed");
  if (event.seq === state.lastSeq) return { state, outcome: { kind: "duplicate" } };
  if (event.seq < state.lastSeq) return resync(state, "seq went backwards");
  if (event.seq > state.lastSeq + 1) return resync(state, "seq gap");
  const next = applyLive(state, event);
  if (typeof next === "string") return resync(state, next);
  return { state: { ...next, lastSeq: event.seq }, outcome: { kind: "applied" } };
}

function resync(state: StreamState, reason: string): ApplyResult {
  return {
    state: { ...state, conversationUid: null, generation: null, lastSeq: null },
    outcome: { kind: "resync", reason },
  };
}

function fromSnapshot(event: SnapshotEvent): StreamState {
  const { data } = event;
  return {
    conversationUid: event.conversation_uid,
    generation: event.generation,
    lastSeq: data.next_seq - 1,
    rows: [...data.rows],
    inputs: Object.fromEntries(data.inputs.map((input) => [input.id, input] as const)),
    turns: Object.fromEntries(data.turns.map((turn) => [turn.turn_id, turn] as const)),
    olderCursor: cursorOf(data.older),
  };
}

/** The older-rows cursor the daemon sent. A missing or non-string value means there are no older rows. */
function cursorOf(value: unknown): string | null {
  return typeof value === "string" ? value : null;
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

/** The transport the store uses: engine requests and the canonical chat stream cursor helper. */
export type ChatTransport = Pick<EngineApi, "request" | "streamChatEvents">;

/** What the chat renders. A new object is made only when something changed. */
export type ChatSnapshot = {
  readonly rows: readonly ChatRow[];
  readonly inputs: Readonly<Record<string, InputInfo>>;
  readonly turns?: Readonly<Record<string, TurnInfo>>;
  /** Local-only private replies; these values never enter StreamState or a persisted row. */
  readonly goalReplies: Readonly<Record<string, LocalGoalReply>>;
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
  /** Resumes a private status poll without resubmitting the goal command. */
  checkGoalReply(inputId: string): void;
}

/** The client prefix for the input ids the web UI picks: newInputId(WEB_INPUT_PREFIX). */
export const WEB_INPUT_PREFIX = "web";

const HEALTHY_CONNECTION_MS = 30_000;
const MAX_BACKOFF_MS = 10_000;
const MAX_UNCONFIRMED_GOAL_POLLS = 12;
const GOAL_REPLY_POLL_INTERVAL_MS = 500;
// Rows per older page: the same size the daemon's history route defaults to.
const HISTORY_PAGE_LIMIT = 50;

type HistoryPage = { rows: ChatRow[]; older: string | null };

/** A send whose row the stream has not delivered yet: the row, and how many POSTs for its id are still running. */
type LocalSend = { row: ChatRow; posts: number; succeeded: boolean };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

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
  private lastConversationUid: string | null = null;
  // Not named `connection`: that name is the public getter below.
  private connectionState: ConnectionState = "connecting";
  // Not named `olderStatus`: that name is the public getter below.
  private paging: OlderStatus = "idle";
  // Counts the window replacements (a resync, a snapshot or a replace). A history page checks it to see that its window is still current.
  private epoch = 0;
  // A later history request supersedes an older one, even while both use the same window.
  private pageRequestId = 0;
  // Sends the stream has not delivered yet, kept separately from authoritative rows.
  private readonly sends = new Map<string, LocalSend>();
  // A private owner capability is associated with its initiating input, never copied into stream state or a row.
  private readonly goalReplyOwners = new Map<string, string>();
  // An ambiguous admission is still unresolved when its mailbox has no reply yet. This stays private and
  // lets a manual retry resume polling without treating an early `missing` as a terminal failure.
  private readonly uncertainGoalAdmissions = new Map<string, string>();
  // Admission is confirmed by a successful POST, an `input.state`/snapshot, or a mailbox `pending` result.
  private readonly confirmedGoalAdmissions = new Map<string, string>();
  private readonly goalReplyPolls = new Map<string, { ownerId: string; controller: AbortController }>();
  private goalReplies: Record<string, LocalGoalReply> = {};
  private attempts = 0;
  private started = false;
  private disposed = false;
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private wake: (() => void) | null = null;
  private readonly listeners = new Set<() => void>();
  private readonly transport: ChatTransport;
  private readonly sessionId: string;
  private readonly enc: string;
  private readonly retryBaseMs: number;
  private readonly maxAttempts: number;
  private readonly maxUnconfirmedGoalPolls: number;
  private readonly goalReplyPollIntervalMs: number;
  private snapshot: ChatSnapshot;

  constructor(
    transport: ChatTransport,
    sessionId: string,
    options: {
      retryBaseMs?: number;
      maxAttempts?: number;
      maxUnconfirmedGoalPolls?: number;
      goalReplyPollIntervalMs?: number;
    } = {},
  ) {
    this.transport = transport;
    this.sessionId = sessionId;
    this.enc = encodeURIComponent(sessionId);
    this.retryBaseMs = options.retryBaseMs ?? 500;
    this.maxAttempts = options.maxAttempts ?? 8;
    this.maxUnconfirmedGoalPolls = options.maxUnconfirmedGoalPolls ?? MAX_UNCONFIRMED_GOAL_POLLS;
    this.goalReplyPollIntervalMs = options.goalReplyPollIntervalMs ?? GOAL_REPLY_POLL_INTERVAL_MS;
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
    for (const poll of this.goalReplyPolls.values()) poll.controller.abort();
    this.goalReplyPolls.clear();
    this.goalReplyOwners.clear();
    this.uncertainGoalAdmissions.clear();
    this.confirmedGoalAdmissions.clear();
    this.goalReplies = {};
    clearTimeout(this.timer);
    const wake = this.wake;
    this.wake = null;
    wake?.();
    this.listeners.clear();
    this.snapshot = this.snapshotOf();
  }

  /** Rechecks an uncertain private goal without resubmitting its input. */
  checkGoalReply(inputId: string): void {
    if (this.goalReplies[inputId]?.state !== "uncertain") return;
    const ownerId = this.goalReplyOwners.get(inputId);
    if (!ownerId || this.uncertainGoalAdmissions.get(inputId) !== ownerId) return;
    this.startGoalReplyPolling(inputId, ownerId, true);
  }

  /** Shows the input at once as a pending row, then posts it. A failed post leaves the row as failed, and rethrows. */
  async send(input: InputRequest): Promise<void> {
    const terminalGoalReply = this.goalReplies[input.id]?.state === "complete" ||
      this.goalReplies[input.id]?.state === "cancelled";
    const tracksGoalReply = Boolean(input.goal_reply_id) && !terminalGoalReply;
    if (input.goal_reply_id && tracksGoalReply) {
      this.goalReplyOwners.set(input.id, input.goal_reply_id);
      this.setGoalReplyPending(input.id);
    }
    const localSend = this.beginSend(input);
    let response: Response;
    try {
      response = await this.transport.request(`/sessions/${this.enc}/inputs`, {
        method: "POST",
        body: JSON.stringify(input),
      });
    } catch (error) {
      this.endSend(input.id, localSend, false);
      const status = errorStatus(error);
      if (input.goal_reply_id && tracksGoalReply && (status === undefined || status >= 500)) {
        // A dropped response or server error may follow admission. Poll with the same capability while
        // the composer retains this exact input for retry.
        this.markGoalAdmissionUncertain(input.id, input.goal_reply_id);
        this.startGoalReplyPolling(input.id, input.goal_reply_id);
      } else if (input.goal_reply_id && tracksGoalReply && !this.goalReplyPolls.has(input.id)) {
        this.forgetRejectedGoalReply(input.id, input.goal_reply_id);
      }
      throw error;
    }
    this.endSend(input.id, localSend, response.ok);
    if (!response.ok) {
      if (input.goal_reply_id && tracksGoalReply && response.status >= 500) {
        this.markGoalAdmissionUncertain(input.id, input.goal_reply_id);
        this.startGoalReplyPolling(input.id, input.goal_reply_id);
      } else if (input.goal_reply_id && tracksGoalReply && !this.goalReplyPolls.has(input.id)) {
        this.forgetRejectedGoalReply(input.id, input.goal_reply_id);
      }
      throw new Error(`send failed: ${response.status}`);
    }
    if (input.goal_reply_id && tracksGoalReply) this.markGoalReplyAdmitted(input.id, input.goal_reply_id);
  }

  /** Adds the pending row at the end of the rows. A send whose id is already on screen adds nothing. */
  private beginSend(input: InputRequest): LocalSend | undefined {
    const send = this.sends.get(input.id);
    if (send) {
      send.posts += 1;
      if (send.row.status === "failed") this.replaceLocalRow(send, { ...send.row, status: "pending" });
      return send;
    }
    if (this.state.rows.some((row) => row.id === input.id)) return undefined;
    const row: ChatRow = {
      id: input.id,
      seq: -1, // not on the stream yet
      kind: "input",
      role: "user",
      turn_id: null,
      status: "pending",
      parts: [
        { type: "text", text: input.text },
        ...(input.attachments ?? []).map((attachment) => ({ type: "image" as const, url: attachment.data_url })),
      ],
      created_at: new Date().toISOString(),
    };
    const localSend = { row, posts: 1, succeeded: false };
    this.sends.set(input.id, localSend);
    this.publish();
    return localSend;
  }

  /** Records one POST's answer. A failure with no other POST for the id still running marks the row failed. */
  private endSend(id: string, send: LocalSend | undefined, ok: boolean): void {
    if (!send || this.sends.get(id) !== send) return; // retired or replaced while the POST was in flight
    send.posts -= 1;
    send.succeeded ||= ok;
    if (!send.succeeded && send.posts === 0) this.replaceLocalRow(send, { ...send.row, status: "failed" });
  }

  private replaceLocalRow(send: LocalSend, row: ChatRow): void {
    send.row = row;
    this.publish();
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
    const goalInputId = this.activeGoalInputId();
    const ownerId = goalInputId ? this.goalReplyOwners.get(goalInputId) : undefined;
    if (goalInputId && ownerId) {
      const response = await this.transport.request(`/sessions/${this.enc}/cancel-owned`, {
        method: "POST",
        body: JSON.stringify({ request_id: ownerId }),
      });
      if (!response.ok) throw new Error(`stop failed: ${response.status}`);
      let result: unknown;
      try {
        result = await response.json();
      } catch {
        result = null;
      }
      if (isRecord(result) && result.cancelled === true) {
        this.finishGoalReply(goalInputId, ownerId, { state: "cancelled" });
      }
      return;
    }
    const response = await this.transport.request(`/sessions/${this.enc}/cancel`, { method: "POST" });
    if (!response.ok) throw new Error(`stop failed: ${response.status}`);
  }

  /** Finds the private goal owned by the currently active shared turn, or a private-only pending goal. */
  private activeGoalInputId(): string | null {
    const activeTurns = Object.values(this.state.turns).filter((turn) => turn.state === "running");
    for (const turn of activeTurns) {
      const inputId = turn.input_ids.find((candidate) => this.goalReplyOwners.has(candidate));
      if (inputId) return inputId;
    }
    if (activeTurns.length === 0) {
      const pending = Object.entries(this.goalReplies).filter(([inputId, reply]) =>
        reply.state === "pending" && this.goalReplyOwners.has(inputId),
      );
      return pending.at(-1)?.[0] ?? null;
    }
    return null;
  }

  private markGoalAdmissionUncertain(inputId: string, ownerId: string): void {
    if (this.goalReplyOwners.get(inputId) !== ownerId) return;
    if (this.state.inputs[inputId] || this.confirmedGoalAdmissions.get(inputId) === ownerId) {
      this.confirmGoalReplyAdmission(inputId, ownerId, true);
      return;
    }
    this.uncertainGoalAdmissions.set(inputId, ownerId);
  }

  private markGoalReplyAdmitted(inputId: string, ownerId: string): void {
    this.confirmGoalReplyAdmission(inputId, ownerId, false);
    // A manual resend can resolve an earlier ambiguous admission while its poll is still in flight.
    // Abort that poll so a stale `missing` cannot race the successful admission response.
    this.startGoalReplyPolling(inputId, ownerId, true);
  }

  private confirmGoalReplyAdmission(inputId: string, ownerId: string, restartPoll: boolean): void {
    if (this.goalReplyOwners.get(inputId) !== ownerId) return;
    this.uncertainGoalAdmissions.delete(inputId);
    this.confirmedGoalAdmissions.set(inputId, ownerId);
    if (restartPoll) this.startGoalReplyPolling(inputId, ownerId, true);
  }

  private reconcileGoalAdmission(inputId: string): void {
    const ownerId = this.goalReplyOwners.get(inputId);
    if (!ownerId) return;
    const wasUncertain = this.uncertainGoalAdmissions.get(inputId) === ownerId;
    this.confirmGoalReplyAdmission(inputId, ownerId, false);
    const poll = this.goalReplyPolls.get(inputId);
    if (wasUncertain || !poll || poll.ownerId !== ownerId || poll.controller.signal.aborted) {
      this.startGoalReplyPolling(inputId, ownerId, true);
    }
  }

  private startGoalReplyPolling(inputId: string, ownerId: string, restart = false): void {
    if (this.disposed || this.goalReplyOwners.get(inputId) !== ownerId) return;
    if (this.goalReplies[inputId]?.state === "complete" || this.goalReplies[inputId]?.state === "cancelled") return;
    const previous = this.goalReplyPolls.get(inputId);
    if (!restart && previous?.ownerId === ownerId && !previous.controller.signal.aborted) return;
    previous?.controller.abort();
    const controller = new AbortController();
    this.goalReplyPolls.set(inputId, { ownerId, controller });
    this.setGoalReplyPending(inputId);
    void this.pollGoalReply(inputId, ownerId, controller);
  }

  private setGoalReplyPending(inputId: string): void {
    if (this.goalReplies[inputId]?.state === "pending") return;
    this.goalReplies = { ...this.goalReplies, [inputId]: { state: "pending" } };
    this.publish();
  }

  private forgetRejectedGoalReply(inputId: string, ownerId: string): void {
    // An earlier request may have been admitted even when a later manual retry was rejected.
    if (this.uncertainGoalAdmissions.get(inputId) === ownerId) {
      if (!this.goalReplyPolls.has(inputId)) this.setGoalReplyUncertain(inputId, ownerId);
      return;
    }
    if (this.confirmedGoalAdmissions.get(inputId) === ownerId) return;
    if (this.goalReplyOwners.get(inputId) === ownerId) this.goalReplyOwners.delete(inputId);
    this.confirmedGoalAdmissions.delete(inputId);
    if (this.goalReplies[inputId]?.state !== "pending") return;
    const next = { ...this.goalReplies };
    delete next[inputId];
    this.goalReplies = next;
    this.publish();
  }

  private async pollGoalReply(inputId: string, ownerId: string, controller: AbortController): Promise<void> {
    let unconfirmedPolls = 0;
    let confirmedMissingPolls = 0;
    while (this.isCurrentGoalPoll(inputId, ownerId, controller)) {
      try {
        const response = await this.transport.request(`/sessions/${this.enc}/goal-replies/poll`, {
          method: "POST",
          body: JSON.stringify({ goal_reply_id: ownerId }),
          signal: controller.signal,
        });
        if (!this.isCurrentGoalPoll(inputId, ownerId, controller)) return;
        if (response.ok) {
          const result: unknown = await response.json();
          if (!this.isCurrentGoalPoll(inputId, ownerId, controller)) return;
          if (
            isRecord(result) && result.status === "complete" &&
            typeof result.success === "boolean" && typeof result.message === "string"
          ) {
            this.finishGoalReply(inputId, ownerId, {
              state: "complete",
              success: result.success,
              message: result.message,
            });
            return;
          }
          if (isRecord(result) && result.status === "pending") {
            // A pending mailbox can only exist after the server admitted this private goal.
            this.confirmGoalReplyAdmission(inputId, ownerId, false);
            unconfirmedPolls = 0;
            confirmedMissingPolls = 0;
          }
          if (isRecord(result) && result.status === "missing") {
            if (this.uncertainGoalAdmissions.get(inputId) === ownerId) {
              // A missing mailbox does not prove that an ambiguous POST was not admitted. Keep
              // polling briefly; a later snapshot or input.state can confirm admission as well.
            } else if (
              this.confirmedGoalAdmissions.get(inputId) === ownerId &&
              this.state.inputs[inputId]?.state !== "ended"
            ) {
              // A confirmed input may have lost its mailbox on restart. Bound misses so stale or
              // absent stream state remains manually recoverable instead of polling forever.
              confirmedMissingPolls += 1;
              if (confirmedMissingPolls >= this.maxUnconfirmedGoalPolls) {
                this.uncertainGoalAdmissions.set(inputId, ownerId);
                this.setGoalReplyUncertain(inputId, ownerId);
                return;
              }
            } else {
              this.finishGoalReply(inputId, ownerId, {
                state: "complete",
                success: false,
                message: typeof result.message === "string"
                  ? result.message
                  : "Goal reply expired or is unavailable.",
              });
              return;
            }
          }
        }
      } catch {
        if (!this.isCurrentGoalPoll(inputId, ownerId, controller)) return;
      }
      if (this.uncertainGoalAdmissions.get(inputId) === ownerId) {
        unconfirmedPolls += 1;
        if (unconfirmedPolls >= this.maxUnconfirmedGoalPolls) {
          this.setGoalReplyUncertain(inputId, ownerId);
          return;
        }
      } else if (this.confirmedGoalAdmissions.get(inputId) === ownerId) {
        unconfirmedPolls = 0;
      }
      await new Promise<void>((resolve) => setTimeout(resolve, this.goalReplyPollIntervalMs));
    }
  }

  private setGoalReplyUncertain(inputId: string, ownerId: string): void {
    if (
      this.disposed || this.goalReplyOwners.get(inputId) !== ownerId ||
      this.uncertainGoalAdmissions.get(inputId) !== ownerId
    ) return;
    this.goalReplyPolls.get(inputId)?.controller.abort();
    this.goalReplyPolls.delete(inputId);
    if (this.goalReplies[inputId]?.state === "uncertain") return;
    this.goalReplies = { ...this.goalReplies, [inputId]: { state: "uncertain" } };
    this.publish();
  }

  private isCurrentGoalPoll(inputId: string, ownerId: string, controller: AbortController): boolean {
    return !this.disposed && !controller.signal.aborted &&
      this.goalReplyOwners.get(inputId) === ownerId &&
      this.goalReplyPolls.get(inputId)?.controller === controller;
  }

  private finishGoalReply(inputId: string, ownerId: string, reply: LocalGoalReply): void {
    if (this.disposed || this.goalReplyOwners.get(inputId) !== ownerId) return;
    this.goalReplyOwners.delete(inputId);
    this.uncertainGoalAdmissions.delete(inputId);
    this.confirmedGoalAdmissions.delete(inputId);
    const poll = this.goalReplyPolls.get(inputId);
    poll?.controller.abort();
    this.goalReplyPolls.delete(inputId);
    this.goalReplies = { ...this.goalReplies, [inputId]: reply };
    this.publish();
  }

  private clearGoalReplies(): void {
    for (const poll of this.goalReplyPolls.values()) poll.controller.abort();
    this.goalReplyPolls.clear();
    this.goalReplyOwners.clear();
    this.uncertainGoalAdmissions.clear();
    this.confirmedGoalAdmissions.clear();
    this.goalReplies = {};
  }

  async loadOlder(): Promise<void> {
    const cursor = this.state.olderCursor;
    // Until a snapshot restores the window, its cursor is not trusted: a resync, or a 409 that called it stale, may have left it.
    if (cursor === null || this.state.lastSeq === null) return;
    // The page belongs to the window it was asked for. A resync, a snapshot or a replace while it is in flight replaces that window.
    const { conversationUid } = this.state;
    const epoch = this.epoch;
    const pageRequestId = ++this.pageRequestId;
    const isLatestRequest = () => this.pageRequestId === pageRequestId;
    const current = () =>
      isLatestRequest() && this.epoch === epoch && this.state.conversationUid === conversationUid;
    this.setPaging("loading");
    const path = `/sessions/${this.enc}/rows?before=${encodeURIComponent(cursor)}&limit=${HISTORY_PAGE_LIMIT}`;
    let page: HistoryPage;
    try {
      const response = await this.requestHistory(path);
      if (response === null) {
        // A stale cursor: forget it and resync. The next snapshot brings a fresh one, so this cursor is never retried.
        if (current()) {
          this.state = { ...this.state, olderCursor: null };
          this.resync();
          this.settlePaging();
        } else if (isLatestRequest()) {
          this.settlePaging();
        }
        return;
      }
      page = (await response.json()) as HistoryPage;
      if (!Array.isArray(page.rows)) throw new Error("history failed: unexpected page");
    } catch (error) {
      if (current()) this.setPaging("failed");
      else if (isLatestRequest()) this.settlePaging();
      else return;
      throw error;
    }
    if (!current()) {
      // The window changed while the page was in flight, so its rows and cursor do not belong to it.
      if (isLatestRequest()) this.settlePaging();
      return;
    }
    const held = new Set(this.state.rows.map((row) => row.id));
    const older = page.rows.filter((row) => !held.has(row.id));
    this.state = { ...this.state, rows: [...older, ...this.state.rows], olderCursor: cursorOf(page.older) };
    this.keepUndelivered();
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
      rows: this.sends.size === 0 ? this.state.rows : [...this.state.rows, ...Array.from(this.sends.values(), (send) => send.row)],
      inputs: this.state.inputs,
      turns: this.state.turns,
      goalReplies: this.goalReplies,
      running: turnsRunning(this.state.turns) || Object.values(this.goalReplies).some((reply) => reply.state === "pending"),
      connection: this.connectionState,
      hasEarlier: this.state.olderCursor !== null && this.state.lastSeq !== null,
      olderStatus: this.paging,
    };
  }

  /** Builds the next snapshot and tells every listener once. */
  private publish(): void {
    this.snapshot = this.snapshotOf();
    for (const listener of [...this.listeners]) listener();
  }

  /** Sets the paging state from the window: complete when the window is known to have no older rows, idle otherwise. */
  private settlePaging(): void {
    this.setPaging(this.state.lastSeq !== null && this.state.olderCursor === null ? "complete" : "idle");
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
      let openedAt: number | null = null;
      this.controller = controller;
      const cursor: ChatStreamCursor | null = this.state.lastSeq === null ||
        this.state.conversationUid === null || this.state.generation === null
        ? null
        : {
            after: this.state.lastSeq,
            conversation_uid: this.state.conversationUid,
            generation: this.state.generation,
          };
      this.connectionState = "connecting";
      this.publish();
      try {
        await this.transport.streamChatEvents(
          this.sessionId,
          cursor,
          controller.signal,
          (event) => this.onEvent(controller, event),
          () => {
            if (this.controller === controller && !controller.signal.aborted) openedAt = performance.now();
            this.onOpen(controller);
          },
          () => { if (this.controller === controller && !controller.signal.aborted) this.resync(); },
        );
      } catch {
        // A refused, failed or aborted connection counts as a drop below.
      }
      this.controller = null;
      if (this.disposed) return;
      if (openedAt !== null && performance.now() - openedAt >= HEALTHY_CONNECTION_MS) this.attempts = 0;
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
    if (event.type === "snapshot") {
      if (this.lastConversationUid !== null && this.lastConversationUid !== event.conversation_uid) {
        this.sends.clear();
        this.clearGoalReplies();
      }
      this.lastConversationUid = event.conversation_uid;
    }
    this.state = result.state;
    if (event.type === "snapshot") {
      for (const input of event.data.inputs) this.reconcileGoalAdmission(input.id);
    } else if (event.type === "input.state") {
      this.reconcileGoalAdmission(event.data.id);
    }
    // A new window or deletion invalidates an in-flight page, which may contain stale rows.
    if (event.type === "snapshot" || event.type === "row.delete") {
      this.epoch += 1;
      this.paging = this.state.olderCursor === null ? "complete" : "idle";
    } else if (event.type === "replace") {
      // The replacement event carries rows but no older-page cursor. Keep its
      // rows visible, discard the previous window's cursor, and resnapshot so
      // paging uses a cursor for the replacement transcript.
      this.state = { ...this.state, olderCursor: null };
      this.paging = "idle";
    }
    this.followSends(event);
    if (event.type !== "snapshot") this.attempts = 0;
    if (event.type === "replace") this.resync();
    this.publish();
  }

  /** A row event with a local send's id retires that send: the stream's row now stands in its place. */
  private followSends(event: ChatStreamEvent): void {
    if (event.type === "row.upsert" || event.type === "row.delete") this.sends.delete(event.data.id);
    else if (event.type === "snapshot" || event.type === "replace") this.keepUndelivered();
  }

  /**
   * After a snapshot, a replace, or a history page, the window's row wins over a local row with the same id. A local row the window
   * lacks stays at the end until delivery, including after a failure, so the text stays on screen.
   */
  private keepUndelivered(): void {
    const inWindow = new Set(this.state.rows.map((row) => row.id));
    for (const id of this.sends.keys()) {
      if (inWindow.has(id)) this.sends.delete(id);
    }
  }

  /** Keeps rows on screen, forgets the cursor, and ends the connection so the loop reconnects without `after`. */
  private resync(): void {
    this.epoch += 1;
    this.state = { ...this.state, conversationUid: null, generation: null, lastSeq: null };
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

// React access: ChatStoreProvider puts one session's store in reach of the chat's components, and
// useChatStore reads its snapshot (re-rendering when the store publishes a new one) plus its actions.

/** What a component reads from the chat store: the current snapshot and the store's actions. */
export type ChatStoreView = ChatSnapshot & {
  readonly send: LiveStore["send"];
  readonly sendNow: LiveStore["sendNow"];
  readonly cancel: LiveStore["cancel"];
  readonly stop: LiveStore["stop"];
  readonly loadOlder: LiveStore["loadOlder"];
  readonly retry: LiveStore["retry"];
  readonly checkGoalReply: LiveStore["checkGoalReply"];
};

const ChatStoreContext = createContext<LiveStore | null>(null);

/** Puts one session's chat store in reach of useChatStore below it. */
export function ChatStoreProvider(props: { store: LiveStore; children?: ReactNode }) {
  return createElement(ChatStoreContext.Provider, { value: props.store }, props.children);
}

/** The chat store's snapshot and actions. The view object changes only when the snapshot does. */
export function useChatStore(): ChatStoreView {
  const store = useContext(ChatStoreContext);
  if (!store) throw new Error("useChatStore needs a ChatStoreProvider above it");
  const snap = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return useMemo(
    () => ({
      ...snap,
      send: (input) => store.send(input),
      sendNow: (inputId) => store.sendNow(inputId),
      cancel: (inputId) => store.cancel(inputId),
      stop: () => store.stop(),
      loadOlder: () => store.loadOlder(),
      retry: () => store.retry(),
      checkGoalReply: (inputId) => store.checkGoalReply(inputId),
    }),
    [snap, store],
  );
}
