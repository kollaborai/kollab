import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { cn } from "@/lib/utils";
import { useGemLook, useLookKey } from "./gem-appearance";
import { GEM_PAD, gemEngine, type GemProps } from "./gem-engine";
import { seasonFor, type EyeStyle, type HatStyle, type Season } from "./gem-face";
import { gemSpec, isPoolGem } from "./gem-specs";

export type GemAvatarProps = Omit<GemProps, "size" | "lookKey"> & {
  size?: number;
  className?: string;
  /** Accessible name; without it the avatar is decorative (aria-hidden). */
  label?: string;
  /** Where the agent runs: its folder here, or its device for one on another computer. */
  where?: string | null;
};

/**
 * A pool identity rendered as a live, soft 3D gem with a face (see gem-engine.ts,
 * gem-blob.ts and gem-face.ts). It breathes and morphs at rest, turns toward the
 * pointer, glows from inside while its agent works,
 * pulls out a laptop for long jobs, talks while it streams, fumes on errors,
 * dances when a turn lands, and goes dark and gray when nothing runs it.
 *
 * `size` is the layout box. The gem and its hat are drawn inside it (FIT in
 * gem-engine.ts), so a hat never reaches into the row above; the canvas
 * overhangs the box by GEM_PAD only for the props that leave it (laptop,
 * thought bubble, flying keyboards).
 */
export function GemAvatar({ size = 32, className, label, where, ...own }: GemAvatarProps) {
  // The Gem Studio's saved look fills what the caller left open: an explicit
  // face or hat wins (studio tiles, the dev lab), an "auto" season follows the
  // user's pick, and a picked color replaces the pool's. Each agent has its
  // own look, so a koordinator per project never wears another's.
  const key = useLookKey(own.gem, where);
  const look = useGemLook(key);
  const season = own.season === "auto" ? ((look.season as Season | "auto" | undefined) ?? "auto") : own.season;
  // A seasonal costume covers the hat a gem was born with, not one the user picked.
  const inSeason = (season === "auto" ? seasonFor() : (season ?? "none")) !== "none";
  const gem: Omit<GemProps, "size"> = {
    ...own,
    face: own.face ?? (look.face as EyeStyle | undefined),
    hat: own.hat ?? ((look.hat ?? (inSeason ? undefined : look.bornHat)) as HatStyle | "auto" | undefined),
    season,
    color: look.color ?? own.color ?? (isPoolGem(own.gem) ? undefined : look.bornColor),
    lookKey: key,
  };
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [fallback, setFallback] = useState(false);
  const props: GemProps = { ...gem, size };
  const propsRef = useRef(props);
  propsRef.current = props;

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (!gemEngine.mount(canvas, propsRef.current)) {
      setFallback(true);
      return;
    }
    return () => gemEngine.unmount(canvas);
  }, []);

  const colorKey = gem.color?.join(",") ?? "";
  useEffect(() => {
    if (canvasRef.current) gemEngine.update(canvasRef.current, propsRef.current);
  }, [gem.gem, gem.caste, colorKey, gem.state, gem.live, gem.activity, gem.face, gem.hat, gem.follow, gem.season, gem.motion, key, size]);

  // Sized in rem, so gems scale with the UI around them (styles.css shrinks the
  // root font on mouse-driven screens); the engine still draws at `size` pixels.
  const rem = (px: number) => `${px / 16}rem`;
  const box: CSSProperties = { width: rem(size), height: rem(size) };
  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };

  if (fallback) {
    const rgb = gem.color?.length === 3 ? `rgb(${gem.color.join(",")})` : gemSpec(gem.gem, gem.caste).color;
    return (
      <span
        {...a11y}
        className={cn("inline-block shrink-0", gem.live === false && "opacity-40 grayscale", className)}
        style={{
          ...box,
          background: `radial-gradient(circle at 35% 30%, #fff8 0, ${rgb} 45%, #0006 100%)`,
          borderRadius: "46%",
        }}
      />
    );
  }
  const overhang = ((GEM_PAD - 1) / 2) * size;
  return (
    <span {...a11y} className={cn("relative inline-block shrink-0", className)} style={box}>
      <canvas
        ref={canvasRef}
        className="pointer-events-none absolute block"
        style={{ left: rem(-overhang), top: rem(-overhang), width: rem(size * GEM_PAD), height: rem(size * GEM_PAD) }}
      />
    </span>
  );
}
