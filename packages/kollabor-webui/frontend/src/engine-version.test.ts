import { describe, expect, it } from "vitest";

import appSource from "./App.tsx?raw";
import { compareBuildIdentity } from "./engine-version";

// App.tsx mounts the banner check inside a large component, so pin the warning
// guard to the same build-identity comparison exercised above.
describe("App version-mismatch guard", () => {
  it("requires a confirmed mismatch using the frontend bundle build SHA", () => {
    expect(appSource).toMatch(
      /compareBuildIdentity\(engine, bundle, __KOLLAB_BUILD_SHA__\)/,
    );
    expect(appSource).toMatch(/if \(identity !== "mismatch"\) return;/);
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
