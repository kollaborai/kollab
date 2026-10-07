// Run: node --test tests/gem-look.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { defaultFace, resolveGemLook, withLook } from "../src/components/gems/gem-look.ts";

const VOCABULARY = {
  faces: new Set(["pill", "disney", "kawaii"]),
  hats: new Set(["auto", "none", "crown"]),
  seasons: new Set(["auto", "none", "halloween"]),
  mixedFaces: ["pill", "disney", "kawaii"],
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
  assert.deepEqual(Object.keys(resolveGemLook(null, "lapis", VOCABULARY)), ["face"]);
});

test("gems without a pick draw stable eyes from the mix", () => {
  const names = ["lapis", "ruby", "sapphire", "topaz", "garnet", "pearl", "opal", "jasper"];
  const mixed = names.map((name) => defaultFace({}, name, VOCABULARY));
  assert.deepEqual(names.map((name) => resolveGemLook(null, name, VOCABULARY).face), mixed);
  assert.ok(mixed.every((face) => VOCABULARY.mixedFaces.includes(face ?? "")));
  assert.ok(new Set(mixed).size > 1, "a mix, not one face for all");
  const shuffled = names.map((name) => defaultFace({ seed: 7 }, name, VOCABULARY));
  assert.notDeepEqual(shuffled, mixed, "a new seed draws again");
  assert.equal(defaultFace({ defaults: { face: "kawaii" } }, "lapis", VOCABULARY), "kawaii");
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
