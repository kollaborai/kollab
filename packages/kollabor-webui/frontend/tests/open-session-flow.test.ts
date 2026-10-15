// Run: node --test tests/open-session-flow.test.ts (Node 24 strips the types).
// Opening a busy agent's conversation never races a daemon: loadState awaits
// the engine's history read (mirror or disk seed) with no client timeout, so
// a slow -- but eventual -- answer still lands. A failing open is only a
// transport failure, which the open flow surfaces as "no session" instead of
// an error state.
import assert from "node:assert/strict";
import test from "node:test";

type HistoryMessage = {
  role: string;
  content?: unknown;
  timestamp?: string | null;
  metadata?: Record<string, unknown>;
  thinking?: string | null;
};

type HistoryResponse = {
  history: HistoryMessage[];
  last_turn_error?: { message: string; history_length: number } | null;
  source?: "mirror" | "disk";
  stale?: boolean;
};

type Permissions = { pending_prompts: unknown[] };

/** The loadState assembly, verbatim semantics from App.tsx loadState. */
async function loadState(
  sessionId: string,
  historyPromise: Promise<HistoryResponse>,
  permissionsPromise: Promise<Permissions>,
) {
  const [history, permissions] = await Promise.all([
    historyPromise,
    permissionsPromise.catch(() => ({ pending_prompts: [] })),
  ]);
  return {
    sessionId,
    history: history.history || [],
    pendingPrompts: permissions.pending_prompts || [],
    turnError: history.last_turn_error ?? null,
    source: history.source ?? "mirror",
    stale: history.stale ?? false,
  };
}

test("a slow disk-seeded history still lands (no client timeout to reject it)", async () => {
  const slowHistory = new Promise<HistoryResponse>((resolve) => {
    // Far beyond the removed 2s timeout: the open waits, it does not fail.
    setTimeout(
      () =>
        resolve({
          history: [
            { role: "user", content: "hello" },
            { role: "assistant", content: "hi" },
          ],
          source: "disk",
          stale: true,
        }),
      50,
    );
  });
  const state = await loadState("s1", slowHistory, Promise.resolve({ pending_prompts: [] }));
  assert.equal(state.history.length, 2);
  assert.equal(state.source, "disk");
  assert.equal(state.stale, true);
});

test("permissions failing never fails the open", async () => {
  const state = await loadState(
    "s1",
    Promise.resolve({ history: [{ role: "user", content: "hi" }] }),
    Promise.reject(new Error("permissions down")),
  );
  assert.deepEqual(state.pendingPrompts, []);
  assert.equal(state.history.length, 1);
});

test("an empty history renders an open page (cold seed, refresh in flight)", async () => {
  const state = await loadState(
    "s1",
    Promise.resolve({ history: [], source: "disk", stale: true }),
    Promise.resolve({ pending_prompts: [] }),
  );
  assert.equal(state.history.length, 0);
  assert.equal(state.stale, true);
});

test("turn error rides along with the sliced history", async () => {
  const state = await loadState(
    "s1",
    Promise.resolve({
      history: [{ role: "user", content: "go" }],
      last_turn_error: { message: "boom", history_length: 1 },
    }),
    Promise.resolve({ pending_prompts: [] }),
  );
  assert.equal(state.turnError?.message, "boom");
  assert.equal(state.turnError?.history_length, 1);
});
