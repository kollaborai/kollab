import assert from "node:assert/strict";
import test from "node:test";
import { parseHubMessageHeader, splitAgentHud } from "../src/hub-notes.ts";

// Made by kollabor/llm/agent_hud.py (merge_agent_hud_with_user_message) from the
// entries plugins/hub/plugin.py queues for incoming messages.
const TURN =
  "<agent_hud>\n[hub:pending_replies]\n+ 1 pending (0 more hidden).\n\n" +
  "[hub:aquamarine->lapis]\n+ [hub channel: aquamarine -> lapis]\n  Yes, I can see your message.\n  \n  Second paragraph.\n  \n  [hub wake instruction]\n  classification: wake (direct). Handle this once if actionable. When no work remains, let the turn end naturally.\n\n" +
  "[hub:sapphire->aquamarine]\n+ [hub channel: sapphire -> aquamarine [thread:abcd1234]]\n  build is green\n  (this message was sent to aquamarine. you do not need to respond unless this is relevant to your current task or you can add value to the discussion.)\n\n" +
  "[hub:ruby->]\n+ [hub channel: ruby -> *]\n  hello everyone\n\n" +
  "[vault:note]\n+ remember the deploy\n</agent_hud>\n\nwhat did they say?";

test("hub messages come out of the HUD; status entries and model guidance stay in", () => {
  const { notes, rest } = splitAgentHud(TURN);
  assert.deepEqual(notes, [
    {
      from: "aquamarine",
      to: "lapis",
      text: "Yes, I can see your message.\n\nSecond paragraph.",
      observed: false,
    },
    { from: "sapphire", to: "aquamarine", threadId: "abcd1234", text: "build is green", observed: true },
    { from: "ruby", to: "", text: "hello everyone", observed: false },
  ]);
  assert.equal(rest, "what did they say?");
});

test("a turn without a HUD is left alone", () => {
  assert.deepEqual(splitAgentHud("plain message"), { notes: [], rest: "plain message" });
});

test("a HUD of status only yields no messages and no text", () => {
  const { notes, rest } = splitAgentHud("<agent_hud>\n[vault:note]\n+ remember\n</agent_hud>");
  assert.deepEqual(notes, []);
  assert.equal(rest, "");
});

test("ACP observation batches replay as peer cards without a duplicate user block", () => {
  const batch = "<agent_hud>\n[status:observations]\n+ 2 older observations omitted.\n\n" +
    "[hub:zircon->nephrite]\n+ [hub channel: zircon -> nephrite [thread:thread-1] [reply-to:request-1]]\n  The roster is available.\n  \n  Second paragraph.\n\n" +
    "[hub:sapphire->]\n+ [hub channel: sapphire -> * [thread:thread-2]]\n  Build passed.\n</agent_hud>";
  assert.deepEqual(splitAgentHud(batch), {
    notes: [
      { from: "zircon", to: "nephrite", threadId: "thread-1", replyTo: "request-1", text: "The roster is available.\n\nSecond paragraph.", observed: false },
      { from: "sapphire", to: "", threadId: "thread-2", text: "Build passed.", observed: false },
    ],
    rest: "",
  });
});

test("parses presentation-only Message from headers", () => {
  assert.deepEqual(parseHubMessageHeader("Message from aquamarine [thread:thread-7]"), {
    sender: "aquamarine",
    threadId: "thread-7",
  });
  assert.deepEqual(parseHubMessageHeader("Message from lapis@devbox [thread:t] [reply-to:req-2]"), {
    sender: "lapis@devbox",
    threadId: "t",
    replyTo: "req-2",
  });
});

test("removes hidden hub thread and reply instructions", () => {
  const result = splitAgentHud("<agent_hud>\n[hub:a->b [thread:t] [reply-to:r]]\n+ Message\n\n[hub thread instruction]\nsecret\n</agent_hud>\n\nhello");
  assert.equal(result.notes[0]?.text, "Message");
  assert.equal(result.notes[0]?.threadId, "t");
  assert.equal(result.notes[0]?.replyTo, "r");
  assert.equal(result.rest, "hello");
});
