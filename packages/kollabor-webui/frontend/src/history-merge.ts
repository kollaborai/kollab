type Message = {
  id?: string;
  role?: string;
  content?: unknown;
  metadata?: { custom?: Record<string, unknown> };
  convertConfig?: { joinStrategy?: string };
};

const historyIndex = (id: unknown): number | null => {
  const index = /^history-(\d+)/.exec(String(id ?? ""))?.[1];
  return index === undefined ? null : Number(index);
};

/** Insert newly reached history rows without dropping previously shown rows. */
export function mergeMissingHistoryMessages<T extends Message>(
  shown: ReadonlyArray<T>,
  loaded: ReadonlyArray<T>,
): { messages: T[]; added: T[] } {
  const present = new Set(shown.map((message) => message.id));
  const order = new Map(loaded.map((message, index) => [message.id, index]));
  const added = loaded.filter((message) => {
    if (historyIndex(message.id) === null || present.has(message.id)) return false;
    present.add(message.id);
    return true;
  });
  const messages: T[] = [];
  let nextAdded = 0;
  for (const existing of shown) {
    const existingOrder = historyOrder(existing, order.get(existing.id) ?? Infinity);
    while (nextAdded < added.length) {
      const candidate = added[nextAdded];
      const candidateOrder = historyOrder(candidate, order.get(candidate.id) ?? Infinity);
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
  const shownIds = new Set(shown.map((message) => message.id));
  for (const message of shown) {
    const index = historyIndex(message.id);
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
    const index = historyIndex(message.id);
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
    if (!call || shownIds.has(call.messageId) || (low > 1 && rows[low - 2].index === call.index)) continue;
    const key = `${call.messageId}:${call.partIndex}`;
    const group = matches.get(key) ?? [];
    group.push(fallback);
    matches.set(key, group);
  }
  const replaced = new Set<string | undefined>();
  const corrected = loaded.map((message) => {
    const index = historyIndex(message.id);
    if (index === null || shownIds.has(message.id) ||
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
      const result = { ...call };
      for (const field of ["result", "isError", "timing"] as const) {
        if (field in prior.part) result[field] = prior.part[field];
      }
      return result;
    });
    return changed ? { ...message, content } as T : message;
  });
  if (!replaced.size) return null;
  return mergeMissingHistoryMessages(shown.filter((message) => !replaced.has(message.id)), corrected).messages;
}

/**
 * True when a message the thread already shows reads differently in the
 * reloaded history, or is gone from it. A tool call imported mid-turn gets its
 * result from a later history row, which never makes a new message, so the
 * append merge would leave the card without it. A reply that a new voice turn
 * now follows ends its group, so the turns show apart. A voice talk-over takes
 * away the reply nobody heard. The thread then reloads whole: assistant-ui
 * keeps every message it was given, so re-importing one by id would branch it.
 */
export function shownMessagesChanged(shown: ReadonlyArray<Message>, loaded: ReadonlyArray<Message>): boolean {
  const shownById = new Map(shown.map((message) => [message.id, message]));
  const loadedIds = new Set(loaded.map((message) => message.id));
  // Rows before the loaded window are older pages, not gone.
  const start = loaded.reduce((low, message) => Math.min(low, historyIndex(message.id) ?? low), Infinity);
  const gone = shown.some((message) => {
    const index = historyIndex(message.id);
    return index !== null && index >= start && !loadedIds.has(message.id);
  });
  return gone || loaded.some((message) => {
    const old = shownById.get(message.id);
    return old !== undefined && (
      JSON.stringify(old.content) !== JSON.stringify(message.content) ||
      old.convertConfig?.joinStrategy !== message.convertConfig?.joinStrategy
    );
  });
}


type HistoryOrder = { section: number; index: number; tie: number };

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
  if (archived !== null) return { section: 0, index: archived, tie };
  const index = historyIndex(message.id);
  if (index !== null) return { section: 1, index, tie };
  return { section: 2, index: 0, tie };
};

const compareHistoryOrder = (left: HistoryOrder, right: HistoryOrder): number =>
  left.section - right.section || left.index - right.index || left.tie - right.tie;

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

/** The request a row belongs to, stamped on its live and stored assistant rows. */
const requestIdOf = (message: Message): string | undefined => {
  const value = message.metadata?.custom?.requestId;
  return typeof value === "string" && value !== "" ? value : undefined;
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
  const liveRequest = requestIdOf(live);
  const storedRequest = requestIdOf(stored);
  if (liveRequest && storedRequest) return liveRequest === storedRequest;
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
    historyIndex(message.id) !== null || compactionArchiveOrder(message) !== null,
  );
  const nonHistory = shown.filter((message) =>
    historyIndex(message.id) === null && compactionArchiveOrder(message) === null,
  );
  const history = loaded.filter((message) => historyIndex(message.id) !== null);
  // The engine keeps its newest rows verbatim across a compaction, so they come
  // back in `history`. Archiving their displayed copies too would show them
  // twice. Each loaded row takes at most one shown row, matched from the newest.
  const positions = new Map<string, number[]>();
  history.forEach((message, index) => {
    const fingerprint = rowFingerprint(message);
    positions.set(fingerprint, [...(positions.get(fingerprint) ?? []), index]);
  });
  const carried = new Set<T>();
  let lastCarried = -1;
  for (const message of [...transcript].reverse()) {
    const position = positions.get(rowFingerprint(message))?.pop();
    if (position !== undefined) {
      carried.add(message);
      lastCarried = Math.max(lastCarried, position);
    }
  }
  const archived = transcript
    .filter((message) => !carried.has(message))
    .map((message, index) => ({
      ...message,
      id: `compaction-archive-${key}-${index}`,
      metadata: {
        ...(message.metadata ?? {}),
        custom: {
          ...(message.metadata?.custom ?? {}),
          compactionArchiveOrder: index,
          compactionArchiveKey: key,
        },
      },
    }) as T);
  // The rows the snapshot brings after the last shown row it carried are the turn
  // a reload replays; its transient copies go as a sync drops them (#666). Live
  // rows sit at the end, outside that check.
  const replayed = history.filter((message, index) =>
    index > lastCarried && !isCompactionMarker(message));
  const live = nonHistory.filter((message) => isLive(message));
  const settled = [...archived, ...history, ...nonHistory.filter((message) => !isLive(message))];
  const deduped = dropTransientTurnDuplicates(settled, replayed) ?? settled;
  return { messages: [...deduped, ...live], history, key };
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
