// The saved conversation (#1451): each session's newest settled rows, kept in the browser so a reopened
// chat (a remote agent, a phone) shows them while its stream connects or cannot connect (#880). The rows
// are only shown until the first snapshot, which replaces them; the daemon stays the only writer.

import { isChatStreamEvent, type ChatRow } from "./chat-stream";

export const CHAT_ROW_CACHE_MAX_ROWS = 50;
export const CHAT_ROW_CACHE_MAX_BYTES = 1_800_000;
export const CHAT_ROW_CACHE_MAX_SESSIONS = 12;
const DATABASE = "kollab-chat-rows-v1";
const STORE = "sessions";

export interface ChatRowCache {
  load(sessionId: string): Promise<ChatRow[] | null>;
  save(sessionId: string, rows: readonly ChatRow[]): Promise<void>;
}

// A row in one of these states waits on the live daemon: saved, it would look live or offer a stale action.
const UNSETTLED = new Set(["queued", "pending", "open", "streaming", "running"]);

/** The rows worth saving: the newest settled ones, without prompts, within the row and byte limits. */
export function cacheableRows(rows: readonly ChatRow[]): ChatRow[] {
  const newest = rows
    .filter((row) => row.kind !== "prompt" && !UNSETTLED.has(row.status))
    .slice(-CHAT_ROW_CACHE_MAX_ROWS);
  const encoder = new TextEncoder();
  let bytes = 2; // the array's brackets
  let start = newest.length;
  while (start > 0) {
    bytes += encoder.encode(JSON.stringify(newest[start - 1])).byteLength + 1; // and its comma
    if (bytes > CHAT_ROW_CACHE_MAX_BYTES) break;
    start -= 1;
  }
  return newest.slice(start);
}

/** Stored rows in the current row shape, or null: anything else in the browser's store is ignored. */
function storedRows(rows: unknown): ChatRow[] | null {
  const event = { seq: 0, conversation_uid: "saved", generation: "saved", type: "replace", data: { rows } };
  return isChatStreamEvent(event) && event.type === "replace" ? event.data.rows : null;
}

type StoredSession = { sessionId: string; rows: unknown; updatedAt: number };

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = transaction.onerror = () => reject(transaction.error);
  });
}

let database: Promise<IDBDatabase> | null = null;

function open(): Promise<IDBDatabase> {
  database ??= new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE, { keyPath: "sessionId" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  }).catch((error) => {
    database = null;
    throw error;
  });
  return database;
}

async function write(sessionId: string, rows: ChatRow[]): Promise<void> {
  const transaction = (await open()).transaction(STORE, "readwrite");
  const done = transactionDone(transaction);
  const store = transaction.objectStore(STORE);
  if (rows.length === 0) {
    store.delete(sessionId);
  } else {
    store.put({ sessionId, rows, updatedAt: Date.now() } satisfies StoredSession);
    const records = await requestResult<StoredSession[]>(store.getAll());
    const others = records
      .filter((record) => record.sessionId !== sessionId)
      .sort((left, right) => left.updatedAt - right.updatedAt);
    for (const record of others.slice(0, Math.max(0, records.length - CHAT_ROW_CACHE_MAX_SESSIONS))) {
      store.delete(record.sessionId);
    }
  }
  await done;
}

// One write at a time, in call order, so an older window never lands over a newer one.
let writes: Promise<void> = Promise.resolve();

/** The browser's saved rows. Best effort: without IndexedDB, or when it fails, the stream alone serves the chat. */
export const browserChatRowCache: ChatRowCache = {
  async load(sessionId) {
    try {
      await writes;
      const transaction = (await open()).transaction(STORE, "readonly");
      const stored = await requestResult<StoredSession | undefined>(transaction.objectStore(STORE).get(sessionId));
      return stored ? storedRows(stored.rows) : null;
    } catch {
      return null;
    }
  },
  save(sessionId, rows) {
    writes = writes.then(() => write(sessionId, [...rows])).catch(() => {});
    return writes;
  },
};
