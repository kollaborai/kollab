import type { AgentPoolEntry, NetworkAgent } from "@/api";

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

/**
 * The @ targets in a chat with an agent on another computer. Its own daemon
 * reads the @names, in that computer's mesh: the agents there go by name and
 * every other computer's agents keep their agent@device handle.
 */
export function targetsOn(
  device: string,
  remote: NetworkAgent[],
): { agents: AgentPoolEntry[]; remote: NetworkAgent[] } {
  return {
    agents: remote
      .filter((agent) => agent.device === device)
      .map((agent) => ({ name: agent.name, active: true, state: agent.state })),
    remote: remote.filter((agent) => agent.device !== device),
  };
}
