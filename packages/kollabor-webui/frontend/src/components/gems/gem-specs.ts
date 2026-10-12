/**
 * How each pool identity looks as a gem: its cut (the shape its soft body rests
 * in, gem-blob.ts HOME_SHAPE) and a fallback color. The engine's GET /agents
 * sends the real pool color (`plugins/hub/organizations/pool.json`), so `color`
 * here only covers a missing or older engine. Names outside this table (custom
 * pools) get a cut from their caste.
 */

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

export type GemSpec = {
  cut: GemCut;
  /** sRGB hex, used only when the engine sent no pool color. */
  color: string;
  /** Rainbow sparkles from dispersion: diamond, zircon. */
  fire?: boolean;
};

const GEMS: Record<string, GemSpec> = {
  lapis: { cut: "cabochon", color: "#1e5ab4" },
  sapphire: { cut: "brilliant", color: "#0f52ba" },
  aquamarine: { cut: "crystal", color: "#64c8eb" },
  zircon: { cut: "brilliant", color: "#4682c8", fire: true },
  bismuth: { cut: "hopper", color: "#c86496" },
  peridot: { cut: "emerald", color: "#78be21" },
  jasper: { cut: "cabochon", color: "#d27832" },
  nephrite: { cut: "cabochon", color: "#50a050" },
  ruby: { cut: "cushion", color: "#c81e32" },
  garnet: { cut: "brilliant", color: "#8c143c" },
  topaz: { cut: "pear", color: "#f0c832" },
  hessonite: { cut: "cushion", color: "#c88c3c" },
  pearl: { cut: "sphere", color: "#e6dcf0" },
  moonstone: { cut: "cabochon", color: "#c8d2e6" },
  opal: { cut: "cabochon", color: "#b4c8ff" },
  padparadscha: { cut: "trillion", color: "#f0aa8c" },
  amethyst: { cut: "crystal", color: "#8c50c8" },
  quartz: { cut: "crystal", color: "#f096aa" },
  spinel: { cut: "octahedron", color: "#e63278" },
  citrine: { cut: "emerald", color: "#f0e664" },
  diamond: { cut: "brilliant", color: "#f5f5fa", fire: true },
  aureate: { cut: "cushion", color: "#ffe632" },
  cobalt: { cut: "pear", color: "#4664c8" },
  coral: { cut: "cabochon", color: "#ff82aa" },
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
/** A gem of the pool: it wears the pool's color, never a born one. */
export const isPoolGem = (name?: string | null): boolean => Boolean(name && GEMS[name.toLowerCase()]);

export const UNASSIGNED_GEM: GemSpec = { cut: "crystal", color: "#c9ccd6" };

export function gemSpec(name?: string | null, caste?: string | null): GemSpec {
  const known = name ? GEMS[name.toLowerCase()] : undefined;
  if (known) return known;
  if (!name) return UNASSIGNED_GEM;
  const cut = (caste && CASTE_CUTS[caste.toLowerCase()]) || "brilliant";
  return { cut, color: "#8a8fa3" };
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
