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

/**
 * The engine rejected the page-down cursor (409 stale, 400 invalid): the
 * history the window paged against changed underneath it (compaction,
 * engine restart, cursor-key rotation). Paging can never succeed from the
 * current cursor; the caller must drop the cached pages and reload a fresh
 * window instead of showing a dead-end retry error.
 */
export class UnusableHistoryCursorError extends Error {
  readonly status: number;

  constructor(detail: string, status: number) {
    super(detail);
    this.name = "UnusableHistoryCursorError";
    this.status = status;
  }
}

function cursorRejectionStatus(reason: unknown): number | null {
  if (typeof reason !== "object" || reason === null) return null;
  const status = (reason as { status?: unknown }).status;
  return status === 409 || status === 400 ? status : null;
}

export function mergeRecentHistoryPage(
  cached: HistoryPage,
  updates: HistoryPage,
): HistoryPage {
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

  if (updates.start_index > cached.end_index) {
    // The refresh page starts beyond the cached window (for example a
    // byte-capped engine response dropped the leading span). Merging by
    // index would leave holes, fail validation, and strand the stale
    // cached page. Serve the contiguous refresh page and keep the missing
    // span reachable through the signed older cursor instead.
    return {
      ...updates,
      has_more_older: true,
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

  // Cursor floor: the byte-trim below may drop leading messages, but it must
  // never push start_index above the position an older_cursor was minted at
  // (cursor position == issuing page's start_index, verified server-side by
  // an HMAC). Keeping the cursor while trimming past that floor would
  // desynchronize the window: the next page-down would land entirely above
  // the cursor position and return an empty merge forever after. When no
  // cursor is in play the trim may run freely.
  const cursorFloor = updates.older_cursor
    ? updates.start_index
    : cached.older_cursor
      ? cached.start_index
      : Number.POSITIVE_INFINITY;

  while (
    history.length > 1 &&
    startIndex < cursorFloor &&
    encodedSize(merged) > HISTORY_MAX_PAGE_BYTES
  ) {
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
): Promise<LoadedHistoryPage> {
  const cachedPages = (await cache.load(sessionId))
    .filter((page) => page.history.length > 0)
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
        has_more_older:
          newestCached.has_more_older ||
          newestCached.start_index < newestCached.end_index - HISTORY_PAGE_SIZE,
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
  if (cached?.newer_cursor) {
    try {
      const updates = await api.getHistoryPage(sessionId, {
        limit: HISTORY_PAGE_SIZE,
        after: cached.newer_cursor,
      });
      const page = mergeRecentHistoryPage(cached, updates);
      if (updates.history.length) await cache.store(sessionId, updates);
      await cache.store(sessionId, page);
      return { page, fromCache: false };
    } catch (error) {
      // 409 = stale cursor, 400 = invalid cursor: both are unusable, drop and refetch.
      if (cursorRejectionStatus(error) === null) return { page: cached, fromCache: true, error };
      await cache.clear(sessionId);
      try {
        const page = await api.getHistoryPage(sessionId, { limit: HISTORY_PAGE_SIZE });
        await cache.store(sessionId, page);
        return { page, fromCache: false };
      } catch (refreshError) {
        await cache.store(sessionId, cached);
        return { page: cached, fromCache: true, error: refreshError };
      }
    }
  }

  try {
    const page = await api.getHistoryPage(sessionId, { limit: HISTORY_PAGE_SIZE });
    await cache.store(sessionId, page);
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
): Promise<HistoryPage | null> {
  if (!current.has_more_older) return null;
  const cached = await cache.findOlder(sessionId, current.start_index);
  if (cached) return cached;
  if (!current.older_cursor) return null;
  let older: HistoryPage;
  try {
    older = await api.getHistoryPage(sessionId, {
      limit: HISTORY_PAGE_SIZE,
      before: current.older_cursor,
    });
  } catch (error) {
    const status = cursorRejectionStatus(error);
    if (status !== null) {
      throw new UnusableHistoryCursorError(
        `The engine rejected the history cursor (HTTP ${status}); cached pages may be stale.`,
        status,
      );
    }
    throw error;
  }
  if (older.history.length) await cache.store(sessionId, older);
  return older;
}


function boundedPages(existing: HistoryPage[], incoming: HistoryPage) {
  if (!validPage(incoming) || encodedSize(incoming) > HISTORY_CACHE_MAX_BYTES) {
    throw new Error("Cannot cache an invalid or oversized history page.");
  }
  const byKey = new Map(existing.filter(validPage).map((page) => [pageKey(page), page]));
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
