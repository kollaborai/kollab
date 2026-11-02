import { spawnSync } from "node:child_process";
import { globSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const testPatterns = [
  "tests/**/*.{test,spec}.{js,cjs,mjs,jsx,ts,cts,mts,tsx}",
  "src/**/*.{test,spec}.{js,cjs,mjs,jsx,ts,cts,mts,tsx}",
];

// Match the Node command in CI exactly: only top-level tests/*.test.ts files
// without this Vitest import are passed to `node --test`.
export function uncollectedTests(root, vitestFiles) {
  const collected = new Set(
    vitestFiles.map((file) => path.relative(root, file).split(path.sep).join("/")),
  );
  const candidates = new Set(
    testPatterns.flatMap((pattern) => [...globSync(pattern, { cwd: root })]),
  );
  return [...candidates]
    .filter((file) => {
      const normalized = file.split(path.sep).join("/");
      if (collected.has(normalized)) return false;
      const nodeCandidate = /^tests\/[^/]+\.test\.ts$/.test(normalized);
      return (
        !nodeCandidate ||
        readFileSync(path.join(root, file), "utf8").includes('from "vitest"')
      );
    })
    .sort();
}

function main() {
  const vitest = path.join(frontendRoot, "node_modules/vitest/vitest.mjs");
  const listing = spawnSync(process.execPath, [vitest, "list", "--filesOnly", "--json"], {
    cwd: frontendRoot,
    encoding: "utf8",
  });
  if (listing.error || listing.status !== 0) {
    if (listing.stderr) process.stderr.write(listing.stderr);
    throw listing.error ?? new Error(`Vitest file discovery failed (${listing.status}).`);
  }
  const files = JSON.parse(listing.stdout).map((entry) => entry.file);
  const missing = uncollectedTests(frontendRoot, files);
  if (missing.length) {
    console.error("These test files are not selected by the CI Vitest or Node commands:");
    for (const file of missing) console.error(`  ${file}`);
    console.error("Add them to Vitest's test.include or update the Node test command.");
    process.exitCode = 1;
    return;
  }
  console.log("All frontend test files are selected by a CI test command.");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
