import type { AgentPoolEntry, NetworkAgent, NetworkGroup } from "@/api";

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
 * The #158 multi-network signature narrows by network and workspace first;
 * rows without those fields (every engine before the aggregate ships) ignore
 * the filter and behave exactly as the flat one.
 */
export function targetsOn(
  device: string,
  remote: NetworkAgent[],
  network?: string,
  workspace?: string,
): { agents: AgentPoolEntry[]; remote: NetworkAgent[] } {
  const candidates = remote.filter((agent) => {
    if (network !== undefined && agent.network !== undefined && agent.network !== network) {
      return false;
    }
    if (
      workspace !== undefined &&
      agent.workspace !== undefined &&
      agent.workspace !== workspace
    ) {
      return false;
    }
    return true;
  });
  return {
    agents: candidates
      .filter((agent) => agent.device === device)
      .map((agent) => ({ name: agent.name, active: true, state: agent.state })),
    remote: candidates.filter((agent) => agent.device !== device),
  };
}

/**
 * The sidebar's multi-network tree (#158): NETWORKS > DEVICES > WORKSPACES >
 * AGENTS. An engine that has not shipped per-network blocks (or a snapshot
 * with none) synthesizes one group from the flat remote rows, so the tree
 * renders everywhere; the flat grouping stays available for that shape.
 */
export function groupNetworkTree(
  networks: NetworkGroup[] | undefined,
  flatDevice: string,
  remote: NetworkAgent[],
): NetworkGroup[] {
  if (networks?.length) return networks;
  // Flat synthesis: one unlabeled network, one device group per remote device,
  // every agent under the "" workspace label. `flatDevice` (this computer)
  // marks the network live exactly as the old flat answer did.
  return [
    {
      id: "flat",
      name: "",
      live: undefined,
      devices: groupRemoteByDevice(remote).map((group) => ({
        device: group.device,
        workspaces: [{ label: "", agents: group.agents }],
      })),
    },
  ].map((group) => ({ ...group, live: group.id === "flat" ? undefined : group.live }));
}
