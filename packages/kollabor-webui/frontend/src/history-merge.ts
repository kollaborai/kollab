type Message = {
  id?: string;
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
    const existingIndex = historyIndex(existing.id) ?? Infinity;
    while (nextAdded < added.length) {
      const candidate = added[nextAdded];
      const candidateIndex = historyIndex(candidate.id)!;
      if (candidateIndex > existingIndex || (
        candidateIndex === existingIndex &&
        (order.get(candidate.id) ?? Infinity) > (order.get(existing.id) ?? Infinity)
      )) break;
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
