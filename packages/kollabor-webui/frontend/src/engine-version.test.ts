import { describe, expect, it } from "vitest";

import appSource from "./App.tsx?raw";
import { sameVersionBase } from "./engine-version";

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
