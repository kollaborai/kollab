// The web UI's chat stream reducer (#1451). It applies one stream event to the
// state the chat shows. Pure: no React, no fetch, and it never mutates its input.
// Spec: docs/specs/webui-chat-single-stream.md. Shapes: ./chat-stream.ts.

import {
  isChatStreamEvent,
  type ChatPart,
  type ChatRow,
  type ChatStreamEvent,
  type InputInfo,
  type TurnInfo,
} from "./chat-stream";

export type ConnectionState = "connecting" | "live" | "reconnecting" | "failed";

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
