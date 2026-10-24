import type { ThreadMessageLike } from "@assistant-ui/react";
import type { ReadonlyJSONObject } from "assistant-stream/utils";

/**
 * The live render of a turn this window did not start.
 *
 * The window that runs a turn streams through its own assistant transport;
 * every other window watching the session only hears the session's event
 * stream (token / acp_update / tool events). These build an ephemeral
 * assistant message here so a passive window sees the reply as it streams.
 * Nothing is persisted: the history reload at turn_complete replaces it, so
 * the id must stay fixed and stay out of the synced bookkeeping.
 */

export const LIVE_MESSAGE_ID = "live-turn";

type LiveTextPart = { type: "text"; text: string };

type LiveToolPart = {
  type: "tool-call";
  toolCallId: string;
  toolName: string;
  args: ReadonlyJSONObject;
  argsText: string;
  result?: { success: boolean; output?: unknown; metadata?: Record<string, unknown> };
  isError?: boolean;
  isPreliminary?: boolean;
};

type LivePart = LiveTextPart | LiveToolPart;

/**
 * `requestId`: the ACP request whose events built the parts, so another
 * request's terminal event cannot end this one. `settled`: its turn ended
 * (finished, stopped or failed); the text stays on screen, no longer running,
 * until the history that carries the stored reply replaces it, so there is no
 * gap between the live reply and the stored one. `replaced`: that happened.
 * `held`: a queued turn's events that came while this one waited for its
 * stored reply; they replay once it is replaced, so its text never blinks out.
 */
export type LiveTurn = {
  parts: LivePart[];
  requestId?: string;
  settled?: "complete" | "cancelled" | "error" | "replaced";
  held?: EventLike[];
};

export function emptyLiveTurn(): LiveTurn {
  return { parts: [] };
}

type EventLike = {
  type?: string;
  request_id?: unknown;
  cancelled?: unknown;
  text?: unknown;
  update?: unknown;
  acp_passthrough?: unknown;
  tool_id?: unknown;
  tool_name?: unknown;
  input?: unknown;
  success?: unknown;
  output?: unknown;
  error?: unknown;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const findTool = (parts: LivePart[], toolCallId: string): LiveToolPart | undefined =>
  parts.find((part): part is LiveToolPart =>
    part.type === "tool-call" && part.toolCallId === toolCallId);

/** Merge one raw ACP session update into the tool card it describes. */
function applyHarnessUpdate(part: LiveToolPart, update: Record<string, unknown>): LiveToolPart {
  const harness: Record<string, unknown> = {
    ...(isRecord(part.result?.metadata?.acp) ? part.result.metadata.acp : {}),
    ...Object.fromEntries(Object.entries(update).filter(([, value]) => value != null)),
  };
  const rawInput = harness.rawInput;
  const args = (isRecord(rawInput) ? rawInput : {}) as ReadonlyJSONObject;
  return {
    ...part,
    args,
    argsText: JSON.stringify(args),
    result: {
      success: harness.status !== "failed",
      output: harness.rawOutput ?? harness.content ?? "",
      metadata: { acp: harness },
    },
    isError: harness.status === "failed",
    isPreliminary: harness.status !== "completed" && harness.status !== "failed",
  };
}

/**
 * Fold one session-stream event into the live turn. Returns the same object
 * when the event carries nothing renderable, so callers can skip the import.
 */
export function applyLiveEvent(current: LiveTurn, event: EventLike): LiveTurn {
  // A cancelled turn's tool that ignored the stop signal reports late, marked
  // by the engine: it belongs to a turn that already ended.
  if (event.cancelled) return current;
  const type = String(event.type ?? "");
  const requestId = typeof event.request_id === "string" ? event.request_id : "";
  const newer = !!requestId && !!current.requestId && requestId !== current.requestId;
  if (newer && current.settled && current.settled !== "replaced") {
    return { ...current, held: [...(current.held ?? []), event] };
  }
  if (type === "turn_cancelled" || endsLiveTurn(type)) {
    // The end of an earlier turn can land after the next one started.
    if (!current.parts.length && !current.requestId) return current;
    if (newer) return current;
    // Not an ACP turn (no request id): the reload this event triggers brings
    // its reply at once.
    if (!current.requestId) return emptyLiveTurn();
    // An ACP turn gets one terminal event of its own after the engine's
    // turn_cancelled, which fires the moment a stop is asked, before the daemon
    // stored the reply. Keep the text until the history brings it.
    if (current.settled) return current;
    return { ...current, settled: type === "turn_cancelled" ? "cancelled" : type === "error" ? "error" : "complete" };
  }
  // A settled turn shows nothing more; a newer request starts its own.
  if (current.settled && !newer) return current;
  const turn: LiveTurn = newer ? { parts: [], requestId } : current;
  const id = turn.requestId || requestId;
  const grown = (parts: LivePart[]): LiveTurn => (id ? { parts, requestId: id } : { parts });
  if (type === "token") {
    const delta = typeof event.text === "string" ? event.text : "";
    if (!delta) return current;
    const parts = [...turn.parts];
    const last = parts[parts.length - 1];
    if (last && last.type === "text") parts[parts.length - 1] = { type: "text", text: last.text + delta };
    else parts.push({ type: "text", text: delta });
    return grown(parts);
  }
  if (type === "acp_update") {
    const update = isRecord(event.update) ? event.update : null;
    if (!update) return current;
    if (update.sessionUpdate !== "tool_call" && update.sessionUpdate !== "tool_call_update") return current;
    const toolCallId = String(update.toolCallId ?? update.tool_call_id ?? "");
    if (!toolCallId) return current;
    const parts = [...turn.parts];
    const existing = findTool(parts, toolCallId);
    // Vendor titles are display text, never native tool-UI routing names.
    const merged = applyHarnessUpdate(
      existing ?? { type: "tool-call", toolCallId, toolName: "harness_tool", args: {}, argsText: "{}" },
      update,
    );
    return grown(existing ? parts.map((part) => (part === existing ? merged : part)) : [...parts, merged]);
  }
  if (type === "tool_start") {
    if (event.acp_passthrough) return current;
    const toolCallId = String(event.tool_id ?? "");
    const toolName = String(event.tool_name ?? "tool");
    if (!toolCallId) return current;
    if (findTool(turn.parts, toolCallId)) return current;
    const args = (isRecord(event.input) ? event.input : {}) as ReadonlyJSONObject;
    return grown([...turn.parts, {
      type: "tool-call",
      toolCallId,
      toolName,
      args,
      argsText: JSON.stringify(args),
      isPreliminary: true,
    }]);
  }
  if (type === "tool_result") {
    if (event.acp_passthrough) return current;
    const toolCallId = String(event.tool_id ?? "");
    if (!toolCallId) return current;
    const existing = findTool(turn.parts, toolCallId);
    if (!existing) return current;
    const result = {
      success: event.success !== false,
      output: event.output ?? "",
      metadata: {},
    };
    const merged: LiveToolPart = {
      ...existing,
      result,
      isError: result.success ? existing.isError : true,
      isPreliminary: false,
    };
    return grown(turn.parts.map((part) => (part === existing ? merged : part)));
  }
  return current;
}

const SETTLED_STATUS: Record<string, NonNullable<ThreadMessageLike["status"]>> = {
  complete: { type: "complete", reason: "stop" },
  cancelled: { type: "incomplete", reason: "cancelled" },
  error: { type: "incomplete", reason: "error" },
};

type HistoryRow = { role?: string; metadata?: { custom?: Record<string, unknown> } };

/**
 * Whether history rows just appended carry the reply the live turn showed.
 * A running turn is replaced only by a row with its own request id; a settled
 * one by any new assistant row (the row may predate request ids on disk).
 */
export function historyReplacesLiveTurn(turn: LiveTurn, tail: ReadonlyArray<HistoryRow>): boolean {
  if (!turn.requestId || !turn.parts.length || turn.settled === "replaced") return false;
  return tail.some((row) => row.role === "assistant" &&
    (!!turn.settled || row.metadata?.custom?.requestId === turn.requestId));
}

/**
 * The live turn after history replaced it: its late events stay ignored, and
 * the queued turn it held back starts from its first event.
 */
export const replacedLiveTurn = (turn: LiveTurn): LiveTurn =>
  (turn.held ?? []).reduce(applyLiveEvent, { parts: [], requestId: turn.requestId, settled: "replaced" });

/** The ephemeral assistant message, or null while nothing has streamed. */
export function liveMessage(turn: LiveTurn): ThreadMessageLike | null {
  const parts = turn.parts.filter(
    (part) => (part.type === "text" && part.text) || part.type === "tool-call",
  );
  if (!parts.length) return null;
  return {
    id: LIVE_MESSAGE_ID,
    role: "assistant",
    content: parts,
    status: SETTLED_STATUS[turn.settled ?? ""] ?? { type: "running" },
  };
}

/**
 * The events after which the history reload brings the finished turn (or the
 * stopped one's stored reply) and the ephemeral message goes. A stop alone
 * (`turn_cancelled`) only freezes it: see applyLiveEvent.
 */
export const endsLiveTurn = (type: string): boolean => type === "turn_complete" || type === "error";

export const isLiveMessage = (message: { id?: unknown }): boolean =>
  String(message.id ?? "") === LIVE_MESSAGE_ID;
