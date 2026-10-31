import { describe, expect, it } from "vitest";
import { hostSnapshotDecision } from "../src/phonelink/phone-transport";

const HOST = "a".repeat(64);
const OLD_SESSION = "1".repeat(32);
const NEW_SESSION = "2".repeat(32);
const NOW = 1_000_000;

// A hostile or corrupt roster row must fail the snapshot closed, never
// throw out of hostSnapshotDecision and kill the phone link.
describe("phone relay roster entry-shape guards", () => {
  // biome-ignore lint/suspicious/noExplicitAny: exercising hostile input.
  const hostileEntries: any[] = [
    null,
    "not-an-entry",
    42,
    [],
    { key: HOST },
    { session: OLD_SESSION },
    { key: HOST, session: 12345 },
  ];

  it.each(hostileEntries)("fails closed on roster entry %j", (entry) => {
    expect(
      hostSnapshotDecision([entry], HOST, OLD_SESSION, 0, NOW),
    ).toEqual({ action: "lost", why: "invalid" });
  });

  it("rejects a roster mixing valid and hostile entries", () => {
    // biome-ignore lint/suspicious/noExplicitAny: exercising hostile input.
    const mixed: any[] = [
      { key: HOST, session: OLD_SESSION },
      null,
    ];
    expect(
      hostSnapshotDecision(mixed, HOST, OLD_SESSION, 0, NOW),
    ).toEqual({ action: "lost", why: "invalid" });
  });

  it("still accepts well-formed rosters", () => {
    expect(
      hostSnapshotDecision(
        [{ key: HOST, session: OLD_SESSION }],
        HOST,
        OLD_SESSION,
        0,
        NOW,
      ),
    ).toEqual({ action: "present", session: OLD_SESSION });
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
});
