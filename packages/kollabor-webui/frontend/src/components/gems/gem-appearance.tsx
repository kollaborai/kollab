import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { EngineApi, GemAppearance } from "@/api";
import { EYE_STYLES, HAT_STYLES, SEASONS } from "./gem-face";
import { lookKey, resolveGemLook, type GemLookVocabulary, type ResolvedGemLook } from "./gem-look";

const everyday = (style: { group: string }) => style.group !== "Halloween" && style.group !== "Christmas";
/** The out-of-season eyes and hats Random Look draws from (the engine's birth roll uses the same). */
export const EVERYDAY_FACES = EYE_STYLES.filter(everyday).map((style) => style.id);
export const EVERYDAY_HATS = HAT_STYLES.filter(everyday).map((style) => style.id);

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
  birthFaces: EVERYDAY_FACES,
  birthHats: EVERYDAY_HATS,
  birthColors: GEM_SWATCHES,
};

type GemAppearanceValue = {
  appearance: GemAppearance | null;
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
  const value = useMemo(() => ({ appearance, save }), [appearance, save]);
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
    () => ({ appearance, save: parent?.save ?? (async () => appearance) }),
    [appearance, parent?.save],
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
