// Run: node --test tests/gem-look.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { resolveGemLook, withLook } from "../src/components/gems/gem-look.ts";

const VOCABULARY = {
  faces: new Set(["pill", "disney", "kawaii"]),
  hats: new Set(["auto", "none", "crown"]),
  seasons: new Set(["auto", "none", "halloween"]),
};

test("a gem's own pick beats the all-gems default", () => {
  const appearance = {
    season: "none",
    defaults: { face: "disney", hat: "crown" },
    gems: { lapis: { face: "kawaii", color: [1, 2, 3] } },
  };
  assert.deepEqual(resolveGemLook(appearance, "lapis", VOCABULARY), {
    face: "kawaii",
    hat: "crown",
    season: "none",
    color: [1, 2, 3],
  });
  assert.deepEqual(resolveGemLook(appearance, "ruby", VOCABULARY), {
    face: "disney",
    hat: "crown",
    season: "none",
  });
});

test("unknown ids and bad colors fall back", () => {
  const appearance = {
    season: "summer",
    defaults: { face: "pill" },
    gems: { lapis: { face: "laser", hat: "fez", color: [300, 0, 0] } },
  };
  assert.deepEqual(resolveGemLook(appearance, "lapis", VOCABULARY), { face: "pill" });
  assert.deepEqual(resolveGemLook(null, "lapis", VOCABULARY), {});
});

test("withLook sets and clears picks without touching the original", () => {
  const original = { gems: { lapis: { face: "kawaii" } } };
  const dressed = withLook(original, "lapis", { hat: "crown" });
  assert.deepEqual(dressed.gems, { lapis: { face: "kawaii", hat: "crown" } });
  assert.deepEqual(original.gems, { lapis: { face: "kawaii" } });

  const cleared = withLook(dressed, "lapis", { face: undefined, hat: undefined });
  assert.deepEqual(cleared.gems, {});

  assert.deepEqual(withLook({}, null, { face: "disney" }).defaults, { face: "disney" });
});
