import assert from "node:assert/strict";
import test from "node:test";
import { markCompleted, readCompleted } from "../src/learn/completed.ts";

function memory(initial?: string) {
  let v = initial ?? null;
  return { getItem: () => v, setItem: (_: string, x: string) => { v = x; } };
}

test("marks once and persists", () => {
  const s = memory();
  let done = markCompleted("agents", readCompleted(s), s);
  done = markCompleted("agents", done, s);
  assert.deepEqual(done, ["agents"]);
  assert.deepEqual(readCompleted(s), ["agents"]);
});

test("corrupt or unavailable storage reads empty and still marks", () => {
  assert.deepEqual(readCompleted(memory("{nope")), []);
  assert.deepEqual(readCompleted(memory('{"a":1}')), []);
  assert.deepEqual(readCompleted(null), []);
  const throwing = { getItem: () => { throw new Error("x"); }, setItem: () => { throw new Error("x"); } };
  assert.deepEqual(readCompleted(throwing), []);
  assert.deepEqual(markCompleted("voice", [], throwing), ["voice"]);
});
