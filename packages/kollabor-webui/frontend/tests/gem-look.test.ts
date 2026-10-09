// Run: node --test tests/gem-look.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { drawn, lookKey, resolveGemLook, withLook } from "../src/components/gems/gem-look.ts";

// Nothing to draw a born look from: these tests read the engine's rolls alone.
const VOCABULARY = {
  faces: new Set(["pill", "disney", "kawaii"]),
  hats: new Set(["auto", "none", "crown", "beret"]),
  seasons: new Set(["auto", "none", "halloween"]),
  birthFaces: [],
  birthHats: [],
  birthColors: [],
};

const BIRTHS = {
  ...VOCABULARY,
  birthFaces: ["pill", "disney", "kawaii"],
  birthHats: ["none", "crown", "beret"],
  birthColors: [
    [1, 1, 1],
    [2, 2, 2],
    [3, 3, 3],
    [4, 4, 4],
  ] as [number, number, number][],
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

test("the same name in another folder or on another computer is another agent", () => {
  const home = "/Users/me/dev/kollab";
  assert.equal(lookKey("koordinator", null, home), "koordinator");
  assert.equal(lookKey("koordinator", home, home), "koordinator");
  assert.equal(lookKey("koordinator", "/Users/me/dev/webapp", home), "koordinator@/Users/me/dev/webapp");
  assert.equal(lookKey("koordinator", "home-server", home), "koordinator@home-server");
  assert.equal(lookKey("", "home-server", home), "");
});

test("every agent is born with a fixed look of its own, and picks and rolls go over it", () => {
  const keys = ["koordinator", "koordinator@home-server", "koordinator@/w/webapp", "koordinator@/w/notes", "lapis@/w/a"];
  const looks = keys.map((key) => resolveGemLook({}, key, BIRTHS));
  // Fixed: the same key draws the same look every time.
  assert.deepEqual(keys.map((key) => resolveGemLook({}, key, BIRTHS)), looks);
  for (const look of looks) {
    assert.ok(look.face && look.bornHat && look.bornColor);
    assert.equal(look.hat, undefined);
    assert.equal(look.color, undefined);
  }
  // Not one look for every koordinator.
  assert.ok(new Set(looks.map((look) => JSON.stringify(look))).size >= 3);

  const appearance = {
    born: { koordinator: { face: "disney", hat: "beret" } },
    gems: { "koordinator@home-server": { face: "kawaii", hat: "crown", color: [9, 9, 9] } },
  };
  const rolled = resolveGemLook(appearance, "koordinator", BIRTHS);
  assert.equal(rolled.face, "disney");
  assert.equal(rolled.bornHat, "beret");
  const dressed = resolveGemLook(appearance, "koordinator@home-server", BIRTHS);
  assert.deepEqual([dressed.face, dressed.hat, dressed.color], ["kawaii", "crown", [9, 9, 9]]);
  // The pick is that agent's alone.
  assert.notDeepEqual(resolveGemLook(appearance, "koordinator@/w/webapp", BIRTHS).hat, "crown");
});

test("a style added later takes only the agents it wins; the others keep their look", () => {
  const keys = Array.from({ length: 60 }, (_, i) => `koordinator@/w/project-${i}`);
  const hats = ["none", "party", "top", "beanie", "crown"];
  const before = keys.map((key) => drawn(key, "hat", hats));
  const after = keys.map((key) => drawn(key, "hat", [...hats, "wizard"]));
  const moved = keys.filter((_, i) => after[i] !== before[i]);
  assert.ok(moved.length > 0 && moved.length < keys.length / 2);
  assert.ok(keys.every((_, i) => after[i] === before[i] || after[i] === "wizard"));
  // The order of the list does not matter.
  assert.deepEqual(keys.map((key) => drawn(key, "hat", [...hats].reverse())), before);
  assert.equal(drawn("lapis", "hat", []), undefined);
});
