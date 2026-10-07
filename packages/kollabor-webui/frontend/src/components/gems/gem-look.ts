import type { GemAppearance, GemLook } from "@/api";

/** The ids a saved look may use: gem-face.ts's eye, hat and season styles. */
export type GemLookVocabulary = {
  faces: ReadonlySet<string>;
  hats: ReadonlySet<string>;
  seasons: ReadonlySet<string>;
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

/**
 * The look the Gem Studio saved for one gem: its own pick, else the all-gems
 * default. Ids outside the vocabulary are skipped so the renderer's defaults
 * apply (a newer engine file read by an older web UI, say).
 */
export function resolveGemLook(
  appearance: GemAppearance | null | undefined,
  name: string | null | undefined,
  vocabulary: GemLookVocabulary,
): ResolvedGemLook {
  if (!appearance) return {};
  const own: GemLook = (name && appearance.gems?.[name]) || {};
  const defaults = appearance.defaults ?? {};
  const known = (value: string | undefined, valid: ReadonlySet<string>) =>
    value && valid.has(value) ? value : undefined;
  const look: ResolvedGemLook = {};
  const face = known(own.face, vocabulary.faces) ?? known(defaults.face, vocabulary.faces);
  const hat = known(own.hat, vocabulary.hats) ?? known(defaults.hat, vocabulary.hats);
  const season = known(appearance.season, vocabulary.seasons);
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
