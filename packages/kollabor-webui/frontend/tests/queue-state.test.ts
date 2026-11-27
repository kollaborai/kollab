import { describe, expect, it } from "vitest";
import type { ChatRow, InputInfo, InputState, RowKind } from "../src/chat-stream";
import { canSendNow, composerAction, isWaitingRow } from "../src/queue-state";

const row = (id: string, kind: RowKind = "input"): ChatRow => ({
  id,
  seq: 1,
  kind,
  role: "user",
  turn_id: null,
  status: "queued",
  parts: [{ type: "text", text: "hello" }],
  created_at: "2026-11-25T12:00:00Z",
});

const input = (id: string, state: InputState): InputInfo => ({
  id,
  state,
  turn_id: state === "queued" ? null : "t1",
  ...(state === "ended" ? { outcome: "completed" as const } : {}),
});

describe("composerAction", () => {
  it("sends an idle composer that has content", () => {
    expect(composerAction({ running: false, hasContent: true })).toBe("send");
  });

  it("disables send for an idle composer that is empty", () => {
    expect(composerAction({ running: false, hasContent: false })).toBe("idle");
  });

  it("queues content typed while a turn runs", () => {
    expect(composerAction({ running: true, hasContent: true })).toBe("queue");
  });

  it("offers Stop while a turn runs and the composer is empty", () => {
    expect(composerAction({ running: true, hasContent: false })).toBe("stop");
  });
});

describe("isWaitingRow", () => {
  it("is true for an input row whose input is queued", () => {
    expect(isWaitingRow(row("web-a1"), { "web-a1": input("web-a1", "queued") })).toBe(true);
  });

  it("is false once the input has started, joined or ended", () => {
    for (const state of ["started", "joined", "ended"] as const) {
      expect([state, isWaitingRow(row("web-a1"), { "web-a1": input("web-a1", state) })]).toEqual([state, false]);
    }
  });

  it("is false for an input row the store has no state for", () => {
    expect(isWaitingRow(row("web-a1"), {})).toBe(false);
  });

  it("is false for rows that are not inputs, even when an input with the same id is queued", () => {
    const inputs = { "web-a1": input("web-a1", "queued") };
    for (const kind of ["reply", "tool", "note", "prompt", "marker"] as const) {
      expect([kind, isWaitingRow(row("web-a1", kind), inputs)]).toEqual([kind, false]);
    }
  });
});

describe("canSendNow", () => {
  it("is true only for a queued input", () => {
    expect(canSendNow(input("web-a1", "queued"))).toBe(true);
    for (const state of ["started", "joined", "ended"] as const) {
      expect([state, canSendNow(input("web-a1", state))]).toEqual([state, false]);
    }
  });

  it("is false when there is no input", () => {
    expect(canSendNow(undefined)).toBe(false);
  });
});
