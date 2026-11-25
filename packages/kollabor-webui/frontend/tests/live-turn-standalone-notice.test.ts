import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyLiveEvent,
  emptyLiveTurn,
  liveMessage,
} from "../src/live-turn.ts";

test("a standalone save notice is settled and stays with a later reply", () => {
  const notice = applyLiveEvent(emptyLiveTurn(), {
    type: "notice",
    message: "Conversation history not saved for this message.",
  });

  assert.equal(liveMessage(notice)?.status?.type, "complete");

  const reply = applyLiveEvent(notice, {
    type: "token",
    request_id: "r1",
    text: "The reply is visible.",
  });
  assert.equal(liveMessage(reply)?.status?.type, "running");
  assert.deepEqual(liveMessage(reply)?.content, [{
    type: "text",
    text: "ℹ Conversation history not saved for this message.\n\nThe reply is visible.",
  }]);
});
