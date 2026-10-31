import { describe, expect, it } from "vitest";
import type { HistoryMessage, HistoryPage } from "../src/api";
import { buildInitialState } from "../src/runtime";
import {
  HISTORY_CACHE_MAX_PAGES,
  HISTORY_CACHE_MAX_SESSIONS,
  MemoryHistoryPageCache,
  UnusableHistoryCursorError,
  loadLatestHistoryPage,
  loadOlderHistoryPage,
  mergeOlderHistoryPage,
  mergeRecentHistoryPage,
} from "../src/history-cache";

function page(start: number, end: number, errorIndex?: number): HistoryPage {
  return {
    history: Array.from({ length: end - start }, (_, offset) => ({
      role: "user",
      content: `message-${start + offset}`,
    } satisfies HistoryMessage)),
    start_index: start,
    end_index: end,
    has_more_older: start > 0,
    older_cursor: start > 0 ? `before-${start}` : null,
    has_more_newer: false,
    newer_cursor: `after-${end}`,
    last_turn_error: errorIndex === undefined
      ? null
      : { message: "synthetic failure", history_length: errorIndex - start },
    source: "mirror",
    stale: false,
  };
}

const outputId = (message: HistoryMessage) => String(message.content);

describe("bounded history cache and paging", () => {
  it("reopens by requesting only messages after the cached newest cursor", async () => {
    const cache = new MemoryHistoryPageCache();
    const cached = page(50, 100);
    await cache.store("session-a", cached);
    const requests: Array<{ limit?: number; before?: string; after?: string }> = [];
    const api = {
      async getHistoryPage(
        _sessionId: string,
        options: { limit?: number; before?: string; after?: string } = {},
      ) {
        requests.push(options);
        return page(100, 110);
      },
    };

    const result = await loadLatestHistoryPage("session-a", api, cache);

    expect(requests).toEqual([{ limit: 50, after: "after-100" }]);
    expect(result.fromCache).toBe(false);
    expect(result.page.start_index).toBe(60);
    expect(result.page.end_index).toBe(110);
    expect(result.page.history.map(outputId)).toEqual(
      Array.from({ length: 50 }, (_, i) => `message-${i + 60}`),
    );
    const cachedPrefix = await cache.findOlder("session-a", result.page.start_index);
    expect(cachedPrefix?.history.map(outputId)).toEqual(
      Array.from({ length: 10 }, (_, i) => `message-${i + 50}`),
    );
  });

  it("serves a cached prefix before continuing with opaque older cursors", async () => {
    const cache = new MemoryHistoryPageCache();
    const cached = page(50, 100);
    await cache.store("session-a", cached);
    const merged = mergeRecentHistoryPage(cached, page(100, 110));
    const requests: unknown[] = [];
    const api = {
      async getHistoryPage(
        _sessionId: string,
        options: { limit?: number; before?: string; after?: string } = {},
      ) {
        requests.push(options);
        return options.before === "before-170" ? page(120, 170) : page(0, 50);
      },
    };

    const cachedOlder = await loadOlderHistoryPage("session-a", merged, api, cache);
    expect(cachedOlder?.history.map(outputId)).toEqual(
      Array.from({ length: 10 }, (_, i) => `message-${i + 50}`),
    );
    const withCachedPrefix = mergeOlderHistoryPage(merged, cachedOlder!);
    expect(withCachedPrefix.start_index).toBe(50);
    expect(withCachedPrefix.history).toHaveLength(60);
    expect(requests).toEqual([]);

    const serverOlder = await loadOlderHistoryPage(
      "session-a",
      withCachedPrefix,
      api,
      cache,
    );
    expect(serverOlder?.history.map(outputId)).toEqual(
      Array.from({ length: 50 }, (_, i) => `message-${i}`),
    );
    expect(requests).toEqual([{ limit: 50, before: "before-50" }]);
  });

  it("uses a latest 50-message suffix after a long offline gap", async () => {
    const cache = new MemoryHistoryPageCache();
    const cached = page(50, 100);
    await cache.store("session-a", cached);
    const requests: Array<{ limit?: number; before?: string; after?: string }> = [];
    const api = {
      async getHistoryPage(
        _sessionId: string,
        options: { limit?: number; before?: string; after?: string } = {},
      ) {
        requests.push(options);
        return options.before ? page(120, 170) : page(170, 220);
      },
    };

    const result = await loadLatestHistoryPage("session-a", api, cache);

    expect(requests).toEqual([{ limit: 50, after: "after-100" }]);
    expect(result.page.start_index).toBe(170);
    expect(result.page.history.map(outputId)).toEqual(
      Array.from({ length: 50 }, (_, i) => `message-${i + 170}`),
    );
    const older = await loadOlderHistoryPage("session-a", result.page, api, cache);
    expect(older?.start_index).toBe(120);
    expect(older?.end_index).toBe(170);
    expect(requests.at(-1)).toEqual({ limit: 50, before: "before-170" });
  });

  it("restarts from a fresh page when the cached cursor is stale", async () => {
    const cache = new MemoryHistoryPageCache();
    await cache.store("session-a", page(50, 100));
    const requests: Array<{ limit?: number; before?: string; after?: string }> = [];
    const api = {
      async getHistoryPage(
        _sessionId: string,
        options: { limit?: number; before?: string; after?: string } = {},
      ) {
        requests.push(options);
        if (options.after) throw Object.assign(new Error("stale"), { status: 409 });
        return page(70, 120);
      },
    };

    const result = await loadLatestHistoryPage("session-a", api, cache);

    expect(requests).toEqual([
      { limit: 50, after: "after-100" },
      { limit: 50 },
    ]);
    expect(result.error).toBeUndefined();
    expect(result.page.start_index).toBe(70);
    expect((await cache.load("session-a")).map((entry) => entry.end_index)).toContain(120);
  });

  it("keeps cached history visible with a retryable refresh failure", async () => {
    const cache = new MemoryHistoryPageCache();
    const cached = page(50, 100);
    await cache.store("session-a", cached);
    const api = {
      async getHistoryPage() {
        throw new Error("offline");
      },
    };

    const result = await loadLatestHistoryPage("session-a", api, cache);

    expect(result.fromCache).toBe(true);
    expect(result.page).toEqual(cached);
    expect(result.error).toBeInstanceOf(Error);
  });

  it("rebases turn errors and keeps the first page under the configured byte cap", () => {
    const cached = page(50, 100, 95);
    const merged = mergeRecentHistoryPage(cached, page(100, 110));

    expect(merged.last_turn_error).toEqual({
      message: "synthetic failure",
      history_length: 35,
    });
    expect(new TextEncoder().encode(JSON.stringify(merged)).byteLength).toBeLessThanOrEqual(150_000);
    expect(merged.history).toHaveLength(50);
  });

  it("keeps first-render history to one page and no more than three request round trips", async () => {
    const cache = new MemoryHistoryPageCache();
    let historyRequests = 0;
    let permissionRequests = 0;
    const api = {
      async getHistoryPage(
        _sessionId: string,
        options: { limit?: number; before?: string; after?: string } = {},
      ) {
        historyRequests += 1;
        return options.before ? page(0, 50) : page(170, 220);
      },
    };
    const [loaded] = await Promise.all([
      loadLatestHistoryPage("session-a", api, cache),
      Promise.resolve().then(() => { permissionRequests += 1; }),
    ]);
    await loadOlderHistoryPage("session-a", loaded.page, api, cache);

    expect(loaded.page.history).toHaveLength(50);
    expect(new TextEncoder().encode(JSON.stringify(loaded.page)).byteLength)
      .toBeLessThanOrEqual(150_000);
    expect(historyRequests + permissionRequests).toBeLessThanOrEqual(3);
    expect(historyRequests + permissionRequests).toBe(3);
  });


  it("preserves global history offsets and lazy tool-output references in runtime state", () => {
    const initial = buildInitialState(
      "session-a",
      [],
      [
        { role: "user", content: "synthetic user message" },
        {
          role: "tool",
          content: "preview only",
          metadata: { history_output_ref: "opaque-output-ref" },
        },
      ],
      [],
      null,
      140,
    );

    expect(initial.synced).toEqual({ history: 142, start: 140, messages: 2 });
    expect(initial.messages.some((message) => String(message.id).includes("history-140")))
      .toBe(true);
    expect(JSON.stringify(initial.messages)).toContain('"kind":"history-output-preview"');
    expect(JSON.stringify(initial.messages)).toContain("opaque-output-ref");
  });


  it("reopens with the newest 50 from a wider cached scroll window", async () => {
    const cache = new MemoryHistoryPageCache();
    const wideWindow = mergeOlderHistoryPage(page(50, 100), page(0, 50));
    await cache.store("session-a", wideWindow);
    const api = {
      async getHistoryPage(
        _sessionId: string,
        options: { limit?: number; before?: string; after?: string } = {},
      ) {
        expect(options.after).toBe("after-100");
        return page(100, 100);
      },
    };

    const loaded = await loadLatestHistoryPage("session-a", api, cache);

    expect(loaded.page.start_index).toBe(50);
    expect(loaded.page.history).toHaveLength(50);
    expect(loaded.page.history.map(outputId)).toEqual(
      Array.from({ length: 50 }, (_, index) => `message-${index + 50}`),
    );
    expect((await cache.findOlder("session-a", 50))?.start_index).toBe(0);
  });


  it("bounds cached pages", async () => {
    const cache = new MemoryHistoryPageCache();
    for (let index = 0; index < HISTORY_CACHE_MAX_PAGES + 5; index += 1) {
      await cache.store("session-a", page(index, index + 1));
    }

    const pages = await cache.load("session-a");
    expect(pages.length).toBe(HISTORY_CACHE_MAX_PAGES);
    expect(pages.at(-1)?.end_index).toBe(HISTORY_CACHE_MAX_PAGES + 5);
  });


  it("bounds the number of cached sessions", async () => {
    const cache = new MemoryHistoryPageCache();
    for (let index = 0; index < HISTORY_CACHE_MAX_SESSIONS + 3; index += 1) {
      await cache.store(`session-${index}`, page(index, index + 1));
    }

    expect((await cache.load("session-0")).length).toBe(0);
    expect((await cache.load(`session-${HISTORY_CACHE_MAX_SESSIONS + 2}`)).length).toBe(1);
  });

  it("throws a typed cursor error when the engine rejects the older cursor", async () => {
    const cache = new MemoryHistoryPageCache();
    const staleApi = {
      async getHistoryPage() {
        throw Object.assign(new Error("stale cursor"), { status: 409 });
      },
    };

    const outcome = loadOlderHistoryPage("session-a", page(100, 150), staleApi, cache);
    await expect(outcome).rejects.toBeInstanceOf(UnusableHistoryCursorError);
    await expect(outcome).rejects.toMatchObject({ status: 409 });

    const offlineApi = {
      async getHistoryPage() {
        throw Object.assign(new Error("server error"), { status: 500 });
      },
    };
    await expect(
      loadOlderHistoryPage("session-a", page(100, 150), offlineApi, cache),
    ).rejects.not.toBeInstanceOf(UnusableHistoryCursorError);
  });

  it("never trims a cursor-synced refresh window past its older cursor", () => {
    const bigPage = (start: number, end: number): HistoryPage => ({
      ...page(start, end),
      history: Array.from({ length: end - start }, () => ({
        role: "user" as const,
        content: "x".repeat(4096),
      })),
    });
    const cached = bigPage(50, 100);
    const updates = bigPage(100, 150);

    const merged = mergeRecentHistoryPage(cached, updates);

    // The window is over the per-page byte cap, but the older cursor was
    // minted at index 100: trimming the leading edge past it would strand
    // page-down paging with an empty merge forever after.
    expect(merged.older_cursor).toBe("before-100");
    expect(merged.start_index).toBe(100);
    expect(merged.history).toHaveLength(50);
    expect(
      new TextEncoder().encode(JSON.stringify(merged)).byteLength,
    ).toBeGreaterThan(150_000);

    // Page-down still merges: the fetched page ends exactly at the floor.
    const widened = mergeOlderHistoryPage(merged, bigPage(50, 100));
    expect(widened.start_index).toBe(50);
    expect(widened.history).toHaveLength(100);
  });

  it("reaches the beginning of history cleanly", async () => {
    const cache = new MemoryHistoryPageCache();
    await cache.store("session-a", page(0, 30));
    const requests: unknown[] = [];
    const api = {
      async getHistoryPage() {
        requests.push("called");
        return page(0, 30);
      },
    };

    const current = page(30, 80);
    const older = await loadOlderHistoryPage("session-a", current, api, cache);
    expect(requests).toEqual([]);
    const merged = mergeOlderHistoryPage(current, older!);
    expect(merged.start_index).toBe(0);
    expect(merged.has_more_older).toBe(false);
    expect(merged.older_cursor).toBeNull();

    const next = await loadOlderHistoryPage("session-a", merged, api, cache);
    expect(next).toBeNull();
    expect(requests).toEqual([]);
  });
});
