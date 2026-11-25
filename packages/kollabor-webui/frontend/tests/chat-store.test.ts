import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { ChatRow, InputInfo, TurnInfo } from "../src/chat-stream";
import {
  applyChatStreamEvent,
  initialStreamState,
  turnsRunning,
  type ApplyOutcome,
  type StreamState,
} from "../src/chat-store";

// The same fixtures chat-stream-contract.test.ts reads (#1451). An ok fixture
// has an empty `expect`, the same rule as the Python contract test.
const dir = new URL("./fixtures/chat-stream/", import.meta.url);
const fixtures = readdirSync(dir)
  .filter((name) => name.endsWith(".json"))
  .map((name) => ({
    name,
    ...(JSON.parse(readFileSync(new URL(name, dir), "utf8")) as { expect: string[]; events: unknown[] }),
  }));

const CONV = "conv-1";
const WHEN = "2026-11-25T00:00:00Z";

function row(id: string, text = "", kind: ChatRow["kind"] = "reply", role: ChatRow["role"] = "assistant"): ChatRow {
  return { id, seq: 1, kind, role, turn_id: null, status: "complete", parts: [{ type: "text", text }], created_at: WHEN };
}

function streamEvent(seq: number, type: string, data: unknown, conversationUid = CONV): unknown {
  return { seq, conversation_uid: conversationUid, type, data };
}

/** A snapshot holding `rows`; live events after it start at `nextSeq`. */
function snapshot(
  nextSeq: number,
  rows: ChatRow[] = [],
  extra: { inputs?: InputInfo[]; turns?: TurnInfo[]; older?: string | null } = {},
): unknown {
  return streamEvent(1, "snapshot", {
    rows,
    next_seq: nextSeq,
    older: extra.older ?? null,
    turns: extra.turns ?? [],
    inputs: extra.inputs ?? [],
  });
}

/** Applies the events in order, expecting each one to apply, and returns the final state. */
function applyAll(events: readonly unknown[], start: StreamState = initialStreamState): StreamState {
  let state = start;
  for (const event of events) {
    const result = applyChatStreamEvent(state, event);
    expect(result.outcome).toEqual({ kind: "applied" });
    state = result.state;
  }
  return state;
}

/** The first event that does not apply, with its outcome; null when every event applies. */
function firstStop(events: readonly unknown[]): { index: number; outcome: ApplyOutcome } | null {
  let state = initialStreamState;
  for (const [index, event] of events.entries()) {
    const result = applyChatStreamEvent(state, event);
    if (result.outcome.kind !== "applied") return { index, outcome: result.outcome };
    state = result.state;
  }
  return null;
}

describe("applyChatStreamEvent", () => {
  it("snapshot sets rows, inputs, turns and olderCursor, and sets lastSeq to next_seq minus 1", () => {
    const input: InputInfo = { id: "web-a1", state: "started", turn_id: "t1" };
    const turn: TurnInfo = { turn_id: "t1", state: "running", input_ids: ["web-a1"] };
    const rows = [row("web-a1", "hi", "input", "user"), row("r1", "hello")];
    // The snapshot's own seq (7) is not used: lastSeq comes from next_seq.
    const result = applyChatStreamEvent(
      initialStreamState,
      streamEvent(7, "snapshot", { rows, next_seq: 42, older: "cursor-9", turns: [turn], inputs: [input] }),
    );
    expect(result.outcome).toEqual({ kind: "applied" });
    expect(result.state).toEqual({
      conversationUid: CONV,
      lastSeq: 41,
      rows,
      inputs: { "web-a1": input },
      turns: { t1: turn },
      olderCursor: "cursor-9",
    });
  });

  it("row.upsert appends a new row and replaces an existing row with the same id in place", () => {
    const start = applyAll([snapshot(2, [row("r1", "one"), row("r2", "two")])]);
    const appended = applyAll([streamEvent(2, "row.upsert", row("r3", "three"))], start);
    expect(appended.rows.map((r) => r.id)).toEqual(["r1", "r2", "r3"]);
    const edited = { ...row("r1", "ONE"), status: "edited" };
    const replaced = applyAll([streamEvent(3, "row.upsert", edited)], appended);
    expect(replaced.rows).toEqual([edited, row("r2", "two"), row("r3", "three")]);
  });

  it("row.append adds text to the text part at index part and leaves the other parts unchanged", () => {
    const parts: ChatRow["parts"] = [
      { type: "text", text: "a" },
      { type: "reasoning", text: "think" },
      { type: "image", url: "x.png" },
      { type: "text", text: "b" },
    ];
    const start = applyAll([snapshot(2, [{ ...row("r1"), parts }])]);
    const first = applyAll([streamEvent(2, "row.append", { id: "r1", part: 3, text: " c" })], start);
    const second = applyAll([streamEvent(3, "row.append", { id: "r1", part: 1, text: "ing" })], first);
    expect(second.rows[0].parts).toEqual([
      { type: "text", text: "a" },
      { type: "reasoning", text: "thinking" },
      { type: "image", url: "x.png" },
      { type: "text", text: "b c" },
    ]);
    // The state before the append is not mutated.
    expect(start.rows[0].parts[3]).toEqual({ type: "text", text: "b" });
    expect(start.rows[0].parts[1]).toEqual({ type: "reasoning", text: "think" });
  });

  it("row.append to an unknown row resyncs", () => {
    const start = applyAll([snapshot(2, [row("r1")])]);
    const result = applyChatStreamEvent(start, streamEvent(2, "row.append", { id: "nope", part: 0, text: "x" }));
    expect(result.outcome).toEqual({ kind: "resync", reason: "append to unknown row" });
  });

  it("row.append to a part that is not text or reasoning resyncs", () => {
    const start = applyAll([snapshot(2, [{ ...row("r1"), parts: [{ type: "image", url: "x.png" }] }])]);
    // Part 0 is an image; part 4 is past the end of the parts.
    for (const part of [0, 4]) {
      const result = applyChatStreamEvent(start, streamEvent(2, "row.append", { id: "r1", part, text: "x" }));
      expect(result.outcome).toEqual({ kind: "resync", reason: "append to a part that is not text" });
    }
  });

  it("row.delete removes the row; delete of an unknown row resyncs", () => {
    const start = applyAll([snapshot(2, [row("r1"), row("r2")])]);
    const deleted = applyChatStreamEvent(start, streamEvent(2, "row.delete", { id: "r1" }));
    expect(deleted.outcome).toEqual({ kind: "applied" });
    expect(deleted.state.rows.map((r) => r.id)).toEqual(["r2"]);
    const unknown = applyChatStreamEvent(deleted.state, streamEvent(3, "row.delete", { id: "r1" }));
    expect(unknown.outcome).toEqual({ kind: "resync", reason: "delete of unknown row" });
  });

  it("replace swaps the rows and keeps inputs and turns", () => {
    const input: InputInfo = { id: "web-a1", state: "started", turn_id: "t1" };
    const turn: TurnInfo = { turn_id: "t1", state: "running", input_ids: ["web-a1"] };
    const start = applyAll([snapshot(2, [row("r1", "one")], { inputs: [input], turns: [turn] })]);
    const replacement = [row("m1", "Compacted", "marker", "system")];
    const replaced = applyChatStreamEvent(start, streamEvent(2, "replace", { rows: replacement }));
    expect(replaced.outcome).toEqual({ kind: "applied" });
    expect(replaced.state.rows).toEqual(replacement);
    expect(replaced.state.inputs).toEqual({ "web-a1": input });
    expect(replaced.state.turns).toEqual({ t1: turn });
  });

  it("input.state records the input; a second ended for the same input resyncs", () => {
    let state = applyAll([snapshot(2)]);
    state = applyAll([streamEvent(2, "input.state", { id: "web-a1", state: "queued", turn_id: null })], state);
    expect(state.inputs).toEqual({ "web-a1": { id: "web-a1", state: "queued", turn_id: null } });
    state = applyAll(
      [
        streamEvent(3, "input.state", { id: "web-a1", state: "started", turn_id: "t1" }),
        streamEvent(4, "input.state", { id: "web-a1", state: "ended", turn_id: "t1", outcome: "completed" }),
      ],
      state,
    );
    expect(state.inputs["web-a1"]).toEqual({ id: "web-a1", state: "ended", turn_id: "t1", outcome: "completed" });
    const again = applyChatStreamEvent(
      state,
      streamEvent(5, "input.state", { id: "web-a1", state: "ended", turn_id: "t1", outcome: "completed" }),
    );
    expect(again.outcome).toEqual({ kind: "resync", reason: "input changed after it ended" });
  });

  it("turn.state records the turn; turnsRunning is true only while some turn is running", () => {
    const running: TurnInfo = { turn_id: "t1", state: "running", input_ids: ["web-a1"] };
    const ended: TurnInfo = { ...running, state: "ended" };
    const ran = applyAll([streamEvent(2, "turn.state", running)], applyAll([snapshot(2)]));
    expect(ran.turns).toEqual({ t1: running });
    expect(turnsRunning(ran.turns)).toBe(true);
    const done = applyAll([streamEvent(3, "turn.state", ended)], ran);
    expect(turnsRunning(done.turns)).toBe(false);
    expect(turnsRunning({ t1: ended, t2: running })).toBe(true);
    expect(turnsRunning({})).toBe(false);
  });

  it("an event whose seq equals lastSeq is a duplicate and leaves the state unchanged", () => {
    const start = applyAll([snapshot(2, [row("r1")])]);
    const result = applyChatStreamEvent(start, streamEvent(1, "row.upsert", row("r9")));
    expect(result.outcome).toEqual({ kind: "duplicate" });
    expect(result.state).toBe(start);
  });

  it("an event whose seq is below lastSeq resyncs", () => {
    const start = applyAll([snapshot(3)]);
    const result = applyChatStreamEvent(start, streamEvent(1, "row.upsert", row("r9")));
    expect(result.outcome).toEqual({ kind: "resync", reason: "seq went backwards" });
  });

  it("an event whose seq skips ahead resyncs with reason seq gap", () => {
    const start = applyAll([snapshot(2)]);
    const result = applyChatStreamEvent(start, streamEvent(3, "row.upsert", row("r9")));
    expect(result.outcome).toEqual({ kind: "resync", reason: "seq gap" });
  });

  it("a non-snapshot event before any snapshot resyncs", () => {
    const result = applyChatStreamEvent(initialStreamState, streamEvent(1, "row.upsert", row("r1")));
    expect(result.outcome).toEqual({ kind: "resync", reason: "event before snapshot" });
  });

  it("an event whose conversation_uid differs from the snapshot's resyncs", () => {
    const start = applyAll([snapshot(2)]);
    const result = applyChatStreamEvent(start, streamEvent(2, "row.upsert", row("r1"), "conv-2"));
    expect(result.outcome).toEqual({ kind: "resync", reason: "conversation changed" });
  });

  it("an event that fails isChatStreamEvent resyncs with reason invalid event shape", () => {
    const start = applyAll([snapshot(2)]);
    expect(applyChatStreamEvent(start, streamEvent(2, "row.delete", {})).outcome).toEqual({
      kind: "resync",
      reason: "invalid event shape",
    });
    expect(applyChatStreamEvent(start, "not an event").outcome).toEqual({
      kind: "resync",
      reason: "invalid event shape",
    });
  });

  it("input.state and turn.state keep the same rows array reference", () => {
    const start = applyAll([snapshot(2, [row("r1")])]);
    const withInput = applyChatStreamEvent(
      start,
      streamEvent(2, "input.state", { id: "web-a1", state: "queued", turn_id: null }),
    );
    expect(withInput.state.rows).toBe(start.rows);
    const withTurn = applyChatStreamEvent(
      withInput.state,
      streamEvent(3, "turn.state", { turn_id: "t1", state: "running", input_ids: ["web-a1"] }),
    );
    expect(withTurn.state.rows).toBe(start.rows);
  });

  it("a resync keeps rows and inputs but clears the cursor, so the next snapshot replaces them", () => {
    const input: InputInfo = { id: "web-a1", state: "queued", turn_id: null };
    const start = applyAll([snapshot(2, [row("r1", "one")], { inputs: [input], older: "c1" })]);
    const gap = applyChatStreamEvent(start, streamEvent(5, "row.upsert", row("r2")));
    expect(gap.outcome).toEqual({ kind: "resync", reason: "seq gap" });
    expect(gap.state).toEqual({ ...start, conversationUid: null, lastSeq: null });
    expect(applyChatStreamEvent(gap.state, streamEvent(6, "row.upsert", row("r2"))).outcome).toEqual({
      kind: "resync",
      reason: "event before snapshot",
    });
    const restored = applyChatStreamEvent(gap.state, snapshot(9, [row("r2")]));
    expect(restored.outcome).toEqual({ kind: "applied" });
    expect(restored.state.lastSeq).toBe(8);
  });
});

describe("fixture replay", () => {
  it("applies every ok fixture (empty expect) with no resync", () => {
    const ok = fixtures.filter((fixture) => fixture.expect.length === 0);
    expect(ok.map((fixture) => fixture.name).sort()).toEqual([
      "compaction.json",
      "reload-mid-turn.json",
      "steered-input.json",
    ]);
    for (const fixture of ok) {
      expect([fixture.name, firstStop(fixture.events)]).toEqual([fixture.name, null]);
    }
  });

  it("stops bad-seq.json at a resync on its seq 3 event", () => {
    const bad = fixtures.find((fixture) => fixture.name === "bad-seq.json");
    expect(bad?.events[1]).toMatchObject({ seq: 3 });
    expect(firstStop(bad?.events ?? [])).toEqual({
      index: 1,
      outcome: { kind: "resync", reason: "seq gap" },
    });
  });
});
