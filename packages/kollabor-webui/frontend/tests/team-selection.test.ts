import assert from "node:assert/strict";
import { test } from "vitest";
import type { Persona, Team, TeamMember } from "../src/api.ts";
import { effectiveTeam, teamRows } from "../src/components/agents/team-model.ts";
import {
  readTeamSelectionPreferences,
  teamSelectionKey,
  TEAM_SELECTION_STORAGE_KEY,
  writeTeamSelectionPreferences,
  type TeamSelectionPreferences,
} from "../src/components/agents/team-preferences.ts";

const seat = (name: string, reports_to: string | null): TeamMember => ({
  persona: { name, display_name: name } as Persona,
  role: name,
  agent: "default",
  profile: "",
  prompt: "",
  reports_to,
});

const team: Team = {
  name: "studio",
  description: "",
  roster: "studio",
  lead: "lead",
  source: "bundled",
  path: "",
  members: [seat("lead", null), seat("manager", "lead"), seat("writer", "manager"), seat("analyst", "lead")],
};

test("selection preview re-parents retained members without mutating the source team", () => {
  const before = team.members.map((member) => member.reports_to);
  const selected = effectiveTeam(team, ["lead", "writer", "analyst"], "lead");
  assert.ok(selected);
  assert.deepEqual(selected.members.map((member) => member.persona.name), ["lead", "writer", "analyst"]);
  assert.equal(selected.members[1]?.reports_to, "lead");
  assert.deepEqual(teamRows(selected).map((row) => [row.member.persona.name, row.depth]), [
    ["lead", 0],
    ["writer", 1],
    ["analyst", 1],
  ]);
  assert.deepEqual(team.members.map((member) => member.reports_to), before);
});

test("a replacement lead must be included in the selection", () => {
  assert.equal(effectiveTeam(team, ["manager", "writer"], "lead"), null);
  assert.equal(effectiveTeam(team, [], "lead"), null);
  assert.equal(effectiveTeam(team, ["unknown", "lead"], "lead")?.members.length, 1);
});

test("preferences are isolated by folder and team and tolerate corrupt storage", () => {
  assert.notEqual(teamSelectionKey("/work/a", "studio"), teamSelectionKey("/work/b", "studio"));
  assert.notEqual(teamSelectionKey("/work/a", "studio"), teamSelectionKey("/work/a", "research"));
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
  const key = teamSelectionKey("/work/a", "studio");
  const preferences: TeamSelectionPreferences = { [key]: { members: ["lead", "writer"], lead: "writer" } };
  assert.equal(writeTeamSelectionPreferences(preferences, storage), true);
  assert.deepEqual(readTeamSelectionPreferences(storage), preferences);
  values.set(TEAM_SELECTION_STORAGE_KEY, "not-json");
  assert.deepEqual(readTeamSelectionPreferences(storage), {});
});

test("storage denial does not break the launch flow", () => {
  const denied = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  assert.deepEqual(readTeamSelectionPreferences(denied), {});
  assert.equal(writeTeamSelectionPreferences({}, denied), false);
});
