import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { buildIdentity } from "../vite.config";

describe("frontend build provenance", () => {
  it("uses packaged data instead of an enclosing repository identity", () => {
    const tempRoot = mkdtempSync(path.join(os.tmpdir(), "kollab-build-provenance-"));
    try {
      const outerRoot = path.join(tempRoot, "outer-checkout");
      mkdirSync(outerRoot);
      const git = (...args: string[]) =>
        execFileSync("git", args, { cwd: outerRoot, encoding: "utf8" }).trim();
      git("init", "--quiet");
      git("checkout", "--quiet", "-b", "outer-checkout");
      git("config", "user.name", "Kollab Test");
      git("config", "user.email", "kollab-test@example.invalid");
      writeFileSync(path.join(outerRoot, "tracked.txt"), "outer repository\n");
      git("add", "tracked.txt");
      git("commit", "--quiet", "-m", "outer repository");
      const outerSha = git("rev-parse", "--verify", "HEAD");

      const sourceRoot = path.join(outerRoot, "unpacked-sdist");
      mkdirSync(sourceRoot);
      const embedded = {
        branch: "release/0.18.0",
        sha: "b".repeat(40),
      };
      const identity = buildIdentity(sourceRoot, {
        KOLLAB_BUILD_BRANCH: embedded.branch,
        KOLLAB_BUILD_SHA: embedded.sha,
      });

      expect(identity).toEqual(embedded);
      expect(identity.sha).not.toBe(outerSha);
      expect(buildIdentity(sourceRoot, {})).toEqual({
        branch: "unknown",
        sha: "unknown",
      });
    } finally {
      rmSync(tempRoot, { recursive: true, force: true });
    }
  });
});
