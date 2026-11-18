import { describe, expect, it } from "vitest";
import {
  buildRelease,
  compareVersions,
  decideWhatsNew,
  dialogLines,
  loadSeenRecord,
} from "../src/whats-new/whats-new-model";

const result = {
  status: "ready" as const,
  sections: [{ version: "0.22.1", groups: [{ category: "Added", items: ["A feature"] }] }],
};

describe("What's New input boundaries", () => {
  const invalid = [
    "zebra", "999999999999999999999999999999999999999999999999999999999999999999",
    "9007199254740992.0.0", "0.22.1\n", "0.22.1\r", "0.22.1\r\n",
    "0.22.1 ", " 0.22.1", "00.22.1", "0.22", "0.22.1.0", "0.22.1-beta",
    "0.22.1+build", "0.22.1\u0000", "0.２２.1", "Unreleased",
  ];

  it.each(invalid)("never assigns an ordering to unsupported version %j", (version) => {
    expect(compareVersions(version, "0.22.1")).toBeNull();
    expect(compareVersions("0.22.1", version)).toBeNull();
    const seenRaw = JSON.stringify({ seenVersion: version });
    expect(loadSeenRecord(seenRaw)).toBeNull();
    expect(decideWhatsNew({ result, seenRaw, firstRun: false }).show).toBe(true);
    expect(buildRelease({ ...result, sections: [{ ...result.sections[0], version }] })).toBeNull();
    expect(buildRelease({ ...result, sections: [...result.sections, { version, groups: [] }] })?.previous).toBeNull();
  });

  it("compares every supported boundary numerically without precision loss", () => {
    const versions = ["0.0.0", "0.9.9", "0.10.0", "1.0.0", "9007199254740991.0.0"];
    for (let a = 0; a < versions.length; a += 1) {
      for (let b = 0; b < versions.length; b += 1) {
        expect(Math.sign(compareVersions(versions[a], versions[b])!)).toBe(Math.sign(a - b));
      }
    }
  });

  it("produces only bounded, labelled sections for every Added/Changed split", () => {
    for (let added = 0; added <= 9; added += 1) {
      for (let changed = 0; changed <= 9; changed += 1) {
        const release = {
          version: "0.22.1", previous: null, fixedCount: 7,
          added: Array.from({ length: added }, (_, i) => `Added ${i}`),
          changed: Array.from({ length: changed }, (_, i) => `Changed ${i}`),
        };
        const expected = [
          { category: "Added", items: release.added.slice(0, 6) },
          { category: "Changed", items: release.changed.slice(0, Math.max(6 - added, 0)) },
        ].filter((section) => section.items.length > 0);
        expect(dialogLines(release)).toEqual({
          sections: expected,
          moreCount: Math.max(added + changed - 6, 0),
        });
      }
    }
  });
});
