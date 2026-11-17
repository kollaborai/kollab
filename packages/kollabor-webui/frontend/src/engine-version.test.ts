import { describe, expect, it } from "vitest";

import appSource from "./App.tsx?raw";
import { compareBuildIdentity, sameVersionBase } from "./engine-version";

describe("sameVersionBase", () => {
  it("ignores the engine's PEP 440 local segment in a source checkout", () => {
    expect(sameVersionBase("0.18.5+9a1b2c3d", "0.18.5")).toBe(true);
  });

  it("treats an unknown local segment as a match", () => {
    expect(sameVersionBase("0.18.5+unknown", "0.18.5")).toBe(true);
  });

  it("still flags genuine base-version drift", () => {
    expect(sameVersionBase("0.19.0+abc12345", "0.18.5")).toBe(false);
  });

  it("matches plain versions on both sides", () => {
    expect(sameVersionBase("0.18.5", "0.18.5")).toBe(true);
  });

  it("is symmetric when only the bundle carries a local segment", () => {
    expect(sameVersionBase("0.18.5", "0.18.5+local")).toBe(true);
  });
});

// App.tsx mounts the banner check inside a large component, so the guard is
// pinned at its source: it must call sameVersionBase, not compare strings.
describe("App version-mismatch guard", () => {
  it("flags a mismatch only after the base-version compare, not strict equality", () => {
    expect(appSource).toMatch(
      /if \(!engine \|\| !bundle \|\| sameVersionBase\(engine, bundle\)\) return;/,
    );
    expect(appSource).not.toMatch(/engine === bundle/);
  });
});

describe("compareBuildIdentity", () => {
  it("confirms the same build when the engine short SHA matches the bundle SHA", () => {
    expect(compareBuildIdentity("0.22.1+9c7d4d12", "0.22.1", "9c7d4d12aabbccddeeff00112233445566778899")).toBe("same");
  });

  it("confirms a same-release build mismatch", () => {
    expect(compareBuildIdentity("0.22.1+9c7d4d12", "0.22.1", "a1b2c3d4aabbccddeeff00112233445566778899")).toBe("mismatch");
  });

  it("confirms release drift even when build identity is unavailable", () => {
    expect(compareBuildIdentity("0.22.0+unknown", "0.22.1", "unknown")).toBe("mismatch");
  });

  it("returns unknown when matching releases have an unknown or malformed build identity", () => {
    expect(compareBuildIdentity("0.22.1+unknown", "0.22.1", "9c7d4d12aabbccddeeff00112233445566778899")).toBe("unknown");
    expect(compareBuildIdentity("0.22.1+not-a-sha", "0.22.1", "9c7d4d12aabbccddeeff00112233445566778899")).toBe("unknown");
  });

  it("compares prerelease versions without treating their build suffix as release text", () => {
    expect(compareBuildIdentity("0.22.1-rc.1+9c7d4d12", "0.22.1-rc.1", "9c7d4d12aabbccddeeff00112233445566778899")).toBe("same");
    expect(compareBuildIdentity("0.22.1-rc.1+9c7d4d12", "0.22.1", "9c7d4d12aabbccddeeff00112233445566778899")).toBe("mismatch");
  });

  it("treats a cached page from an older release as mismatched", () => {
    expect(compareBuildIdentity("0.22.1+9c7d4d12", "0.22.0", "9c7d4d12aabbccddeeff00112233445566778899")).toBe("mismatch");
  });

  it("returns unknown for empty or malformed release identifiers", () => {
    expect(compareBuildIdentity("", "0.22.1", "9c7d4d12aabbccddeeff00112233445566778899")).toBe("unknown");
    expect(compareBuildIdentity("not-a-version+9c7d4d12", "0.22.1", "9c7d4d12aabbccddeeff00112233445566778899")).toBe("unknown");
  });
});
