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

export type LiveTurn = { parts: LivePart[] };

export function emptyLiveTurn(): LiveTurn {
  return { parts: [] };
}

type EventLike = {
  type?: string;
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
export function applyLiveEvent(turn: LiveTurn, event: EventLike): LiveTurn {
  const type = String(event.type ?? "");
  if (type === "token") {
    const delta = typeof event.text === "string" ? event.text : "";
    if (!delta) return turn;
    const parts = [...turn.parts];
    const last = parts[parts.length - 1];
    if (last && last.type === "text") parts[parts.length - 1] = { type: "text", text: last.text + delta };
    else parts.push({ type: "text", text: delta });
    return { parts };
  }
  if (type === "acp_update") {
    const update = isRecord(event.update) ? event.update : null;
    if (!update) return turn;
    if (update.sessionUpdate !== "tool_call" && update.sessionUpdate !== "tool_call_update") return turn;
    const toolCallId = String(update.toolCallId ?? update.tool_call_id ?? "");
    if (!toolCallId) return turn;
    const parts = [...turn.parts];
    const existing = findTool(parts, toolCallId);
    // Vendor titles are display text, never native tool-UI routing names.
    const merged = applyHarnessUpdate(
      existing ?? { type: "tool-call", toolCallId, toolName: "harness_tool", args: {}, argsText: "{}" },
      update,
    );
    return { parts: existing ? parts.map((part) => (part === existing ? merged : part)) : [...parts, merged] };
  }
  if (type === "tool_start") {
    if (event.acp_passthrough) return turn;
    const toolCallId = String(event.tool_id ?? "");
    const toolName = String(event.tool_name ?? "tool");
    if (!toolCallId) return turn;
    if (findTool(turn.parts, toolCallId)) return turn;
    const args = (isRecord(event.input) ? event.input : {}) as ReadonlyJSONObject;
    return {
      parts: [...turn.parts, {
        type: "tool-call",
        toolCallId,
        toolName,
        args,
        argsText: JSON.stringify(args),
        isPreliminary: true,
      }],
    };
  }
  if (type === "tool_result") {
    if (event.acp_passthrough) return turn;
    const toolCallId = String(event.tool_id ?? "");
    if (!toolCallId) return turn;
    const existing = findTool(turn.parts, toolCallId);
    if (!existing) return turn;
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
    return { parts: turn.parts.map((part) => (part === existing ? merged : part)) };
  }
  return turn;
}

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
    status: { type: "running" },
  };
}

export const isLiveMessage = (message: { id?: unknown }): boolean =>
  String(message.id ?? "") === LIVE_MESSAGE_ID;
