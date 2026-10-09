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

/** A chat's name where it is open (header, toolbar, Session settings). An agent
 * on another computer has no session name of its own, so it goes by its
 * identity, as its sidebar row does. */
export function sessionHeading(session: Pick<Session, "name" | "session_id" | "identity" | "device">): string {
  if (session.device) {
    const identity = session.identity || session.name || "";
    if (identity) return titleCase(identity);
  }
  return formatSessionName(session.name, session.session_id);
}
