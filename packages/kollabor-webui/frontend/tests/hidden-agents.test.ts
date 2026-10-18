import assert from "node:assert/strict";
import test from "node:test";
import { dropLegacyHiddenAgents, sidebarAgentKey } from "../src/components/shell/hidden-agents.ts";

test("a running agent is hidden as the process it is, not by its name in a folder", () => {
  const key = sidebarAgentKey({ instance: "a1b2c3d4e5f6" });
  assert.equal(key, sidebarAgentKey({ instance: "a1b2c3d4e5f6", name: "sapphire", workspace: "/project" }));
  // The same name in the same folder, launched later, is another process: it shows.
  assert.notEqual(key, sidebarAgentKey({ instance: "0000000000aa", name: "sapphire", workspace: "/project" }));
});

test("an offline row goes by folder and name, and never matches a running agent", () => {
  const offline = sidebarAgentKey({ name: " Sapphire ", workspace: "/project" });
  assert.equal(offline, sidebarAgentKey({ name: "sapphire", workspace: "/project" }));
  assert.notEqual(offline, sidebarAgentKey({ name: "sapphire", workspace: "/other-project" }));
  assert.notEqual(offline, sidebarAgentKey({ name: "lapis", workspace: "/project" }));
  assert.notEqual(offline, sidebarAgentKey({ instance: "sapphire", workspace: "/project" }));
});

test("remote visibility is scoped to its network, device, workspace and agent", () => {
  const scope = { name: "lapis", network: "home", device: "server", workspace: "project" };
  const key = sidebarAgentKey(scope);
  for (const change of [{ network: "lab" }, { device: "laptop" }, { workspace: "other" }, { name: "ruby" }]) {
    assert.notEqual(key, sidebarAgentKey({ ...scope, ...change }));
  }
  assert.notEqual(key, sidebarAgentKey({ name: "lapis", workspace: "project" }));
});

test("delimiter-containing locations cannot hide one another", () => {
  assert.notEqual(
    sidebarAgentKey({ name: "a:b", workspace: "c" }),
    sidebarAgentKey({ name: "b", workspace: "c:a" }),
  );
  assert.notEqual(sidebarAgentKey({ instance: "a" }), sidebarAgentKey({ name: "a" }));
});

test("the old per-browser list is dropped, never read", () => {
  const stored = new Map([
    ["kollab.sidebar.hidden-agents", JSON.stringify([{ key: '["local","/project","sapphire"]', label: "Sapphire", detail: "/project" }])],
    ["kollab.sidebar.folded", "{}"],
  ]);
  const removed: string[] = [];
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: { removeItem: (key: string) => { removed.push(key); stored.delete(key); } },
  });
  try {
    dropLegacyHiddenAgents();
    assert.deepEqual(removed, ["kollab.sidebar.hidden-agents"]);
    assert.deepEqual([...stored.keys()], ["kollab.sidebar.folded"]); // other preferences stay
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: { removeItem: () => { throw new Error("blocked"); } },
    });
    assert.doesNotThrow(dropLegacyHiddenAgents); // blocked storage holds nothing to drop
  } finally {
    Reflect.deleteProperty(globalThis, "localStorage");
  }
});
