import type { NetworkAgent } from "@/api";

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
