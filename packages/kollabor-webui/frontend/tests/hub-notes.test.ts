import assert from "node:assert/strict";
import test from "node:test";
import { parseHubMessageHeader, resolveAgentSender, splitAgentHud, splitObservationReplies } from "../src/hub-notes.ts";

test("saved Reply from batches become separate peer messages, including repeated senders", () => {
  const batch = "Reply from zircon [thread:thread-z]\nzircon here.\n\n" +
    "Reply from aquamarine [thread:thread-a]\nAcknowledged.\n\n" +
    "Reply from zircon [thread:thread-z]\nA second reply.";
  const parsed = splitObservationReplies(batch);
  assert.deepEqual(parsed.notes.map(({ from, text, threadId }) => ({ from, text, threadId })), [
    { from: "zircon", text: "zircon here.", threadId: "thread-z" },
    { from: "aquamarine", text: "Acknowledged.", threadId: "thread-a" },
    { from: "zircon", text: "A second reply.", threadId: "thread-z" },
  ]);
  assert.equal(parsed.rest, "");
  assert.equal(splitAgentHud(batch).rest, batch);
  assert.deepEqual(splitObservationReplies("Please review:\n" + batch), { notes: [], rest: "Please review:\n" + batch });
});

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

test("marks online system notices for compact chip rendering", () => {
  const result = splitAgentHud("Message from nephrite [thread:online-1]\nagent 'nephrite' just came online\n  cwd: /workspace\n  model: test");
  assert.equal(result.notes[0]?.isOnlineNotice, true);
  assert.equal(result.notes[0]?.from, "nephrite");
});

test("direct Message from wrappers become styled hub cards without user bubbles", () => {
  const turn = "Message from 9424e4bf03fb [thread:d606396ec8e14cb48a9b6ab68c5dbe6b]\nImplement the Web UI bug";
  const result = splitAgentHud(turn);
  assert.equal(result.notes.length, 1);
  assert.equal(result.notes[0]?.from, "9424e4bf03fb");
  assert.equal(result.notes[0]?.threadId, "d606396ec8e14cb48a9b6ab68c5dbe6b");
  assert.equal(result.notes[0]?.text, "Implement the Web UI bug");
  assert.equal(result.notes[0]?.rawText, turn);
  assert.equal(result.notes[0]?.isDirect, true);
  assert.equal(result.notes[0]?.isOnlineNotice, false);
  assert.equal(result.rest, ""); // rest is empty, so no user bubble is generated
});

test("resolves device IDs like d01769a1ed99 to agent@device from roster data", () => {
  const roster = [
    { name: "nephrite", device: "devbox", agent_id: "d01769a1ed99" },
    { name: "zircon", device: "laptop", id: "a1b2c3d4e5f6" },
  ];
  assert.equal(resolveAgentSender("d01769a1ed99", roster), "nephrite@devbox");
  assert.equal(resolveAgentSender("a1b2c3d4e5f6", roster), "zircon@laptop");
  assert.equal(resolveAgentSender("lapis@devbox", roster), "lapis@devbox");
  assert.equal(resolveAgentSender("lapis", [{ name: "lapis" }]), "lapis");

  const rosterObj = {
    remoteAgents: [{ name: "nephrite", device: "macbook", device_id: "d01769a1ed99" }],
  };
  assert.equal(resolveAgentSender("d01769a1ed99", rosterObj), "nephrite@macbook");

  // In direct message parsing with roster
  const msg = "Message from d01769a1ed99 [thread:t1]\nhello there";
  const result = splitAgentHud(msg, roster);
  assert.equal(result.notes[0]?.from, "nephrite@devbox");
});

test("hides thread IDs and literal hub_msg reply instructions while preserving raw prompt text", () => {
  const rawPrompt =
    "Message from d01769a1ed99 [thread:51d8f9d32c154a9c9a2b5923eaacdcd2]\n" +
    "agent 'nephrite' just came online in project /workspace.\n" +
    "cwd: /workspace\n" +
    "model: unknown\n" +
    "effort: default\n" +
    "provider: acp\n" +
    "profile: antigravity\n" +
    "current hub roster:\n" +
    "  - lapis (coordinator): idle\n" +
    "if you need help with anything, let them know.\n" +
    'respond back using: <hub_msg to="nephrite">your message</hub_msg>';

  const result = splitAgentHud(rawPrompt, [
    { name: "nephrite", device: "devbox", agent_id: "d01769a1ed99" },
  ]);

  assert.equal(result.notes.length, 1);
  const note = result.notes[0];
  assert.equal(note?.from, "nephrite@devbox");
  assert.equal(note?.threadId, "51d8f9d32c154a9c9a2b5923eaacdcd2");
  assert.ok(!note?.text.includes("[thread:"));
  assert.ok(!note?.text.includes("respond back using:"));
  assert.ok(!note?.text.includes("<hub_msg"));
  assert.ok(note?.text.includes("agent 'nephrite' just came online in project /workspace."));
  assert.ok(note?.text.includes("current hub roster:"));
  assert.equal(note?.rawText, rawPrompt);
  assert.equal(note?.isOnlineNotice, true);
  assert.equal(result.rest, "");
});
