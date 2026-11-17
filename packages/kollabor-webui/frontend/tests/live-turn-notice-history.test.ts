import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyLiveEvent,
  emptyLiveTurn,
  liveMessage,
  replacedLiveTurn,
} from "../src/live-turn.ts";

test("history reconciliation keeps an unsaved-row notice without duplicating the reply", () => {
  let turn = applyLiveEvent(emptyLiveTurn(), {
    type: "token",
    text: "The reply was saved.",
    request_id: "r1",
  });
  turn = applyLiveEvent(turn, {
    type: "notice",
    request_id: "r1",
    message: "Conversation history not saved for this message.",
  });
  turn = applyLiveEvent(turn, { type: "turn_complete", request_id: "r1" });

  const reconciled = liveMessage(replacedLiveTurn(turn));
  assert.deepEqual(reconciled?.content, [{
    type: "text",
    text: "ℹ Conversation history not saved for this message.",
  }]);
});
