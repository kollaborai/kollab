type Message = { id?: string; content?: unknown; convertConfig?: { joinStrategy?: string } };

/**
 * True when a message the thread already shows reads differently in the
 * reloaded history. A tool call imported mid-turn gets its result from a later
 * history row, which never makes a new message, so the append merge would
 * leave the card without it. A reply that a new voice turn now follows ends
 * its group, so the turns show apart. The thread then reloads whole:
 * assistant-ui keeps every message it was given, so re-importing one by id
 * would branch it.
 */
export function shownMessagesChanged(shown: ReadonlyArray<Message>, loaded: ReadonlyArray<Message>): boolean {
  const shownById = new Map(shown.map((message) => [message.id, message]));
  return loaded.some((message) => {
    const old = shownById.get(message.id);
    return old !== undefined && (
      JSON.stringify(old.content) !== JSON.stringify(message.content) ||
      old.convertConfig?.joinStrategy !== message.convertConfig?.joinStrategy
    );
  });
}
