// Run: node --test tests/live-turn-branches.test.ts (Node 24 strips the types).
// A passive window shows a turn it did not start as an ephemeral live reply and
// drops it when history brings the stored one. assistant-ui keeps every message
// it was given, so the dropped one stays behind as a sibling of the stored reply
// and the thread shows a "2 / 2" branch picker; and a live reply that is joined
// to the stored reply before it leaves that message when a user row lands
// between them. These feed the same conversion and the same thread core the web
// UI uses (runtime.tsx: converter).
import assert from "node:assert/strict";
import { test } from "node:test";

import { unstable_createMessageConverter as createMessageConverter } from "@assistant-ui/react";
import { ExternalStoreThreadRuntimeCore } from "@assistant-ui/core/internal";

import {
  apartFromLive,
  applyLiveEvent,
  emptyLiveTurn,
  LIVE_MESSAGE_ID,
  liveMessage,
} from "../src/live-turn.ts";

type Like = Parameters<ReturnType<typeof createMessageConverter<any>>["toThreadMessages"]>[0][number];

const converter = createMessageConverter<Like>((message) => message);
const convert = (messages: Like[]) => converter.toThreadMessages(apartFromLive(messages), false, {});

/** What EngineRuntimeProvider does with the messages it holds, minus React. */
function thread() {
  const core = new ExternalStoreThreadRuntimeCore({ getModelContext: () => ({}) } as never, {
    messages: [],
    onNew: async () => {},
  } as never);
  return {
    show(messages: Like[]) {
      core.__internal_setAdapter({ messages: convert(messages), isRunning: false, onNew: async () => {} } as never);
    },
    branches: (id: string) => core.repository.getBranches(id).length,
    has(id: string) {
      try {
        core.repository.getMessage(id);
        return true;
      } catch {
        return false;
      }
    },
    parent: (id: string) => core.repository.getMessage(id).parentId,
    shown: () => core.repository.getMessages().map((message) => message.id),
  };
}

const user = (id: string, text = "go"): Like => ({ id, role: "user", content: [{ type: "text", text }] });
const reply = (id: string, text = "hi"): Like => ({
  id,
  role: "assistant",
  content: [{ type: "text", text }],
  status: { type: "complete", reason: "stop" },
});
const streaming = (text: string, request = "r1"): Like => {
  const message = liveMessage(applyLiveEvent(emptyLiveTurn(), { type: "token", text, request_id: request }));
  assert.ok(message);
  return message as Like;
};
const live = (request = "r1") => `${LIVE_MESSAGE_ID}:${request}`;

test("the live message is marked optimistic, the flag assistant-ui evicts by", () => {
  assert.deepEqual(streaming("hello").metadata, { isOptimistic: true });
});

test("a live reply that history replaces leaves no sibling under the user row", () => {
  // A passive window sees the reply first: the turn's user row arrives with the stored reply.
  const t = thread();
  t.show([streaming("chunk 1 of 10.")]);
  t.show([user("history-0"), reply("history-1")]);
  assert.equal(t.branches("history-0"), 1);
  assert.equal(t.has(live()), false);
  assert.deepEqual(t.shown(), ["history-0", "history-1"]);
});

test("a live reply that history replaces leaves no sibling under the stored reply", () => {
  // The user row was already on screen when the reply streamed in behind it.
  const t = thread();
  t.show([user("history-0")]);
  t.show([user("history-0"), streaming("chunk 1 of 10.")]);
  t.show([user("history-0"), reply("history-1")]);
  assert.equal(t.branches("history-1"), 1);
  assert.equal(t.has(live()), false);
});

test("a queued turn: history lands around the live reply, then replaces it", () => {
  const t = thread();
  const first = [user("history-0"), reply("history-1")];
  t.show(first);
  t.show([...first, streaming("chunk 1 of 10.")]);
  // A peer note and its user row land before the live reply.
  t.show([...first, user("history-2"), user("history-3", "PEER"), streaming("chunk 1 of 10. chunk 2 of 10.")]);
  assert.ok(t.has(live()));
  // The stored replies replace it.
  t.show([...first, user("history-2"), user("history-3", "PEER"), reply("history-4"), reply("history-5")]);
  for (const id of ["history-0", "history-1", "history-2", "history-3", "history-4"]) {
    assert.equal(t.branches(id), 1, `${id} has no sibling`);
  }
  assert.equal(t.has(live()), false);
});

test("two live replies in a row leave nothing behind", () => {
  const t = thread();
  t.show([streaming("one")]);
  t.show([user("history-0"), reply("history-1", "one")]);
  t.show([user("history-0"), reply("history-1", "one"), user("history-2"), streaming("two", "r2")]);
  t.show([user("history-0"), reply("history-1", "one"), user("history-2"), reply("history-3", "two")]);
  for (const id of ["history-0", "history-1", "history-2", "history-3"]) {
    assert.equal(t.branches(id), 1, `${id} has no sibling`);
  }
  assert.equal(t.has(live()), false);
  assert.equal(t.has(live("r2")), false);
});

test("a queued turn's live reply is a message of its own, never the last one's", () => {
  // Its stored reply lands with the next turn already streaming: the old live
  // message goes in that same update, so its text never shows twice.
  const t = thread();
  t.show([user("history-0"), streaming("one")]);
  t.show([user("history-0"), reply("history-1", "one"), user("history-2", "PEER"), streaming("two", "r2")]);
  assert.equal(t.has(live()), false);
  assert.deepEqual(t.shown(), ["history-0", "history-1", "history-2", live("r2")]);
  for (const id of ["history-0", "history-1", "history-2"]) {
    assert.equal(t.branches(id), 1, `${id} has no sibling`);
  }
  // Within a turn the id stays, so the reply keeps its element as it grows.
  assert.equal(streaming("two, more", "r2").id, live("r2"));
});

test("a live reply behind a stored reply is a message of its own", () => {
  const rows = [user("history-0"), reply("history-1"), streaming("chunk 1")];
  // assistant-ui joins adjacent assistant messages into one, and the live reply would be a part of it.
  assert.deepEqual(converter.toThreadMessages(rows, false, {}).map((m) => m.id), ["history-0", "history-1"]);
  assert.deepEqual(convert(rows).map((m) => m.id), ["history-0", "history-1", live()]);
});

test("history landing rows before the live reply moves it, it is not a new message", () => {
  const t = thread();
  const first = [user("history-0"), reply("history-1")];
  t.show([...first, streaming("chunk 1")]);
  assert.equal(t.parent(live()), "history-1");
  t.show([...first, user("history-2"), streaming("chunk 1 chunk 2")]);
  assert.deepEqual(t.shown(), ["history-0", "history-1", "history-2", live()]);
  assert.equal(t.parent(live()), "history-2");
  // The stored reply before it keeps its own text: nothing of the live reply stayed behind in it.
  const [, stored] = convert([...first, user("history-2"), streaming("chunk 1 chunk 2")]);
  assert.deepEqual(stored.content.map((part) => (part as { text?: string }).text), ["hi"]);
});

test("a stored reply joins the assistant row after it once the live reply is gone", () => {
  const first = [user("history-0"), reply("history-1")];
  assert.deepEqual(convert([...first, streaming("chunk 1")]).map((m) => m.id), ["history-0", "history-1", live()]);
  assert.deepEqual(convert([...first, reply("history-2", "next")]).map((m) => m.id), ["history-0", "history-1"]);
});

test("the copy that keeps the live reply apart is the same object every time", () => {
  const stored = reply("history-1");
  const live = streaming("chunk 1");
  assert.equal(apartFromLive([stored, live])[0], apartFromLive([stored, live])[0]);
  assert.equal(apartFromLive([stored])[0], stored);
});
