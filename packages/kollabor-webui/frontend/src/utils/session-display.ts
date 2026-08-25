/** Return the compact label shown to users without changing the session key. */
export function formatSessionName(
  name?: string | null,
  sessionId?: string | null,
): string {
  const value = (name || sessionId || "").replace(/^sess_/, "");
  return value.replace(/^\d{10}-/, "") || "session";
}
