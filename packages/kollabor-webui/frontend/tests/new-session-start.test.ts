import assert from "node:assert/strict";
import test from "node:test";
import { startingChoice } from "../src/components/shell/model-choice.ts";

// The terminal starts on kollabor.llm.default_loadout. A web session always names a profile,
// which makes the terminal skip that default, so the form has to start on it itself (#957).
const profiles = [{ name: "default" }, { name: "openai-oauth" }];
const lunaMax = {
  name: "luna-max",
  profile: "openai-oauth",
  provider: "openai_responses",
  model: "gpt-6-luna",
  effort: "max",
};
const own = { provider: "", model: "", effort: "" }; // the profile's own saved model

test("starts on the default loadout's profile, model and level, not the active profile's saved model", () => {
  assert.deepEqual(startingChoice(profiles, "default", lunaMax), {
    profile: "openai-oauth",
    provider: "openai_responses",
    model: "gpt-6-luna",
    effort: "max",
  });
});

test("with no default loadout it starts on the active profile's own model", () => {
  assert.deepEqual(startingChoice(profiles, "default", null), { profile: "default", ...own });
  assert.deepEqual(startingChoice(profiles, "openai-oauth", undefined), { profile: "openai-oauth", ...own });
  // Nothing active: the first profile, then the built-in name.
  assert.deepEqual(startingChoice(profiles, undefined, null), { profile: "default", ...own });
  assert.deepEqual(startingChoice([{ name: "openai-oauth" }], undefined, null), { profile: "openai-oauth", ...own });
  assert.deepEqual(startingChoice([], undefined, null), { profile: "default", ...own });
});

test("a default loadout on a profile that is gone is not used", () => {
  assert.deepEqual(startingChoice(profiles, "default", { ...lunaMax, profile: "deleted" }), {
    profile: "default",
    ...own,
  });
});

test("a harness default loadout keeps today's start", () => {
  // An ACP harness owns its model and level, and the form applies those through the harness's own
  // options after the session exists; that path is not part of this change.
  const harness = { ...lunaMax, profile: "default", provider: "acp" };
  assert.deepEqual(startingChoice(profiles, "default", harness), { profile: "default", ...own });
});
