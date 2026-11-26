// The saved conversation (#1451): the chat keeps each session's newest settled rows in the browser, so a
// reopened chat (a remote agent, a phone) shows them while its stream connects or cannot connect (#880).
import { describe, expect, it, vi } from "vitest";
import { cacheableRows, type ChatRowCache } from "../src/chat-row-cache";
import { LiveChatStore, type ChatTransport } from "../src/chat-store";
import type { ChatRow, ChatStreamEvent } from "../src/chat-stream";
import { initialHistoryBanner, SAVED_CONVERSATION_SHOWN } from "../src/history-banner";

const row = (id: string, patch: Partial<ChatRow> = {}): ChatRow => ({
  id,
  seq: 1,
  kind: "reply",
  role: "assistant",
  turn_id: null,
  status: "completed",
  parts: [{ type: "text", text: id }],
  created_at: "2026-11-25T00:00:00Z",
  ...patch,
});

const snapshot = (rows: ChatRow[]): ChatStreamEvent => ({
  seq: 1,
  conversation_uid: "conv-1",
  generation: "daemon-1",
  type: "snapshot",
  data: { rows, next_seq: 2, older: null, turns: [], inputs: [] },
});

function memoryCache(initial: ChatRow[] | null = null) {
  let saved = initial;
  const cache: ChatRowCache = {
    load: vi.fn(async () => saved),
    save: vi.fn(async (_sessionId: string, rows: readonly ChatRow[]) => {
      saved = [...rows];
    }),
  };
  return { cache, saved: () => saved };
}

/** A transport whose stream delivers `events` and stays open, or fails when `events` is null. */
function transport(events: ChatStreamEvent[] | null): ChatTransport {
  return {
    request: vi.fn(async () => new Response("{}", { status: 202 })),
    streamChatEvents: vi.fn(async (_id, _cursor, signal, onEvent, onOpen) => {
      if (events === null) throw new Error("synthetic remote read failure");
      onOpen?.();
      for (const event of events) onEvent?.(event);
      await new Promise<void>((resolve) => signal?.addEventListener("abort", () => resolve()));
    }),
  };
}

const ids = (store: LiveChatStore) => store.getSnapshot().rows.map((item) => item.id);

describe("the saved conversation", () => {
  it("shows the saved rows when the stream cannot connect, and keeps them", async () => {
    const memory = memoryCache([row("saved-1"), row("saved-2")]);
    const store = new LiveChatStore(transport(null), "s1", { cache: memory.cache, maxAttempts: 1 });
    store.start();
    await vi.waitFor(() => expect(store.getSnapshot().connection).toBe("failed"));
    await vi.waitFor(() => expect(ids(store)).toEqual(["saved-1", "saved-2"]));
    expect(store.getSnapshot().saved).toBe(true);
    expect(memory.saved()?.map((item) => item.id)).toEqual(["saved-1", "saved-2"]);
    store.dispose();
  });

  it("replaces the saved rows with the first snapshot and saves it", async () => {
    const memory = memoryCache([row("saved-1")]);
    const store = new LiveChatStore(transport([snapshot([row("live-1")])]), "s1", { cache: memory.cache });
    store.start();
    await vi.waitFor(() => expect(ids(store)).toEqual(["live-1"]));
    await vi.waitFor(() => expect(memory.saved()?.map((item) => item.id)).toEqual(["live-1"]));
    expect(store.getSnapshot().saved).toBeFalsy();
    store.dispose();
  });

  it("ignores saved rows that load after the snapshot", async () => {
    let release: (rows: ChatRow[]) => void = () => {};
    const cache: ChatRowCache = {
      load: () => new Promise((resolve) => { release = resolve; }),
      save: vi.fn(async () => {}),
    };
    const store = new LiveChatStore(transport([snapshot([])]), "s1", { cache });
    store.start();
    await vi.waitFor(() => expect(store.getSnapshot().connection).toBe("live"));
    release([row("stale")]);
    await Promise.resolve();
    await Promise.resolve();
    expect(ids(store)).toEqual([]);
    // An empty conversation (a cleared one) clears what was saved.
    expect(cache.save).toHaveBeenLastCalledWith("s1", []);
    store.dispose();
  });

  it("saves only settled rows: no prompt, queued or streaming row", () => {
    const kept = cacheableRows([
      row("input", { kind: "input", role: "user" }),
      row("prompt", { kind: "prompt", status: "open", parts: [{ type: "prompt", prompt_id: "p", data: {} }] }),
      row("queued", { kind: "input", role: "user", status: "queued" }),
      row("streaming", { status: "streaming" }),
      row("running", { kind: "tool", status: "running" }),
      row("reply"),
    ]);
    expect(kept.map((item) => item.id)).toEqual(["input", "reply"]);
  });

  it("keeps the newest 50 rows within 1.8 MB", () => {
    const many = Array.from({ length: 60 }, (_, index) => row(`r${index}`));
    expect(cacheableRows(many).map((item) => item.id)).toEqual(many.slice(10).map((item) => item.id));
    const big = Array.from({ length: 10 }, (_, index) =>
      row(`b${index}`, { parts: [{ type: "text", text: "x".repeat(250_000) }] }));
    const kept = cacheableRows(big);
    expect(kept.length).toBeGreaterThan(0);
    expect(kept.at(-1)?.id).toBe("b9");
    expect(new TextEncoder().encode(JSON.stringify(kept)).byteLength).toBeLessThanOrEqual(1_800_000);
  });

  it("says the saved conversation is on screen once the stream has failed, with a reconnect", () => {
    expect(initialHistoryBanner("failed", 2, true)).toEqual({
      kind: "error",
      message: SAVED_CONVERSATION_SHOWN,
      retry: "reconnect",
    });
    expect(initialHistoryBanner("connecting", 2, true)).toBeNull();
    expect(initialHistoryBanner("failed", 2)).toBeNull();
  });
});
