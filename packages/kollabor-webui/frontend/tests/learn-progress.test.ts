import assert from "node:assert/strict";
import test from "node:test";
import {
  LEARN_PROGRESS_STORAGE_KEY,
  parseLearnProgress,
  readLearnProgress,
  toggleLearnProgress,
  writeLearnProgress,
} from "../src/learn/progress.ts";

test("progress filters unknown and duplicate IDs and follows catalog order", () => {
  assert.deepEqual(
    parseLearnProgress('["voice","not-a-topic","networks","voice"]'),
    ["networks", "voice"],
  );
  assert.deepEqual(parseLearnProgress("not-json"), []);
  assert.deepEqual(parseLearnProgress('{"voice":true}'), []);
});

test("progress toggles, reads, and persists only known topics", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };

  const next = toggleLearnProgress(["voice"], "networks");
  assert.deepEqual(next, ["networks", "voice"]);
  assert.equal(writeLearnProgress(next, storage), null);
  assert.equal(values.get(LEARN_PROGRESS_STORAGE_KEY), '["networks","voice"]');
  assert.deepEqual(readLearnProgress(storage), {
    completed: next,
    warning: null,
    canRetry: false,
    retryOperation: null,
  });
  assert.deepEqual(toggleLearnProgress(next, "voice"), ["networks"]);
});

test("corrupt or stale browser progress is surfaced without discarding valid topics", () => {
  const corrupt = readLearnProgress({ getItem: () => "not-json", setItem: () => {} });
  assert.deepEqual(corrupt.completed, []);
  assert.match(corrupt.warning ?? "", /unreadable/);
  assert.equal(corrupt.canRetry, false);
  assert.equal(corrupt.retryOperation, null);

  const stale = readLearnProgress({
    getItem: () => '["agents","removed-topic"]',
    setItem: () => {},
  });
  assert.deepEqual(stale.completed, ["agents"]);
  assert.match(stale.warning ?? "", /no longer available/);
  assert.equal(stale.canRetry, false);
  assert.equal(stale.retryOperation, null);
});

test("read and write storage failures are surfaced with the appropriate retry", () => {
  const denied = {
    getItem: () => {
      throw new Error("storage denied");
    },
    setItem: () => {
      throw new Error("storage denied");
    },
  };
  const read = readLearnProgress(denied);
  assert.deepEqual(read.completed, []);
  assert.match(read.warning ?? "", /Enable site storage/);
  assert.equal(read.canRetry, true);
  assert.equal(read.retryOperation, "read");
  assert.match(writeLearnProgress(["agents"], denied) ?? "", /Enable site storage/);
});
