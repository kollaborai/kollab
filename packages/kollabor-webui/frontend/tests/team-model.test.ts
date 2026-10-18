import assert from "node:assert/strict";
import test from "node:test";
import { launchLines, teamRows } from "../src/components/agents/team-model.ts";
import type { Persona, TeamMember } from "../src/api.ts";

const seat = (name: string, reports_to: string | null): TeamMember => ({
  persona: { name, display_name: name } as Persona,
  role: "",
  agent: "",
  profile: "",
  prompt: "",
  reports_to,
});

test("a team reads as an outline: the lead, then each seat under whoever it reports to", () => {
  const rows = teamRows({
    lead: "omar",
    members: [seat("omar", null), seat("maya", "omar"), seat("theo", "omar"), seat("priya", "omar"), seat("jonas", "theo"), seat("luz", "omar")],
  });
  assert.deepEqual(
    rows.map((row) => [row.member.persona.name, row.depth, row.lead]),
    [["omar", 0, true], ["maya", 1, false], ["theo", 1, false], ["jonas", 2, false], ["priya", 1, false], ["luz", 1, false]],
  );
});

test("a seat listed before its boss still sits under it, and a lead listed late still comes first", () => {
  const rows = teamRows({ lead: "cto", members: [seat("dev", "lead-dev"), seat("lead-dev", "cto"), seat("cto", null)] });
  assert.deepEqual(rows.map((row) => row.member.persona.name), ["cto", "lead-dev", "dev"]);
  assert.deepEqual(rows.map((row) => row.depth), [0, 1, 2]);
});

test("a reporting loop or a missing boss never spins or drops a seat", () => {
  const loop = teamRows({ lead: "a", members: [seat("a", "b"), seat("b", "a")] });
  assert.deepEqual(loop.map((row) => row.member.persona.name).sort(), ["a", "b"]);
  const orphan = teamRows({ lead: "a", members: [seat("a", null), seat("b", "ghost")] });
  assert.deepEqual(orphan.map((row) => [row.member.persona.name, row.depth]), [["a", 0], ["b", 0]]);
});

test("a launch says what started, what was already running and what failed, and nothing else", () => {
  const nameOf = (name: string) => name.toUpperCase();
  assert.deepEqual(
    launchLines({ started: ["omar", "maya"], skipped: [], failed: [] }, nameOf),
    [{ label: "Started", text: "OMAR, MAYA", failed: false }],
  );
  assert.deepEqual(
    launchLines({ started: [], skipped: [{ persona: "omar", reason: "live" }], failed: [{ persona: "theo", error: "no profile" }] }, nameOf),
    [
      { label: "Already Running", text: "OMAR", failed: false },
      { label: "Failed", text: "THEO (no profile)", failed: true },
    ],
  );
  assert.deepEqual(launchLines({ started: [], skipped: [], failed: [] }, nameOf), []);
});
