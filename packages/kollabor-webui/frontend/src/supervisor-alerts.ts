// Hub idle-supervisor alerts (issue #283). The hub publishes
// {type: "supervisor_alert", message} on a chat's event stream when the
// coordinator stalls, a nudge or handoff goes unanswered, or a task is
// handed off and never acknowledged.

export type StreamEvent = { type?: string; message?: unknown };
export type SupervisorAlert = { key: string; message: string };

/** The visible alerts in `events`, newest first, repeats collapsed. */
export function supervisorAlerts(events: ReadonlyArray<StreamEvent>): SupervisorAlert[] {
  const seen = new Set<string>();
  const out: SupervisorAlert[] = [];
  for (let index = events.length - 1; index >= 0; index--) {
    const event = events[index];
    if (event?.type !== "supervisor_alert") continue;
    const message = typeof event.message === "string" ? event.message.replace(/\s+/g, " ").trim() : "";
    if (!message || seen.has(message)) continue;
    seen.add(message);
    out.push({ key: message, message });
  }
  return out;
}
