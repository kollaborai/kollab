import type { AgentPoolEntry, NetworkAgent, Session } from "@/api";

/** Remote agents grouped by the computer they run on, computers and agents sorted by name. */
export function groupRemoteByDevice(
  remote: NetworkAgent[],
): { device: string; agents: NetworkAgent[] }[] {
  const byDevice = new Map<string, NetworkAgent[]>();
  for (const agent of remote) {
    byDevice.set(agent.device, [...(byDevice.get(agent.device) ?? []), agent]);
  }
  return [...byDevice]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([device, agents]) => ({
      device,
      agents: [...agents].sort((a, b) => a.name.localeCompare(b.name)),
    }));
}

/** Live agents on this computer with no session row here: a terminal, or a session in another folder. */
export function terminalAgents(agents: AgentPoolEntry[], sessions: Session[]): AgentPoolEntry[] {
  const sessionIdentities = new Set(sessions.map((session) => session.identity));
  return agents.filter((agent) => agent.active && !sessionIdentities.has(agent.name));
}
