import { TAU, WHITE, mix, rgba, type Aura, type Rgb } from "./gem-face";

/**
 * Auras around a gem, drawn in its 2D pass (gem-engine.ts paint2d) in one
 * aura color: a glow and flames behind the body, orbs circling it (behind on
 * the far side, in front on the near one), and sparkles twinkling around it.
 * `strength` is how hard the agent works: about a third while it idles, 1
 * busy, 0 offline (nothing is drawn).
 */
export type AuraPaint = {
  ctx: CanvasRenderingContext2D;
  t: number;
  /** The body's middle and the gem frame's size, canvas pixels. */
  cx: number;
  cy: number;
  g: number;
  rgb: Rgb;
  strength: number;
  auras: readonly Aura[];
  seed: number;
};

/** A sparkle around the gem: x, y in gem frames from its middle, age and life in seconds. */
export type Spark = { x: number; y: number; age: number; ttl: number; size: number };

const FLAMES = 11;
const ORBS = 3;

export function drawAuraBehind(a: AuraPaint) {
  if (a.strength <= 0.01) return;
  if (a.auras.includes("glow")) glow(a);
  if (a.auras.includes("flames")) flames(a);
  if (a.auras.includes("orbit")) orbit(a, false);
}

export function drawAuraFront(a: AuraPaint) {
  if (a.strength <= 0.01) return;
  if (a.auras.includes("orbit")) orbit(a, true);
}

function glow({ ctx, t, cx, cy, g, rgb, strength }: AuraPaint) {
  // Breathes slowly at rest, quicker at work.
  const r = g * (0.6 + 0.1 * strength) * (1 + 0.06 * Math.sin(t * (1.2 + 2.4 * strength)));
  const halo = ctx.createRadialGradient(cx, cy, g * 0.26, cx, cy, r);
  halo.addColorStop(0, rgba(rgb, 0.3 + 0.35 * strength));
  halo.addColorStop(0.45, rgba(rgb, 0.12 + 0.2 * strength));
  halo.addColorStop(1, rgba(rgb, 0));
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, TAU);
  ctx.fill();
}

/** Tongues of fire around the top and sides, each with a hot core over a warm glow; the body hides their roots. */
function flames({ ctx, t, cx, cy, g, rgb, strength, seed }: AuraPaint) {
  const hot = mix(rgb, WHITE, 0.7);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const warm = ctx.createRadialGradient(cx, cy - g * 0.12, g * 0.2, cx, cy - g * 0.12, g * 0.66);
  warm.addColorStop(0, rgba(rgb, 0.22 + 0.2 * strength));
  warm.addColorStop(1, rgba(rgb, 0));
  ctx.fillStyle = warm;
  ctx.beginPath();
  ctx.arc(cx, cy - g * 0.12, g * 0.66, 0, TAU);
  ctx.fill();
  for (let i = 0; i < FLAMES; i += 1) {
    // From the right side, over the top, to the left side; tallest on top.
    const around = Math.PI * (0.04 + (0.92 * i) / (FLAMES - 1));
    const x = cx + Math.cos(around) * g * 0.36;
    const y = cy - Math.sin(around) * g * 0.34 + g * 0.06;
    const flicker = 0.72 + 0.28 * Math.sin(t * (7.1 + i * 0.9) + seed + i * 1.7) * Math.sin(t * 2.9 + i * 0.6);
    const h = g * (0.17 + 0.16 * strength) * flicker * (0.7 + 0.3 * Math.sin(around));
    const w = g * 0.1;
    const sway = Math.sin(t * 3.7 + i * 2.3) * w * 0.5;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(Math.cos(around) * 0.4);
    // The flame, then its hot core.
    for (const [k, base, tip] of [
      [1, rgba(rgb, 0.55 + 0.35 * strength), rgba(hot, 0)],
      [0.55, rgba(hot, 0.5 + 0.4 * strength), rgba(WHITE, 0)],
    ] as const) {
      const fill = ctx.createLinearGradient(0, 0, 0, -h * k);
      fill.addColorStop(0, base);
      fill.addColorStop(1, tip);
      ctx.fillStyle = fill;
      tongue(ctx, w * k, h * k, sway * k);
    }
    ctx.restore();
  }
  ctx.restore();
}

/** One flame from (0, 0) up: a wide rounded base narrowing to a tip bent by `sway`. */
function tongue(ctx: CanvasRenderingContext2D, w: number, h: number, sway: number) {
  ctx.beginPath();
  ctx.moveTo(-w / 2, 0);
  ctx.bezierCurveTo(-w * 0.62, -h * 0.35, -w * 0.12 + sway * 0.5, -h * 0.62, sway, -h);
  ctx.bezierCurveTo(w * 0.22 + sway * 0.5, -h * 0.6, w * 0.62, -h * 0.35, w / 2, 0);
  ctx.quadraticCurveTo(0, w * 0.45, -w / 2, 0);
  ctx.fill();
}

/** Orbs on a tilted ring around the gem: the near half passes in front of it, the far half behind. */
function orbit({ ctx, t, cx, cy, g, rgb, strength, seed }: AuraPaint, front: boolean) {
  const speed = 0.6 + 1.8 * strength;
  const tilt = -0.28;
  const core = mix(rgb, WHITE, 0.6);
  for (let i = 0; i < ORBS; i += 1) {
    const turn = t * speed + seed + (i * TAU) / ORBS;
    const depth = Math.sin(turn);
    if (depth >= 0 !== front) continue;
    const ex = Math.cos(turn) * g * 0.6;
    const ey = depth * g * 0.14;
    const x = cx + ex * Math.cos(tilt) - ey * Math.sin(tilt);
    const y = cy + 0.05 * g + ex * Math.sin(tilt) + ey * Math.cos(tilt);
    const r = g * 0.036 * (1 + 0.25 * depth);
    const halo = ctx.createRadialGradient(x, y, 0, x, y, r * 3);
    halo.addColorStop(0, rgba(rgb, 0.5));
    halo.addColorStop(1, rgba(rgb, 0));
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(x, y, r * 3, 0, TAU);
    ctx.fill();
    ctx.fillStyle = rgba(core, 0.95);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, TAU);
    ctx.fill();
  }
}

/** Four-point sparkles twinkling around the gem, in the aura color. */
export function drawSparks(ctx: CanvasRenderingContext2D, sparks: readonly Spark[], cx: number, cy: number, g: number, rgb: Rgb) {
  if (!sparks.length) return;
  const light = mix(rgb, WHITE, 0.55);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (const spark of sparks) {
    const k = Math.sin((Math.PI * spark.age) / spark.ttl);
    const r = spark.size * g * k;
    if (r < 0.5) continue;
    const x = cx + spark.x * g;
    const y = cy + spark.y * g;
    ctx.fillStyle = rgba(light, 0.95 * k);
    ctx.beginPath();
    ctx.moveTo(x, y - r);
    ctx.quadraticCurveTo(x, y, x + r * 0.7, y);
    ctx.quadraticCurveTo(x, y, x, y + r);
    ctx.quadraticCurveTo(x, y, x - r * 0.7, y);
    ctx.quadraticCurveTo(x, y, x, y - r);
    ctx.fill();
  }
  ctx.restore();
}

/** Sparkles a second around the gem for a pick, at rest and at work. */
export const SPARKS_PER_SECOND = { few: [1.2, 3], lots: [6, 14] } as const;
