const SESSION_REFRESH_EVENTS = new Set([
  "hub_message",
  "history_updated",
  "user_message",
  "turn_complete",
  "turn_cancelled",
  "error",
  "permission_request",
  "permission_parked",
  "permission_granted",
  "permission_denied",
  "permission_cancelled",
]);

/** Whether a daemon event can change session data shown outside the live chat store. */
export function refreshesSessionData(type: string): boolean {
  return SESSION_REFRESH_EVENTS.has(type);
}
