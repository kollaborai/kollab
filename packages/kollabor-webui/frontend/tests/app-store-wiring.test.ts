import { readFileSync } from "node:fs";
import { expect, test } from "vitest";

// App.tsx is not unit-tested, so this reads its source. The chat renders from the
// chat store (#1451, lane 4): the old reconciliation, history paging, remount and
// runtime pieces are gone from App, and the queue panel reads store state.

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

const retired = [
  'from "./runtime"',
  'from "./history-reconciliation"',
  'from "./history-status"',
  'from "./history-cache"',
  "createHistoryReconciliation",
  "useAuiState",
  "useEngineRuntimeState",
  "buildInitialState",
  "runtimeEpoch",
  "historyStatus",
  "historyStartIndex",
  "onLoadOlder",
  "onRetryHistory",
  "loadOlderHistory",
  "retryHistory",
  "resetThread",
  "refreshActiveState",
  "initialState",
  "loadState",
  "liveEventsRef",
  "recoverPendingTurn",
  "hasPendingPermission",
  "RESTART_COMMAND",
  "onStale",
];

test.each(retired)("App.tsx no longer has %s", (name) => {
  expect(app).not.toContain(name);
});

test("the chat is gated by the active session alone", () => {
  expect(app).toContain(") : activeSession ? (");
});

test("the agent's todo list is read from the chat store's rows", () => {
  expect(app).toMatch(/latestTodoBlock\((store\.)?rows\)/);
});

test("the queue panel is mounted from the chat store", () => {
  expect(app).toContain("queuePanel={<WebQueuePanelMount canSendNow={!Boolean(session.harness)} />}");
  expect(app).not.toContain("submitBusyWebMessage");
  expect(app).not.toContain("onQueueBusyMessage");
});
