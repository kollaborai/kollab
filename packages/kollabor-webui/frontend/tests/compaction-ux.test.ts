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
import {
  mergeCompactionHistorySnapshot,
  mergeMissingHistoryMessages,
  shownMessagesChanged,
} from "../src/history-merge";

type Message = {
  id: string;
  content: unknown;
  metadata?: { custom?: Record<string, unknown> };
};

const message = (id: string, content: string): Message => ({ id, content });

const compaction = (round: number, content = `Summary ${round}`): Message => ({
  id: "history-0",
  content,
  metadata: {
    custom: {
      contextCompaction: true,
      compactionRound: round,
      preMessageCount: 20,
      postMessageCount: 4,
    },
  },
});

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

describe("compaction history reconciliation", () => {
  it("keeps the visible transcript and imports the compacted snapshot without stale rows", () => {
    const shown = [
      message("history-0", "Original opening"),
      message("history-1", "Original answer"),
      message("live-message:active", "Streaming reply"),
    ];
    const loaded = [
      compaction(1),
      message("history-1", "Recent retained turn"),
      message("history-2", "Turn appended during compaction"),
    ];

    const rebased = mergeCompactionHistorySnapshot(shown, loaded);

    expect(rebased).not.toBeNull();
    expect(rebased?.messages.map((item) => item.content)).toEqual([
      "Original opening",
      "Original answer",
      "Summary 1",
      "Recent retained turn",
      "Turn appended during compaction",
      "Streaming reply",
    ]);
    expect(rebased?.messages[0].id).toMatch(/^compaction-archive-/);
    expect(shownMessagesChanged(rebased!.messages, loaded)).toBe(false);
    expect(mergeCompactionHistorySnapshot(rebased!.messages, loaded)).toBeNull();
  });

  it("appends post-compaction history after the retained transcript", () => {
    const shown = [message("history-0", "Before compaction")];
    const snapshot = [compaction(1), message("history-1", "Retained")];
    const rebased = mergeCompactionHistorySnapshot(shown, snapshot)!;
    const next = mergeMissingHistoryMessages(
      rebased.messages,
      [message("history-2", "New after compaction")],
    );

    expect(next.messages.map((item) => item.content)).toEqual([
      "Before compaction",
      "Summary 1",
      "Retained",
      "New after compaction",
    ]);
  });

  it("archives earlier rounds again when a distinct compaction round arrives", () => {
    const initial = [message("history-0", "Original transcript")];
    const first = mergeCompactionHistorySnapshot(initial, [compaction(1)])!;
    const second = mergeCompactionHistorySnapshot(first.messages, [compaction(2)])!;

    expect(second.messages.map((item) => item.content)).toEqual([
      "Original transcript",
      "Summary 1",
      "Summary 2",
    ]);
    expect(shownMessagesChanged(second.messages, [compaction(2)])).toBe(false);
  });

  it("keeps ordinary removed history on the existing stale-reset path", () => {
    expect(
      mergeCompactionHistorySnapshot(
        [message("history-0", "Visible")],
        [message("history-0", "Replacement")],
      ),
    ).toBeNull();
    expect(
      shownMessagesChanged(
        [message("history-0", "Visible"), message("history-1", "Removed")],
        [message("history-0", "Visible")],
      ),
    ).toBe(true);
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
