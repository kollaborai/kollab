/**
 * When the open chat reloads its history from the daemon's event stream.
 *
 * Mid-turn included: an external message (another agent, another window, the
 * agent's terminal) must show without waiting for turn_complete. A page that
 * runs the turn itself is safe: the runtime only appends history past what the
 * thread shows, by message id, and skips it while its own run streams.
 */
export function refreshesOnEvent(type: string): boolean {
  return ["hub_message", "history_updated", "turn_complete", "turn_cancelled", "error", "permission_request"].includes(type);
}
