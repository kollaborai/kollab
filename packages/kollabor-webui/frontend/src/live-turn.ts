import type { ThreadMessageLike } from "@assistant-ui/react";
import type { ReadonlyJSONObject } from "assistant-stream/utils";

/**
 * The live render of a turn this window did not start.
 *
 * The window that runs a turn streams through its own assistant transport;
 * every other window watching the session only hears the session's event
 * stream (token / acp_update / tool events). These build an ephemeral
 * assistant message here so a passive window sees the reply as it streams.
 * Nothing is persisted: App reconciles history after terminal events and this
 * live copy stays visible until its matching saved assistant row arrives. Its
 * id stays fixed through a turn and out of the synced bookkeeping.
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
  request_ids?: unknown;
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
  message?: unknown;
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
 * The request a turn's event names. An ACP turn names its one request
 * (`request_id`); any other turn names the requests of its sends
 * (`request_ids`), and the first stays first when a later send is steered in.
 * With it a turn the page only watches keeps its words through its end, until
 * the saved reply takes their place (#1359).
 */
const eventRequestId = (event: EventLike): string => {
  if (typeof event.request_id === "string") return event.request_id;
  const first = Array.isArray(event.request_ids) ? event.request_ids[0] : undefined;
  return typeof first === "string" ? first : "";
};

/** Whether an event names `requestId`: as its one request, or among its turn's sends. */
export const eventNamesRequest = (event: EventLike, requestId: string): boolean =>
  event.request_id === requestId || (Array.isArray(event.request_ids) && event.request_ids.includes(requestId));

/**
 * Fold one session-stream event into the live turn. Returns the same object
 * when the event carries nothing renderable, so callers can skip the import.
 */
export function applyLiveEvent(current: LiveTurn, event: EventLike): LiveTurn {
  // A cancelled turn's tool that ignored the stop signal reports late, marked
  // by the engine: it belongs to a turn that already ended.
  if (event.cancelled) return current;
  const type = String(event.type ?? "");
  const requestId = eventRequestId(event);
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
    if (!current.requestId) {
      const hasHistoryNotice = current.parts.some(
        (part) => part.type === "text" && part.text.includes("ℹ Conversation history not saved for this "),
      );
      if (!hasHistoryNotice) return emptyLiveTurn();
      return {
        ...current,
        settled: type === "error" ? "error" : type === "turn_cancelled" ? "cancelled" : "complete",
      };
    }
    // An ACP turn gets one terminal event of its own after the engine's
    // turn_cancelled, which fires the moment a stop is asked, before the daemon
    // stored the reply. Keep the text until the history brings it.
    if (current.settled) return current;
    // Nothing left to wait for (history already shows every part): a turn
    // settled with no parts would hold the next one back for good.
    if (!current.parts.length) return { parts: [], requestId: current.requestId, settled: "replaced" };
    return { ...current, settled: type === "turn_cancelled" ? "cancelled" : type === "error" ? "error" : "complete" };
  }
  // A settled turn shows nothing more; a newer request starts its own.
  const settledNotice = current.settled === "complete" && current.parts.some(
    (part) => part.type === "text" && part.text.includes("ℹ Conversation history not saved for this "),
  );
  if (current.settled && !newer && !settledNotice) return current;
  const turn: LiveTurn = newer || settledNotice ? { parts: [], requestId } : current;
  const id = turn.requestId || requestId;
  const grown = (parts: LivePart[]): LiveTurn => (id ? { parts, requestId: id } : { parts });
  if (type === "notice") {
    const message = typeof event.message === "string" ? event.message : "";
    if (!message || !/^Conversation history not saved for this (message|reply)\.$/.test(message)) return current;
    const parts = [...turn.parts];
    const last = parts.at(-1);
    const notice = `ℹ ${message}`;
    if (last?.type === "text") {
      parts[parts.length - 1] = { type: "text", text: `${last.text}\n\n${notice}` };
    } else {
      parts.push({ type: "text", text: `${notice}\n\n` });
    }
    return grown(parts);
  }
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
    if (update.sessionUpdate === "agent_message_chunk") {
      const content = isRecord(update.content) ? update.content : null;
      const delta = typeof content?.text === "string" ? content.text : "";
      if (!delta) return current;
      const parts = [...turn.parts];
      const last = parts[parts.length - 1];
      if (last?.type === "text") parts[parts.length - 1] = { type: "text", text: last.text + delta };
      else parts.push({ type: "text", text: delta });
      return grown(parts);
    }
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

type HistoryRow = { role?: string; content?: unknown; metadata?: { custom?: Record<string, unknown> } };

/**
 * Whether newly appended history rows carry the reply the live turn showed.
 * Prefer the exact request/voice id; use an unmarked assistant row only for a
 * settled legacy turn whose saved history predates request ids.
 */
export function historyReplacesLiveTurn(turn: LiveTurn, tail: ReadonlyArray<HistoryRow>): boolean {
  if (!turn.requestId || !turn.parts.length || turn.settled === "replaced") return false;
  return tail.some((row) => {
    if (row.role !== "assistant") return false;
    const requestId = row.metadata?.custom?.requestId;
    const voiceTurnId = row.metadata?.custom?.voiceTurnId;
    if (requestId === turn.requestId || voiceTurnId === turn.requestId) return true;
    // Older saved ACP rows may have no request marker. Keep the historical
    // fallback for those rows, but never let a different marked turn replace
    // this one.
    return !!turn.settled && typeof requestId !== "string" && typeof voiceTurnId !== "string";
  });
}

const SAVE_NOTICE = /^ℹ Conversation history not saved for this (message|reply)\.$/;
const oneLine = (text: string): string => text.replace(/\s+/g, " ").trim();

/**
 * The live turn without the parts the loaded history rows now show: a tool
 * call one of them holds, words one of them contains. A page that watches a
 * turn draws it from its events and from the history that lands while it
 * runs; without this both showed every tool call until the turn ended (#1359).
 * A save notice stays: no row holds it. A settled turn with nothing left is
 * replaced (see replacedLiveTurn). Returns the same object when nothing goes.
 */
export function withoutSavedParts(turn: LiveTurn, rows: ReadonlyArray<HistoryRow>): LiveTurn {
  if (!turn.parts.length || turn.settled === "replaced") return turn;
  const calls = new Set<string>();
  const words: string[] = [];
  for (const row of rows) {
    if (row.role !== "assistant") continue;
    if (typeof row.content === "string") { words.push(oneLine(row.content)); continue; }
    if (!Array.isArray(row.content)) continue;
    for (const part of row.content as Array<{ type?: unknown; text?: unknown; toolCallId?: unknown }>) {
      if (part?.type === "tool-call" && typeof part.toolCallId === "string") calls.add(part.toolCallId);
      else if (part?.type === "text" && typeof part.text === "string") words.push(oneLine(part.text));
    }
  }
  const saved = words.join(" ");
  if (!calls.size && !saved) return turn;
  let changed = false;
  const parts = turn.parts.flatMap((part): LivePart[] => {
    if (part.type === "tool-call") {
      if (!calls.has(part.toolCallId)) return [part];
      changed = true;
      return [];
    }
    const lines = part.text.split("\n\n");
    const notices = lines.filter((line) => SAVE_NOTICE.test(line));
    const said = oneLine(lines.filter((line) => !SAVE_NOTICE.test(line)).join("\n\n"));
    if (!said || !saved.includes(said)) return [part];
    changed = true;
    return notices.length ? [{ type: "text", text: notices.join("\n\n") }] : [];
  });
  if (!changed) return turn;
  const next = { ...turn, parts };
  // Only save notices left, if anything: the history holds the turn.
  const left = parts.some((part) => part.type === "tool-call" ||
    !part.text.split("\n\n").every((line) => !line.trim() || SAVE_NOTICE.test(line)));
  return turn.settled && !left ? replacedLiveTurn(next) : next;
}

/**
 * The live turn after history replaced it: its late events stay ignored, and
 * the queued turn it held back starts from its first event.
 */
export const replacedLiveTurn = (turn: LiveTurn): LiveTurn => {
  const notices = turn.parts.flatMap((part) =>
    part.type === "text"
      ? part.text
          .split("\n\n")
          .filter((line) => /^ℹ Conversation history not saved for this (message|reply)\.$/.test(line))
          .map((text) => ({ type: "text" as const, text }))
      : [],
  );
  if (turn.held?.length) {
    const queued = turn.held.reduce(applyLiveEvent, {
      parts: [],
      requestId: turn.requestId,
      settled: "replaced",
    });
    return notices.length ? { ...queued, parts: [...notices, ...queued.parts] } : queued;
  }
  return {
    parts: notices,
    requestId: turn.requestId,
    settled: notices.length ? "complete" : "replaced",
  };
};

/**
 * The ephemeral assistant message, or null while nothing has streamed.
 *
 * `isOptimistic` tells assistant-ui this message stands in for one that has
 * not landed yet. It keeps every message it was given, so a live reply that
 * history replaces would stay behind as a sibling of the stored one and show as
 * a "2 / 2" branch picker. For an optimistic message it evicts the left-behind
 * copy itself once the thread's last message is somewhere else.
 *
 * Each request gets its own message: a queued turn's reply mounts fresh, never
 * as the previous reply's element re-rendering, which showed that reply twice
 * beside its stored copy until the deferred markdown caught up.
 */
export function liveMessage(turn: LiveTurn): ThreadMessageLike | null {
  const parts = turn.parts.filter(
    (part) => (part.type === "text" && part.text) || part.type === "tool-call",
  );
  if (!parts.length) return null;
  const standaloneNotice = !turn.requestId && parts.every(
    (part) => part.type === "text" &&
      /^ℹ Conversation history not saved for this (message|reply)\.$/.test(part.text.trim()),
  );
  return {
    id: turn.requestId ? `${LIVE_MESSAGE_ID}:${turn.requestId}` : LIVE_MESSAGE_ID,
    role: "assistant",
    content: parts,
    status: SETTLED_STATUS[turn.settled ?? ""] ?? (standaloneNotice ? SETTLED_STATUS.complete : { type: "running" }),
    metadata: { isOptimistic: true },
  };
}

/**
 * The events after which the history reload brings the finished turn (or the
 * stopped one's stored reply) and the ephemeral message goes. A stop alone
 * (`turn_cancelled`) only freezes it: see applyLiveEvent.
 */
export const endsLiveTurn = (type: string): boolean => type === "turn_complete" || type === "error";

export const isLiveMessage = (message: { id?: unknown }): boolean => {
  const id = String(message.id ?? "");
  return id === LIVE_MESSAGE_ID || id.startsWith(`${LIVE_MESSAGE_ID}:`);
};

const apartCopies = new WeakMap<object, object>();

/**
 * Keeps the live reply a message of its own. assistant-ui joins adjacent
 * assistant messages into one, so a live reply that follows a stored one would
 * be a part of it, then leave it and mount again as a message of its own when
 * history lands a user row between them: the text blinks out for a few frames.
 * The join strategy `none` on a message keeps the next one out of it. The copy
 * is made per message, so a stored reply that no longer has the live one behind
 * it joins again.
 */
export function apartFromLive<T extends { id?: unknown; role?: string }>(messages: readonly T[]): T[] {
  return messages.map((message, index) => {
    const next = messages[index + 1];
    if (message.role !== "assistant" || !next || !isLiveMessage(next)) return message;
    let copy = apartCopies.get(message);
    if (!copy) {
      copy = { ...message, convertConfig: { joinStrategy: "none" } };
      apartCopies.set(message, copy);
    }
    return copy as T;
  });
}
