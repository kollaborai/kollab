/**
 * When the open chat reloads its history from the daemon's event stream.
 *
 * Mid-turn included: an external message (another agent, another window, the
 * agent's terminal) must show without waiting for turn_complete. A page that
 * runs the turn itself is safe: the runtime only appends history past what the
 * thread shows, by message id, and skips it while its own run streams.
 */
export function refreshesOnEvent(type: string): boolean {
  return ["hub_message", "history_updated", "user_message", "turn_complete", "turn_cancelled", "error", "permission_request"].includes(type);
}

/** Request ids whose events can add visible assistant output to the live turn. */
export function tracksStreamedRequest(event: Record<string, unknown>): boolean {
  const requestId = typeof event.request_id === "string" && event.request_id.length > 0;
  if (!requestId) return false;
  if (event.type === "token") return typeof event.text === "string" && event.text.length > 0;
  if (event.type !== "acp_update" || !event.update || typeof event.update !== "object") return false;
  const update = event.update as Record<string, unknown>;
  if (update.sessionUpdate === "agent_message_chunk") {
    const content = update.content as Record<string, unknown> | undefined;
    return typeof content?.text === "string" && content.text.length > 0;
  }
  return (update.sessionUpdate === "tool_call" || update.sessionUpdate === "tool_call_update")
    && Boolean(update.toolCallId ?? update.tool_call_id);
}
