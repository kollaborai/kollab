import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { uncollectedTests } from "../scripts/check-test-discovery.mjs";

const root = mkdtempSync(path.join(os.tmpdir(), "kollab-test-discovery-"));
const files = [
  "tests/node.test.ts",
  "tests/new.test.ts",
  "tests/nested/other.test.ts",
  "src/component.test.tsx",
];
mkdirSync(path.join(root, "tests/nested"), { recursive: true });
mkdirSync(path.join(root, "src"));
for (const file of files) writeFileSync(path.join(root, file), "");
after(() => {
  for (const file of files) unlinkSync(path.join(root, file));
  rmdirSync(path.join(root, "tests/nested"));
  rmdirSync(path.join(root, "tests"));
  rmdirSync(path.join(root, "src"));
  rmdirSync(root);
});

test("accepts collected Vitest files and top-level Node test files", () => {
  assert.deepEqual(
    uncollectedTests(root, [
      path.join(root, "tests/nested/other.test.ts"),
      path.join(root, "src/component.test.tsx"),
    ]),
    [],
  );
});

test("reports an unlisted Vitest file even when the Node command would skip it", () => {
  writeFileSync(
    path.join(root, "tests/new.test.ts"),
    'import { it } from ' + JSON.stringify("vitest"),
  );
  assert.deepEqual(uncollectedTests(root, []), [
    "src/component.test.tsx",
    "tests/nested/other.test.ts",
    "tests/new.test.ts",
  ]);
});
