import assert from "node:assert/strict";
import test from "node:test";
import {
  LEARN_PROGRESS_STORAGE_KEY,
  applyToggleLearnProgress,
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
    canPersist: true,
    retryOperation: null,
  });
  assert.deepEqual(toggleLearnProgress(next, "voice"), ["networks"]);
});

test("a guarded state blocks writes and keeps the stored entry untouched", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
  values.set(LEARN_PROGRESS_STORAGE_KEY, "not-json");
  const corrupt = readLearnProgress(storage);
  assert.equal(corrupt.canPersist, false);

  const blocked = applyToggleLearnProgress(corrupt, "networks", storage);
  assert.equal(blocked.completed, corrupt.completed);
  assert.equal(blocked.warning, corrupt.warning);
  assert.equal(blocked.canPersist, false);
  assert.equal(blocked.retryOperation, null);
  assert.equal(values.get(LEARN_PROGRESS_STORAGE_KEY), "not-json");

  const stale = readLearnProgress({
    getItem: () => '["agents","removed-topic"]',
    setItem: () => {},
  });
  const blockedStale = applyToggleLearnProgress(stale, "networks", storage);
  assert.deepEqual(blockedStale.completed, ["agents"]);
  assert.equal(values.get(LEARN_PROGRESS_STORAGE_KEY), "not-json");
});

test("a clean state applies the toggle and persists through applyToggleLearnProgress", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
  const clean = readLearnProgress(storage);
  assert.equal(clean.canPersist, true);

  const after = applyToggleLearnProgress(clean, "networks", storage);
  assert.deepEqual(after.completed, ["networks"]);
  assert.equal(after.warning, null);
  assert.equal(after.canPersist, true);
  assert.equal(values.get(LEARN_PROGRESS_STORAGE_KEY), '["networks"]');

  const afterWriteFailure = applyToggleLearnProgress(after, "voice", {
    getItem: () => '["networks"]',
    setItem: () => {
      throw new Error("storage denied");
    },
  });
  assert.deepEqual(afterWriteFailure.completed, ["networks", "voice"]);
  assert.match(afterWriteFailure.warning ?? "", /Enable site storage/);
  assert.equal(afterWriteFailure.canRetry, true);
  assert.equal(afterWriteFailure.retryOperation, "write");
});

test("corrupt or stale browser progress is surfaced without discarding valid topics", () => {
  const corrupt = readLearnProgress({ getItem: () => "not-json", setItem: () => {} });
  assert.deepEqual(corrupt.completed, []);
  assert.match(corrupt.warning ?? "", /unreadable/);
  assert.equal(corrupt.canRetry, false);
  assert.equal(corrupt.canPersist, false);
  assert.equal(corrupt.retryOperation, null);

  const stale = readLearnProgress({
    getItem: () => '["agents","removed-topic"]',
    setItem: () => {},
  });
  assert.deepEqual(stale.completed, ["agents"]);
  assert.match(stale.warning ?? "", /no longer available/);
  assert.equal(stale.canRetry, false);
  assert.equal(stale.canPersist, false);
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
  assert.equal(read.canPersist, false);
  assert.equal(read.retryOperation, "read");
  assert.match(writeLearnProgress(["agents"], denied) ?? "", /Enable site storage/);
});
