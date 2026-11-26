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
const PARTS = [
  { type: "text", text: "hi" },
  { type: "reasoning", text: "thinking" },
  { type: "tool-call", tool_call_id: "call-1", tool_name: "shell", args: { cmd: "ls" } },
  { type: "tool-call", tool_call_id: "call-2", tool_name: "shell", args: null, result: "ok", is_error: false },
  { type: "image", url: "data:image/png;base64,AAAA" },
  { type: "prompt", prompt_id: "p1", data: { options: [] } },
  { type: "hub-note", from: "lapis", to: "", text: "done", observed: true },
  { type: "hub-note", from: "lapis", to: "me", text: "done", observed: false, kind: "reply", thread_id: "th-1", reply_to: "m-1", is_direct: true, presence: "online" },
];
const without = (data: Record<string, unknown>, key: string) => Object.fromEntries(Object.entries(data).filter(([k]) => k !== key));
const upsert = (data: unknown) => ({ seq: 1, conversation_uid: "c", generation: "daemon-1", type: "row.upsert", data });
const snapshot = (data: unknown) => ({ seq: 1, conversation_uid: "c", generation: "daemon-1", type: "snapshot", data });
const withGeneration = (value: Record<string, unknown>) => ({ ...value, generation: "daemon-1" });

const contentPreview = (field: string, rowId = "r1") => ({
  kind: "chat-stream-content-preview",
  session_id: "session-1",
  row_id: rowId,
  part_index: 0,
  field,
  output_bytes: 40_000,
  output_chars: 40_000,
  output_type: field === "args" || field === "part" ? "json" : "text",
  output_revision: "a".repeat(64),
  preview: "bounded preview",
});
const partPreview = (rowId = "r1") => ({
  kind: "chat-stream-part-preview",
  session_id: "session-1",
  row_id: rowId,
  part_index: 0,
  output_bytes: 40_000,
  output_chars: 40_000,
  output_type: "json",
  output_revision: "b".repeat(64),
  preview: "[Large chat part. Expand to load full content.]",
});
const partsPreview = (rowId = "r1") => ({
  kind: "chat-stream-parts-preview",
  session_id: "session-1",
  row_id: rowId,
  part_count: 120,
  output_bytes: 500_000,
  output_chars: 420_000,
  output_type: "json",
  output_revision: "c".repeat(64),
});

describe("chat stream contract", () => {
  it("reads all eight fixtures", () => {
    expect(cases.map((c) => c.name).sort()).toEqual([
      "bad-gap.json", "bad-never-ends.json", "bad-regress.json", "bad-reid.json", "bad-seq.json", "compaction.json",
      "reload-mid-turn.json", "steered-input.json",
    ]);
  });

  it("accepts a row that carries every part type", () => {
    expect(isChatStreamEvent(upsert({ ...ROW, parts: PARTS }))).toBe(true);
  });

  it("accepts row paging and per-part retrieval markers on bounded placeholders", () => {
    const row = {
      ...ROW,
      parts: [{ type: "text", text: "[Large chat row. Expand to load full content.]", chat_stream_part_preview: partPreview() }],
      chat_stream_parts_preview: partsPreview(),
    };
    expect(isChatStreamEvent(upsert(row))).toBe(true);
    expect(isChatStreamEvent(upsert({
      ...ROW,
      parts: [{ type: "text", text: "prefix", chat_stream_content_preview: contentPreview("text") }],
    }))).toBe(true);
    expect(isChatStreamEvent(upsert({
      ...ROW,
      parts: [{ type: "text", text: "[Large part]", chat_stream_part_preview: partPreview() }],
    }))).toBe(true);
  });

  it("rejects preview markers whose row identity disagrees with their row", () => {
    expect(isChatStreamEvent(upsert({
      ...ROW,
      chat_stream_parts_preview: partsPreview("another-row"),
    }))).toBe(false);
    expect(isChatStreamEvent(upsert({
      ...ROW,
      parts: [{ type: "text", text: "x", chat_stream_part_preview: partPreview("another-row") }],
    }))).toBe(false);
  });

  it.each([
    ["null", null],
    ["a string", "text"],
    ["an unknown type", { type: "nope" }],
    ["text without text", { type: "text" }],
    ["text with a number", { type: "text", text: 5 }],
    ["reasoning without text", { type: "reasoning" }],
    ["a tool call without tool_call_id", { type: "tool-call", tool_name: "shell", args: {} }],
    ["a tool call without tool_name", { type: "tool-call", tool_call_id: "c", args: {} }],
    ["a tool call without args", { type: "tool-call", tool_call_id: "c", tool_name: "shell" }],
    ["a tool call with a string is_error", { type: "tool-call", tool_call_id: "c", tool_name: "shell", args: {}, is_error: "no" }],
    ["an image without url", { type: "image" }],
    ["an image with an empty url", { type: "image", url: "" }],
    ["a prompt without prompt_id", { type: "prompt", data: {} }],
    ["a prompt without data", { type: "prompt", prompt_id: "p1" }],
    ["a hub note without observed", { type: "hub-note", from: "lapis", to: "", text: "x" }],
    ["a hub note with a string observed", { type: "hub-note", from: "lapis", to: "", text: "x", observed: "yes" }],
    ["a hub note without to", { type: "hub-note", from: "lapis", text: "x", observed: true }],
    ["a hub note with an unknown presence", { type: "hub-note", from: "lapis", to: "", text: "x", observed: true, presence: "away" }],
    ["a hub note with a numeric is_direct", { type: "hub-note", from: "lapis", to: "", text: "x", observed: true, is_direct: 1 }],
  ])("rejects a row whose part is %s", (_label, part) => {
    expect(isChatStreamEvent(upsert({ ...ROW, parts: [part] }))).toBe(false);
  });

  it.each([
    ["a queued input without turn_id", { id: "a", state: "queued" }],
    ["a queued input with a numeric turn_id", { id: "a", state: "queued", turn_id: 5 }],
    ["an ended input with a numeric turn_id", { id: "a", state: "ended", turn_id: 5, outcome: "completed" }],
    ["a queued input with an outcome", { id: "a", state: "queued", turn_id: null, outcome: "completed" }],
    ["a started input with an unknown outcome", { id: "a", state: "started", turn_id: "t1", outcome: "nope" }],
  ])("rejects %s, as an event and inside a snapshot", (_label, info) => {
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", generation: "daemon-1", type: "input.state", data: info })).toBe(false);
    expect(isChatStreamEvent(snapshot({ ...SNAPSHOT, inputs: [info] }))).toBe(false);
  });

  it.each([[-1], [true], [1.5]])("rejects a row.append whose part index is %s", (part) => {
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", generation: "daemon-1", type: "row.append", data: { id: "r1", part, text: "x" } })).toBe(false);
  });

  it("validates the optional goal origin on turns and snapshots", () => {
    const turn = { turn_id: "t", state: "running", input_ids: [] };
    const valid = { kind: "goal", goal_id: "full-goal", attempt_id: "full-attempt" };
    const event = (origin: unknown) => ({ seq: 1, conversation_uid: "c", generation: "daemon-1", type: "turn.state", data: { ...turn, origin } });
    expect(isChatStreamEvent(event(valid))).toBe(true);
    expect(isChatStreamEvent(snapshot({ ...SNAPSHOT, turns: [{ ...turn, origin: valid }] }))).toBe(true);
    for (const origin of [null, {}, { ...valid, attempt_id: "" }, { ...valid, kind: "user" }]) {
      expect(isChatStreamEvent(event(origin))).toBe(false);
      expect(isChatStreamEvent(snapshot({ ...SNAPSHOT, turns: [{ ...turn, origin }] }))).toBe(false);
    }
  });

  it("accepts a full row and a snapshot whose older is a cursor or null", () => {
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", generation: "daemon-1", type: "row.upsert", data: ROW })).toBe(true);
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", generation: "daemon-1", type: "snapshot", data: SNAPSHOT })).toBe(true);
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", generation: "daemon-1", type: "snapshot", data: { ...SNAPSHOT, older: "cursor-1" } })).toBe(true);
  });

  it("requires a non-empty daemon generation on every stream event", () => {
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", type: "row.delete", data: { id: "r1" } })).toBe(false);
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", generation: "", type: "row.delete", data: { id: "r1" } })).toBe(false);
    expect(isChatStreamEvent({ seq: 1, conversation_uid: "c", generation: "daemon-1", type: "row.delete", data: { id: "r1" } })).toBe(true);
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
    expect(isChatStreamEvent(withGeneration(event as Record<string, unknown>))).toBe(false);
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
