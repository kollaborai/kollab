import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { isChatStreamEvent, newInputId } from "../src/chat-stream";

// The same fixtures the Python contract tests read (#1451).
const dir = new URL("./fixtures/chat-stream/", import.meta.url);
const cases = readdirSync(dir)
  .filter((name) => name.endsWith(".json"))
  .map((name) => ({ name, events: JSON.parse(readFileSync(new URL(name, dir), "utf8")).events as unknown[] }));

const ROW = { id: "r1", seq: 1, kind: "input", role: "user", turn_id: null, status: "queued", parts: [], created_at: "2026-11-25T10:00:00Z" };
const SNAPSHOT = { rows: [], next_seq: 1, older: null, turns: [], inputs: [] };
const without = (data: Record<string, unknown>, key: string) => Object.fromEntries(Object.entries(data).filter(([k]) => k !== key));
const upsert = (data: unknown) => ({ seq: 1, conversation_uid: "c", type: "row.upsert", data });
const snapshot = (data: unknown) => ({ seq: 1, conversation_uid: "c", type: "snapshot", data });

describe("chat stream contract", () => {
  it("reads all seven fixtures", () => {
    expect(cases.map((c) => c.name).sort()).toEqual([
      "bad-never-ends.json", "bad-regress.json", "bad-reid.json", "bad-seq.json", "compaction.json", "reload-mid-turn.json",
      "steered-input.json",
    ]);
  });

  it("accepts a full row and a snapshot whose older is a cursor or null", () => {
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", type: "row.upsert", data: ROW })).toBe(true);
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", type: "snapshot", data: SNAPSHOT })).toBe(true);
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", type: "snapshot", data: { ...SNAPSHOT, older: "cursor-1" } })).toBe(true);
  });

  it("accepts every fixture event, since the bad fixtures break the sequence rules, not the shapes", () => {
    for (const { name, events } of cases) {
      for (const event of events) expect([name, isChatStreamEvent(event)]).toEqual([name, true]);
    }
  });

  it.each([
    ["an unknown type", { seq: 1, conversation_uid: "c", type: "nope", data: {} }],
    ["a negative seq", { seq: -1, conversation_uid: "c", type: "row.delete", data: { id: "a" } }],
    ["a fractional seq", { seq: 1.5, conversation_uid: "c", type: "row.delete", data: { id: "a" } }],
    ["an empty conversation_uid", { seq: 1, conversation_uid: "", type: "row.delete", data: { id: "a" } }],
    ["an ended input without an outcome", { seq: 1, conversation_uid: "c", type: "input.state", data: { id: "a", state: "ended" } }],
    ["a joined input without a turn", { seq: 1, conversation_uid: "c", type: "input.state", data: { id: "a", state: "joined" } }],
    ["an append without a part", { seq: 1, conversation_uid: "c", type: "row.append", data: { id: "a", text: "x" } }],
    ["a snapshot without next_seq", { seq: 1, conversation_uid: "c", type: "snapshot", data: { rows: [], turns: [], inputs: [] } }],
    ["a row of an unknown kind", { seq: 1, conversation_uid: "c", type: "row.upsert", data: { id: "a", seq: 1, kind: "x", role: "user", parts: [] } }],
    ["data that is an array", { seq: 1, conversation_uid: "c", type: "row.delete", data: [] }],
    ["a row with an unknown role", upsert({ ...ROW, role: "alien" })],
    ["a row without created_at", upsert(without(ROW, "created_at"))],
    ["a row without status", upsert(without(ROW, "status"))],
    ["a row without turn_id", upsert(without(ROW, "turn_id"))],
    ["a row with a fractional seq", upsert({ ...ROW, seq: 1.5 })],
    ["a row with a negative seq", upsert({ ...ROW, seq: -1 })],
    ["a row with a numeric turn_id", upsert({ ...ROW, turn_id: 5 })],
    ["a row with a numeric created_at", upsert({ ...ROW, created_at: 0 })],
    ["a snapshot without older", snapshot(without(SNAPSHOT, "older"))],
    ["a snapshot with a numeric older", snapshot({ ...SNAPSHOT, older: 5 })],
    ["a snapshot with a negative next_seq", snapshot({ ...SNAPSHOT, next_seq: -1 })],
    ["a snapshot with a turn that is not an object", snapshot({ ...SNAPSHOT, turns: ["not a turn"] })],
    ["a snapshot with a turn in an unknown state", snapshot({ ...SNAPSHOT, turns: [{ turn_id: "t1", state: "paused", input_ids: [] }] })],
    ["a snapshot with a turn without input_ids", snapshot({ ...SNAPSHOT, turns: [{ turn_id: "t1", state: "running" }] })],
  ])("rejects %s", (_label, event) => {
    expect(isChatStreamEvent(event)).toBe(false);
  });

  it("makes prefixed ids from getRandomValues, so plain-http pages without randomUUID still work", () => {
    vi.stubGlobal("crypto", { getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto) });
    try {
      const a = newInputId("web");
      const b = newInputId("web");
      expect(a).toMatch(/^web-[0-9a-f]{24}$/);
      expect(a).not.toBe(b);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
