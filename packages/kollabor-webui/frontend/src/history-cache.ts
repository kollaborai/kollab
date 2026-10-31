import { HISTORY_MAX_PAGE_BYTES, HISTORY_PAGE_SIZE, type HistoryMessage, type HistoryPage } from "./api";

export const HISTORY_CACHE_MAX_PAGES = 12;

export const HISTORY_CACHE_MAX_SESSIONS = 12;
export const HISTORY_CACHE_MAX_BYTES = 1_800_000;
const DATABASE = "kollab-history-pages-v1";
const STORE = "sessions";
const DATABASE_VERSION = 1;

type StoredSession = {
  sessionId: string;
  pages: HistoryPage[];
  updatedAt: number;
};

export interface HistoryPageCache {
  load(sessionId: string): Promise<HistoryPage[]>;
  store(sessionId: string, page: HistoryPage): Promise<void>;
  findOlder(sessionId: string, beforeIndex: number): Promise<HistoryPage | null>;
  clear(sessionId: string): Promise<void>;
}

// IndexedDB writes can finish after a newer history request. Serialize each
// session's mutations and check the requesting operation immediately before
// its write so an obsolete conversation cannot replace the current cache.
const pendingMutations = new WeakMap<HistoryPageCache, Map<string, Promise<void>>>();

async function mutateIfCurrent(
  cache: HistoryPageCache,
  sessionId: string,
  stillCurrent: () => boolean,
  mutate: () => Promise<void>,
): Promise<void> {
  let sessions = pendingMutations.get(cache);
  if (!sessions) {
    sessions = new Map();
    pendingMutations.set(cache, sessions);
  }
  const previous = sessions.get(sessionId) ?? Promise.resolve();
  const current = previous.catch(() => {}).then(async () => {
    if (stillCurrent()) await mutate();
  });
  sessions.set(sessionId, current);
  try {
    await current;
  } finally {
    if (sessions.get(sessionId) === current) sessions.delete(sessionId);
  }
}

export async function storeHistoryPageIfCurrent(
  sessionId: string,
  page: HistoryPage,
  cache: HistoryPageCache,
  stillCurrent: () => boolean,
): Promise<void> {
  await mutateIfCurrent(cache, sessionId, stillCurrent, () => cache.store(sessionId, page));
}


export interface HistoryPageClient {
  getHistoryPage(sessionId: string, options?: { limit?: number; before?: string; after?: string }): Promise<HistoryPage>;
}

export type LoadedHistoryPage = {
  page: HistoryPage;
  fromCache: boolean;
  error?: unknown;
};

function pageKey(page: HistoryPage) {
  return `${page.start_index}:${page.end_index}`;
}

function sameConversation(left: HistoryPage, right: HistoryPage): boolean {
  return (left.conversation_uid ?? null) === (right.conversation_uid ?? null);
}

function encodedSize(page: HistoryPage) {
  return new TextEncoder().encode(JSON.stringify(page)).byteLength;
}

function validPage(value: unknown): value is HistoryPage {
  if (!value || typeof value !== "object") return false;
  const page = value as Partial<HistoryPage>;
  return Array.isArray(page.history) &&
    Number.isSafeInteger(page.start_index) &&
    Number.isSafeInteger(page.end_index) &&
    page.end_index! >= page.start_index! &&
    page.history.length === page.end_index! - page.start_index! &&
    typeof page.has_more_older === "boolean" &&
    typeof page.has_more_newer === "boolean" &&
    (page.older_cursor === null || typeof page.older_cursor === "string") &&
    (page.newer_cursor === null || typeof page.newer_cursor === "string");
}

export function mergeRecentHistoryPage(
  cached: HistoryPage,
  updates: HistoryPage,
): HistoryPage {
  if (!sameConversation(cached, updates)) return updates;
  if (!updates.history.length) {
    return {
      ...cached,
      source: updates.source ?? cached.source,
      stale: updates.stale ?? cached.stale,
      newer_cursor: updates.newer_cursor ?? cached.newer_cursor,
      has_more_newer: updates.has_more_newer,
      last_turn_error: updates.last_turn_error ?? cached.last_turn_error,
    };
  }

  const byIndex = new Map<number, HistoryMessage>();
  for (const page of [cached, updates]) {
    page.history.forEach((message, offset) => {
      byIndex.set(page.start_index + offset, message);
    });
  }
  const endIndex = updates.end_index;
  const newest = [...byIndex.entries()]
    .filter(([index]) => index < endIndex)
    .sort(([left], [right]) => left - right)
    .slice(-HISTORY_PAGE_SIZE);
  let startIndex = newest[0]?.[0] ?? endIndex;
  let history = newest.map(([, message]) => message);
  const errorPage = updates.last_turn_error ? updates : cached;
  const globalErrorIndex = errorPage.last_turn_error
    ? errorPage.start_index + errorPage.last_turn_error.history_length
    : null;
  const merged: HistoryPage = {
    ...updates,
    history,
    start_index: startIndex,
    end_index: endIndex,
    has_more_older: updates.has_more_older || cached.has_more_older,
    // The refresh cursor is the exact boundary used for load-older. Any
    // cached prefix between it and startIndex is served from IndexedDB first.
    older_cursor: updates.older_cursor ?? cached.older_cursor,
    last_turn_error: null,
  };

  while (history.length > 1 && encodedSize(merged) > HISTORY_MAX_PAGE_BYTES) {
    history = history.slice(1);
    startIndex += 1;
    merged.history = history;
    merged.start_index = startIndex;
  }
  if (
    globalErrorIndex !== null &&
    globalErrorIndex >= startIndex &&
    globalErrorIndex <= endIndex &&
    errorPage.last_turn_error
  ) {
    merged.last_turn_error = {
      ...errorPage.last_turn_error,
      history_length: globalErrorIndex - startIndex,
    };
  }
  return merged;
}

export function mergeOlderHistoryPage(
  current: HistoryPage,
  older: HistoryPage,
): HistoryPage {
  if (!sameConversation(current, older)) return current;
  if (older.end_index < current.start_index) return current;
  const byIndex = new Map<number, HistoryMessage>();
  older.history.forEach((message, offset) => {
    const index = older.start_index + offset;
    if (index < current.start_index) byIndex.set(index, message);
  });
  current.history.forEach((message, offset) => {
    byIndex.set(current.start_index + offset, message);
  });
  const entries = [...byIndex.entries()].sort(([left], [right]) => left - right);
  const startIndex = entries[0]?.[0] ?? current.start_index;
  const lastTurnError = current.last_turn_error ?? older.last_turn_error ?? null;
  const globalErrorIndex = current.last_turn_error
    ? current.start_index + current.last_turn_error.history_length
    : older.last_turn_error
      ? older.start_index + older.last_turn_error.history_length
      : null;
  return {
    ...current,
    history: entries.map(([, message]) => message),
    start_index: startIndex,
    has_more_older: older.has_more_older,
    older_cursor: older.older_cursor,
    last_turn_error: lastTurnError && globalErrorIndex !== null
      ? { ...lastTurnError, history_length: globalErrorIndex - startIndex }
      : null,
  };
}


export async function loadLatestHistoryPage(
  sessionId: string,
  api: HistoryPageClient,
  cache: HistoryPageCache,
  stillCurrent: () => boolean = () => true,
  expectedConversationUid: string | null = null,
): Promise<LoadedHistoryPage> {
  // Cache failures must not replace an authoritative network response with
  // an older conversation's cached history.
  const store = (page: HistoryPage) =>
    storeHistoryPageIfCurrent(sessionId, page, cache, stillCurrent).catch(() => {});
  const clear = () =>
    mutateIfCurrent(cache, sessionId, stillCurrent, () => cache.clear(sessionId)).catch(() => {});
  const cachedPages = (await cache.load(sessionId))
    .filter((page) => page.history.length > 0)
    // If a switch was already observed, an unavailable or broken cache must
    // not revive the prior conversation while the network is offline.
    .filter((page) => !expectedConversationUid || page.conversation_uid === expectedConversationUid)
    .sort((left, right) =>
      right.end_index - left.end_index ||
      Math.abs(left.history.length - HISTORY_PAGE_SIZE) -
        Math.abs(right.history.length - HISTORY_PAGE_SIZE),
    );
  const newestCached = cachedPages[0] ?? null;
  const cached = newestCached && newestCached.history.length > HISTORY_PAGE_SIZE
    ? {
        ...newestCached,
        history: newestCached.history.slice(-HISTORY_PAGE_SIZE),
        start_index: newestCached.end_index - HISTORY_PAGE_SIZE,
        last_turn_error: newestCached.last_turn_error &&
          newestCached.start_index + newestCached.last_turn_error.history_length >=
            newestCached.end_index - HISTORY_PAGE_SIZE
          ? {
              ...newestCached.last_turn_error,
              history_length:
                newestCached.start_index + newestCached.last_turn_error.history_length -
                (newestCached.end_index - HISTORY_PAGE_SIZE),
            }
          : null,
      }
    : newestCached;
  const isStaleCursor = (reason: unknown) =>
    typeof reason === "object" && reason !== null &&
    "status" in reason && (reason as { status?: unknown }).status === 409;

  if (cached?.newer_cursor) {
    try {
      const updates = await api.getHistoryPage(sessionId, {
        limit: HISTORY_PAGE_SIZE,
        after: cached.newer_cursor,
      });
      if (!sameConversation(cached, updates)) {
        // A cursor can still match an identical prefix after /resume. Never
        // join pages across conversations, even when their contents match.
        await clear();
        try {
          const page = await api.getHistoryPage(sessionId, { limit: HISTORY_PAGE_SIZE });
          await store(page);
          return { page, fromCache: false };
        } catch (error) {
          return { page: updates, fromCache: false, error };
        }
      }
      const page = mergeRecentHistoryPage(cached, updates);
      if (updates.history.length) await store(updates);
      await store(page);
      return { page, fromCache: false };
    } catch (error) {
      if (!isStaleCursor(error)) return { page: cached, fromCache: true, error };
      await clear();
      try {
        const page = await api.getHistoryPage(sessionId, { limit: HISTORY_PAGE_SIZE });
        await store(page);
        return { page, fromCache: false };
      } catch (refreshError) {
        await store(cached);
        return { page: cached, fromCache: true, error: refreshError };
      }
    }
  }

  try {
    const page = await api.getHistoryPage(sessionId, { limit: HISTORY_PAGE_SIZE });
    if (cached && !sameConversation(cached, page)) await clear();
    await store(page);
    return { page, fromCache: false };
  } catch (error) {
    if (!cached) throw error;
    return { page: cached, fromCache: true, error };
  }
}


export async function loadOlderHistoryPage(
  sessionId: string,
  current: HistoryPage,
  api: HistoryPageClient,
  cache: HistoryPageCache,
  stillCurrent: () => boolean = () => true,
): Promise<HistoryPage | null> {
  if (!current.has_more_older) return null;
  const cached = await cache.findOlder(sessionId, current.start_index);
  if (cached && sameConversation(current, cached)) return cached;
  if (!current.older_cursor) return null;
  const older = await api.getHistoryPage(sessionId, {
    limit: HISTORY_PAGE_SIZE,
    before: current.older_cursor,
  });
  if (!sameConversation(current, older)) throw new Error("Conversation changed while loading history.");
  if (older.history.length) await storeHistoryPageIfCurrent(sessionId, older, cache, stillCurrent);
  return older;
}


function boundedPages(existing: HistoryPage[], incoming: HistoryPage) {
  if (!validPage(incoming) || encodedSize(incoming) > HISTORY_CACHE_MAX_BYTES) {
    throw new Error("Cannot cache an invalid or oversized history page.");
  }
  const byKey = new Map(existing.filter((page) => validPage(page) && sameConversation(page, incoming)).map((page) => [pageKey(page), page]));
  byKey.set(pageKey(incoming), incoming);
  const newestFirst = [...byKey.values()].sort(
    (left, right) =>
      right.end_index - left.end_index ||
      Math.abs(left.history.length - HISTORY_PAGE_SIZE) -
        Math.abs(right.history.length - HISTORY_PAGE_SIZE) ||
      right.history.length - left.history.length ||
      left.start_index - right.start_index,
  );
  const kept: HistoryPage[] = [];
  let bytes = 0;
  for (const page of newestFirst) {
    const size = encodedSize(page);
    if (kept.length >= HISTORY_CACHE_MAX_PAGES || bytes + size > HISTORY_CACHE_MAX_BYTES) continue;
    kept.push(page);
    bytes += size;
  }
  return kept.sort((left, right) => left.start_index - right.start_index);
}

export class MemoryHistoryPageCache implements HistoryPageCache {
  private readonly sessions = new Map<string, HistoryPage[]>();

  async load(sessionId: string) {
    const pages = this.sessions.get(sessionId);
    if (!pages) return [];
    this.sessions.delete(sessionId);
    this.sessions.set(sessionId, pages);
    return [...pages];
  }

  async store(sessionId: string, page: HistoryPage) {
    const pages = boundedPages(this.sessions.get(sessionId) ?? [], page);
    this.sessions.delete(sessionId);
    this.sessions.set(sessionId, pages);
    while (this.sessions.size > HISTORY_CACHE_MAX_SESSIONS) {
      const oldest = this.sessions.keys().next().value as string | undefined;
      if (!oldest) break;
      this.sessions.delete(oldest);
    }
  }

  async findOlder(sessionId: string, beforeIndex: number) {
    const candidates = (this.sessions.get(sessionId) ?? [])
      .filter((page) => page.start_index < beforeIndex && page.end_index >= beforeIndex)
      .sort((left, right) => right.start_index - left.start_index);
    const page = candidates[0];
    if (!page) return null;
    const count = beforeIndex - page.start_index;
    if (count <= 0) return null;
    return {
      ...page,
      history: page.history.slice(0, count),
      end_index: beforeIndex,
    };
  }

  async clear(sessionId: string) {
    this.sessions.delete(sessionId);
  }
}

function requestResult<T>(request: IDBRequest<T>) {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed."));
  });
}

function transactionDone(transaction: IDBTransaction) {
  return new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = transaction.onerror = () =>
      reject(transaction.error ?? new Error("IndexedDB transaction failed."));
  });
}

export class IndexedDbHistoryPageCache implements HistoryPageCache {
  private database: Promise<IDBDatabase> | null = null;
  private readonly fallback = new MemoryHistoryPageCache();

  private open() {
    if (!globalThis.indexedDB) return Promise.reject(new Error("IndexedDB is unavailable."));
    if (!this.database) {
      this.database = new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open(DATABASE, DATABASE_VERSION);
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains(STORE)) {
            request.result.createObjectStore(STORE, { keyPath: "sessionId" });
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error ?? new Error("Cannot open history cache."));
        request.onblocked = () => reject(new Error("History cache upgrade is blocked."));
      }).catch((error) => {
        this.database = null;
        throw error;
      });
    }
    return this.database;
  }

  async load(sessionId: string) {
    try {
      const database = await this.open();
      const transaction = database.transaction(STORE, "readonly");
      const done = transactionDone(transaction);
      const stored = await requestResult<StoredSession | undefined>(
        transaction.objectStore(STORE).get(sessionId),
      );
      await done;
      const pages = Array.isArray(stored?.pages) ? stored.pages.filter(validPage) : [];
      return pages.length ? pages : this.fallback.load(sessionId);
    } catch {
      return this.fallback.load(sessionId);
    }
  }

  async store(sessionId: string, page: HistoryPage) {
    await this.fallback.store(sessionId, page);
    try {
      const pages = await this.load(sessionId);
      const database = await this.open();
      const transaction = database.transaction(STORE, "readwrite");
      const store = transaction.objectStore(STORE);
      const done = transactionDone(transaction);
      store.put({
        sessionId,
        pages: boundedPages(pages, page),
        updatedAt: Date.now(),
      } satisfies StoredSession);
      const records = await requestResult<StoredSession[]>(store.getAll());
      const stale = records
        .filter((record) => record.sessionId !== sessionId)
        .sort((left, right) => left.updatedAt - right.updatedAt);
      for (const record of stale.slice(0, Math.max(0, records.length - HISTORY_CACHE_MAX_SESSIONS))) {
        store.delete(record.sessionId);
      }
      await done;
    } catch {
      // The memory fallback still serves this window; the network remains authoritative.
    }
  }

  async findOlder(sessionId: string, beforeIndex: number) {
    const candidates = (await this.load(sessionId))
      .filter((page) => page.start_index < beforeIndex && page.end_index >= beforeIndex)
      .sort((left, right) => right.start_index - left.start_index);
    const page = candidates[0];
    if (!page) return null;
    const count = beforeIndex - page.start_index;
    if (count <= 0) return null;
    return { ...page, history: page.history.slice(0, count), end_index: beforeIndex };
  }

  async clear(sessionId: string) {
    await this.fallback.clear(sessionId);
    try {
      const database = await this.open();
      const transaction = database.transaction(STORE, "readwrite");
      transaction.objectStore(STORE).delete(sessionId);
      await transactionDone(transaction);
    } catch {
      // Clearing a best-effort cache must not block the session lifecycle.
    }
  }
}

export const historyPageCache: HistoryPageCache = new IndexedDbHistoryPageCache();
