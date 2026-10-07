/**
 * How each pool identity looks as a 3D gem: its cut (silhouette), finish
 * (material family) and a fallback color. The engine's GET /agents sends the
 * real pool color (`plugins/hub/organizations/pool.json`), so `color` here only
 * covers a missing or older engine. Names outside this table (custom pools)
 * get a cut from their caste.
 */

import type { StonePattern } from "./gem-textures";

export type GemCut =
  | "brilliant"
  | "emerald"
  | "crystal"
  | "pear"
  | "trillion"
  | "cushion"
  | "cabochon"
  | "sphere"
  | "octahedron"
  | "hopper";

export type GemFinish = "faceted" | "polished" | "metal" | "pearl";

export type GemSpec = {
  cut: GemCut;
  finish: GemFinish;
  /** sRGB hex, used only when the engine sent no pool color. */
  color: string;
  /** Thin-film rainbow: opal play-of-color, pearl nacre, bismuth oxide. */
  iridescence?: number;
  /** Metallic flecks that twinkle on the surface, e.g. lapis pyrite. */
  flecks?: string;
  /** Procedural surface pattern for a polished stone (gem-textures.ts). */
  pattern?: StonePattern;
  /** Rainbow sparkles from dispersion: diamond, zircon. */
  fire?: boolean;
  /** Width and height of a cabochon dome. */
  aspect?: [number, number];
};

const GEMS: Record<string, GemSpec> = {
  lapis: { cut: "cabochon", finish: "polished", color: "#1e5ab4", flecks: "#e8c25a", pattern: "lapis" },
  sapphire: { cut: "brilliant", finish: "faceted", color: "#0f52ba" },
  aquamarine: { cut: "crystal", finish: "faceted", color: "#64c8eb" },
  zircon: { cut: "brilliant", finish: "faceted", color: "#4682c8", iridescence: 0.45, fire: true },
  bismuth: { cut: "hopper", finish: "metal", color: "#c86496", iridescence: 1 },
  peridot: { cut: "emerald", finish: "faceted", color: "#78be21" },
  jasper: { cut: "cabochon", finish: "polished", color: "#d27832", aspect: [1.08, 0.86], pattern: "jasper" },
  nephrite: { cut: "cabochon", finish: "polished", color: "#50a050", aspect: [0.86, 1.06], pattern: "jade" },
  ruby: { cut: "cushion", finish: "faceted", color: "#c81e32" },
  garnet: { cut: "brilliant", finish: "faceted", color: "#8c143c" },
  topaz: { cut: "pear", finish: "faceted", color: "#f0c832" },
  hessonite: { cut: "cushion", finish: "faceted", color: "#c88c3c" },
  pearl: { cut: "sphere", finish: "pearl", color: "#e6dcf0", iridescence: 0.85 },
  moonstone: { cut: "cabochon", finish: "polished", color: "#c8d2e6", iridescence: 0.4, pattern: "moonstone" },
  opal: { cut: "cabochon", finish: "polished", color: "#b4c8ff", iridescence: 0.6, aspect: [1.06, 0.9], pattern: "opal" },
  padparadscha: { cut: "trillion", finish: "faceted", color: "#f0aa8c" },
  amethyst: { cut: "crystal", finish: "faceted", color: "#8c50c8" },
  quartz: { cut: "crystal", finish: "faceted", color: "#f096aa" },
  spinel: { cut: "octahedron", finish: "faceted", color: "#e63278" },
  citrine: { cut: "emerald", finish: "faceted", color: "#f0e664" },
  diamond: { cut: "brilliant", finish: "faceted", color: "#f5f5fa", iridescence: 0.6, fire: true },
  aureate: { cut: "cushion", finish: "metal", color: "#ffe632" },
  cobalt: { cut: "pear", finish: "faceted", color: "#4664c8" },
  coral: { cut: "cabochon", finish: "polished", color: "#ff82aa", aspect: [0.92, 1], pattern: "coral" },
};

const CASTE_CUTS: Record<string, GemCut> = {
  communication: "brilliant",
  engineering: "emerald",
  defense: "cushion",
  intelligence: "cabochon",
  creative: "crystal",
  leadership: "octahedron",
};

/** A clear quartz point for sessions that run without a pool identity. */
export const UNASSIGNED_GEM: GemSpec = { cut: "crystal", finish: "faceted", color: "#c9ccd6" };

export function gemSpec(name?: string | null, caste?: string | null): GemSpec {
  const known = name ? GEMS[name.toLowerCase()] : undefined;
  if (known) return known;
  if (!name) return UNASSIGNED_GEM;
  const cut = (caste && CASTE_CUTS[caste.toLowerCase()]) || "brilliant";
  return { cut, finish: cut === "cabochon" ? "polished" : "faceted", color: "#8a8fa3" };
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
