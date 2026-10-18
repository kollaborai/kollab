import assert from "node:assert/strict";
import test from "node:test";
import { buildLaunchChoices, keepPick } from "../src/components/agents/launch-choices.ts";
import type { AgentPoolEntry, Persona, RostersResponse } from "../src/api.ts";

const persona = (name: string, roster: string, extra: Partial<Persona> = {}): Persona => ({
  name,
  roster,
  display_name: name[0].toUpperCase() + name.slice(1),
  avatar: { kind: "initials" },
  color: [1, 2, 3],
  agent: "research",
  role: "",
  personality: "",
  skills: [],
  profile: "",
  prompt: "",
  caste: "",
  role_aliases: [],
  ...extra,
});

const library: RostersResponse = {
  workspace: "/work",
  default: "gems",
  problems: [],
  rosters: [
    {
      name: "gems",
      display_name: "Gems",
      description: "",
      avatar: { kind: "gem" },
      source: "bundled",
      path: "/b/gems.json",
      personas: [persona("lapis", "gems", { avatar: { kind: "gem" }, agent: "coder" })],
    },
    {
      name: "marketing-crew",
      display_name: "Marketing Crew",
      description: "",
      avatar: { kind: "initials" },
      source: "bundled",
      path: "/b/marketing-crew.json",
      personas: [
        persona("maya", "marketing-crew", { role: "Brand Strategist", display_name: "Maya Lindqvist" }),
        persona("lapis", "marketing-crew", { role: "Clash" }),
      ],
    },
  ],
};

const row = (name: string, extra: Partial<AgentPoolEntry> = {}): AgentPoolEntry => ({
  name,
  roster: name === "koordinator" ? "" : "gems",
  display_name: name,
  avatar: { kind: "gem" },
  agent: name === "koordinator" ? "koordinator" : "coder",
  agent_type: name === "koordinator" ? "koordinator" : "coder",
  available: true,
  active: false,
  pool: true,
  ...extra,
});

test("the folder's own list comes first, then the personas of every other roster as roster/name", () => {
  const choices = buildLaunchChoices([row("koordinator"), row("lapis")], library);
  assert.deepEqual(
    choices.map((choice) => choice.ref),
    ["koordinator", "lapis", "marketing-crew/maya", "marketing-crew/lapis"],
  );
  const maya = choices[2];
  assert.equal(maya.rosterLabel, "Marketing Crew");
  assert.equal(maya.role, "Brand Strategist");
  assert.equal(maya.bundle, "research");
  assert.equal(maya.display_name, "Maya Lindqvist");
  assert.deepEqual(maya.avatar, { kind: "initials" });
  assert.equal(choices[0].rosterLabel, "");
  assert.equal(choices[1].rosterLabel, "Gems");
});

test("a name running in the folder takes that name from every roster", () => {
  const choices = buildLaunchChoices([row("koordinator"), row("lapis", { active: true, available: false })], library);
  const lapisOfMarketing = choices.find((choice) => choice.ref === "marketing-crew/lapis");
  assert.equal(lapisOfMarketing?.active, true);
  assert.equal(lapisOfMarketing?.available, false);
  assert.equal(choices.find((choice) => choice.ref === "marketing-crew/maya")?.available, true);
});

test("a folder that has not answered offers nobody, even when the library is in", () => {
  assert.deepEqual(buildLaunchChoices([], library), []);
});

test("a live agent the roster does not name is never offered, and a missing library offers the list as it is", () => {
  const live = row("lapis-2", { pool: false, active: true, available: false });
  assert.deepEqual(
    buildLaunchChoices([row("koordinator"), live], null).map((choice) => choice.ref),
    ["koordinator"],
  );
});

test("the pick stays while it can start, else falls to the first agent that can", () => {
  const choices = buildLaunchChoices([row("koordinator", { active: true, available: false }), row("lapis")], library);
  assert.equal(keepPick("marketing-crew/maya", choices), "marketing-crew/maya");
  assert.equal(keepPick("koordinator", choices), "lapis");
  assert.equal(keepPick("", choices), "lapis");
  assert.equal(keepPick("lapis", []), "");
});
