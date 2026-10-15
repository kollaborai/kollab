import assert from "node:assert/strict";
import test from "node:test";
import { readHiddenAgents, sidebarAgentKey } from "../src/components/shell/hidden-agents.ts";

test("a hidden local agent stays hidden across session restarts and its offline row", () => {
  const running = sidebarAgentKey({ name: " Sapphire ", workspace: "/project", sessionId: "old-session" });
  assert.equal(running, sidebarAgentKey({ name: "sapphire", workspace: "/project", sessionId: "new-session" }));
  assert.equal(running, sidebarAgentKey({ name: "sapphire", workspace: "/project" }));
  assert.notEqual(running, sidebarAgentKey({ name: "sapphire", workspace: "/other-project" }));
  assert.notEqual(running, sidebarAgentKey({ name: "lapis", workspace: "/project" }));
});

test("remote visibility is scoped to its network, device, workspace and agent", () => {
  const scope = { name: "lapis", network: "home", device: "server", workspace: "project" };
  const key = sidebarAgentKey(scope);
  for (const change of [{ network: "lab" }, { device: "laptop" }, { workspace: "other" }, { name: "ruby" }]) {
    assert.notEqual(key, sidebarAgentKey({ ...scope, ...change }));
  }
  assert.notEqual(key, sidebarAgentKey({ name: "lapis", workspace: "project" }));
});

test("unnamed sessions and delimiter-containing locations cannot hide one another", () => {
  assert.notEqual(sidebarAgentKey({ sessionId: "first" }), sidebarAgentKey({ sessionId: "second" }));
  assert.notEqual(
    sidebarAgentKey({ name: "a:b", workspace: "c" }),
    sidebarAgentKey({ name: "b", workspace: "c:a" }),
  );
});

test("hidden preferences round-trip, deduplicate and tolerate invalid browser data", () => {
  const entry = { key: sidebarAgentKey({ name: "sapphire", workspace: "/project" }), label: "Sapphire", detail: "/project" };
  assert.deepEqual(readHiddenAgents(JSON.stringify([entry])), [entry]);
  assert.deepEqual(readHiddenAgents(JSON.stringify([entry, null, 12, {}, { ...entry, label: 3 }, entry])), [entry]);
  for (const value of [null, "bad json", "null", "{}", "42"]) assert.deepEqual(readHiddenAgents(value), []);
});
