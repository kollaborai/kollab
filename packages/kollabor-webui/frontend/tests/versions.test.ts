// Run: node --test tests/versions.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { versionRows } from "../src/utils/versions.ts";

const embeddedBuild = {
  branch: "live/0.18.0",
  sha: "0123456789abcdef0123456789abcdef01234567",
};

test("versions show package sources and the embedded short build identity", () => {
  const rows = versionRows(
    { kollab_version: "0.17.0", engine_version: "0.17.0", server_version: "0.17.1" },
    "0.17.2",
    embeddedBuild,
  );
  assert.deepEqual(rows, [
    { label: "Kollab", value: "v0.17.0" },
    { label: "Engine", value: "v0.17.0" },
    { label: "Web UI Server", value: "v0.17.1" },
    { label: "Frontend Bundle", value: "v0.17.2" },
    {
      label: "Build",
      value: "live/0.18.0 @ 01234567",
      title: "live/0.18.0 @ 0123456789abcdef0123456789abcdef01234567",
    },
  ]);
});

test("missing config does not hide the signed PhoneLink bundle identity", () => {
  const rows = versionRows(null, "0.17.0", embeddedBuild);
  assert.deepEqual(
    rows.map((row) => row.value),
    ["unknown", "unknown", "unknown", "v0.17.0", "live/0.18.0 @ 01234567"],
  );
});

test("unavailable build values are explicit and never blank", () => {
  const rows = versionRows(null, "", { branch: "unknown", sha: "unknown" });
  assert.deepEqual(
    rows.map((row) => row.value),
    ["unknown", "unknown", "unknown", "unknown", "unknown @ unknown"],
  );
});
