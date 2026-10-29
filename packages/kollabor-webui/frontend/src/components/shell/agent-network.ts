import type { AgentNetwork, AgentPoolEntry, EngineApi, NetworkAgent, NetworkGroup } from "@/api";

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
 * The network an agent on another computer is on: whether it answered, whether
 * a local agent exists in its folder, and that network's rows.
 */
export function networkOf(
  network: AgentNetwork,
  handle: string,
): { live: boolean; folder?: string; agents: NetworkAgent[]; localAgentRunning?: boolean | null } {
  for (const net of network.networks ?? []) {
    const agents = net.devices.flatMap((device) => device.workspaces.flatMap((ws) => ws.agents));
    if (agents.some((agent) => agent.handle === handle)) {
      return { live: net.live !== false, folder: net.folder, agents, localAgentRunning: net.local_agent_running };
    }
  }
  return { live: network.live !== false, folder: network.folder, agents: network.remote, localAgentRunning: network.local_agent_running };
}

/** Check the local carrier immediately before starting one for a remote chat. */
export async function prepareRemoteOpen(
  api: Pick<EngineApi, "listSessions" | "createSession">,
  network: AgentNetwork,
  handle: string,
  profile: string,
): Promise<"open" | "wait"> {
  if (networkOf(network, handle).live) return "open";
  const latest = await api.listSessions();
  const current = networkOf(latest.network ?? { device: "", remote: [] }, handle);
  if (current.live || current.localAgentRunning !== false) return "open";

  const carrier = { profile, approval_mode: "trust_all", workspace: current.folder || undefined };
  try {
    await api.createSession({ ...carrier, identity: "koordinator", agent: "koordinator" });
  } catch (reason) {
    const body = reason && typeof reason === "object" && "body" in reason ? reason.body : null;
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    if (!(reason instanceof Error) || !("status" in reason) || reason.status !== 409
      || typeof detail !== "string" || !detail.startsWith("hub identity 'koordinator' is already in use in ")) {
      throw reason;
    }
    // A different window may have started the carrier during this request.
    const retry = await api.listSessions();
    const refreshed = networkOf(retry.network ?? { device: "", remote: [] }, handle);
    if (refreshed.live || refreshed.localAgentRunning == null) return "open";
    if (!refreshed.localAgentRunning) await api.createSession(carrier);
  }
  return "wait";
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
