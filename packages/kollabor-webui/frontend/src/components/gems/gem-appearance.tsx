import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { EngineApi, GemAppearance, GemLook, LookColor } from "@/api";
import { AURAS, COAT_KNOBS, EYE_STYLES, FUR_STYLES, HAIR_COLORS, HAIR_STYLES, HAT_STYLES, SEASONS, SPARKLES, type EyeStyle } from "./gem-face";
import { GEM_CUTS } from "./gem-specs";
import {
  BORN_FUR_SHARE,
  BORN_HAIR_SHARE,
  lookKey,
  resolveGemLook,
  type GemLookVocabulary,
  type ResolvedGemLook,
} from "./gem-look";

const everyday = (style: { group: string }) => style.group !== "Halloween" && style.group !== "Christmas";
/** Visually checked birth eyes, in the same order as the engine birth roll. */
export const EVERYDAY_FACES: EyeStyle[] = ["pill", "gleam", "bean", "smile", "drowsy", "kawaii"];
/** The out-of-season hats Random Look draws from. */
export const EVERYDAY_HATS = HAT_STYLES.filter(everyday).map((style) => style.id);

/** The fur colors a birth draws from: the body's own color or a natural shade
 * (gem_appearance.py BIRTH_FUR_COLORS, kept in step by a test). */
const BIRTH_FUR_COLORS: readonly LookColor[] = ["gem", ...HAIR_COLORS.map((shade) => shade.rgb)];

/** Hair or fur for Random Look, at the engine's birth odds (gem_appearance.py HAIR_SHARE, FUR_SHARE). */
export function randomCoat(): Pick<GemLook, "hair" | "hairColor" | "fur" | "furColor"> {
  const roll = Math.random();
  const pick = <T,>(pool: readonly T[]) => pool[Math.floor(Math.random() * pool.length)];
  if (roll < BORN_FUR_SHARE)
    return { hair: "none", hairColor: undefined, fur: pick(FUR_STYLES).id, furColor: pick(BIRTH_FUR_COLORS) };
  if (roll < BORN_FUR_SHARE + BORN_HAIR_SHARE)
    return { hair: pick(HAIR_STYLES).id, hairColor: pick(HAIR_COLORS).rgb, fur: "none", furColor: undefined };
  return { hair: "none", hairColor: undefined, fur: "none", furColor: undefined };
}

/** The studio's color swatches; an agent the pool gives no color is born with one. */
export const GEM_SWATCHES: readonly [number, number, number][] = [
  [200, 30, 50], [255, 120, 100], [240, 160, 40], [240, 220, 80],
  [120, 190, 33], [30, 160, 90], [40, 180, 170], [100, 200, 235],
  [15, 82, 186], [140, 80, 200], [230, 90, 160], [230, 225, 240],
];

export const GEM_LOOK_VOCABULARY: GemLookVocabulary = {
  faces: new Set(EYE_STYLES.map((style) => style.id)),
  hats: new Set(["auto", ...HAT_STYLES.map((style) => style.id)]),
  seasons: new Set(["auto", ...SEASONS.map((season) => season.id)]),
  cuts: new Set(GEM_CUTS),
  hairs: new Set(["none", ...HAIR_STYLES.map((style) => style.id)]),
  furs: new Set(["none", ...FUR_STYLES.map((style) => style.id)]),
  knobs: Object.fromEntries(COAT_KNOBS.map((knob) => [knob.key, [knob.min, knob.max] as const])),
  auras: new Set(AURAS.map((aura) => aura.id)),
  sparkles: new Set(SPARKLES.map((amount) => amount.id)),
  birthFaces: EVERYDAY_FACES,
  birthHats: EVERYDAY_HATS,
  birthColors: GEM_SWATCHES,
  birthHairs: HAIR_STYLES.map((style) => style.id),
  birthFurs: FUR_STYLES.map((style) => style.id),
  birthHairColors: HAIR_COLORS.map((shade) => shade.rgb),
  birthFurColors: BIRTH_FUR_COLORS,
};

type GemAppearanceValue = {
  appearance: GemAppearance | null;
  api: EngineApi | null;
  preview: boolean;
  save: (next: GemAppearance) => Promise<GemAppearance>;
};

const GemAppearanceContext = createContext<GemAppearanceValue | null>(null);

/**
 * Loads every gem's look; every GemAvatar below reads it. A new `refreshKey`
 * (the gems alive) loads it again: the engine rolls a gem's born look the
 * first time it lists the gem alive.
 */
export function GemAppearanceProvider({
  api,
  refreshKey,
  children,
}: {
  api: EngineApi;
  refreshKey?: string;
  children: ReactNode;
}) {
  const [appearance, setAppearance] = useState<GemAppearance | null>(null);
  useEffect(() => {
    let cancelled = false;
    // An engine without the route keeps every gem in its built-in look.
    api.getGemAppearance().then(
      (saved) => {
        if (!cancelled) setAppearance(saved);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [api, refreshKey]);
  const save = useCallback(
    async (next: GemAppearance) => {
      const saved = await api.saveGemAppearance(next);
      setAppearance(saved);
      return saved;
    },
    [api],
  );
  const value = useMemo(() => ({ appearance, save, api, preview: false }), [appearance, save, api]);
  return <GemAppearanceContext.Provider value={value}>{children}</GemAppearanceContext.Provider>;
}

/** Gems inside render `appearance` instead of the saved looks: the studio's draft. */
export function GemAppearancePreview({
  appearance,
  children,
}: {
  appearance: GemAppearance;
  children: ReactNode;
}) {
  const parent = useContext(GemAppearanceContext);
  const value = useMemo(
    () => ({ appearance, save: parent?.save ?? (async () => appearance), api: parent?.api ?? null, preview: true }),
    [appearance, parent?.save, parent?.api],
  );
  return <GemAppearanceContext.Provider value={value}>{children}</GemAppearanceContext.Provider>;
}

export function useGemAppearance(): GemAppearanceValue | null {
  return useContext(GemAppearanceContext);
}

/** The key one agent's look is saved under (see lookKey); the bare name outside a provider. */
export function useLookKey(name: string | null | undefined, where?: string | null): string {
  const home = useContext(GemAppearanceContext)?.appearance?.home;
  return lookKey(name || "", where, home);
}

/** One agent's look, by its key; the born draw alone outside a provider (the dev gem lab). */
export function useGemLook(key: string | null | undefined): ResolvedGemLook {
  const appearance = useContext(GemAppearanceContext)?.appearance;
  return useMemo(() => resolveGemLook(appearance, key, GEM_LOOK_VOCABULARY), [appearance, key]);
}
