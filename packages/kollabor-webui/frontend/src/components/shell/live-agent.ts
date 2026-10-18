import type { AgentPoolEntry, LiveAgent, Session } from "@/api";

type Asker = Pick<Session, "identity" | "instance" | "workspace">;

const folderOf = (path?: string | null) => (path ?? "").replace(/\/+$/, "");

/**
 * The running hub agent a sidebar row or a chat is. Gem names repeat across
 * folders (a koordinator per project), so a name alone never says which one. A
 * session that knows its process (`instance`, its daemon's presence agent id) is
 * exactly that agent, or none when presence does not have it (still starting, or
 * off the hub). One with no instance yet (just created or patched) goes by name
 * in its own folder, where a name is unique; with no folder, by a name only one
 * agent runs.
 */
export function liveAgentOf(who: Asker, live: readonly LiveAgent[]): LiveAgent | undefined {
  if (who.instance) return live.find((agent) => agent.agent_id === who.instance);
  const named = live.filter((agent) => agent.name === who.identity);
  const folder = folderOf(who.workspace);
  if (!folder) return named.length === 1 ? named[0] : undefined;
  return named.find((agent) => folderOf(agent.project) === folder);
}

/**
 * `pool` as one chat's hub sees it. The pool holds one agent per name (the
 * engine's folder first), so another folder's koordinator would answer for this
 * chat's. Here each name is the agent running it in the chat's folder, and the
 * chat's own agent is exactly its own.
 */
export function poolSeenBy(
  pool: readonly AgentPoolEntry[],
  live: readonly LiveAgent[],
  chat: Asker,
): AgentPoolEntry[] {
  const own = liveAgentOf(chat, live);
  return pool.map((entry) => {
    const agent =
      entry.name === chat.identity ? own : liveAgentOf({ identity: entry.name, workspace: chat.workspace }, live);
    return agent
      ? {
          ...entry,
          available: false,
          active: true,
          state: agent.state,
          current_task: agent.current_task,
          agent_id: agent.agent_id,
          solo: agent.solo,
          project: agent.project,
        }
      : { ...entry, available: true, active: false, state: "", current_task: "", agent_id: "", solo: false, project: "" };
  });
}
