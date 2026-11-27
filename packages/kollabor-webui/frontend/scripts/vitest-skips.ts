import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// Suites written for Node's built-in runner (`node --test`) import "node:test";
// Vitest skips them. Found on disk so a new test file never edits vite.config.ts.
export function nodeTestSuites(root: string): string[] {
  return ["tests", "src"].flatMap((dir) =>
    (readdirSync(path.join(root, dir), { recursive: true }) as string[])
      .filter((file) => /\.test\.(ts|tsx|mjs)$/.test(file))
      .map((file) => `${dir}/${file.split(path.sep).join("/")}`)
      .filter((file) => readFileSync(path.join(root, file), "utf8").includes('from "node:test"')),
  );
}
