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
  getStartedOpensSetup,
  hasChosenProvider,
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
import * as guideModel from "../src/setup/guide-model.ts";

// Namespace imports keep the regression module loadable against the PR base,
// where the first-run policy helpers do not exist yet. The startup wiring
// assertions below then fail as behavior assertions instead of link errors.
const guidePolicyAvailable =
  typeof guideModel.shouldAutoOpenGuide === "function" &&
  typeof guideModel.shouldPersistGuide === "function";

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
  assert.equal(prevStep("network"), "welcome");
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

test("a profile without a provider does not count as chosen", () => {
  assert.equal(hasChosenProvider([{ name: "work", provider: null }], "work"), false);
  assert.equal(hasChosenProvider([{ name: "work" }], "work"), false);
  assert.equal(hasChosenProvider([{ name: "work", provider: "" }], "work"), false);
});

test("an active real provider counts, but auto and missing profiles do not", () => {
  const profiles = [
    { name: "default", provider: "auto" },
    { name: "claude-code", provider: "acp" },
  ];
  assert.equal(hasChosenProvider(profiles, "default"), false);
  assert.equal(hasChosenProvider(profiles, "claude-code"), true);
  assert.equal(hasChosenProvider(profiles, undefined), false);
  assert.equal(hasChosenProvider(profiles, "gone"), false);
});

test("Get Started opens Setup for missing and auto providers only", () => {
  assert.equal(getStartedOpensSetup(emptyFacts()), true);
  assert.equal(
    getStartedOpensSetup({ ...emptyFacts(), providerChosen: true }),
    false,
  );
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
  const state: GuideState = { step: "network", dismissed: false };
  const facts: SetupFacts = {
    providerChosen: true,
    activeProfile: "local",
    anyAgentActive: true,
    networkLive: true,
  };
  const result = resolveStep(state, facts);
  assert.equal(result.step, "done");
  assert.deepEqual(result.skipped, ["network", "agent"]);
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
  assert.equal(stepTarget("network"), "settings:network");
  assert.equal(stepTarget("agent"), "session:new");
  assert.equal(stepTarget("welcome"), null);
  assert.equal(stepTarget("done"), null);
});

test("storage key is namespaced and stable", () => {
  assert.equal(STORAGE_KEY, "kollab.setup-guide.v1");
});

// Issue #1098: first-run auto-open decisions, both pure. No record is a
// first run; a record stuck at an undismissed welcome is one too (old builds
// wrote exactly that on mount, which is why auto-open never fired). Engaged
// or dismissed records never reopen, and a corrupt record must not block the
// guide forever. Persistence skips the untouched initial state: writing it on
// mount is what defeated the first-run check in the first place.
test("shouldAutoOpenGuide: no record or stale welcome record is a first run", { skip: !guidePolicyAvailable }, () => {
  assert.equal(guideModel.shouldAutoOpenGuide(null), true);
  assert.equal(guideModel.shouldAutoOpenGuide('{"step":"welcome","dismissed":false}'), true);
  assert.equal(guideModel.shouldAutoOpenGuide("not-json{"), true);
});

test("shouldAutoOpenGuide: valid malformed storage opens its normalized welcome state", { skip: !guidePolicyAvailable }, () => {
  assert.equal(
    guideModel.shouldAutoOpenGuide('{"step":"unknown-step","dismissed":"yes"}'),
    true,
  );
  assert.equal(
    guideModel.shouldAutoOpenGuide('{"step":"unknown-step","dismissed":true}'),
    true,
  );
});

test("shouldAutoOpenGuide: prototype property names are corrupt steps", { skip: !guidePolicyAvailable }, () => {
  for (const step of ["toString", "constructor", "__proto__"]) {
    assert.equal(
      guideModel.shouldAutoOpenGuide(JSON.stringify({ step, dismissed: false })),
      true,
      `${step} is not a setup step`,
    );
  }
});

test("shouldAutoOpenGuide: engaged or dismissed records never reopen", { skip: !guidePolicyAvailable }, () => {
  assert.equal(guideModel.shouldAutoOpenGuide('{"step":"network","dismissed":false}'), false);
  assert.equal(guideModel.shouldAutoOpenGuide('{"step":"agent","dismissed":false}'), false);
  assert.equal(guideModel.shouldAutoOpenGuide('{"step":"done","dismissed":true}'), false);
  assert.equal(guideModel.shouldAutoOpenGuide('{"step":"welcome","dismissed":true}'), false);
});

test("shouldPersistGuide: the untouched initial state writes nothing", { skip: !guidePolicyAvailable }, () => {
  const initial = initialGuideState();
  assert.equal(guideModel.shouldPersistGuide(initial, initial), false);
});

test("shouldPersistGuide: engage, advance and dismiss all record", { skip: !guidePolicyAvailable }, () => {
  const initial = initialGuideState();
  assert.equal(guideModel.shouldPersistGuide(initial, { step: "network", dismissed: false }), true);
  assert.equal(guideModel.shouldPersistGuide(initial, { step: "welcome", dismissed: true }), true);
  const engaged: GuideState = { step: "network", dismissed: false };
  assert.equal(guideModel.shouldPersistGuide(engaged, { step: "agent", dismissed: false }), true);
  assert.equal(guideModel.shouldPersistGuide(engaged, { step: "network", dismissed: true }), true);
});

test("shouldPersistGuide: identical states and pristine reverts do not write", { skip: !guidePolicyAvailable }, () => {
  const engaged: GuideState = { step: "network", dismissed: false };
  assert.equal(guideModel.shouldPersistGuide(engaged, engaged), false);
  // A revert to the pristine state must not clobber the record with an
  // undismissed-welcome entry (that record hides first runs from the check).
  assert.equal(guideModel.shouldPersistGuide(engaged, initialGuideState()), false);
  const dismissed: GuideState = { step: "done", dismissed: true };
  assert.equal(guideModel.shouldPersistGuide(dismissed, initialGuideState()), false);
});

test("SetupGuide gates its storage write through the persistence policy", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(
    new URL("../src/setup/SetupGuide.tsx", import.meta.url),
    "utf8",
  );
  const effect = source.slice(
    source.indexOf("const prevStateRef"),
    source.indexOf("// Re-read engine facts"),
  );
  assert.match(effect, /shouldPersistGuide\(prev, state\)/);
  assert.match(
    effect,
    /window\.localStorage\.setItem\(STORAGE_KEY, saveGuideState\(state\)\)/,
  );
});

test("App startup applies the first-run policy only when no session restores", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(
    new URL("../src/App.tsx", import.meta.url),
    "utf8",
  );
  const firstRunPolicy = source.slice(
    source.indexOf("const noGuideYet"),
    source.indexOf("const RESTART_COMMAND"),
  );
  const startup = source.slice(
    source.indexOf("const first = chatToRestore"),
    source.indexOf("setError(null)", source.indexOf("const first = chatToRestore")),
  );

  assert.match(
    firstRunPolicy,
    /shouldAutoOpenGuide\(window\.localStorage\.getItem\(STORAGE_KEY\)\)/,
  );
  assert.match(startup, /if \(first\) \{/);
  assert.match(startup, /else if \(noGuideYet\(\)\)/);
  const firstRunBranch = startup.match(/else if \(noGuideYet\(\)\)\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(firstRunBranch, /setSetupGuideOpen\(true\)/);
});
