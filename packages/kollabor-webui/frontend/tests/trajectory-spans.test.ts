// Run: node --test tests/trajectory-spans.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { traceSpans } from "../src/components/trajectory/trajectory-spans.ts";

const t = (s: number) => new Date(Date.UTC(2026, 9, 7, 0, 9, 0) + s * 1000).toISOString();
const base = { index: 0, title: "", sourceIndex: 0 };

test("a request lays out model call, then its tools, ending at the assistant write", () => {
  const records = [
    { ...base, id: "u", kind: "user", turn: 1, request: null, summary: "prompt", timestamp: t(0), durationSeconds: 900, opensTurn: true },
    { ...base, id: "a1", kind: "assistant", turn: 1, request: 1, summary: "2 calls", timestamp: t(5.5), durationSeconds: 3.5 },
    { ...base, id: "ls", kind: "tool", turn: 1, request: 1, summary: "ls", timestamp: t(5.5), durationSeconds: 0.1 },
    { ...base, id: "ws", kind: "tool", turn: 1, request: 1, summary: "search", timestamp: t(5.5), durationSeconds: 1.5 },
    { ...base, id: "a2", kind: "assistant", turn: 1, request: 2, summary: "answer", timestamp: t(8.3), durationSeconds: 2.3 },
    { ...base, id: "x", kind: "message", turn: 1, request: 2, summary: "untimed", timestamp: null, durationSeconds: null },
  ] as Parameters<typeof traceSpans>[0];
  const trace = traceSpans(records);
  const row = (id: string) => {
    const span = trace.spans.find((s) => s.id === id)!;
    const sec = (ms: number) => Math.round((ms - Date.parse(t(0))) / 100) / 10;
    return [sec(span.startedAt), sec(span.endedAt!), span.parentSpanId];
  };
  assert.deepEqual(row("u"), [0, 8.3, null]);
  assert.deepEqual(row("a1"), [0.4, 3.9, "u"]);
  assert.deepEqual(row("ls"), [3.9, 4, "a1"]);
  assert.deepEqual(row("ws"), [4, 5.5, "a1"]);
  assert.deepEqual(row("a2"), [6, 8.3, "u"]);
  assert.equal(trace.untimed, 1);
  assert.deepEqual(trace.ranges.get("ws"), { min: Date.parse(t(0)), max: Date.parse(t(8.3)) });
});
