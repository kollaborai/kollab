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
import { resolveGemLook, type GemLookVocabulary, type ResolvedGemLook } from "./gem-look";

const everyday = (style: { group: string }) => style.group !== "Halloween" && style.group !== "Christmas";
/** The out-of-season eyes and hats that Mixed and Random Look draw from. */
export const MIXED_FACES = EYE_STYLES.filter(everyday).map((style) => style.id);
export const MIXED_HATS = HAT_STYLES.filter(everyday).map((style) => style.id);

export const GEM_LOOK_VOCABULARY: GemLookVocabulary = {
  faces: new Set(EYE_STYLES.map((style) => style.id)),
  hats: new Set(["auto", ...HAT_STYLES.map((style) => style.id)]),
  seasons: new Set(["auto", ...SEASONS.map((season) => season.id)]),
  mixedFaces: MIXED_FACES,
};

type GemAppearanceValue = {
  appearance: GemAppearance | null;
  save: (next: GemAppearance) => Promise<GemAppearance>;
};

const GemAppearanceContext = createContext<GemAppearanceValue | null>(null);

/** Loads the Gem Studio's saved looks once; every GemAvatar below reads them. */
export function GemAppearanceProvider({ api, children }: { api: EngineApi; children: ReactNode }) {
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
  }, [api]);
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

/** The saved look for one gem; empty outside a provider (the dev gem lab). */
export function useGemLook(name: string | null | undefined): ResolvedGemLook {
  const appearance = useContext(GemAppearanceContext)?.appearance;
  return useMemo(() => resolveGemLook(appearance, name, GEM_LOOK_VOCABULARY), [appearance, name]);
}
