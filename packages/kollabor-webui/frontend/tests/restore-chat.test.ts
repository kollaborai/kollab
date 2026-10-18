// Run: node --test tests/restore-chat.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { chatToRestore } from "../src/restore-chat.ts";

const chats = [{ session_id: "a" }, { session_id: "b" }, { session_id: "c", external: true }];

test("the page opens this engine's newest chat, never an agent started elsewhere", () => {
  assert.equal(chatToRestore(chats, false)?.session_id, "b");
});

test("once the person has opened a chat, or is opening one, there is nothing to restore", () => {
  assert.equal(chatToRestore(chats, true), undefined);
});

test("no chats of this engine's own: nothing to restore", () => {
  assert.equal(chatToRestore([], false), undefined);
  assert.equal(chatToRestore([{ session_id: "c", external: true }], false), undefined);
});
