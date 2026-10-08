// Run: node --test tests/agent-network.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { groupRemoteByDevice, terminalAgents } from "../src/components/shell/agent-network.ts";

test("remote agents group under the computer they run on, both sorted by name", () => {
  const groups = groupRemoteByDevice([
    { name: "lapis", device: "prod-box", handle: "lapis@prod-box" },
    { name: "ruby", device: "devbox", handle: "ruby@devbox" },
    { name: "agate", device: "prod-box", handle: "agate@prod-box" },
  ]);

  assert.deepEqual(groups.map((group) => group.device), ["devbox", "prod-box"]);
  assert.deepEqual(groups[1].agents.map((agent) => agent.name), ["agate", "lapis"]);
});

test("no remote rows means no computer groups", () => {
  assert.deepEqual(groupRemoteByDevice([]), []);
});

test("a live local agent with no session row is a terminal agent", () => {
  const pool = [
    { name: "lapis", active: true },
    { name: "ruby", active: true },
    { name: "agate", active: false },
  ];
  const sessions = [{ session_id: "s1", identity: "lapis" }];

  assert.deepEqual(terminalAgents(pool, sessions).map((agent) => agent.name), ["ruby"]);
});
