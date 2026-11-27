import { expect, test } from "vitest";

import { isNewChatView } from "../src/components/assistant-ui/thread";
import type { ChatRow } from "../src/chat-stream";

// The new-chat layout (centered composer and welcome) shows only for an empty chat
// whose stream is open. The rows come from the chat store (#1451).

const row: ChatRow = {
  id: "r1",
  seq: 1,
  kind: "reply",
  role: "assistant",
  turn_id: "t1",
  status: "complete",
  parts: [{ type: "text", text: "hi" }],
  created_at: "2026-11-25T10:00:00Z",
};

test("an empty chat whose stream is open is a new chat", () => {
  expect(isNewChatView([], "live")).toBe(true);
});

test("a chat with rows is not a new chat", () => {
  expect(isNewChatView([row], "live")).toBe(false);
});

test("an empty chat still connecting is not a new chat: the snapshot may hold rows", () => {
  expect(isNewChatView([], "connecting")).toBe(false);
});

test("an empty chat whose stream failed is not offered as a new chat", () => {
  expect(isNewChatView([], "failed")).toBe(false);
});
