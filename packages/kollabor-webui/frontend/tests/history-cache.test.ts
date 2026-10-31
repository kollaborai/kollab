import { describe, expect, it, vi } from "vitest";
import type { HistoryMessage, HistoryPage } from "../src/api";
import { buildInitialState } from "../src/runtime";
import {
  HISTORY_CACHE_MAX_PAGES,
  HISTORY_CACHE_MAX_SESSIONS,
  MemoryHistoryPageCache,
  loadLatestHistoryPage,
  loadOlderHistoryPage,
  mergeOlderHistoryPage,
  mergeRecentHistoryPage,
  restoreSlimmedHistoryMetadata,
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
});


function markedSlimmed(reference: string, kept: Record<string, unknown> = {}): HistoryMessage {
  return {
    role: "assistant",
    content: "done",
    metadata: { ...kept, acp_updates_dropped: 3, history_output_ref: reference },
  };
}

describe("restoreSlimmedHistoryMetadata", () => {
  it("returns the page untouched when nothing is slimmed", async () => {
    const api = { getHistoryMetadata: vi.fn() };
    const history: HistoryMessage[] = [
      { role: "user", content: "hi", metadata: {} },
      { role: "assistant", content: "ok", metadata: { acp_updates: [] } },
    ];

    const result = await restoreSlimmedHistoryMetadata("session-1", api, history);

    expect(result).toBe(history);
    expect(api.getHistoryMetadata).not.toHaveBeenCalled();
  });

  it("merges missing keys and clears the marker for a slimmed entry", async () => {
    const api = {
      getHistoryMetadata: vi.fn().mockResolvedValue({
        session_id: "session-1",
        metadata: {
          acp_updates: [{ kind: "text", text: "a" }],
          provider: "acp",
          extra: 1,
        },
      }),
    };
    const history: HistoryMessage[] = [
      { role: "user", content: "hi", metadata: {} },
      markedSlimmed("ref-1", { provider: "acp" }),
    ];

    const result = await restoreSlimmedHistoryMetadata("session-1", api, history);

    expect(api.getHistoryMetadata).toHaveBeenCalledWith("session-1", "ref-1");
    const metadata = result[1].metadata as Record<string, unknown>;
    expect(metadata.acp_updates).toEqual([{ kind: "text", text: "a" }]);
    expect(metadata.extra).toBe(1);
    expect(metadata.provider).toBe("acp");
    expect("acp_updates_dropped" in metadata).toBe(false);
  });

  it("keeps the slimmed entry when the metadata fetch fails", async () => {
    const api = { getHistoryMetadata: vi.fn().mockRejectedValue(new Error("gone")) };
    const history: HistoryMessage[] = [markedSlimmed("ref-1")];

    const result = await restoreSlimmedHistoryMetadata("session-1", api, history);

    const metadata = result[0].metadata as Record<string, unknown>;
    expect(metadata.acp_updates_dropped).toBe(3);
    expect("acp_updates" in metadata).toBe(false);
  });
});
