// Run: node --test tests/last-ran.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { lastRanLabel, since } from "../src/last-ran.ts";

const now = Date.UTC(2026, 9, 10, 12);
const ago = (minutes: number) => now / 1000 - minutes * 60;

test("a gem that never ran here has no label", () => {
  assert.equal(lastRanLabel(null, now), "");
  assert.equal(lastRanLabel(undefined, now), "");
});

test("times read in the largest whole unit", () => {
  assert.equal(since(ago(0.2), now), "1m Ago");
  assert.equal(since(ago(42), now), "42m Ago");
  assert.equal(since(ago(5 * 60), now), "5h Ago");
  assert.equal(lastRanLabel(ago(3 * 1440), now), "Ran 3d Ago");
  assert.equal(lastRanLabel(ago(95 * 1440), now), "Ran 3mo Ago");
});
