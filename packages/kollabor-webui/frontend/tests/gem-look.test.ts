// Run: node --test tests/gem-look.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
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

const HAIR_SHADES = [
  [30, 26, 24],
  [95, 60, 35],
  [150, 60, 35],
  [225, 190, 120],
  [170, 170, 175],
  [235, 232, 225],
] as [number, number, number][];
// The birth pools the engine draws from (gem_appearance.py BIRTH_*), so this
// suite pins the same draw the engine's roll makes for a gem it never rolled.
const COATS = {
  ...VOCABULARY,
  hairs: new Set(["none", "short", "long", "spiky", "mohawk", "buzz", "crop", "fringe", "bob", "page", "mane", "plume", "crest", "quiff", "halo"]),
  furs: new Set(["none", "short", "shaggy", "velvet", "plush", "long", "flowing"]),
  birthFaces: ["pill", "disney"],
  birthHats: ["none", "beret"],
  birthHairs: ["short", "long", "spiky", "mohawk", "buzz", "crop", "fringe", "bob", "page", "mane", "plume", "crest", "quiff", "halo"],
  birthFurs: ["short", "shaggy", "velvet", "plush", "long", "flowing"],
  birthHairColors: HAIR_SHADES,
  birthFurColors: ["gem", ...HAIR_SHADES] as ("gem" | number[])[],
};

test("a gem the engine never rolled is born furry 85% of the time, the same on every reload", () => {
  const keys = Array.from({ length: 1000 }, (_, i) => `gem${i}`);
  const looks = keys.map((key) => resolveGemLook({}, key, COATS));
  const furry = looks.filter((look) => look.fur !== undefined && look.fur !== "none");
  assert.ok(furry.length >= 800 && furry.length <= 900, `born furry: ${furry.length} of 1000`);
  for (const look of furry) {
    assert.ok(look.furColor !== undefined, "born fur comes with a color");
  }
  for (const look of looks) {
    if (look.hair !== undefined) assert.ok(COATS.hairs.has(look.hair));
    if (look.hairColor !== undefined) assert.notEqual(look.hairColor, "gem");
    if (look.fur !== undefined) assert.ok(COATS.furs.has(look.fur));
  }
  // A fixed draw of the key: the same gem, the same coat, on every device.
  assert.deepEqual(resolveGemLook({}, "gem123", COATS), looks[123]);
});

test("the user's picks win over the coat a gem was born with, and a roll is never redrawn", () => {
  const keys = Array.from({ length: 100 }, (_, i) => `gem${i}`);
  const looks = keys.map((key) => resolveGemLook({}, key, COATS));
  const index = looks.findIndex((look) => look.fur !== undefined && look.fur !== "none");
  const key = keys[index];
  // Picked "None" takes the born fur off.
  assert.equal(resolveGemLook({ gems: { [key]: { fur: "none" } } }, key, COATS).fur, "none");
  // A picked hair goes over the born coat; the un-picked fur keeps its draw.
  const dressed = resolveGemLook({ gems: { [key]: { hair: "mohawk" } } }, key, COATS);
  assert.equal(dressed.hair, "mohawk");
  assert.equal(dressed.fur, looks[index].fur);
  // The engine's record wins over the fallback draw, bare included.
  assert.equal(resolveGemLook({ born: { [key]: { hair: "none" } } }, key, COATS).fur, undefined);
});

// The engine test pins the same names and checks the ordered source pools match.
test("the checked birth eyes draw the same faces as the engine", () => {
  const source = readFileSync(new URL("../src/components/gems/gem-appearance.tsx", import.meta.url), "utf8");
  const pool = source.match(/export const EVERYDAY_FACES: EyeStyle\[\] = \[(.*?)\]/)?.[1];
  assert.ok(pool);
  const faces = [...pool.matchAll(/"([a-z0-9_-]+)"/g)].map((match) => match[1]);
  const expected = { lapis: "drowsy", ruby: "bean", opal: "drowsy", peridot: "drowsy", sapphire: "smile", garnet: "kawaii", bismuth: "bean" };
  for (const [key, face] of Object.entries(expected)) assert.equal(drawn(key, "face", faces), face);
});
