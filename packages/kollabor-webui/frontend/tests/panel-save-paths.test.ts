import assert from "node:assert/strict";
import test from "node:test";
import { savePaths } from "../src/components/panels/panel-model.ts";

const FALLBACK = "~/.kollab/voice/preferences.json";
const GLOBAL = "~/.kollab/config.json";

test("nothing changed saves to the panel's own file", () => {
  assert.deepEqual(savePaths([{ path: "enabled" }], {}, FALLBACK), [FALLBACK]);
});

test("a changed field with save_path saves only to its own file", () => {
  const fields = [{ path: "enabled" }, { path: "quick_model", save_path: GLOBAL }];
  assert.deepEqual(savePaths(fields, { quick_model: "gpt" }, FALLBACK), [GLOBAL]);
});

test("a changed field without save_path keeps the panel's file beside its own", () => {
  const fields = [{ path: "enabled" }, { path: "quick_model", save_path: GLOBAL }];
  assert.deepEqual(savePaths(fields, { enabled: true, quick_model: "gpt" }, FALLBACK), [FALLBACK, GLOBAL]);
});

test("changed fields sharing a save_path name that file once", () => {
  const fields = [
    { path: "quick_model", save_path: GLOBAL },
    { path: "voice_personality", save_path: GLOBAL },
  ];
  assert.deepEqual(savePaths(fields, { quick_model: "gpt", voice_personality: "max_gen_z" }, FALLBACK), [GLOBAL]);
});
