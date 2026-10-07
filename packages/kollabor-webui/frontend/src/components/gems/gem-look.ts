import type { GemAppearance, GemLook } from "@/api";

/** The ids a saved look may use: gem-face.ts's eye, hat and season styles. */
export type GemLookVocabulary = {
  faces: ReadonlySet<string>;
  hats: ReadonlySet<string>;
  seasons: ReadonlySet<string>;
};

export type ResolvedGemLook = {
  face?: string;
  /** A hat the user picked: it beats a seasonal costume. */
  hat?: string;
  /** The hat the gem was born with: a seasonal costume covers it. */
  bornHat?: string;
  season?: string;
  color?: [number, number, number];
};

const isColor = (value: unknown): value is [number, number, number] =>
  Array.isArray(value) &&
  value.length === 3 &&
  value.every((channel) => Number.isInteger(channel) && channel >= 0 && channel <= 255);

/**
 * One gem's look: the user's Gem Studio picks over the random eyes and hat it
 * was born with (the engine rolls those the first time it sees the gem alive).
 * Ids outside the vocabulary are skipped so the renderer's defaults apply (a
 * newer engine file read by an older web UI, say).
 */
export function resolveGemLook(
  appearance: GemAppearance | null | undefined,
  name: string | null | undefined,
  vocabulary: GemLookVocabulary,
): ResolvedGemLook {
  const own: GemLook = (name && appearance?.gems?.[name]) || {};
  const born: GemLook = (name && appearance?.born?.[name]) || {};
  const known = (value: string | undefined, valid: ReadonlySet<string>) =>
    value && valid.has(value) ? value : undefined;
  const look: ResolvedGemLook = {};
  const face = known(own.face, vocabulary.faces) ?? known(born.face, vocabulary.faces);
  const hat = known(own.hat, vocabulary.hats);
  const bornHat = known(born.hat, vocabulary.hats);
  const season = known(appearance?.season, vocabulary.seasons);
  if (face) look.face = face;
  if (hat) look.hat = hat;
  if (bornHat) look.bornHat = bornHat;
  if (season) look.season = season;
  if (isColor(own.color)) look.color = own.color;
  return look;
}

/**
 * A copy of `appearance` with `patch` applied to one gem's picks. An undefined
 * value removes that pick, and a gem left with no picks drops out.
 */
export function withLook(appearance: GemAppearance, name: string, patch: Partial<GemLook>): GemAppearance {
  const next: Record<string, unknown> = { ...appearance.gems?.[name] };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) delete next[key];
    else next[key] = value;
  }
  const gems = { ...appearance.gems };
  if (Object.keys(next).length) gems[name] = next;
  else delete gems[name];
  return { ...appearance, gems };
}
