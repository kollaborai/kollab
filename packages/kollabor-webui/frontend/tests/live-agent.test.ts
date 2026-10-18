import assert from "node:assert/strict";
import test from "node:test";
import { liveAgentOf, poolSeenBy } from "../src/components/shell/live-agent.ts";

const agent = (name: string, agent_id: string, project: string, state = "idle", current_task = "") => ({
  name,
  agent_id,
  project,
  state,
  current_task,
  solo: false,
});

// A koordinator in each of two folders; the one in /ws2 is busy.
const live = [agent("koordinator", "k1", "/ws"), agent("koordinator", "k2", "/ws2", "working", "slow job")];

test("a row that knows its process is that agent, not the first of its name", () => {
  assert.equal(liveAgentOf({ identity: "koordinator", instance: "k2", workspace: "/ws2" }, live)?.state, "working");
  assert.equal(liveAgentOf({ identity: "koordinator", instance: "k1", workspace: "/ws" }, live)?.state, "idle");
});

test("a process presence does not have is not live, whatever else shares its name", () => {
  assert.equal(liveAgentOf({ identity: "koordinator", instance: "starting-1", workspace: "/ws" }, live), undefined);
});

test("with no instance yet a row goes by its name in its own folder", () => {
  assert.equal(liveAgentOf({ identity: "koordinator", workspace: "/ws2/" }, live)?.agent_id, "k2");
  assert.equal(liveAgentOf({ identity: "koordinator", workspace: "/elsewhere" }, live), undefined);
  assert.equal(liveAgentOf({ identity: "lapis", workspace: "/ws" }, live), undefined);
});

test("with no instance and no folder a name only one agent runs is that agent; a shared name is nobody's", () => {
  assert.equal(liveAgentOf({ identity: "koordinator" }, live), undefined);
  assert.equal(liveAgentOf({ identity: "koordinator" }, [live[1]])?.agent_id, "k2");
  assert.equal(liveAgentOf({ identity: "", workspace: "" }, live), undefined);
});

test("a chat sees its own folder's agents: the pool's one koordinator never speaks for another folder's", () => {
  // The engine's pool names one koordinator, whichever folder it picked.
  const pool = [
    { name: "koordinator", display_name: "Koordinator", active: true, state: "idle", agent_id: "k1", project: "/ws" },
    { name: "lapis", display_name: "Lapis", active: true, state: "working", agent_id: "l1", project: "/ws" },
    { name: "ruby", display_name: "Ruby", available: true },
  ];
  const world = [...live, agent("lapis", "l1", "/ws", "working")];

  const seen = poolSeenBy(pool, world, { identity: "koordinator", instance: "k2", workspace: "/ws2" });
  const by = Object.fromEntries(seen.map((entry) => [entry.name, entry]));

  assert.equal(by.koordinator.agent_id, "k2");
  assert.equal(by.koordinator.state, "working");
  assert.equal(by.koordinator.current_task, "slow job");
  assert.equal(by.koordinator.project, "/ws2");
  // lapis runs in /ws only: not in this chat's hub.
  assert.equal(by.lapis.active, false);
  assert.equal(by.lapis.agent_id, "");
  // Persona fields ride along untouched.
  assert.equal(by.lapis.display_name, "Lapis");
  assert.equal(by.ruby.active, false);
  assert.equal(by.ruby.available, true);
});

test("the other chat sees the other koordinator", () => {
  const pool = [{ name: "koordinator", active: true, state: "working", agent_id: "k2", project: "/ws2" }];

  const [seen] = poolSeenBy(pool, live, { identity: "koordinator", instance: "k1", workspace: "/ws" });

  assert.equal(seen.agent_id, "k1");
  assert.equal(seen.state, "idle");
  assert.equal(seen.current_task, "");
});
