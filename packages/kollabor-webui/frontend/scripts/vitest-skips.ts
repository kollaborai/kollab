import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { hasTestFrameworkImport } from "./test-framework-import.mjs";

// Suites written for Node's built-in runner (`node --test`) import node:test (either quotes);
// Vitest skips them. Found on disk so a new test file never edits vite.config.ts.
export function nodeTestSuites(root: string): string[] {
  return ["tests", "src"].flatMap((dir) =>
    (readdirSync(path.join(root, dir), { recursive: true }) as string[])
      .filter((file) => /\.test\.(ts|tsx|mjs)$/.test(file))
      .map((file) => `${dir}/${file.split(path.sep).join("/")}`)
      .filter((file) =>
        hasTestFrameworkImport(readFileSync(path.join(root, file), "utf8"), "node:test", file),
      ),
  );
}
