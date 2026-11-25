import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { isChatStreamEvent, newInputId } from "../src/chat-stream";

// The same fixtures the Python contract tests read (#1451).
const dir = new URL("./fixtures/chat-stream/", import.meta.url);
const cases = readdirSync(dir)
  .filter((name) => name.endsWith(".json"))
  .map((name) => ({ name, events: JSON.parse(readFileSync(new URL(name, dir), "utf8")).events as unknown[] }));

describe("chat stream contract", () => {
  it("reads all six fixtures", () => {
    expect(cases.map((c) => c.name).sort()).toEqual([
      "bad-never-ends.json", "bad-reid.json", "bad-seq.json", "compaction.json", "reload-mid-turn.json", "steered-input.json",
    ]);
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
