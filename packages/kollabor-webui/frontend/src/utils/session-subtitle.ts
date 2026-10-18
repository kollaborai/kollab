import type { Session } from "../api";

/** The folder an agent runs in: the last segment of its workspace path. */
export function sessionFolder(session: Pick<Session, "workspace">): string {
  return session.workspace?.split("/").filter(Boolean).pop() ?? "";
}

/** What every live agent's row reads, however it started: what it runs and where. */
export function sessionSubtitle(
  session: Pick<Session, "model" | "profile" | "workspace">,
  offHub = false,
): string {
  return (
    [offHub ? "Off Hub" : "", session.model || session.profile, sessionFolder(session)]
      .filter(Boolean)
      .join(" · ") || "Running"
  );
}
