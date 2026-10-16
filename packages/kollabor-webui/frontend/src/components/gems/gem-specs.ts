/**
 * Who each pool identity is as a gem. Every one has its own silhouette (its
 * cut, the shape its soft body rests in, gem-blob.ts HOME), proportions
 * (`build`), finish (`material`) and temperament (`pace`, `energy`), so a
 * roster reads as a cast: no two gems of a caste share a cut, and gems of
 * close colors never do. The temperaments follow the pool's personalities
 * (`plugins/hub/organizations/pool.json`). The engine's GET /agents sends the
 * real pool color, so `color` here only covers a missing or older engine.
 * Names outside this table (custom pools, koordinator) draw a cut from their
 * name, so they join the cast too.
 */

export type GemCut =
  | "brilliant"
  | "cushion"
  | "emerald"
  | "crystal"
  | "sphere"
  | "cabochon"
  | "egg"
  | "pear"
  | "trillion"
  | "heart"
  | "hopper"
  | "hex"
  | "ingot"
  | "octahedron"
  | "cloud";

/** How the body takes light (gem-blob.ts paintBody). */
export type GemMaterial = {
  /** 0 matte stone .. 1 polished glass or metal: highlight strength and tightness. */
  gloss: number;
  /** Light carried through the body: glass glows from inside, jade a little. */
  clear: number;
  /** Highlights tinted by the body, as on gold. */
  metal?: number;
  /** A thin-film rainbow toward the edges: opal, bismuth, nacre. */
  iris?: number;
  /** Specks in the stone and their color: lapis's pyrite, jasper's spots. */
  fleck?: number;
  fleckColor?: readonly [number, number, number];
  /** A floating sheen and its color: moonstone's blue glow. */
  sheen?: readonly [number, number, number];
};

const GLASS: GemMaterial = { gloss: 1, clear: 0.6 };
const CLEAR: GemMaterial = { gloss: 1, clear: 0.85 };
const WAXY: GemMaterial = { gloss: 0.45, clear: 0.35 };

export type GemSpec = {
  cut: GemCut;
  /** sRGB hex, used only when the engine sent no pool color. */
  color: string;
  /** Rainbow sparkles from dispersion: diamond, zircon. */
  fire?: boolean;
  /** Width and height against the cut's own proportions (both at most 1, so a hat keeps its room). */
  build: readonly [number, number];
  material: GemMaterial;
  /** Seconds per breath at rest. */
  pace: number;
  /** 0 composed .. 1 restless: how often it glances, how quick it turns, how much it sways. */
  energy: number;
  caste?: string;
};

const gem = (
  caste: string,
  cut: GemCut,
  color: string,
  build: readonly [number, number],
  material: GemMaterial,
  pace: number,
  energy: number,
  fire = false,
): GemSpec => ({ caste, cut, color, build, material, pace, energy, ...(fire ? { fire } : {}) });

const GEMS: Record<string, GemSpec> = {
  // Communication: calm, serene, sharp, methodical.
  lapis: gem("communication", "cabochon", "#1e5ab4", [1, 0.94], { gloss: 0.35, clear: 0, fleck: 1, fleckColor: [255, 214, 110] }, 4.8, 0.35),
  sapphire: gem("communication", "brilliant", "#0f52ba", [1, 1], GLASS, 5.4, 0.2),
  aquamarine: gem("communication", "crystal", "#64c8eb", [0.84, 1], CLEAR, 3.8, 0.7),
  zircon: gem("communication", "octahedron", "#4682c8", [0.82, 1], CLEAR, 4.4, 0.45, true),
  // Engineering: passionate, perfectionist, relentless, reliable.
  bismuth: gem("engineering", "hopper", "#c86496", [1, 0.96], { gloss: 1, clear: 0, metal: 0.6, iris: 0.85 }, 3.6, 0.85),
  peridot: gem("engineering", "emerald", "#78be21", [0.88, 1], GLASS, 4, 0.6),
  jasper: gem("engineering", "cabochon", "#d27832", [1, 0.84], { gloss: 0.25, clear: 0, fleck: 0.8, fleckColor: [96, 44, 20] }, 4.2, 0.5),
  nephrite: gem("engineering", "egg", "#50a050", [0.94, 1], WAXY, 4.4, 0.4),
  // Defense: fiery, composed, unbreakable, strategic.
  ruby: gem("defense", "cushion", "#c81e32", [0.96, 0.94], GLASS, 3.4, 0.9),
  garnet: gem("defense", "hex", "#8c143c", [0.96, 0.96], GLASS, 5.2, 0.2),
  topaz: gem("defense", "pear", "#f0c832", [0.94, 1], GLASS, 5.6, 0.1),
  hessonite: gem("defense", "brilliant", "#c88c3c", [1, 0.94], GLASS, 4.6, 0.35),
  // Intelligence: meticulous, watchful, scattered genius, clear reporter.
  pearl: gem("intelligence", "sphere", "#e6dcf0", [0.94, 0.94], { gloss: 0.6, clear: 0.15, iris: 0.22 }, 4.8, 0.3),
  moonstone: gem("intelligence", "egg", "#c8d2e6", [0.9, 1], { gloss: 0.75, clear: 0.45, sheen: [120, 170, 255] }, 5.4, 0.25),
  opal: gem("intelligence", "pear", "#b4c8ff", [0.96, 0.98], { gloss: 0.8, clear: 0.4, iris: 0.75 }, 3.9, 0.8),
  padparadscha: gem("intelligence", "trillion", "#f0aa8c", [1, 0.96], GLASS, 4.2, 0.55),
  // Creative: laid-back, warm, chaotic, rapid.
  amethyst: gem("creative", "crystal", "#8c50c8", [0.92, 1], GLASS, 5, 0.4),
  quartz: gem("creative", "heart", "#f096aa", [1, 0.96], { gloss: 0.6, clear: 0.5 }, 4.8, 0.4),
  spinel: gem("creative", "octahedron", "#e63278", [0.96, 1], GLASS, 3.4, 1),
  citrine: gem("creative", "cushion", "#f0e664", [0.92, 0.96], GLASS, 3.6, 0.85),
  // Leadership: absolute, efficient, empathetic, disruptive.
  diamond: gem("leadership", "brilliant", "#f5f5fa", [1, 1], CLEAR, 6, 0.1, true),
  aureate: gem("leadership", "ingot", "#ffe632", [1, 0.94], { gloss: 1, clear: 0, metal: 1 }, 4.4, 0.5),
  cobalt: gem("leadership", "emerald", "#4664c8", [1, 0.8], GLASS, 5, 0.3),
  coral: gem("leadership", "cloud", "#ff82aa", [1, 0.94], WAXY, 4, 0.7),
};

/** Cuts a name outside the pool can draw: the ones that read at a glance, nothing borrowed from a mood. */
const CUTS: readonly GemCut[] = [
  "brilliant", "cushion", "emerald", "crystal", "sphere", "cabochon", "egg",
  "pear", "trillion", "heart", "hopper", "hex", "ingot", "octahedron",
];

/** A gem of the pool: it wears the pool's color, never a born one. */
export const isPoolGem = (name?: string | null): boolean => Boolean(name && GEMS[name.toLowerCase()]);

/** A clear quartz point for sessions that run without a pool identity. */
export const UNASSIGNED_GEM: GemSpec = { cut: "crystal", color: "#c9ccd6", build: [0.84, 1], material: CLEAR, pace: 4.6, energy: 0.3 };

/** The pool names, in pool order (the dev lab's lineup). */
export const POOL_GEMS: readonly string[] = Object.keys(GEMS);

function fnv(text: string): number {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

const outside = new Map<string, GemSpec>();

export function gemSpec(name?: string | null, caste?: string | null): GemSpec {
  const key = (name || "").toLowerCase();
  const known = GEMS[key];
  if (known) return known;
  if (!key) return UNASSIGNED_GEM;
  let spec = outside.get(key);
  if (!spec) {
    // A stable draw from the name: a koordinator in every folder keeps one silhouette.
    const roll = fnv(`cut:${key}`);
    spec = {
      cut: CUTS[roll % CUTS.length],
      color: "#8a8fa3",
      build: [1, 0.96],
      material: (roll >>> 8) % 3 === 0 ? WAXY : GLASS,
      pace: 3.8 + ((roll >>> 12) % 17) / 10,
      energy: ((roll >>> 20) % 9) / 10,
      ...(caste ? { caste: caste.toLowerCase() } : {}),
    };
    outside.set(key, spec);
  }
  return spec;
}

/** What the avatar is doing, folded from hub presence and session states. */
export type GemMood = "working" | "waiting" | "dreaming" | "idle" | "booting" | "offline";

const MOODS: Record<string, GemMood> = {
  working: "working",
  thinking: "working",
  processing: "working",
  busy: "working",
  running: "working",
  streaming: "working",
  waiting: "waiting",
  blocked: "waiting",
  dreaming: "dreaming",
  booting: "booting",
  connecting: "booting",
  registered: "booting",
  dead: "offline",
  offline: "offline",
  disconnecting: "offline",
  stopped: "offline",
};

/**
 * `live` is whether a process backs this identity at all: a pool entry nobody
 * runs reports state "available", which must read as offline, not idle.
 */
export function gemMood(state: string | null | undefined, live: boolean): GemMood {
  if (!live) return "offline";
  return MOODS[(state || "").toLowerCase()] || "idle";
}
