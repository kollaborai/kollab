import { describe, expect, it } from "vitest";
import { createSessionRefreshGate } from "../src/utils/session-refresh.ts";
import { reuseIfEqual } from "../src/utils/state-snapshot.ts";

describe("sessions list refresh against a local Apply", () => {
  it("an older list carrying xhigh cannot overwrite an Apply of max", () => {
    const gate = createSessionRefreshGate();
    let effort = "xhigh";
    const olderList = gate.startList();
    gate.recordApply();
    effort = "max";
    const staleResponse = { effort: "xhigh" };
    if (olderList()) effort = staleResponse.effort;
    expect(effort).toBe("max");
  });

  it("a list requested after the Apply is applied", () => {
    const gate = createSessionRefreshGate();
    gate.recordApply();
    const fresh = gate.startList();
    expect(fresh()).toBe(true);
  });

  it("every list in flight at the Apply is dropped, not only the newest", () => {
    const gate = createSessionRefreshGate();
    const first = gate.startList();
    const second = gate.startList();
    gate.recordApply();
    expect(first()).toBe(false);
    expect(second()).toBe(false);
  });
});

describe("poll snapshot publication", () => {
  it("retains the current reference for an identical poll response", () => {
    const current = [{ session_id: "s1", title: "Chat" }];
    const polled = [{ session_id: "s1", title: "Chat" }];

    expect(reuseIfEqual(current, polled)).toBe(current);
  });

  it("publishes changed sessions and profiles", () => {
    const sessions = [{ session_id: "s1", title: "Chat" }];
    const profiles = [{ name: "default", model: "alpha" }];
    const updatedSessions = [{ session_id: "s1", title: "Renamed" }];
    const updatedProfiles = [{ name: "default", model: "beta" }];

    expect(reuseIfEqual(sessions, updatedSessions)).toBe(updatedSessions);
    expect(reuseIfEqual(profiles, updatedProfiles)).toBe(updatedProfiles);
  });
});
