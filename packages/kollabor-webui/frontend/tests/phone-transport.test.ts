import { describe, expect, it } from "vitest";
import {
  hostSnapshotDecision,
  HOST_MISSING_GRACE_MS,
} from "../src/phonelink/phone-transport";

const HOST = "a".repeat(64);
const OLD_SESSION = "1".repeat(32);
const NEW_SESSION = "2".repeat(32);
const NOW = 1_000_000;

// The engine legitimately restarts (updates, crashes, lease pruning) and
// re-registers with a fresh relay session. The tolerance rules below keep a
// connected phone from tearing its relay socket down on every such blip —
// the 2026-10-30 reconnect loop did exactly that.
describe("phone transport host snapshot tolerance", () => {
  it("adopts a host session change instead of losing the link", () => {
    expect(
      hostSnapshotDecision(
        [{ key: HOST, session: NEW_SESSION }],
        HOST,
        OLD_SESSION,
        0,
        NOW,
      ),
    ).toEqual({ action: "changed", session: NEW_SESSION });
  });

  it("keeps the link while the host is missing inside the grace window", () => {
    const since = NOW - 5_000;
    expect(hostSnapshotDecision([], HOST, OLD_SESSION, since, NOW)).toEqual({
      action: "absent",
      missingSinceMs: since,
    });
  });

  it("starts the grace clock on the first absent snapshot", () => {
    expect(hostSnapshotDecision([], HOST, OLD_SESSION, 0, NOW)).toEqual({
      action: "absent",
      missingSinceMs: NOW,
    });
  });

  it("loses the link only after the grace window lapses", () => {
    expect(
      hostSnapshotDecision(
        [],
        HOST,
        OLD_SESSION,
        NOW - HOST_MISSING_GRACE_MS,
        NOW,
      ),
    ).toEqual({ action: "lost", why: "missing" });
  });

  it("still requires the host before the first settle", () => {
    expect(hostSnapshotDecision([], HOST, "", 0, NOW)).toEqual({
      action: "lost",
      why: "missing",
    });
  });

  it("rejects invalid rosters and invalid host sessions", () => {
    // biome-ignore lint/suspicious/noExplicitAny: exercising non-array input.
    const notAnArray: any = { peers: [] };
    expect(hostSnapshotDecision(notAnArray, HOST, OLD_SESSION, 0, NOW)).toEqual({
      action: "lost",
      why: "invalid",
    });
    expect(
      hostSnapshotDecision(
        new Array(257).fill({ key: HOST, session: OLD_SESSION }),
        HOST,
        OLD_SESSION,
        0,
        NOW,
      ),
    ).toEqual({ action: "lost", why: "invalid" });
    expect(
      hostSnapshotDecision(
        [{ key: HOST, session: "not-a-session" }],
        HOST,
        OLD_SESSION,
        0,
        NOW,
      ),
    ).toEqual({ action: "lost", why: "invalid" });
  });

  it("reports a present, unchanged host session", () => {
    expect(
      hostSnapshotDecision(
        [{ key: HOST, session: OLD_SESSION }],
        HOST,
        OLD_SESSION,
        NOW,
        NOW,
      ),
    ).toEqual({ action: "present", session: OLD_SESSION });
  });
});
