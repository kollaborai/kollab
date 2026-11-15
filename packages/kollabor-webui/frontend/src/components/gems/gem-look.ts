import type { AvatarSpec, GemAppearance, GemLook, LookColor } from "@/api";

/**
 * The ids a saved look may use (gem-face.ts's eye, hat and season styles), and
 * what an agent the engine never rolled is born with.
 */
export type GemLookVocabulary = {
  faces: ReadonlySet<string>;
  hats: ReadonlySet<string>;
  seasons: ReadonlySet<string>;
  /** Body shapes (gem-specs.ts GemCut); absent, a picked shape is ignored. */
  cuts?: ReadonlySet<string>;
  /** Hair and fur styles, "none" among them; absent, a gem's coats are ignored. */
  hairs?: ReadonlySet<string>;
  furs?: ReadonlySet<string>;
  /** Each coat knob's range (gem-face.ts COAT_KNOBS); absent, knob settings are ignored. */
  knobs?: Readonly<Record<string, readonly [number, number]>>;
  /** Auras and sparkle amounts; absent, a gem's auras are ignored. */
  auras?: ReadonlySet<string>;
  sparkles?: ReadonlySet<string>;
  birthFaces: readonly string[];
  birthHats: readonly string[];
  birthColors: readonly (readonly [number, number, number])[];
  /** The coat a gem is born with (gem_appearance.py BIRTH_*): styles without
   * "none", hair in natural shades, fur in its own or a natural color.
   * Absent, a gem the engine never rolled wears no coat. */
  birthHairs?: readonly string[];
  birthFurs?: readonly string[];
  birthHairColors?: readonly (readonly [number, number, number])[];
  birthFurColors?: readonly LookColor[];
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
  /** The body shape the user picked over the one its name draws. */
  cut?: string;
  /** The avatar the user picked over the roster's. */
  avatar?: AvatarSpec;
  /** Hair and fur, picked over the born look ("none" takes born hair off), and their colors. */
  hair?: string;
  hairColor?: LookColor;
  fur?: string;
  furColor?: LookColor;
  /** The user's settings of their knobs, each held to its range; nothing is born with them. */
  hairKnobs?: Record<string, number>;
  furKnobs?: Record<string, number>;
  /** The user's auras, sparkle amount and aura color; nothing is born with them. */
  auras?: string[];
  sparkles?: string;
  auraColor?: LookColor;
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

/** The share of gems born furry, then born with hair; the rest are born bare.
 * The engine's roll uses the same shares (gem_appearance.py, kept in step by
 * the engine's appearance tests). */
export const BORN_FUR_SHARE = 0.85;
export const BORN_HAIR_SHARE = 0.1;

/**
 * The coat a gem is born with when the engine never rolled one: a fixed draw
 * from its key at the birth odds — furry BORN_FUR_SHARE of keys, then hair
 * BORN_HAIR_SHARE, bare the rest — the same draw the engine's roll makes
 * (gem_appearance.py `_birth_coat`), so a gem dresses alike on every device.
 */
function bornCoat(
  key: string | null | undefined,
  vocabulary: GemLookVocabulary,
): {
  hair?: string;
  /** The birth pools hold readonly shades; the engine's LookColor takes them as number[]. */
  hairColor?: LookColor | readonly [number, number, number];
  fur?: string;
  furColor?: LookColor;
} {
  if (!key) return {};
  const share = fnv(`coat:${key}`) / 2 ** 32;
  if (share < BORN_FUR_SHARE && vocabulary.birthFurs?.length) {
    return {
      fur: drawn(key, "fur", vocabulary.birthFurs),
      furColor: drawn(key, "furColor", vocabulary.birthFurColors ?? []),
    };
  }
  if (share < BORN_FUR_SHARE + BORN_HAIR_SHARE && vocabulary.birthHairs?.length) {
    return {
      hair: drawn(key, "hair", vocabulary.birthHairs),
      hairColor: drawn(key, "hairColor", vocabulary.birthHairColors ?? []),
    };
  }
  return {};
}

/** Hair a crew gem is born with: styles that read over fur (gem_appearance.py CREW_HAIRS). */
export const CREW_HAIRS = ["short", "long", "spiky", "mohawk", "crop", "fringe", "bob", "page", "mane", "plume", "crest", "quiff"] as const;
/** How far apart (0-255 luma) a crew gem's hair and fur shades are (gem_appearance.py CREW_CONTRAST). */
export const CREW_CONTRAST = 80;
const luma = ([red, green, blue]: readonly number[]) => 0.299 * red + 0.587 * green + 0.114 * blue;

/**
 * A crew persona's born coat and hat (gem_appearance.py ``crew_coat``): fur in a
 * natural shade, hair on top in a shade that stands out from it, never a hat.
 * A fixed draw from the key, so it sticks.
 */
export function crewCoat(key: string, vocabulary: GemLookVocabulary): Omit<GemLook, "color"> {
  const shades = vocabulary.birthHairColors ?? [];
  const furColor = drawn(key, "crewFurColor", shades);
  if (!furColor) return { hat: "none" };
  const contrast = shades.filter((shade) => Math.abs(luma(shade) - luma(furColor)) >= CREW_CONTRAST);
  return {
    hat: "none",
    fur: drawn(key, "fur", vocabulary.birthFurs ?? []),
    furColor: [...furColor],
    hair: drawn(key, "crewHair", CREW_HAIRS),
    hairColor: [...(drawn(key, "crewHairColor", contrast) ?? furColor)],
  };
}

const crewShaped = (look: GemLook) =>
  look.hat === "none" && !!look.fur && look.fur !== "none" && !!look.hair && look.hair !== "none";

const isColor = (value: unknown): value is [number, number, number] =>
  Array.isArray(value) &&
  value.length === 3 &&
  value.every((channel) => Number.isInteger(channel) && channel >= 0 && channel <= 255);
const lookColor = (value: unknown): LookColor | undefined => (value === "gem" || isColor(value) ? value : undefined);

/** Known knobs only, each held to its range. */
function knobSettings(value: unknown, ranges: Readonly<Record<string, readonly [number, number]>>) {
  if (!value || typeof value !== "object") return undefined;
  const kept: Record<string, number> = {};
  for (const [knob, setting] of Object.entries(value)) {
    const range = ranges[knob];
    if (Array.isArray(range) && typeof setting === "number" && Number.isFinite(setting)) {
      kept[knob] = Math.min(range[1], Math.max(range[0], setting));
    }
  }
  return Object.keys(kept).length ? kept : undefined;
}

/**
 * One gem's look (`key` from lookKey): the user's Gem Studio picks over the
 * eyes, hat, coat and color it was born with. The engine rolls a pool gem's
 * eyes, hat and coat the first time it sees it alive in its own folder; every
 * other agent is born with the same fixed draw from its key (bornCoat and
 * drawn, mirrored by the engine's roll). Ids outside the vocabulary are
 * skipped so the renderer's defaults apply (a newer engine file read by an
 * older web UI, say).
 */
export function resolveGemLook(
  appearance: GemAppearance | null | undefined,
  key: string | null | undefined,
  vocabulary: GemLookVocabulary,
): ResolvedGemLook {
  const own: GemLook = (key && appearance?.gems?.[key]) || {};
  const recorded: GemLook = (key && appearance?.born?.[key]) || {};
  // A crew persona wears the crew coat and no hat; one rolled before that rule keeps only its eyes.
  const crew = !!key && !!appearance?.crew?.includes(key.split("@")[0]);
  const rolled: GemLook = crew && !crewShaped(recorded) ? { ...recorded, ...crewCoat(key, vocabulary) } : recorded;
  const known = (value: string | undefined, valid: ReadonlySet<string>) =>
    value && valid.has(value) ? value : undefined;
  const look: ResolvedGemLook = {};
  const bornFace = known(rolled.face, vocabulary.faces) ?? (key ? drawn(key, "face", vocabulary.birthFaces) : undefined);
  const face = known(own.face, vocabulary.faces) ?? bornFace;
  const hat = known(own.hat, vocabulary.hats);
  const bornHat = known(rolled.hat, vocabulary.hats) ?? (key && !crew ? drawn(key, "hat", vocabulary.birthHats) : undefined);
  const bornColor = key ? drawn(key, "color", vocabulary.birthColors) : undefined;
  const season = known(appearance?.season, vocabulary.seasons);
  if (face) look.face = face;
  if (hat) look.hat = hat;
  if (bornHat) look.bornHat = bornHat;
  if (season) look.season = season;
  if (isColor(own.color)) look.color = own.color;
  if (bornColor) look.bornColor = [...bornColor];
  if (own.avatar?.kind) look.avatar = own.avatar;
  if (vocabulary.cuts) {
    const cut = known(own.cut, vocabulary.cuts);
    if (cut) look.cut = cut;
  }
  // The engine's coat roll, or the same fixed draw it would make for a gem it
  // never rolled (a roll of bare, hair:"none", is a roll: it is not redrawn).
  const coat = rolled.hair === undefined && rolled.fur === undefined ? bornCoat(key, vocabulary) : {};
  if (vocabulary.hairs) {
    const hair =
      known(own.hair, vocabulary.hairs) ?? known(rolled.hair, vocabulary.hairs) ?? known(coat.hair, vocabulary.hairs);
    const hairColor = lookColor(own.hairColor) ?? lookColor(rolled.hairColor) ?? lookColor(coat.hairColor);
    if (hair) look.hair = hair;
    if (hairColor) look.hairColor = hairColor;
  }
  if (vocabulary.furs) {
    const fur = known(own.fur, vocabulary.furs) ?? known(rolled.fur, vocabulary.furs) ?? known(coat.fur, vocabulary.furs);
    const furColor = lookColor(own.furColor) ?? lookColor(rolled.furColor) ?? lookColor(coat.furColor);
    if (fur) look.fur = fur;
    if (furColor) look.furColor = furColor;
  }
  if (vocabulary.knobs) {
    const hairKnobs = knobSettings(own.hairKnobs, vocabulary.knobs);
    const furKnobs = knobSettings(own.furKnobs, vocabulary.knobs);
    if (hairKnobs) look.hairKnobs = hairKnobs;
    if (furKnobs) look.furKnobs = furKnobs;
  }
  if (vocabulary.auras) {
    const valid = vocabulary.auras;
    const auras = [...new Set((Array.isArray(own.auras) ? own.auras : []).filter((aura) => valid.has(aura)))];
    const sparkles = known(own.sparkles, vocabulary.sparkles ?? new Set());
    const auraColor = lookColor(own.auraColor);
    if (auras.length) look.auras = auras;
    if (sparkles) look.sparkles = sparkles;
    if (auraColor) look.auraColor = auraColor;
  }
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
