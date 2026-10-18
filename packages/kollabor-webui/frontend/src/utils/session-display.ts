import type { Session } from "@/api";
import { titleCase } from "@/components/panels/panel-model";

/** Return the compact label shown to users without changing the session key. */
export function formatSessionName(
  name?: string | null,
  sessionId?: string | null,
): string {
  const value = (name || sessionId || "").replace(/^sess_/, "");
  return value.replace(/^\d{10}-/, "") || "session";
}

/** An agent's name for people: its persona's display name ("Maya Lindqvist"), else its identity in Title Case. */
export function agentName(identity: string, displayName?: string | null): string {
  return displayName?.trim() || titleCase(identity);
}

/**
 * Agent identity leads everywhere; generated session names are a fallback.
 * `displayName` is the identity's persona name, for a caller that knows the pool.
 */
export function sessionHeading(
  session: Pick<Session, "name" | "session_id" | "identity" | "device" | "agent">,
  displayName?: string | null,
): string {
  const identity = session.identity?.trim() || session.agent?.trim();
  if (identity) return agentName(identity, displayName);
  if (session.device && session.name) return titleCase(session.name);
  return formatSessionName(session.name, session.session_id);
}
