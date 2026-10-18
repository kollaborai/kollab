/** A team as the Agents view shows it, and what its launch did, in words. Pure: tests/team-model.test.ts runs it under node. */

import type { Team, TeamLaunchResult, TeamMember } from "../../api.ts";

export type TeamRow = { member: TeamMember; depth: number; lead: boolean };

/** A team's seats as an outline: the lead, then each seat under whoever it reports to, in listing order. */
export function teamRows(team: Pick<Team, "lead" | "members">): TeamRow[] {
  const names = new Set(team.members.map((member) => member.persona.name));
  const under = new Map<string, TeamMember[]>();
  const roots: TeamMember[] = [];
  for (const member of team.members) {
    const boss = member.reports_to;
    if (boss && boss !== member.persona.name && names.has(boss)) under.set(boss, [...(under.get(boss) ?? []), member]);
    else roots.push(member);
  }
  roots.sort((a, b) => Number(b.persona.name === team.lead) - Number(a.persona.name === team.lead));
  const rows: TeamRow[] = [];
  const seen = new Set<string>();
  const walk = (member: TeamMember, depth: number) => {
    // The engine drops a team with a reporting loop; this keeps a hand-built one from spinning.
    if (seen.has(member.persona.name)) return;
    seen.add(member.persona.name);
    rows.push({ member, depth, lead: member.persona.name === team.lead });
    for (const child of under.get(member.persona.name) ?? []) walk(child, depth + 1);
  };
  for (const root of roots) walk(root, 0);
  // A loop has no root; its seats are still seats.
  for (const member of team.members) walk(member, 0);
  return rows;
}

/** What Launch Team did, one plain line per outcome that happened. */
export function launchLines(
  result: Pick<TeamLaunchResult, "started" | "skipped" | "failed">,
  nameOf: (persona: string) => string,
): { label: string; text: string; failed: boolean }[] {
  const lines: { label: string; text: string; failed: boolean }[] = [];
  if (result.started.length) lines.push({ label: "Started", text: result.started.map(nameOf).join(", "), failed: false });
  if (result.skipped.length) {
    lines.push({ label: "Already Running", text: result.skipped.map((item) => nameOf(item.persona)).join(", "), failed: false });
  }
  if (result.failed.length) {
    lines.push({
      label: "Failed",
      text: result.failed.map((item) => `${nameOf(item.persona)} (${item.error})`).join(", "),
      failed: true,
    });
  }
  return lines;
}
