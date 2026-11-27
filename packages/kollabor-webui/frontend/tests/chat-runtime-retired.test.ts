import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "vitest";

// #1451, lane 4h: the engine runtime and its history reconciliation are retired.
// Nothing under src/ may name them, so a leftover reference fails here, not at build.

const srcRoot = fileURLToPath(new URL("../src", import.meta.url));

const retiredNames = [
  "useAssistantTransportRuntime",
  "importExternalState",
  "historyToMessages",
  "onStale",
  "runtimeEpoch",
  "live-turn",
  "run-stream",
  "history-merge",
  "history-reconciliation",
  "history-cache",
  "live-follow",
  "history-content",
];

// An import of ./runtime, ../runtime or @/runtime. ./chat-runtime does not match.
const runtimeImport = /(?:\bfrom|\bimport\(|\brequire\()\s*["'](?:\.{1,2}\/)*(?:@\/)?runtime(?:\.tsx?)?["']/;

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const file = path.join(dir, name);
    return statSync(file).isDirectory() ? filesUnder(file) : [file];
  });
}

function retiredReferences(): string[] {
  return filesUnder(srcRoot).flatMap((file) => {
    const rel = path.relative(srcRoot, file).split(path.sep).join("/");
    return readFileSync(file, "utf8").split("\n").flatMap((line, index) => {
      const where = `src/${rel}:${index + 1}`;
      const hits = retiredNames
        .filter((name) => line.includes(name))
        .map((name) => `${where} ${name}`);
      if (runtimeImport.test(line)) hits.push(`${where} import from ./runtime`);
      return hits;
    });
  });
}

test("no file under src/ names the retired runtime, reconciliation or live-turn modules", () => {
  expect(retiredReferences()).toEqual([]);
});
