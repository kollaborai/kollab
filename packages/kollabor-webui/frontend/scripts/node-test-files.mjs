import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { hasTestFrameworkImport } from "./test-framework-import.mjs";

export function nodeTestFiles(root = process.cwd()) {
  return readdirSync(path.join(root, "tests"), { withFileTypes: true })
    .filter((entry) => entry.isFile() && /^.+\.test\.ts$/.test(entry.name))
    .map((entry) => `tests/${entry.name}`)
    .filter((file) =>
      !hasTestFrameworkImport(readFileSync(path.join(root, file), "utf8"), "vitest", file),
    )
    .sort();
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  for (const file of nodeTestFiles()) console.log(file);
}
