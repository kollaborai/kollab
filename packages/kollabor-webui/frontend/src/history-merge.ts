type Message = { id?: string; content?: unknown; convertConfig?: { joinStrategy?: string } };

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
