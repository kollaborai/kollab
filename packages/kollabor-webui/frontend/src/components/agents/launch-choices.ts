/**
 * What New Session can start in a folder: the folder's own list (GET /agents:
 * the koordinator, then its roster's personas, with who is already running
 * there) and the personas of every other roster (GET /rosters), which launch as
 * `roster/name`. Pure, so tests/launch-choices.test.ts runs it under node.
 */

import type { AgentPoolEntry, RostersResponse } from "../../api.ts";

export type LaunchChoice = AgentPoolEntry & {
  /** The identity a launch sends: "lapis", or "marketing-crew/maya" for a roster the folder does not draw from. */
  ref: string;
  /** Its job title; empty for the gems. */
  role: string;
  /** The roster's name for people; empty for the koordinator. */
  rosterLabel: string;
  /** The agent bundle it runs; empty is the default one. */
  bundle: string;
};

/** "marketing-crew" is "Marketing Crew": a roster file that names no display name. */
const titled = (slug: string) =>
  slug
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");

export function buildLaunchChoices(
  pool: readonly AgentPoolEntry[],
  library: RostersResponse | null | undefined,
): LaunchChoice[] {
  // Who is running in the folder decides who is free: with no answer from it nobody is offered.
  if (!pool.length) return [];
  const rosters = library?.rosters ?? [];
  const byName = new Map(rosters.map((roster) => [roster.name, roster]));
  const label = (roster: string) => (roster ? byName.get(roster)?.display_name || titled(roster) : "");
  const roleOf = (roster: string, name: string) => byName.get(roster)?.personas.find((p) => p.name === name)?.role ?? "";
  // A name is free or taken per folder, whichever roster it came from.
  const taken = new Set(pool.filter((row) => row.active).map((row) => row.name));

  const choices: LaunchChoice[] = [];
  for (const row of pool) {
    if (row.pool === false) continue;
    const roster = row.roster ?? "";
    choices.push({
      ...row,
      ref: row.name,
      role: roleOf(roster, row.name),
      rosterLabel: label(roster),
      bundle: row.agent || row.agent_type || "",
    });
  }
  const own = library?.default ?? "";
  for (const roster of rosters) {
    if (roster.name === own) continue;
    for (const persona of roster.personas) {
      const active = taken.has(persona.name);
      choices.push({
        name: persona.name,
        identity: persona.name,
        roster: roster.name,
        display_name: persona.display_name,
        avatar: persona.avatar,
        color: persona.color,
        caste: persona.caste,
        role_aliases: persona.role_aliases,
        personality: persona.personality,
        agent: persona.agent,
        agent_type: persona.agent,
        available: !active,
        active,
        pool: true,
        state: "available",
        last_ran: null,
        ref: `${roster.name}/${persona.name}`,
        role: persona.role,
        rosterLabel: roster.display_name || titled(roster.name),
        bundle: persona.agent || "",
      });
    }
  }
  return choices;
}

/** The pick New Session keeps: the current one while it can still start, else the first agent that can. */
export function keepPick(current: string, choices: readonly LaunchChoice[]): string {
  return choices.some((choice) => choice.ref === current && choice.available)
    ? current
    : choices.find((choice) => choice.available)?.ref || "";
}
