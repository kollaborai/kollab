// Run: node --test tests/agent-status.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { agentStatus } from "../src/components/panels/panel-model.ts";

test("an agent's state reads in Title Case", () => {
  assert.equal(agentStatus("idle"), "Idle");
  assert.equal(agentStatus("working"), "Working");
  assert.equal(agentStatus("waiting_for_input"), "Waiting For Input");
});

test("an agent with no state is Online", () => {
  assert.equal(agentStatus(), "Online");
  assert.equal(agentStatus(""), "Online");
  assert.equal(agentStatus(null), "Online");
});
