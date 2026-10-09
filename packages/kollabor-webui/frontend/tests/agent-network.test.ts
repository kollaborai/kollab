// Run: node --test tests/agent-network.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { groupRemoteByDevice, targetsOn } from "../src/components/shell/agent-network.ts";

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

test("in a chat with an agent on another computer, @names are that computer's", () => {
  const targets = targetsOn("prod-box", [
    { name: "lapis", device: "prod-box", handle: "lapis@prod-box", state: "working" },
    { name: "ruby", device: "devbox", handle: "ruby@devbox" },
  ]);

  assert.deepEqual(targets.agents, [{ name: "lapis", active: true, state: "working" }]);
  assert.deepEqual(targets.remote.map((agent) => agent.handle), ["ruby@devbox"]);
});

test("a failure reads as a sentence: no HTTP status, capitalized", async () => {
  const { ApiError, errorText } = await import("../src/api.ts");
  assert.equal(
    errorText(new ApiError("503: could not open this agent: box has not let mac open its agents", 503, null)),
    "Could not open this agent: box has not let mac open its agents",
  );
  assert.equal(errorText("engine unreachable"), "Engine unreachable");
});
