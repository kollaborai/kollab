import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { hasTestFrameworkImport } from "../scripts/test-framework-import.mjs";
import { uncollectedTests } from "../scripts/check-test-discovery.mjs";
import { nodeTestSuites } from "../scripts/vitest-skips.ts";

const root = mkdtempSync(path.join(os.tmpdir(), "kollab-test-discovery-"));
const files = [
  "tests/node.test.ts",
  "tests/new.test.ts",
  "tests/nested/other.test.ts",
  "src/component.test.tsx",
  "tests/runner.test.mjs",
  "tests/vitest-comment.test.ts",
  "tests/vitest-string.test.ts",
  "tests/vitest-require-single.test.ts",
  "tests/vitest-require-double.test.ts",
  "tests/node-single.test.ts",
  "tests/node-double.test.ts",
  "tests/node-require-single.test.ts",
  "tests/node-require-double.test.ts",
  "tests/embedded-node-string.test.ts",
  "tests/embedded-node-comment.test.ts",
  "tests/embedded-node-template.test.ts",
  "tests/multiline-vitest-import.test.ts",
];
const suiteFixtures = {
  "tests/vitest-comment.test.ts": ["import { it }", "from", '"vitest"; // fixture mentions "node:test"\n'].join(" "),
  "tests/vitest-string.test.ts": ["import { it }", "from", "'vitest';\n"].join(" "),
  "tests/vitest-require-single.test.ts": ["const { it } = require(", "'vitest'", ");\n"].join(""),
  "tests/vitest-require-double.test.ts": ["const { it } = require(", '"vitest"', ");\n"].join(""),
  "tests/node-single.test.ts": "import { test } from 'node:test';\n",
  "tests/node-double.test.ts": 'import { test } from "node:test";\n',
  "tests/node-require-single.test.ts": "const { test } = require('node:test');\n",
  "tests/node-require-double.test.ts": 'const { test } = require("node:test");\n',
};
const embeddedNodeSources = {
  "tests/embedded-node-string.test.ts": [
    'import { it } from "vitest";',
    'const fixture = \'import { test } from "node:test";\';',
    'it("runs under Vitest", () => {});',
  ].join("\n"),
  "tests/embedded-node-comment.test.ts": [
    'import { it } from "vitest";',
    '// fixture sample: import { test } from "node:test";',
    'it("runs under Vitest", () => {});',
  ].join("\n"),
  "tests/embedded-node-template.test.ts": [
    'import { it } from "vitest";',
    'const fixture = `import { test } from "node:test";`;',
    'it("runs under Vitest", () => {});',
  ].join("\n"),
  "tests/multiline-vitest-import.test.ts": [
    "import { it }",
    "from",
    '  "vitest";',
    'it("runs under Vitest", () => {});',
  ].join("\n"),
};
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
    ], ["tests/runner.test.mjs"]),
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
    "tests/runner.test.mjs",
  ]);
});

test("classifies suites by their framework import rather than matching text", () => {
  writeFileSync(path.join(root, "tests/new.test.ts"), "");
  for (const [file, source] of Object.entries(suiteFixtures)) {
    writeFileSync(path.join(root, file), source);
  }

  assert.deepEqual(uncollectedTests(root, [
    path.join(root, "tests/vitest-comment.test.ts"),
    path.join(root, "tests/vitest-require-single.test.ts"),
    path.join(root, "tests/vitest-require-double.test.ts"),
  ]), [
    "src/component.test.tsx",
    "tests/nested/other.test.ts",
    "tests/runner.test.mjs",
    "tests/vitest-string.test.ts",
  ]);
});

test("CI runs the shared Node selector and ignores framework text inside fixtures", () => {
  for (const [file, source] of Object.entries(embeddedNodeSources)) {
    writeFileSync(path.join(root, file), source);
    assert.equal(hasTestFrameworkImport(source, "vitest"), true, `${file} is a Vitest suite`);
    assert.equal(hasTestFrameworkImport(source, "node:test"), false, `${file} has no Node import`);
  }
  assert.deepEqual(
    nodeTestSuites(root).filter((file) => file.startsWith("tests/embedded-node-") || file.includes("multiline-vitest")),
    [],
  );
  assert.deepEqual(
    uncollectedTests(root, [...Object.keys(suiteFixtures), ...Object.keys(embeddedNodeSources)].map((file) => path.join(root, file)), ["tests/runner.test.mjs"]),
    ["src/component.test.tsx", "tests/nested/other.test.ts"],
  );
});

test("CI runner selects the same Node files as the shared matcher", () => {
  const workflow = readFileSync(new URL("../../../../.github/workflows/ci.yml", import.meta.url), "utf8");
  const runnerLine = workflow.split("\n").find((line) => line.includes("node --test $(node scripts/node-test-files.mjs)"));
  assert.ok(runnerLine, "CI Node runner line uses the shared selector script");
  for (const [file, source] of Object.entries(embeddedNodeSources)) writeFileSync(path.join(root, file), source);
  const script = new URL("../scripts/node-test-files.mjs", import.meta.url);
  const selected = spawnSync(process.execPath, [script.pathname], {
    cwd: root,
    encoding: "utf8",
  });
  assert.equal(selected.status, 0, selected.stderr || selected.error?.message);
  const selectedByMatcher = files
    .filter((file) => /^tests\/[^/]+\.test\.ts$/.test(file))
    .filter((file) => !hasTestFrameworkImport(readFileSync(path.join(root, file), "utf8"), "vitest"))
    .sort();
  assert.deepEqual(selected.stdout.trim().split("\n").sort(), selectedByMatcher);
});
