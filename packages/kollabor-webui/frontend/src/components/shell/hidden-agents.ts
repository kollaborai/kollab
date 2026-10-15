export const HIDDEN_AGENTS_KEY = "kollab.sidebar.hidden-agents";

export type HiddenAgent = { key: string; label: string; detail: string };

/** Agent visibility follows its location and identity, including after a restart. */
export function sidebarAgentKey({ name, workspace, device, network, sessionId }: {
  name?: string | null;
  workspace?: string | null;
  device?: string | null;
  network?: string | null;
  sessionId?: string;
}): string {
  const identity = name?.trim().toLowerCase() || sessionId || "";
  return JSON.stringify(device
    ? ["remote", network || "", device, workspace || "", identity]
    : ["local", workspace || "", identity]);
}

/** Ignore malformed storage without losing other valid visibility preferences. */
export function readHiddenAgents(stored: string | null): HiddenAgent[] {
  try {
    const values: unknown = JSON.parse(stored || "[]");
    if (!Array.isArray(values)) return [];
    const entries = new Map<string, HiddenAgent>();
    for (const value of values) {
      if (value && typeof value === "object" && typeof value.key === "string" && value.key
        && typeof value.label === "string" && value.label && typeof value.detail === "string") {
        entries.set(value.key, { key: value.key, label: value.label, detail: value.detail });
      }
    }
    return [...entries.values()];
  } catch {
    return [];
  }
}
