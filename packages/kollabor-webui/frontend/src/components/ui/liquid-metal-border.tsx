import { useEffect, useRef, type ReactNode } from "react";
import type { ShaderMount } from "@paper-design/shaders";

import { cn } from "@/lib/utils";

/**
 * Liquid-metal border, the same effect as Mentiko's login card
 * (web/components/ui/liquid-metal-border.tsx). The shader fills a layer behind
 * the content and an opaque inner panel, inset by `borderWidth`, covers the
 * centre, so the metal shows only as a rim around every edge. The shader is
 * @paper-design/shaders (PolyForm Shield), listed in public/THIRD_PARTY_NOTICES.txt.
 */

const UNIFORMS: Record<string, number> = {
  u_repetition: 4,
  u_softness: 0.5,
  u_shiftRed: 0.3,
  u_shiftBlue: 0.3,
  u_distortion: 0,
  u_contour: 0,
  u_angle: 45,
  u_scale: 8,
  u_shape: 1,
  u_offsetX: 0.1,
  u_offsetY: -0.1,
};

// Static metal shown until the shader starts, or for good when WebGL is unavailable.
const FALLBACK_METAL =
  "conic-gradient(from 210deg, #2b2c30, #d9dde3 12%, #6c7079 27%, #f4f6fa 44%, #3a3d44 60%, #b8bcc4 78%, #2b2c30)";

export function LiquidMetalBorder({
  children,
  className,
  frameClassName,
  radius = 10,
  borderWidth = 2,
  speed = 0.6,
}: {
  children: ReactNode;
  /** Classes for the inner content panel; give it an opaque background. */
  className?: string;
  /** Classes for the outer frame, e.g. to size it inside a flex parent. */
  frameClassName?: string;
  /** Outer corner radius in px; the panel gets `radius - borderWidth`. */
  radius?: number;
  /** Rim thickness in px. */
  borderWidth?: number;
  /** Shader animation speed; 0 holds one frame. */
  speed?: number;
}) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let mount: ShaderMount | null = null;
    let cancelled = false;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    // ponytail: the shader paints the whole frame though only the rim shows;
    // drop maxPixelCount (8th arg) if a big frame costs too much GPU.
    import("@paper-design/shaders")
      .then(({ ShaderMount, liquidMetalFragmentShader }) => {
        if (cancelled || !hostRef.current) return;
        mount = new ShaderMount(
          hostRef.current,
          liquidMetalFragmentShader,
          UNIFORMS,
          undefined,
          still ? 0 : speed,
        );
        Object.assign(mount.canvasElement.style, {
          position: "absolute",
          inset: "0",
          width: "100%",
          height: "100%",
          display: "block",
        });
      })
      .catch(() => {
        // No WebGL: the fallback gradient stays.
      });
    return () => {
      cancelled = true;
      mount?.dispose();
    };
  }, [speed]);

  return (
    <div
      className={cn("relative isolate", frameClassName)}
      style={{ borderRadius: radius, padding: borderWidth, background: FALLBACK_METAL }}
    >
      <div
        ref={hostRef}
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden"
        style={{ borderRadius: radius, zIndex: 0 }}
      />
      <div
        className={cn("relative", className)}
        style={{ borderRadius: radius - borderWidth, zIndex: 1 }}
      >
        {children}
      </div>
    </div>
  );
}
