type Message = {
  id?: string;
  role?: string;
  content?: unknown;
  metadata?: { custom?: Record<string, unknown>; isOptimistic?: boolean };
  convertConfig?: { joinStrategy?: string };
};

/** Canonical row identity is scoped to the current history snapshot. Display IDs survive a rebase. */
const sourceId = (message: Message): string | null => {
  const custom = message.metadata?.custom;
  if (compactionArchiveOrder(message) !== null) return null;
  if (typeof custom?.historySourceId === "string") return custom.historySourceId;
  const index = custom?.historySourceIndex;
  if (typeof index === "number" && Number.isInteger(index) && index >= 0) return `history-${index}`;
  return message.id && /^history-\d+(?:-|$)/.test(message.id) ? message.id : null;
};

const historyIndex = (message: Message): number | null => {
  const index = /^history-(\d+)/.exec(sourceId(message) ?? "")?.[1];
  return index === undefined ? null : Number(index);
};

function displayRow<T extends Message>(row: T, id: string | undefined): T {
  if (!id || row.id === id) return row;
  return {
    ...row, id, metadata: { ...row.metadata, custom: {
      ...row.metadata?.custom, historySourceId: sourceId(row), historySourceIndex: historyIndex(row),
    } },
  };
}

function reserveRow<T extends Message>(row: T, used: Set<string | undefined>): T {
  let id = row.id;
  for (let n = 1; used.has(id); n++) id = `history-view-${row.id}-${n}`;
  used.add(id);
  return displayRow(row, id);
}

/** The tool-call ids a message draws. */
const toolCallIds = (message: Message): string[] =>
  Array.isArray(message.content)
    ? message.content.flatMap((part) => part && typeof part === "object" &&
      (part as { type?: unknown }).type === "tool-call" &&
      typeof (part as { toolCallId?: unknown }).toolCallId === "string"
      ? [(part as { toolCallId: string }).toolCallId] : [])
    : [];

/** The source history row for a displayed message, including stable-id aliases. */
export function historyMessageIndex(message: Message): number | null {
  return historyIndex(message);
}

const isLiveRow = (message: Message): boolean => {
  const id = String(message.id ?? "");
  return id === "live-turn" || id.startsWith("live-turn:");
};

/**
 * The page's own last turn: the rows it drew (user-/assistant-<session>-<n>)
 * at the end of the thread, live rows aside. While a run goes on, assistant-ui
 * keeps the last user row with a reply after it at the top of the view, so a
 * row landing inside this turn would become that turn and scroll the reader to
 * it (#1364). The turn sorts as saved where its reply is: rows saved before
 * that go above it, rows saved after it below, and while the reply's position
 * is unknown every history row goes above it.
 */
const trailingTurn = (shown: ReadonlyArray<Message>): { rows: Set<Message>; order: HistoryOrder } | null => {
  // Only its last turn: the engine keeps those ids on rows of turns that
  // ended, which are history like any other. The turn is its reply rows at the
  // end and the sends right before them.
  const rows = new Set<Message>();
  let last: Message | undefined;
  let sends = false;
  for (let index = shown.length - 1; index >= 0; index -= 1) {
    const message = shown[index];
    if (isLiveRow(message)) continue;
    if (!TRANSIENT_ROW_ID.test(String(message.id ?? ""))) break;
    const user = transientRole(message) === "user";
    if (sends && !user) break;
    sends ||= user;
    last ??= message;
    rows.add(message);
  }
  if (![...rows].some((message) => transientRole(message) === "user") || !last) return null;
  const savedAt = transientRole(last) === "assistant" ? historyIndex(last) : null;
  return {
    rows,
    order: savedAt === null
      ? { section: 2, index: 0, piece: Infinity, tie: Infinity }
      : { section: 1, index: savedAt, piece: Infinity, tie: Infinity },
  };
};

/** Insert newly reached history rows without dropping previously shown rows. */
export function mergeMissingHistoryMessages<T extends Message>(
  shown: ReadonlyArray<T>,
  loaded: ReadonlyArray<T>,
): { messages: T[]; added: T[] } {
  const used = new Set(shown.map((message) => message.id));
  const represented = new Set(shown.map(sourceId));
  const order = new Map(loaded.map((message, index) => [sourceId(message), index]));
  // A turn the page ran draws its tool calls inside its reply row; history
  // saves them as calls rows of their own. Adding those before the reply makes
  // assistant-ui join them under another id and keep the reply row as a
  // second branch, so a calls row the reply already draws is not added (#1358).
  // By call id, the saved position of the reply that drew it: a calls row is
  // saved before its reply, and some providers reuse call ids every turn.
  const drawnCalls = new Map<string, number>();
  for (const message of shown) {
    if (transientRole(message) !== "assistant" || !TRANSIENT_ROW_ID.test(String(message.id ?? ""))) continue;
    const at = historyIndex(message) ?? Infinity;
    for (const id of toolCallIds(message)) drawnCalls.set(id, Math.max(drawnCalls.get(id) ?? -Infinity, at));
  }
  const added = loaded.flatMap((message) => {
    const key = sourceId(message);
    if (!key || represented.has(key)) return [];
    const calls = transientRole(message) === "assistant" ? toolCallIds(message) : [];
    const index = historyIndex(message) ?? Infinity;
    if (calls.length && calls.every((id) => (drawnCalls.get(id) ?? -Infinity) > index)) return [];
    represented.add(key);
    return [reserveRow(message, used)];
  });
  const turn = trailingTurn(shown);
  const messages: T[] = [];
  let nextAdded = 0;
  for (const existing of shown) {
    const existingOrder = turn?.rows.has(existing) ? turn.order
      : historyOrder(existing, order.get(sourceId(existing)) ?? Infinity);
    while (nextAdded < added.length) {
      const candidate = added[nextAdded];
      const candidateOrder = historyOrder(candidate, order.get(sourceId(candidate)) ?? Infinity);
      if (compareHistoryOrder(candidateOrder, existingOrder) > 0) break;
      messages.push(candidate);
      nextAdded += 1;
    }
    messages.push(existing);
  }
  while (nextAdded < added.length) messages.push(added[nextAdded++]);
  return { messages, added };
}

/**
 * An older window can reveal the call for a result that was shown as a
 * fallback at the page boundary. Replace only that proven fallback; an older
 * or offline page cannot overwrite any other row already on screen. If the
 * displayed result differs from the cached one, keep the displayed result.
 */
export function reconcileBoundaryToolResults<T extends Message>(
  shown: ReadonlyArray<T>,
  loaded: ReadonlyArray<T>,
  start: number,
  end: number,
): T[] | null {
  type Fallback = { callId: string; message: T; part: Record<string, unknown>; index: number };
  type Call = { messageId: string | undefined; index: number; partIndex: number };
  const pending: Fallback[] = [];
  const represented = new Set(shown.map(sourceId));
  for (const message of shown) {
    const index = historyIndex(message);
    const callId = message.metadata?.custom?.historyToolFallback;
    if (index === null || index < start || index >= end || typeof callId !== "string" ||
      !Array.isArray(message.content)) continue;
    const part = message.content.find((item: unknown) =>
      !!item && typeof item === "object" && (item as { type?: unknown }).type === "tool-call" &&
      (item as { toolCallId?: unknown }).toolCallId === callId);
    if (part) pending.push({ callId, message, part: part as Record<string, unknown>, index });
  }
  if (!pending.length) return null;
  const calls = new Map<string, Call[]>();
  for (const message of loaded) {
    const index = historyIndex(message);
    if (index === null || message.metadata?.custom?.historyToolFallback || !Array.isArray(message.content)) continue;
    message.content.forEach((part: unknown, partIndex: number) => {
      if (!part || typeof part !== "object") return;
      const call = part as Record<string, unknown>;
      if (call.type !== "tool-call" || typeof call.toolCallId !== "string") return;
      const rows = calls.get(call.toolCallId) ?? [];
      rows.push({ messageId: message.id, index, partIndex });
      calls.set(call.toolCallId, rows);
    });
  }
  for (const rows of calls.values()) rows.sort((a, b) => a.index - b.index);
  const matches = new Map<string, Fallback[]>();
  for (const fallback of pending) {
    const rows = calls.get(fallback.callId) ?? [];
    let low = 0;
    let high = rows.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (rows[middle].index < fallback.index) low = middle + 1;
      else high = middle;
    }
    const call = rows[low - 1];
    if (!call || represented.has(call.messageId ?? null) ||
      (low > 1 && rows[low - 2].index === call.index)) continue;
    const key = `${call.messageId}:${call.partIndex}`;
    const group = matches.get(key) ?? [];
    group.push(fallback);
    matches.set(key, group);
  }
  const replaced = new Set<string | undefined>();
  const stableIds = new Map<string | undefined, { id: string | undefined; index: number }>();
  const corrected = loaded.map((message) => {
    const index = historyIndex(message);
    if (index === null || represented.has(sourceId(message)) ||
      message.metadata?.custom?.historyToolFallback || !Array.isArray(message.content)) return message;
    let changed = false;
    const content = message.content.map((part: unknown, partIndex: number) => {
      if (!part || typeof part !== "object") return part;
      const call = part as Record<string, unknown>;
      const group = matches.get(`${message.id}:${partIndex}`);
      if (call.type !== "tool-call" || group?.length !== 1) return part;
      const prior = group[0];
      changed = true;
      replaced.add(prior.message.id);
      stableIds.set(message.id, { id: prior.message.id, index });
      const result = { ...call };
      for (const field of ["result", "isError", "timing"] as const) {
        if (field in prior.part) result[field] = prior.part[field];
      }
      return result;
    });
    return changed ? { ...message, content } as T : message;
  });
  if (!replaced.size) return null;
  const stable = corrected.map((message) => {
    const source = stableIds.get(message.id);
    if (!source) return message;
    const custom: Record<string, unknown> = {
      ...(message.metadata?.custom ?? {}),
      historySourceIndex: source.index,
      historySourceId: sourceId(message),
    };
    delete custom.historyToolFallback;
    return {
      ...message,
      id: source.id,
      metadata: { ...message.metadata, custom },
    } as T;
  });
  return mergeMissingHistoryMessages(shown.filter((message) => !replaced.has(message.id)), stable).messages;
}

/**
 * True when a message the thread already shows reads differently in the
 * reloaded history, or is gone from it. A tool call imported mid-turn gets its
 * result from a later history row, which never makes a new message, so the
 * append merge would leave the card without it. A reply that a new voice turn
 * now follows ends its group, so the turns show apart. A voice talk-over takes
 * away the reply nobody heard. The thread then reloads whole: assistant-ui
 * keeps every message it was given, so re-importing one by id would branch it.
 * `savedBefore` is the loaded history's row count: a row of the page's own run
 * (user-<session>-<n>) that the engine placed at or past it was saved after
 * this history was read, so it is not gone (#1358).
 */
export function shownMessagesChanged(
  shown: ReadonlyArray<Message>, loaded: ReadonlyArray<Message>, savedBefore = Infinity,
): boolean {
  const bySource = new Map(shown.flatMap((message) => {
    const key = sourceId(message);
    return key ? [[key, message] as const] : [];
  }));
  const loadedIds = new Set(loaded.map(sourceId));
  // Rows before the loaded window are older pages, not gone.
  const start = loaded.reduce((low, message) => Math.min(low, historyIndex(message) ?? low), Infinity);
  const gone = shown.some((message) => {
    const index = historyIndex(message);
    if (index !== null && index >= savedBefore && TRANSIENT_ROW_ID.test(String(message.id ?? ""))) return false;
    return index !== null && index >= start && !loadedIds.has(sourceId(message));
  });
  return gone || loaded.some((message) => {
    const key = sourceId(message);
    const old = key ? bySource.get(key) : undefined;
    // The run's own reply keeps what it streamed (see withSavedCopies).
    if (old && TRANSIENT_ROW_ID.test(String(old.id ?? "")) && transientRole(old) === "assistant") return false;
    return old !== undefined && (
      JSON.stringify(old.content) !== JSON.stringify(message.content) ||
      old.convertConfig?.joinStrategy !== message.convertConfig?.joinStrategy
    );
  });
}


type HistoryOrder = { section: number; index: number; piece: number; tie: number };

/** A send's piece of a row the daemon saved for several sends (history-<n>-send-<i>, #1359). */
const pieceOf = (message: Message): number => {
  const piece = /-send-(\d+)$/.exec(sourceId(message) ?? "")?.[1];
  return piece === undefined ? -1 : Number(piece);
};

const compactionArchiveOrder = (message: Message): number | null => {
  const value = message.metadata?.custom?.compactionArchiveOrder;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
};

export const isCompactionMarker = (message: Message): boolean => {
  const value = message.metadata?.custom?.contextCompaction;
  return value === true || value === "true";
};


/** Only a persisted assistant row for this exact request can replace a live turn. */
export function historyHasAssistantRequestId(
  history: ReadonlyArray<Message>,
  requestId: string | null | undefined,
): boolean {
  return !!requestId && history.some((message) =>
    message.role === "assistant" && message.metadata?.custom?.requestId === requestId);
}

const hashText = (value: string): string => {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
};

const compactionMarkerKey = (message: Message): string => {
  const custom = message.metadata?.custom ?? {};
  const fingerprint = JSON.stringify({
    round: custom.compactionRound ?? null,
    before: custom.preMessageCount ?? null,
    after: custom.postMessageCount ?? null,
    content: message.content ?? null,
  });
  return hashText(fingerprint);
};

const historyOrder = (message: Message, tie: number): HistoryOrder => {
  const archived = compactionArchiveOrder(message);
  if (archived !== null) return { section: 0, index: archived, piece: -1, tie };
  const index = historyIndex(message);
  if (index !== null) return { section: 1, index, piece: pieceOf(message), tie };
  return { section: 2, index: 0, piece: -1, tie };
};

const compareHistoryOrder = (left: HistoryOrder, right: HistoryOrder): number =>
  left.section - right.section || left.index - right.index || left.piece - right.piece || left.tie - right.tie;

/**
 * A paged tool result carries a reference signed with its row position, and
 * compaction renumbers the retained rows. The output it showed is what stays.
 */
const stableResult = (result: unknown): unknown => {
  if (!result || typeof result !== "object" ||
    (result as { kind?: unknown }).kind !== "history-output-preview") return result;
  const stable = { ...(result as Record<string, unknown>) };
  delete stable.reference;
  return stable;
};

/**
 * What tells two rows apart besides their words: tool calls (call ids and
 * results) and pictures. A pasted picture is not text, so the same words with
 * another picture are another turn.
 */
const turnParts = (message: Message): unknown[] =>
  Array.isArray(message.content)
    ? message.content.flatMap((part): unknown[] => {
      if (!part || typeof part !== "object") return [];
      const { type } = part as { type?: unknown };
      if (type === "tool-call") {
        return [{
          type,
          id: (part as { toolCallId?: unknown }).toolCallId,
          result: stableResult((part as { result?: unknown }).result),
        }];
      }
      if (type === "image") return [{ type, image: (part as { image?: unknown }).image }];
      return [];
    })
    : [];

/** Role and content: what a retained row keeps across compaction (its id is renumbered). */
const rowFingerprint = (message: Message): string => {
  const content = Array.isArray(message.content)
    ? message.content.map((part) => part && typeof part === "object" && "result" in part
      ? { ...part, result: stableResult((part as { result?: unknown }).result) }
      : part)
    : message.content ?? null;
  return `${String((message as { role?: unknown }).role ?? "")}\n${JSON.stringify(content)}`;
};

/**
 * The sends a row belongs to: its own request id, and the ids a saved user row
 * holds, one per send that went into it (#1359).
 */
const requestIdsOf = (message: Message): string[] => {
  const custom = message.metadata?.custom;
  const held = Array.isArray(custom?.requestIds) ? custom.requestIds : [];
  return [...held, custom?.requestId].filter((id): id is string => typeof id === "string" && id !== "");
};

/**
 * A picture the thread shows only as its placeholder text, [imageN]. The text
 * names no picture, so two placeholders with the same words prove nothing.
 */
const PICTURE_PLACEHOLDER = /\[image\d+\]/;
const hasPlaceholderPicture = (message: Message): boolean => {
  const content = message.content;
  if (typeof content === "string") return PICTURE_PLACEHOLDER.test(content);
  return Array.isArray(content) && content.some((part) =>
    part && typeof part === "object" && (part as { type?: unknown }).type === "text" &&
    PICTURE_PLACEHOLDER.test(String((part as { text?: unknown }).text ?? "")));
};

/**
 * Same turn, on top of same words: equal request ids when both rows carry one,
 * else equal tool calls (call ids and results) and equal pictures. A repeated
 * prompt whose turn made another call, or came with another picture, is a
 * different turn; the composer sends pictures without a request id. A placeholder
 * picture has no identity to compare, so without a shared request id it is not
 * proven the same turn: keep both rows (a duplicate) rather than drop the new one.
 */
const sameTurn = (live: Message, stored: Message): boolean => {
  const liveRequests = requestIdsOf(live);
  const storedRequests = requestIdsOf(stored);
  if (liveRequests.length && storedRequests.length) {
    return liveRequests.some((id) => storedRequests.includes(id));
  }
  if (hasPlaceholderPicture(live) || hasPlaceholderPicture(stored)) return false;
  return JSON.stringify(turnParts(live)) === JSON.stringify(turnParts(stored));
};

/**
 * Rebase a compacted snapshot in place: preserve all history the user has
 * already seen under non-conflicting IDs, then add the compacted rows in their
 * authoritative order. Returning null means this is not a new compaction.
 */
export function mergeCompactionHistorySnapshot<T extends Message>(
  shown: ReadonlyArray<T>,
  loaded: ReadonlyArray<T>,
  isLive: (message: T) => boolean = () => false,
): { messages: T[]; history: T[]; key: string } | null {
  const shownMarkers = new Set(
    shown.filter(isCompactionMarker).map(compactionMarkerKey),
  );
  const marker = loaded.find(
    (message) => isCompactionMarker(message) && !shownMarkers.has(compactionMarkerKey(message)),
  );
  if (!marker) return null;
  const key = compactionMarkerKey(marker);

  const transcript = shown.filter((message) =>
    historyIndex(message) !== null || compactionArchiveOrder(message) !== null,
  );
  const nonHistory = shown.filter((message) =>
    historyIndex(message) === null && compactionArchiveOrder(message) === null,
  );
  const history = loaded.filter((message) => historyIndex(message) !== null);
  // The engine keeps its newest rows verbatim across a compaction, so they come
  // back in `history`. Archiving their displayed copies too would show them
  // twice. Each loaded row takes at most one shown row, matched from the newest.
  const positions = new Map<string, number[]>();
  history.forEach((message, index) => {
    const fingerprint = rowFingerprint(message);
    positions.set(fingerprint, [...(positions.get(fingerprint) ?? []), index]);
  });
  const carried = new Set<T>();
  const carriedRows = new Map<number, T>();
  let lastCarried = -1;
  for (const message of [...transcript].reverse()) {
    if (compactionArchiveOrder(message) !== null) continue;
    const position = positions.get(rowFingerprint(message))?.pop();
    if (position !== undefined) {
      carried.add(message);
      carriedRows.set(position, message);
      lastCarried = Math.max(lastCarried, position);
    }
  }
  // Keep assistant-ui's keys for rows the compacted snapshot carried forward.
  // A stable alias can point at a different history index after compaction, so
  // refresh its source index while preserving the existing message id.
  const used = new Set(shown.map((message) => message.id));
  const rebasedHistory = history.map((message, position) => {
    const previous = carriedRows.get(position);
    return previous ? displayRow(message, previous.id) : reserveRow(message, used);
  });
  const archived = transcript.filter((message) => !carried.has(message)).map((message, index) => {
    const custom = { ...message.metadata?.custom };
    delete custom.historySourceIndex;
    delete custom.historySourceId;
    return { ...message, metadata: { ...message.metadata, custom: {
      ...custom, compactionArchiveOrder: index, compactionArchiveKey: key,
    } } } as T;
  });
  // The rows the snapshot brings after the last shown row it carried are the turn
  // a reload replays; its transient copies go as a sync drops them (#666). Live
  // rows sit at the end, outside that check.
  const replayed = rebasedHistory.filter((message, index) =>
    index > lastCarried && !isCompactionMarker(message));
  const live = nonHistory.filter((message) => isLive(message));
  const settled = [...archived, ...rebasedHistory, ...nonHistory.filter((message) => !isLive(message))];
  const deduped = dropTransientTurnDuplicates(settled, replayed) ?? settled;
  return { messages: [...deduped, ...live], history: rebasedHistory, key };
}

const TRANSIENT_ROW_ID = /^(?:user|assistant)-.+-(\d+)$/;

const transientText = (content: unknown): string => {
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  return content
    .map((part) =>
      part && typeof part === "object" && (part as { type?: unknown }).type === "text" &&
        typeof (part as { text?: unknown }).text === "string"
        ? (part as { text: string }).text
        : "")
    .join("\n")
    .trim();
};

const transientRole = (message: Message): string =>
  String((message as { role?: unknown }).role ?? "");

/**
 * The turn a window just ran renders as transient rows (user-<session>-<n>,
 * assistant-<session>-<n>) while its stored rows are still unwritten. When
 * the sync point missed the turn -- the engine's history refresh failed, or
 * read the mirror before the rows landed -- the next reload replays the turn
 * as a tail next to those rows, and assistant-ui draws it twice until a
 * manual reload (#666). Drop the trailing transient block once every row in
 * it exists in the kept history as the same turn (same role and text, and the
 * same request id, or tool calls and pictures, see sameTurn), and the tail's first row
 * matches the block's same-role row; null keeps the rows as they are, so a
 * replay that proves nothing never hides content.
 */
export function dropTransientTurnDuplicates<T extends Message>(
  shown: ReadonlyArray<T>,
  tail: ReadonlyArray<T>,
): T[] | null {
  if (!tail.length) return null;
  let blockStart = shown.length;
  while (
    blockStart > 0 &&
    TRANSIENT_ROW_ID.test(String(shown[blockStart - 1].id ?? ""))
  ) {
    blockStart -= 1;
  }
  if (blockStart === shown.length) return null;
  const block = shown.slice(blockStart);
  const kept = shown.slice(0, blockStart);
  const anchor = tail[0];
  const anchorRow = block.find(
    (message) => transientRole(message) === transientRole(anchor) &&
      transientText(message.content) === transientText(anchor.content),
  );
  if (!anchorRow) return null;
  // The stored copies of this turn are the newest kept rows, in order. Matching
  // by position keeps an identical earlier turn from covering a reply that was
  // never stored.
  if (kept.length < block.length) return null;
  const stored = kept.slice(kept.length - block.length);
  const aligned = block.every((message, index) =>
    transientRole(stored[index]) === transientRole(message) &&
    transientText(stored[index].content) === transientText(message.content) &&
    sameTurn(message, stored[index]));
  if (!aligned) return null;
  return shown.slice(0, blockStart);
}

/**
 * The rows the page's own run drew (user-<session>-<n>, assistant-<session>-<n>)
 * carry the saved row the engine matched them to. Once loaded history has that
 * row, show its saved content under the drawn row's id, so assistant-ui updates
 * the message in place instead of the thread reloading whole: a blank frame and
 * a scroll jump at the end of every turn (#1358). Returns `shown` itself when
 * every such row already reads as its saved copy.
 */
export function withSavedCopies<T extends Message>(shown: ReadonlyArray<T>, loaded: ReadonlyArray<T>): ReadonlyArray<T> {
  const saved = new Map(loaded.map((message) => [sourceId(message), message]));
  let swapped = false;
  const messages = shown.map((message) => {
    const key = sourceId(message);
    // Only the run's user rows: its reply keeps the tool calls it drew, which
    // history saves as rows of their own (see mergeMissingHistoryMessages).
    if (!key || transientRole(message) !== "user" || !TRANSIENT_ROW_ID.test(String(message.id ?? ""))) return message;
    // A row saved with other sends shows a row per send (#1359): the engine
    // names the saved row, the request id names this send's piece of it.
    const index = historyIndex(message);
    const ids = requestIdsOf(message);
    const copy = (ids.length ? loaded.find((row) => historyIndex(row) === index && transientRole(row) === "user" &&
      requestIdsOf(row).some((id) => ids.includes(id))) : undefined) ?? saved.get(key);
    if (!copy || (sourceId(copy) === key && JSON.stringify(copy.content) === JSON.stringify(message.content) &&
      copy.convertConfig?.joinStrategy === message.convertConfig?.joinStrategy)) return message;
    swapped = true;
    return displayRow(copy, message.id);
  });
  return swapped ? messages : shown;
}

/** This page's sends in flight: its engine rows, then its pending copies (see withoutInFlightSends). */
function inFlightSends(shown: ReadonlyArray<Message>, sendIds: ReadonlySet<string>): Message[] {
  const users = shown.filter((message) => transientRole(message) === "user");
  const engineRows = users.filter((message) =>
    message.metadata?.isOptimistic !== true && sendIds.has(String(message.id ?? "")) &&
    TRANSIENT_ROW_ID.test(String(message.id ?? "")));
  const pendingRows = users.filter((message) => message.metadata?.isOptimistic === true)
    .slice(0, Math.max(0, sendIds.size - engineRows.length));
  return [...engineRows, ...pendingRows];
}

/**
 * While this page's own send is in flight it shows as a transient user row
 * (user-<session>-<n>): the pending copy, then the engine's. A history sync in
 * that wait can carry the same send as a newly saved row, and adding it draws
 * the message twice until the run settles (#1356). Leave those saved rows out
 * of the rows to add. A saved row that names its sends (#1359) is left out
 * when it names one in flight here, so another sender's identical words stay.
 * Sends the daemon saved as one row show as a row each (history-<n>-send-<i>),
 * so the other sender's piece stays; a row it cannot split (a picture) shows
 * whole in the send's row once the run settles (withSavedCopies). A row that
 * names none (an older daemon) pairs by words with the sends
 * no named row claimed (same words, see sameTurn): when more such rows have
 * them than those sends, another sender's message is among them and every row
 * stays (a duplicate). Only the page's own sends count, since the engine keeps user-<session>-<n> on settled
 * rows of earlier turns too: the engine's rows named by the ids its running
 * request sent, and its pending copies, whose ids follow the shown row count,
 * in order up to the number of sends in flight (later ones are still queued).
 */
export function withoutInFlightSends<T extends Message>(
  shown: ReadonlyArray<Message>,
  tail: ReadonlyArray<T>,
  sendIds: ReadonlySet<string>,
): T[] {
  const inFlight = inFlightSends(shown, sendIds);
  const left = new Set<T>();
  const claimed = new Set<Message>();
  const savedUsers = tail.filter((row) => transientRole(row) === "user");
  for (const row of savedUsers) {
    const ids = requestIdsOf(row);
    const sends = ids.length ? inFlight.filter((message) => requestIdsOf(message).some((id) => ids.includes(id))) : [];
    if (sends.length) left.add(row);
    sends.forEach((message) => claimed.add(message));
  }
  const sendsByWords = new Map<string, Message[]>();
  for (const message of inFlight.filter((send) => !claimed.has(send))) {
    const words = transientText(message.content);
    sendsByWords.set(words, [...(sendsByWords.get(words) ?? []), message]);
  }
  for (const [words, sends] of sendsByWords) {
    const saved = savedUsers.filter((row) => !requestIdsOf(row).length &&
      transientText(row.content) === words && sends.some((message) => sameTurn(message, row)));
    if (saved.length <= sends.length) saved.forEach((row) => left.add(row));
  }
  return tail.filter((row) => !left.has(row));
}

/**
 * The provisional row ids of the messages a request sends: row L + i after the
 * L-row state it carries, as the engine and the pending copies name them. A
 * request without a message (a tool answer, a resume) continues the turn that
 * carries the earlier send, so the sends in flight stay as they were.
 */

export function inFlightSendIdsFor(
  request: { commands: ReadonlyArray<{ type?: string }>; state?: unknown },
  sessionId: string,
  previous: ReadonlySet<string>,
): ReadonlySet<string> {
  const sends = request.commands.filter((command) => command.type === "add-message").length;
  if (!sends) return previous;
  const sentAfter = (request.state as { messages?: unknown } | undefined)?.messages;
  if (!Array.isArray(sentAfter)) return new Set();
  return new Set(Array.from({ length: sends }, (_, index) => `user-${sessionId}-${sentAfter.length + index}`));
}
