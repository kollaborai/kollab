import { describe, expect, it } from "vitest";
import {
  completeComposerDraftSubmission,
  composerDraftStorageKey,
  loadComposerDraft,
  queueComposerDraftSubmission,
  saveComposerDraft,
  takeComposerDraftSubmission,
  type ComposerDraftStorage,
} from "../src/composer-draft";
import { applyChatStreamEvent, initialStreamState, type StreamState } from "../src/chat-store";
import { toRepository, toThreadMessageLike } from "../src/chat-runtime";
import type { ChatRow } from "../src/chat-stream";

const CONV = "conv-1";
const WHEN = "2026-11-25T12:00:00Z";
const NOTICE = "Earlier messages compacted";

function row(
  id: string,
  text: string,
  kind: ChatRow["kind"] = "reply",
  role: ChatRow["role"] = "assistant",
  status = "complete",
): ChatRow {
  return {
    id,
    seq: 1,
    kind,
    role,
    turn_id: null,
    status,
    parts: [{ type: "text", text }],
    created_at: WHEN,
  };
}

/** A compaction marker: the notice the daemon sends in place of the rows it dropped. */
const marker = (id: string, text = NOTICE): ChatRow => row(id, text, "marker", "system");

/** The store after a snapshot of `rows`; the next live event must carry `nextSeq`. */
function windowOf(rows: ChatRow[], nextSeq: number): StreamState {
  const result = applyChatStreamEvent(initialStreamState, {
    seq: 1,
    conversation_uid: CONV,
    generation: "fixture-daemon-1",
    type: "snapshot",
    data: { rows, next_seq: nextSeq, older: null, turns: [], inputs: [] },
  });
  expect(result.outcome).toEqual({ kind: "applied" });
  return result.state;
}

/** Applies one live event and asserts it was applied, not resynced or dropped. */
function applyLive(state: StreamState, seq: number, type: string, data: unknown): StreamState {
  const result = applyChatStreamEvent(state, { seq, conversation_uid: CONV, generation: "fixture-daemon-1", type, data });
  expect(result.outcome).toEqual({ kind: "applied" });
  return result.state;
}

class MemoryStorage implements ComposerDraftStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

describe("compaction replace over marker rows", () => {
  it("keeps the rows the replace carries under their ids, puts the marker first, and drops the rest", () => {
    const shown = windowOf(
      [
        row("web-a1", "hello", "input", "user"),
        row("r1", "hi"),
        row("live-1", "partial reply", "reply", "assistant", "streaming"),
      ],
      5,
    );
    const compacted = applyLive(shown, 5, "replace", {
      rows: [marker("m1"), row("web-a1", "hello", "input", "user"), row("r1", "hi")],
    });

    expect(compacted.rows.map((item) => item.id)).toEqual(["m1", "web-a1", "r1"]);
    // The replace is the window: the streaming row it omits is gone, and kept rows keep their content.
    expect(compacted.rows.slice(1)).toEqual(shown.rows.slice(0, 2));
    expect(new Set(compacted.rows.map((item) => item.id)).size).toBe(compacted.rows.length);
  });

  it("appends rows that arrive after the compaction behind the retained rows", () => {
    const compacted = applyLive(
      windowOf([row("web-a1", "hello", "input", "user"), row("r1", "hi")], 3),
      3,
      "replace",
      { rows: [marker("m1"), row("web-a1", "hello", "input", "user"), row("r1", "hi")] },
    );
    const next = applyLive(compacted, 4, "row.upsert", row("r2", "after the compaction"));

    expect(next.rows.map((item) => item.id)).toEqual(["m1", "web-a1", "r1", "r2"]);
  });

  it("keeps each later round's markers in the order the replace sends them", () => {
    const first = applyLive(windowOf([row("r1", "hi")], 2), 2, "replace", {
      rows: [marker("m1"), row("r1", "hi")],
    });
    const second = applyLive(first, 3, "replace", {
      rows: [marker("m1"), marker("m2", "Earlier messages compacted again"), row("r2", "after")],
    });

    expect(second.rows.map((item) => item.id)).toEqual(["m1", "m2", "r2"]);
    expect(second.rows.filter((item) => item.kind === "marker")).toHaveLength(2);
  });

  it("renders a marker row as a system message that carries the compaction notice", () => {
    const state = applyLive(windowOf([], 2), 2, "replace", { rows: [marker("m1")] });
    const repositoryMessage = toRepository(state.rows).messages[0]?.message;

    expect(toThreadMessageLike(marker("m1"))).toMatchObject({
      id: "m1",
      role: "system",
      content: [{ type: "text", text: NOTICE }],
    });
    expect(repositoryMessage).toMatchObject({
      id: "m1",
      role: "system",
      content: [{ type: "text", text: NOTICE }],
    });
  });
});

describe("composer draft persistence", () => {
  it("scopes stored drafts by agent and conversation and restores them after remount", () => {
    const storage = new MemoryStorage();
    const firstKey = composerDraftStorageKey("support-agent", "conversation-a");
    const otherConversation = composerDraftStorageKey("support-agent", "conversation-b");
    const otherAgent = composerDraftStorageKey("writer-agent", "conversation-a");
    expect(firstKey).not.toBeNull();
    expect(firstKey).not.toBe(otherConversation);
    expect(firstKey).not.toBe(otherAgent);

    saveComposerDraft(firstKey, "unfinished response", storage);

    // A remounted composer reads the same conversation-scoped browser entry.
    expect(loadComposerDraft(firstKey, storage)).toBe("unfinished response");
    expect(loadComposerDraft(otherConversation, storage)).toBeNull();
    expect(loadComposerDraft(otherAgent, storage)).toBeNull();
    expect(composerDraftStorageKey("", "conversation-a")).toBeNull();
  });

  it("retains a failed send's stored draft and clears only after success", () => {
    const storage = new MemoryStorage();
    const key = composerDraftStorageKey("agent", "conversation-failure")!;
    saveComposerDraft(key, "retry this", storage);
    queueComposerDraftSubmission(key, "retry this", storage);
    const submission = takeComposerDraftSubmission(key);

    // A failed/error/cancelled request never calls the success clear operation.
    expect(loadComposerDraft(key, storage)).toBe("retry this");
    expect(completeComposerDraftSubmission(submission, storage)).toBe(true);
    expect(loadComposerDraft(key, storage)).toBeNull();
  });

  it("keeps a queued draft when the composer resets before a failed response", () => {
    const storage = new MemoryStorage();
    const key = composerDraftStorageKey("agent", "conversation-reset")!;
    saveComposerDraft(key, "retry after error", storage);
    queueComposerDraftSubmission(key, "retry after error", storage);

    // assistant-ui clears its in-memory composer as soon as submit starts.
    saveComposerDraft(key, "", storage);
    const submission = takeComposerDraftSubmission(key);
    expect(submission?.text).toBe("retry after error");

    // A failed transport response cannot clear the unsent persisted message.
    expect(loadComposerDraft(key, storage)).toBe("retry after error");
  });


  it("drops an unprepared stale ticket when a newer draft is submitted", () => {
    const storage = new MemoryStorage();
    const key = composerDraftStorageKey("agent", "conversation-retry")!;
    saveComposerDraft(key, "old attempt", storage);
    queueComposerDraftSubmission(key, "old attempt", storage);
    saveComposerDraft(key, "new attempt", storage);
    queueComposerDraftSubmission(key, "new attempt", storage);

    const submission = takeComposerDraftSubmission(key);
    expect(submission?.text).toBe("new attempt");
    expect(completeComposerDraftSubmission(submission, storage)).toBe(true);
    expect(loadComposerDraft(key, storage)).toBeNull();
  });

  it("does not clear a newer draft when the previous send succeeds", () => {
    const storage = new MemoryStorage();
    const key = composerDraftStorageKey("agent", "conversation-newer")!;
    saveComposerDraft(key, "submitted text", storage);
    queueComposerDraftSubmission(key, "submitted text", storage);
    const submission = takeComposerDraftSubmission(key);
    saveComposerDraft(key, "newer unsent text", storage);

    expect(completeComposerDraftSubmission(submission, storage)).toBe(false);
    expect(loadComposerDraft(key, storage)).toBe("newer unsent text");
  });
});
