// Run: node --test tests/setup-guide.test.ts (Node 24 strips the types).
// The guided-setup walkthrough is a pure state machine over real engine facts:
// steps auto-skip when their goal already holds, progress survives a reload
// through a localStorage-shaped string, and dismissal is sticky until the
// caller forces the guide back.

import assert from "node:assert/strict";
import test from "node:test";

import {
  SETUP_STEPS,
  STORAGE_KEY,
  emptyFacts,
  initialGuideState,
  loadGuideState,
  nextStep,
  prevStep,
  resolveStep,
  saveGuideState,
  stepIndex,
  stepTarget,
  type SetupFacts,
  type GuideState,
} from "../src/setup/guide-model.ts";

test("steps are ordered and unique", () => {
  const ids = SETUP_STEPS.map((step) => step.id);
  assert.deepEqual(ids, [...new Set(ids)]);
  assert.equal(ids[0], "welcome");
  assert.equal(ids[ids.length - 1], "done");
});

test("stepIndex maps every step and next/prev stay in bounds", () => {
  for (const step of SETUP_STEPS) {
    assert.equal(typeof stepIndex(step.id), "number");
  }
  assert.equal(nextStep("done"), "done");
  assert.equal(prevStep("welcome"), "welcome");
  assert.equal(nextStep("agent"), "done");
  assert.equal(prevStep("provider"), "welcome");
});

test("fresh state starts at welcome, not dismissed", () => {
  const state = initialGuideState();
  assert.equal(state.step, "welcome");
  assert.equal(state.dismissed, false);
  assert.deepEqual(resolveStep(state, emptyFacts()), {
    step: "welcome",
    skipped: [],
  });
});

test("loadGuideState survives a round trip", () => {
  const state: GuideState = { step: "network", dismissed: false };
  const restored = loadGuideState(saveGuideState(state));
  assert.deepEqual(restored, state);
});

test("loadGuideState rejects malformed JSON and unknown steps", () => {
  assert.deepEqual(loadGuideState("not json at all"), initialGuideState());
  assert.deepEqual(
    loadGuideState(JSON.stringify({ step: "chaos", dismissed: "yes" })),
    { step: "welcome", dismissed: false },
  );
  assert.equal(loadGuideState(null).step, "welcome");
});

test("a finished provider setup auto-skips the provider step", () => {
  const state: GuideState = { step: "provider", dismissed: false };
  const facts: SetupFacts = {
    ...emptyFacts(),
    profileCount: 2,
    activeProfile: "openai",
  };
  const result = resolveStep(state, facts);
  assert.equal(result.step, "network");
  assert.deepEqual(result.skipped, ["provider"]);
});

test("a live network auto-skips the network step", () => {
  const state: GuideState = { step: "network", dismissed: false };
  const facts: SetupFacts = { ...emptyFacts(), networkLive: true };
  assert.equal(resolveStep(state, facts).step, "agent");
});

test("an active agent auto-skips the agent step", () => {
  const state: GuideState = { step: "agent", dismissed: false };
  const facts: SetupFacts = { ...emptyFacts(), anyAgentActive: true };
  assert.equal(resolveStep(state, facts).step, "done");
});

test("skips chain: everything already set lands on done", () => {
  const state: GuideState = { step: "provider", dismissed: false };
  const facts: SetupFacts = {
    profileCount: 1,
    activeProfile: "local",
    anyAgentActive: true,
    networkLive: true,
  };
  const result = resolveStep(state, facts);
  assert.equal(result.step, "done");
  assert.deepEqual(result.skipped, ["provider", "network", "agent"]);
});

test("dismissal is sticky and resolves to done", () => {
  const state: GuideState = { step: "agent", dismissed: true };
  assert.deepEqual(resolveStep(state, emptyFacts()), {
    step: "done",
    skipped: [],
  });
  const restored = loadGuideState(saveGuideState(state));
  assert.equal(restored.dismissed, true);
});

test("each actionable step targets a real surface", () => {
  assert.equal(stepTarget("provider"), "settings:setup");
  assert.equal(stepTarget("network"), "settings:network");
  assert.equal(stepTarget("agent"), "session:new");
  assert.equal(stepTarget("welcome"), null);
  assert.equal(stepTarget("done"), null);
});

test("storage key is namespaced and stable", () => {
  assert.equal(STORAGE_KEY, "kollab.setup-guide.v1");
});
