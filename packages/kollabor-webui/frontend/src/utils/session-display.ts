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

/** Agent identity leads everywhere; generated session names are a fallback. */
export function sessionHeading(session: Pick<Session, "name" | "session_id" | "identity" | "device" | "agent">): string {
  const identity = session.identity?.trim() || session.agent?.trim();
  if (identity) return titleCase(identity);
  if (session.device && session.name) return titleCase(session.name);
  return formatSessionName(session.name, session.session_id);
}
