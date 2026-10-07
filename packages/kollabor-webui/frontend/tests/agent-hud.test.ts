// Run: node --test tests/agent-hud.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { stripAgentHud } from "../src/api.ts";

const hud = "<agent_hud>\n[vault]\nrebirth: 3 sessions\n</agent_hud>";

test("a turn keeps only what the user typed after the agent status", () => {
  assert.equal(stripAgentHud(`${hud}\n\nfix the build`), "fix the build");
});

test("a status-only turn strips to nothing", () => {
  assert.equal(stripAgentHud(`${hud}\n\n`), "");
});

test("text without a leading status block is untouched", () => {
  assert.equal(stripAgentHud("hello <agent_hud>x</agent_hud>"), "hello <agent_hud>x</agent_hud>");
});
