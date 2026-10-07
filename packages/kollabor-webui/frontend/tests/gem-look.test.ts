// Run: node --test tests/gem-look.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { resolveGemLook, withLook } from "../src/components/gems/gem-look.ts";

const VOCABULARY = {
  faces: new Set(["pill", "disney", "kawaii"]),
  hats: new Set(["auto", "none", "crown", "beret"]),
  seasons: new Set(["auto", "none", "halloween"]),
};

test("the user's picks go over the look a gem was born with", () => {
  const appearance = {
    season: "none",
    born: { lapis: { face: "disney", hat: "beret" }, ruby: { face: "kawaii", hat: "crown" } },
    gems: { lapis: { face: "kawaii", color: [1, 2, 3] } },
  };
  assert.deepEqual(resolveGemLook(appearance, "lapis", VOCABULARY), {
    face: "kawaii",
    bornHat: "beret",
    season: "none",
    color: [1, 2, 3],
  });
  assert.deepEqual(resolveGemLook(appearance, "ruby", VOCABULARY), {
    face: "kawaii",
    bornHat: "crown",
    season: "none",
  });
  // Not born yet: nothing but the season.
  assert.deepEqual(resolveGemLook(appearance, "opal", VOCABULARY), { season: "none" });
});

test("a picked hat is kept apart from the born hat", () => {
  const appearance = { born: { lapis: { hat: "beret" } }, gems: { lapis: { hat: "crown" } } };
  assert.deepEqual(resolveGemLook(appearance, "lapis", VOCABULARY), { hat: "crown", bornHat: "beret" });
});

test("unknown ids and bad colors fall back", () => {
  const appearance = {
    season: "summer",
    born: { lapis: { face: "pill", hat: "fez" } },
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
  assert.deepEqual(withLook(dressed, "lapis", { face: undefined, hat: undefined }).gems, {});
});
