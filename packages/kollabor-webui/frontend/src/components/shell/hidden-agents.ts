import type { HiddenAgent } from "@/api";

export type { HiddenAgent };

/**
 * What hides a sidebar row. A running agent is its process (`instance`, the
 * daemon's hub presence agent id), so an agent launched later under the same
 * name in the same folder is another row and shows. An offline row (nothing
 * runs) and a row of another computer's agent have no process to name: they go
 * by where they are and what they are called.
 */
export function sidebarAgentKey({ instance, name, workspace, device, network }: {
  instance?: string | null;
  name?: string | null;
  workspace?: string | null;
  device?: string | null;
  network?: string | null;
}): string {
  if (instance) return JSON.stringify(["agent", instance]);
  const identity = name?.trim().toLowerCase() || "";
  return JSON.stringify(device
    ? ["remote", network || "", device, workspace || "", identity]
    : ["offline", workspace || "", identity]);
}

const LEGACY_KEY = "kollab.sidebar.hidden-agents";

/**
 * This browser used to keep its own list under LEGACY_KEY, keyed by folder and
 * name, which could not tell two agents of one name apart (importing it would
 * hide every sapphire in a folder). The engine keeps the list now, so the old
 * one is dropped and never read.
 */
export function dropLegacyHiddenAgents(): void {
  try { localStorage.removeItem(LEGACY_KEY); }
  catch { /* Blocked storage holds nothing to drop. */ }
}
