import type { GemAppearance, GemLook } from "@/api";

/**
 * The ids a saved look may use (gem-face.ts's eye, hat and season styles), and
 * what an agent the engine never rolled is born with.
 */
export type GemLookVocabulary = {
  faces: ReadonlySet<string>;
  hats: ReadonlySet<string>;
  seasons: ReadonlySet<string>;
  birthFaces: readonly string[];
  birthHats: readonly string[];
  birthColors: readonly (readonly [number, number, number])[];
};

export type ResolvedGemLook = {
  face?: string;
  /** A hat the user picked: it beats a seasonal costume. */
  hat?: string;
  /** The hat the gem was born with: a seasonal costume covers it. */
  bornHat?: string;
  season?: string;
  color?: [number, number, number];
  /** Worn when neither the user nor the pool gives it a color. */
  bornColor?: [number, number, number];
};

/**
 * Whose look it is. A name repeats across folders and computers (a
 * koordinator per project), and each of those agents dresses on its own. The
 * bare name is the one in the engine's own folder (`home`), where the launch
 * list's gems start; `name@folder` is one started in another folder here, and
 * `name@device` one on another computer.
 */
export function lookKey(name: string, where: string | null | undefined, home: string | null | undefined): string {
  return name && where && where !== home ? `${name}@${where}` : name;
}

function fnv(text: string): number {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/**
 * A fixed draw from the key: the option that scores highest for it. A style
 * added later takes only the agents it now wins, so born looks stick when the
 * vocabulary grows (a plain hash % length would re-dress everyone).
 */
export function drawn<T>(key: string, salt: string, pool: readonly T[]): T | undefined {
  let best: T | undefined;
  let bestScore = -1;
  for (const option of pool) {
    const score = fnv(`${salt}:${key}:${String(option)}`);
    if (score > bestScore) {
      best = option;
      bestScore = score;
    }
  }
  return best;
}

const isColor = (value: unknown): value is [number, number, number] =>
  Array.isArray(value) &&
  value.length === 3 &&
  value.every((channel) => Number.isInteger(channel) && channel >= 0 && channel <= 255);

/**
 * One gem's look (`key` from lookKey): the user's Gem Studio picks over the
 * eyes, hat and color it was born with. The engine rolls a pool gem's eyes
 * and hat the first time it sees it alive in its own folder; every other
 * agent is born with a fixed draw from its key. Ids outside the vocabulary are
 * skipped so the renderer's defaults apply (a newer engine file read by an
 * older web UI, say).
 */
export function resolveGemLook(
  appearance: GemAppearance | null | undefined,
  key: string | null | undefined,
  vocabulary: GemLookVocabulary,
): ResolvedGemLook {
  const own: GemLook = (key && appearance?.gems?.[key]) || {};
  const rolled: GemLook = (key && appearance?.born?.[key]) || {};
  const known = (value: string | undefined, valid: ReadonlySet<string>) =>
    value && valid.has(value) ? value : undefined;
  const look: ResolvedGemLook = {};
  const bornFace = known(rolled.face, vocabulary.faces) ?? (key ? drawn(key, "face", vocabulary.birthFaces) : undefined);
  const face = known(own.face, vocabulary.faces) ?? bornFace;
  const hat = known(own.hat, vocabulary.hats);
  const bornHat = known(rolled.hat, vocabulary.hats) ?? (key ? drawn(key, "hat", vocabulary.birthHats) : undefined);
  const bornColor = key ? drawn(key, "color", vocabulary.birthColors) : undefined;
  const season = known(appearance?.season, vocabulary.seasons);
  if (face) look.face = face;
  if (hat) look.hat = hat;
  if (bornHat) look.bornHat = bornHat;
  if (season) look.season = season;
  if (isColor(own.color)) look.color = own.color;
  if (bornColor) look.bornColor = [...bornColor];
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
