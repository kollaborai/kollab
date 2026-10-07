import type { GemAppearance, GemLook } from "@/api";

/** The ids a saved look may use: gem-face.ts's eye, hat and season styles. */
export type GemLookVocabulary = {
  faces: ReadonlySet<string>;
  hats: ReadonlySet<string>;
  seasons: ReadonlySet<string>;
  /** The eyes a gem without a pick draws from: the same gem and seed draw the same pair. */
  mixedFaces: readonly string[];
};

export type ResolvedGemLook = {
  face?: string;
  hat?: string;
  season?: string;
  color?: [number, number, number];
};

const isColor = (value: unknown): value is [number, number, number] =>
  Array.isArray(value) &&
  value.length === 3 &&
  value.every((channel) => Number.isInteger(channel) && channel >= 0 && channel <= 255);

/** FNV-1a over `key`, as an index into `pool`. */
function draw(pool: readonly string[], key: string): string | undefined {
  let hash = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) hash = Math.imul(hash ^ key.charCodeAt(i), 0x01000193);
  return pool[(hash >>> 0) % pool.length];
}

/** The eyes `name` wears without its own pick: the all-gems pick, else its draw from the mix. */
export function defaultFace(
  appearance: GemAppearance | null | undefined,
  name: string | null | undefined,
  vocabulary: GemLookVocabulary,
): string | undefined {
  const pick = appearance?.defaults?.face;
  if (pick && vocabulary.faces.has(pick)) return pick;
  return name ? draw(vocabulary.mixedFaces, `${appearance?.seed ?? 0}:${name}`) : undefined;
}

/**
 * The look the Gem Studio saved for one gem: its own pick, else the all-gems
 * default; eyes nobody picked come from the mix. Ids outside the vocabulary are
 * skipped so the renderer's defaults apply (a newer engine file read by an
 * older web UI, say). Before the saved looks load, every gem shows its mix.
 */
export function resolveGemLook(
  appearance: GemAppearance | null | undefined,
  name: string | null | undefined,
  vocabulary: GemLookVocabulary,
): ResolvedGemLook {
  const saved = appearance ?? {};
  const own: GemLook = (name && saved.gems?.[name]) || {};
  const defaults = saved.defaults ?? {};
  const known = (value: string | undefined, valid: ReadonlySet<string>) =>
    value && valid.has(value) ? value : undefined;
  const look: ResolvedGemLook = {};
  const face = known(own.face, vocabulary.faces) ?? defaultFace(saved, name, vocabulary);
  const hat = known(own.hat, vocabulary.hats) ?? known(defaults.hat, vocabulary.hats);
  const season = known(saved.season, vocabulary.seasons);
  if (face) look.face = face;
  if (hat) look.hat = hat;
  if (season) look.season = season;
  if (isColor(own.color)) look.color = own.color;
  return look;
}

/**
 * A copy of `appearance` with `patch` applied to one gem, or to the all-gems
 * defaults when `name` is null. An undefined value removes that pick, and a
 * gem left with no picks drops out.
 */
export function withLook(
  appearance: GemAppearance,
  name: string | null,
  patch: Partial<GemLook>,
): GemAppearance {
  const next: Record<string, unknown> = {
    ...(name ? appearance.gems?.[name] : appearance.defaults),
  };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) delete next[key];
    else next[key] = value;
  }
  if (!name) return { ...appearance, defaults: next };
  const gems = { ...appearance.gems };
  if (Object.keys(next).length) gems[name] = next;
  else delete gems[name];
  return { ...appearance, gems };
}
