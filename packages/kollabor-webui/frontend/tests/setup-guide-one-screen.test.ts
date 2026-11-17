// Run: node --test tests/setup-guide-one-screen.test.ts (Node 24 strips the types).
// #1217: the welcome guide must not carry a second "Choose a provider" screen.
// Settings > Setup is the one provider screen; "Get Started" opens it when no
// real provider is set, and the engine's built-in profiles do not count as one.

import assert from "node:assert/strict";
import test from "node:test";

import {
  SETUP_STEPS,
  emptyFacts,
  getStartedOpensSetup,
  hasChosenProvider,
  loadGuideState,
  type SetupFacts,
} from "../src/setup/guide-model.ts";

// The four profiles a fresh config dir reports (GET /profiles), "auto" first.
const builtIns = [
  { name: "default", provider: "auto" },
  { name: "claude-code", provider: "acp" },
  { name: "codex-cli", provider: "acp" },
  { name: "local", provider: "custom" },
];

test("the guide has no provider card: Settings > Setup is the one provider screen", () => {
  assert.deepEqual(
    SETUP_STEPS.map((step) => step.id),
    ["welcome", "network", "agent", "done"],
  );
});

test("a fresh install (only built-in profiles, auto active) has no provider chosen", () => {
  assert.equal(hasChosenProvider(builtIns, "default"), false);
});

test("an active profile with a real provider counts as chosen", () => {
  assert.equal(hasChosenProvider(builtIns, "claude-code"), true);
  assert.equal(
    hasChosenProvider([...builtIns, { name: "work", provider: "openai" }], "work"),
    true,
  );
});

test("no active profile, or one missing from the list, is not chosen", () => {
  assert.equal(hasChosenProvider(builtIns, undefined), false);
  assert.equal(hasChosenProvider(builtIns, null), false);
  assert.equal(hasChosenProvider(builtIns, "gone"), false);
  assert.equal(hasChosenProvider([], "default"), false);
});

test("Get Started opens Setup only while no provider is chosen", () => {
  const unset: SetupFacts = { ...emptyFacts(), providerChosen: false };
  const set: SetupFacts = { ...emptyFacts(), providerChosen: true };
  assert.equal(getStartedOpensSetup(unset), true);
  assert.equal(getStartedOpensSetup(set), false);
  assert.equal(getStartedOpensSetup(emptyFacts()), true);
});

test("progress saved while the provider card existed resumes at welcome", () => {
  const saved = JSON.stringify({ step: "provider", dismissed: false });
  assert.equal(loadGuideState(saved).step, "welcome");
});
