// Run: node --test tests/hub-notes.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { splitAgentHud } from "../src/hub-notes.ts";

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
    { from: "sapphire", to: "aquamarine", text: "build is green", observed: true },
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
