import { describe, expect, it } from "vitest";
import {
  MAX_LINES,
  buildRelease,
  compareVersions,
  decideWhatsNew,
  dialogLines,
  loadSeenRecord,
  saveSeenRecord,
  sublineVersion,
} from "../src/whats-new/whats-new-model";
import type { ChangeSection, WhatsNewResult } from "../src/learn/changelog";

const group = (category: string, items: string[]) => ({ category, items });

const release = (version: string, sections: ChangeSection[] = []): WhatsNewResult => ({
  status: "ready",
  sections:
    sections.length > 0
      ? sections
      : [
          {
            version,
            groups: [group("Added", ["First feature."]), group("Fixed", ["One fix."])],
          },
        ],
});

describe("compareVersions", () => {
  it("orders releases oldest first", () => {
    expect(compareVersions("0.22.0", "0.22.1")).toBeLessThan(0);
    expect(compareVersions("0.22.1", "0.22.0")).toBeGreaterThan(0);
    expect(compareVersions("0.22.0", "0.22.0")).toBe(0);
  });

  it("compares numerically, not lexicographically", () => {
    expect(compareVersions("0.9.0", "0.10.0")).toBeLessThan(0);
    expect(compareVersions("0.10.0", "0.9.0")).toBeGreaterThan(0);
  });

  it("never crashes on a non-version string", () => {
    expect(compareVersions("Unreleased", "0.22.0")).not.toBeNaN();
    expect(compareVersions("0.22.0", "Unreleased")).not.toBeNaN();
  });
});

describe("buildRelease", () => {
  it("collects Added, Changed and the Fixed count from the newest section", () => {
    const built = buildRelease(
      release("0.22.1", [
        {
          version: "0.22.1",
          groups: [
            group("Added", ["New panel.", "New card."]),
            group("Changed", ["Faster startup."]),
            group("Fixed", ["Fix one.", "Fix two.", "Fix three."]),
          ],
        },
        { version: "0.22.0", groups: [group("Added", ["Older."])] },
      ]),
    );
    expect(built).not.toBeNull();
    expect(built?.version).toBe("0.22.1");
    expect(built?.previous).toBe("0.22.0");
    expect(built?.added).toEqual(["New panel.", "New card."]);
    expect(built?.changed).toEqual(["Faster startup."]);
    expect(built?.fixedCount).toBe(3);
  });

  it("skips an Unreleased section: a dev build announces nothing", () => {
    expect(buildRelease(release("Unreleased"))).toBeNull();
  });

  it("has no previous when the second section is Unreleased or missing", () => {
    const built = buildRelease(release("0.22.0", [
      { version: "0.22.0", groups: [group("Added", ["One."])] },
      { version: "Unreleased", groups: [] },
    ]));
    expect(built?.previous).toBeNull();
    expect(buildRelease(release("0.22.0", [
      { version: "0.22.0", groups: [group("Added", ["One."])] },
    ]))?.previous).toBeNull();
  });

  it("is null for loading, error and empty results", () => {
    expect(buildRelease({ status: "loading" })).toBeNull();
    expect(buildRelease({ status: "error", message: "x" })).toBeNull();
    expect(buildRelease({ status: "empty" })).toBeNull();
  });
});

describe("seen record storage shape", () => {
  it("round-trips", () => {
    const raw = saveSeenRecord({ seenVersion: "0.22.0" });
    expect(loadSeenRecord(raw)).toEqual({ seenVersion: "0.22.0" });
  });

  it("falls back to never-seen on malformed or foreign data", () => {
    expect(loadSeenRecord(null)).toBeNull();
    expect(loadSeenRecord("")).toBeNull();
    expect(loadSeenRecord("not json")).toBeNull();
    expect(loadSeenRecord(JSON.stringify({ seen: 3 }))).toBeNull();
    expect(loadSeenRecord(JSON.stringify({ seenVersion: 22 }))).toBeNull();
    expect(loadSeenRecord(JSON.stringify({ seenVersion: "zebra" }))).toBeNull();
  });

  it("rejects numeric-looking versions outside the supported safe range", () => {
    const raw = JSON.stringify({
      seenVersion: "999999999999999999999999999999999999999999999999999999999999999999",
    });
    expect(loadSeenRecord(raw)).toBeNull();
    expect(
      decideWhatsNew({ result: release("0.22.1"), seenRaw: raw, firstRun: false }).show,
    ).toBe(true);
  });
});

describe("decideWhatsNew", () => {
  const seen = (version: string) => saveSeenRecord({ seenVersion: version });

  it("shows a release newer than the last seen version", () => {
    const decision = decideWhatsNew({ result: release("0.22.1"), seenRaw: seen("0.22.0"), firstRun: false });
    expect(decision.show).toBe(true);
    expect(decision.release?.version).toBe("0.22.1");
  });

  it("skips the same version and anything older than seen", () => {
    expect(decideWhatsNew({ result: release("0.22.0"), seenRaw: seen("0.22.0"), firstRun: false }).show).toBe(false);
    expect(decideWhatsNew({ result: release("0.21.9"), seenRaw: seen("0.22.0"), firstRun: false }).show).toBe(false);
  });

  it("skips the first run: the guided setup owns that moment", () => {
    expect(decideWhatsNew({ result: release("0.22.1"), seenRaw: null, firstRun: true }).show).toBe(false);
  });

  it("shows once for browsers that ran before this feature existed", () => {
    expect(decideWhatsNew({ result: release("0.22.1"), seenRaw: null, firstRun: false }).show).toBe(true);
  });

  it("skips a release with no Added or Changed entries, even when newer", () => {
    const fixedOnly: WhatsNewResult = {
      status: "ready",
      sections: [{ version: "0.22.2", groups: [group("Fixed", ["A fix."])] }],
    };
    expect(decideWhatsNew({ result: fixedOnly, seenRaw: seen("0.22.0"), firstRun: false }).show).toBe(false);
  });

  it("skips dev builds and unloadable changelogs", () => {
    expect(decideWhatsNew({ result: release("Unreleased"), seenRaw: null, firstRun: false }).show).toBe(false);
    expect(decideWhatsNew({ result: { status: "error", message: "x" }, seenRaw: null, firstRun: false }).show).toBe(false);
  });
});

describe("dialogLines", () => {
  it("caps Added+Changed lines and counts the rest", () => {
    const built = buildRelease(release("0.23.0", [
      {
        version: "0.23.0",
        groups: [
          group("Added", Array.from({ length: 4 }, (_, i) => `Added ${i + 1}.`)),
          group("Changed", Array.from({ length: 4 }, (_, i) => `Changed ${i + 1}.`)),
        ],
      },
    ]))!;
    const capped = dialogLines(built);
    expect(capped.lines).toHaveLength(MAX_LINES);
    expect(capped.lines[0]).toBe("Added 1.");
    expect(capped.lines[3]).toBe("Added 4.");
    expect(capped.lines[4]).toBe("Changed 1.");
    expect(capped.moreCount).toBe(2);
  });

  it("reports nothing extra under the cap and never counts fixes as lines", () => {
    const built = buildRelease(release("0.23.0", [
      { version: "0.23.0", groups: [group("Added", ["One."]), group("Fixed", ["F1", "F2"])] },
    ]))!;
    expect(dialogLines(built)).toEqual({ lines: ["One."], moreCount: 0 });
  });
});

describe("sublineVersion", () => {
  const built = buildRelease(release("0.22.1", [
    { version: "0.22.1", groups: [group("Added", ["One."])] },
    { version: "0.22.0", groups: [group("Added", ["Old."])] },
  ]))!;

  it("prefers what this browser last saw", () => {
    expect(sublineVersion(built, { seenVersion: "0.19.3" })).toBe("0.19.3");
  });

  it("falls back to the changelog's previous release, then to nothing", () => {
    expect(sublineVersion(built, null)).toBe("0.22.0");
    expect(sublineVersion({ ...built, previous: null }, null)).toBeNull();
  });
});
