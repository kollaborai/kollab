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

test("a picked avatar and a known shape come through; an unknown shape does not", () => {
  const cuts = { ...VOCABULARY, cuts: new Set(["heart", "hex"]) };
  const appearance = {
    gems: {
      omar: { avatar: { kind: "emoji", value: "🦊" } as const, cut: "heart" },
      maya: { cut: "blob" },
    },
  };
  const omar = resolveGemLook(appearance, "omar", cuts);
  assert.deepEqual(omar.avatar, { kind: "emoji", value: "🦊" });
  assert.equal(omar.cut, "heart");
  assert.equal(resolveGemLook(appearance, "maya", cuts).cut, undefined);
  // A vocabulary with no shapes ignores a picked one.
  assert.equal(resolveGemLook(appearance, "omar", VOCABULARY).cut, undefined);
});

test("hair and fur go over the coat a gem was born with, and auras keep only known ids", () => {
  const vocabulary = {
    ...VOCABULARY,
    hairs: new Set(["none", "short", "long"]),
    furs: new Set(["none", "short"]),
    auras: new Set(["glow", "orbit"]),
    sparkles: new Set(["none", "few", "lots"]),
  };
  const appearance = {
    born: { lapis: { hair: "long", hairColor: [95, 60, 35] }, ruby: { fur: "short" } },
    gems: {
      lapis: { hair: "short" },
      ruby: { fur: "none", furColor: "gem" as const, auras: ["glow", "flames", "glow"], sparkles: "lots", auraColor: [1, 2, 3] },
      opal: { hair: "mullet", hairColor: [300, 0, 0], auras: "glow" as unknown as string[] },
    },
  };
  const lapis = resolveGemLook(appearance, "lapis", vocabulary);
  // A new style keeps the born shade.
  assert.equal(lapis.hair, "short");
  assert.deepEqual(lapis.hairColor, [95, 60, 35]);
  const ruby = resolveGemLook(appearance, "ruby", vocabulary);
  assert.equal(ruby.fur, "none", "None takes born fur off");
  assert.equal(ruby.furColor, "gem");
  assert.deepEqual(ruby.auras, ["glow"]);
  assert.equal(ruby.sparkles, "lots");
  assert.deepEqual(ruby.auraColor, [1, 2, 3]);
  const opal = resolveGemLook(appearance, "opal", vocabulary);
  assert.equal(opal.hair, undefined);
  assert.equal(opal.hairColor, undefined);
  assert.equal(opal.auras, undefined);
});

test("knob settings keep known knobs only, each held to its range, and are never born", () => {
  const vocabulary = { ...VOCABULARY, knobs: { length: [0.05, 0.8], lie: [0, 5] } as const };
  const appearance = {
    born: { lapis: { hairKnobs: { length: 0.5 } } },
    gems: {
      lapis: { hairKnobs: { length: 2, lie: 1.5, sway: 0.3, constructor: 1 }, furKnobs: { lie: Number.NaN } },
      ruby: { furKnobs: { length: 0.01 } },
    },
  };
  const lapis = resolveGemLook(appearance, "lapis", vocabulary);
  assert.deepEqual(lapis.hairKnobs, { length: 0.8, lie: 1.5 });
  assert.equal(lapis.furKnobs, undefined);
  assert.deepEqual(resolveGemLook(appearance, "ruby", vocabulary).furKnobs, { length: 0.05 });
  assert.equal(resolveGemLook({ born: appearance.born }, "lapis", vocabulary).hairKnobs, undefined);
});
