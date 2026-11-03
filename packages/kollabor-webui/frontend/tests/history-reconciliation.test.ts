import { afterEach, describe, expect, it, vi } from "vitest";
import type { ThreadMessageLike } from "@assistant-ui/react";
import { buildInitialState } from "../src/runtime";
import {
  applyLiveEvent,
  emptyLiveTurn,
  historyReplacesLiveTurn,
  liveMessage,
  replacedLiveTurn,
} from "../src/live-turn";
import { createHistoryReconciliation } from "../src/history-reconciliation";
import { mergeMissingHistoryMessages } from "../src/history-merge";

describe("delayed voice history reconciliation", () => {
  afterEach(() => vi.useRealTimers());

  it("keeps the streamed reply past the old settle window and replaces it when both canonical rows arrive", async () => {
    vi.useFakeTimers();
    const requestId = "voice-turn-1";
    let turn = applyLiveEvent(emptyLiveTurn(), {
      type: "token",
      request_id: requestId,
      text: "I received the transcription: Testing testing one two three.",
    });
    turn = applyLiveEvent(turn, { type: "turn_complete", request_id: requestId });
    expect(liveMessage(turn)).not.toBeNull();

    let canonicalRows: Array<{ role: string; content: string; metadata?: Record<string, unknown> }> = [];
    const startedAt = Date.now();
    let attempts = 0;
    let exhausted = false;
    let visibleMessages: ThreadMessageLike[] = [liveMessage(turn)!];
    const reconciliation = createHistoryReconciliation({
      delaysMs: [250, 500, 1000, 2000, 3000, 5000],
      isCurrent: () => true,
      refresh: async () => {
        attempts += 1;
        if (Date.now() - startedAt >= 5_500) {
          canonicalRows = [
            { role: "user", content: "Testing testing one two three" },
            {
              role: "assistant",
              content: "I received the transcription: Testing testing one two three.",
              metadata: { request_id: requestId, voice_turn_id: requestId },
            },
          ];
        }
        if (!canonicalRows.some((row) => row.role === "assistant" && row.metadata?.voice_turn_id === requestId)) {
          return false;
        }
        const mapped = buildInitialState("session-a", [], canonicalRows as never).messages;
        const merged = mergeMissingHistoryMessages(
          visibleMessages.filter((message) => message.id !== liveMessage(turn)?.id),
          mapped,
        );
        if (!mapped.some((message) => message.role === "user") ||
            !historyReplacesLiveTurn(turn, merged.added as ThreadMessageLike[])) return false;
        turn = replacedLiveTurn(turn);
        visibleMessages = merged.messages.filter((message) => message.id !== liveMessage(turn)?.id);
        return true;
      },
      onExhausted: () => { exhausted = true; },
    });
    reconciliation.start();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(liveMessage(turn)).not.toBeNull();
    expect(exhausted).toBe(false);

    await vi.advanceTimersByTimeAsync(2_000);
    expect(attempts).toBeGreaterThan(1);
    expect(exhausted).toBe(false);
    expect(canonicalRows.filter((row) => row.role === "user")).toHaveLength(1);
    expect(canonicalRows.filter((row) => row.role === "assistant")).toHaveLength(1);
    expect(liveMessage(turn)).toBeNull();
    expect(visibleMessages.filter((message) => message.role === "user")).toHaveLength(1);
    expect(visibleMessages.filter((message) => message.role === "assistant")).toHaveLength(1);
    reconciliation.cancel();
  });

  it("cancels delayed reads when the thread leaves the session", async () => {
    vi.useFakeTimers();
    let sessionId = "session-a";
    let attempts = 0;
    let exhausted = false;
    const reconciliation = createHistoryReconciliation({
      delaysMs: [1000, 2000],
      isCurrent: () => sessionId === "session-a",
      refresh: async () => { attempts += 1; return false; },
      onExhausted: () => { exhausted = true; },
    });
    reconciliation.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(attempts).toBe(2);
    sessionId = "session-b";
    reconciliation.cancel();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(attempts).toBe(2);
    expect(exhausted).toBe(false);
  });

  it("stops after its retry budget while leaving the streamed reply available for Retry", async () => {
    vi.useFakeTimers();
    const requestId = "voice-turn-2";
    const turn = applyLiveEvent(
      applyLiveEvent(emptyLiveTurn(), { type: "token", request_id: requestId, text: "Visible reply" }),
      { type: "turn_complete", request_id: requestId },
    );
    let attempts = 0;
    let exhausted = 0;
    const reconciliation = createHistoryReconciliation({
      delaysMs: [50, 100],
      isCurrent: () => true,
      refresh: async () => { attempts += 1; return false; },
      onExhausted: () => { exhausted += 1; },
    });
    reconciliation.start();
    await vi.advanceTimersByTimeAsync(200);
    expect(attempts).toBe(3);
    expect(exhausted).toBe(1);
    expect(liveMessage(turn)?.content).toEqual([{ type: "text", text: "Visible reply" }]);
    expect(reconciliation.refreshNow()).toBe(false);
    reconciliation.cancel();
  });

  it("does not let repeated refresh events bypass the attempt budget", async () => {
    vi.useFakeTimers();
    let attempts = 0;
    let exhausted = 0;
    const reconciliation = createHistoryReconciliation({
      delaysMs: [100, 200],
      isCurrent: () => true,
      refresh: async () => { attempts += 1; return false; },
      onExhausted: () => { exhausted += 1; },
    });
    reconciliation.start();
    for (let i = 0; i < 20; i += 1) expect(reconciliation.refreshNow()).toBe(true);
    await vi.runAllTimersAsync();
    expect(attempts).toBe(3);
    expect(exhausted).toBe(1);
    expect(reconciliation.refreshNow()).toBe(false);
  });

  it("allows the caller to perform a normal late-event refresh after exhaustion", async () => {
    vi.useFakeTimers();
    let canonicalAvailable = false;
    let attempts = 0;
    const reconciliation = createHistoryReconciliation({
      delaysMs: [],
      isCurrent: () => true,
      refresh: async () => { attempts += 1; return canonicalAvailable; },
      onExhausted: () => {},
    });
    reconciliation.start();
    await vi.runAllTimersAsync();
    canonicalAvailable = true;
    let normalReads = 0;
    const normalHistoryRefresh = async () => {
      normalReads += 1;
      return canonicalAvailable;
    };
    // Mirror App's history_updated/user_message/reconnect handler: exhausted
    // helper -> one ordinary current-session read.
    const handleLateHistoryEvent = () => reconciliation.refreshNow()
      ? Promise.resolve(false)
      : normalHistoryRefresh();
    const lateEventFoundCanonical = await handleLateHistoryEvent();
    expect(lateEventFoundCanonical).toBe(true);
    expect(attempts).toBe(1);
    expect(normalReads).toBe(1);
  });
});
