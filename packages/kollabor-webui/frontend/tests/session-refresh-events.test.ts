import { describe, expect, it } from "vitest";
import { refreshesSessionData } from "../src/session-refresh-events";

/** Events that can change session data outside the ordered chat store. */
const refreshed = (types: string[]) => types.filter(refreshesSessionData);

describe("session event refreshes", () => {
  it("refreshes after an external message without waiting for turn completion", () => {
    expect(refreshed(["token", "tool_start", "hub_message", "tool_result", "history_updated", "token"]))
      .toEqual(["hub_message", "history_updated"]);
  });

  it("refreshes session state after turn and error events", () => {
    expect(refreshed(["turn_complete", "turn_cancelled", "error", "permission_request"]))
      .toEqual(["turn_complete", "turn_cancelled", "error", "permission_request"]);
  });

  it("refreshes permission outcomes and parking", () => {
    expect(refreshed([
      "permission_parked",
      "permission_granted",
      "permission_denied",
      "permission_cancelled",
    ])).toEqual([
      "permission_parked",
      "permission_granted",
      "permission_denied",
      "permission_cancelled",
    ]);
  });

  it("refreshes after accepted user messages but not token or tool traffic", () => {
    expect(refreshed(["user_message", "token", "thinking", "tool_start", "tool_result", "usage_update"]))
      .toEqual(["user_message"]);
  });
});
