// Run: node --test tests/versions.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { versionRows } from "../src/utils/versions.ts";

test("every version reads from its source with a v prefix", () => {
  const rows = versionRows(
    { kollab_version: "0.17.0", engine_version: "0.17.0", server_version: "0.17.1" },
    "0.17.2",
  );
  assert.deepEqual(rows, [
    { label: "Kollab", value: "v0.17.0" },
    { label: "Engine", value: "v0.17.0" },
    { label: "Web UI Server", value: "v0.17.1" },
    { label: "Frontend Bundle", value: "v0.17.2" },
  ]);
});

test("a missing config shows unknown, never a blank", () => {
  const rows = versionRows(null, "0.17.0");
  assert.deepEqual(
    rows.map((row) => row.value),
    ["unknown", "unknown", "unknown", "v0.17.0"],
  );
});
