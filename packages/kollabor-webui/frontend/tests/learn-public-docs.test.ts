import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const learnDoc = readFileSync(new URL("../../../../docs/features/learn.md", import.meta.url), "utf8");

test("the public Learn doc names no home-directory tooling or absolute user paths", () => {
  assert.doesNotMatch(learnDoc, /\/Users\//);
  const dotHomePaths = [...learnDoc.matchAll(/~\/\.[\w-]+/g)].map((match) => match[0]);
  assert.deepEqual(
    [...new Set(dotHomePaths)].filter((path) => path !== "~/.kollab"),
    [],
  );
});
