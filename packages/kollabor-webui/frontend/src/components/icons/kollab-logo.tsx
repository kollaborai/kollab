import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/** The K as polygons in SVG units (viewBox below); the SVG paths and the 3D glass both come from these. */
export const K_POLYS: ReadonlyArray<ReadonlyArray<readonly [number, number]>> =
  [
    [
      [150, 903.46],
      [821.26, 423],
      [1350, 423],
      [623.56, 813.8],
      [886.78, 1077.02],
      [685.63, 1077.02],
      [384.48, 903.46],
    ],
    [
      [297.89, 726.61],
      [657.18, 469.45],
      [375.29, 469.45],
    ],
  ];
const PATHS = K_POLYS.map(
  (poly) => `M${poly.map(([x, y]) => `${x} ${y}`).join("L")}Z`,
);
const VIEW_BOX = "150 423 1200 654.02";

type Glass = { stop: () => void; flare: () => void };

/**
 * The Kollabor K mark from the kollabor.ai navbar (k-logo-dark.svg), in currentColor.
 * `glass` renders it as a 3D glass K with the electric mist inside (kollab-logo-glass.ts,
 * loaded lazily with three.js). The flat mark shows until the glass is drawing, and stays
 * when WebGL2 is missing, the GPU is a software renderer, or setup fails.
 */
export function KollabLogo({
  className,
  glass = false,
}: {
  className?: string;
  glass?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const glassRef = useRef<Glass | null>(null);
  const [live, setLive] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!glass || !canvas) return;
    let cancelled = false;
    import("./kollab-logo-glass")
      .then(({ startGlass }) => {
        if (cancelled) return;
        // The flat mark hides only once a frame is really on screen, and returns if the GPU context is lost.
        const handle = startGlass(canvas, K_POLYS, {
          onFrame: () => {
            if (!cancelled) setLive(true);
          },
          onLost: () => {
            glassRef.current?.stop();
            glassRef.current = null;
            if (!cancelled) setLive(false);
          },
        });
        if (!handle) return;
        glassRef.current = handle;
      })
      .catch(() => {}); // chunk failed to load: the flat mark stays
    return () => {
      cancelled = true;
      glassRef.current?.stop();
      glassRef.current = null;
      setLive(false);
    };
  }, [glass]);

  const svg = (
    <svg
      aria-hidden="true"
      viewBox={VIEW_BOX}
      className={glass ? cn("h-full w-auto", live && "invisible") : className}
      fill="currentColor"
    >
      {PATHS.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
  if (!glass) return svg;

  return (
    <span
      className={cn("relative inline-block", className)}
      onPointerEnter={() => glassRef.current?.flare()}
    >
      {svg}
      {/* Larger than the mark so the tilted glass is never clipped. */}
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-[-40%] h-[180%] w-[180%]"
      />
    </span>
  );
}
