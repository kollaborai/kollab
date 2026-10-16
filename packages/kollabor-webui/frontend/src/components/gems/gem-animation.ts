/** Stable motion even when the renderer skips frames or resumes after being hidden. */
export class Spring {
  x: number;
  v = 0;

  constructor(x: number) {
    this.x = x;
  }

  to(target: number, k: number, c: number, dt: number): number {
    const elapsed = Math.min(0.1, Math.max(0, dt));
    const steps = Math.max(1, Math.ceil(elapsed * 120));
    const h = elapsed / steps;
    for (let i = 0; i < steps; i += 1) {
      this.v += (k * (target - this.x) - c * this.v) * h;
      this.x += this.v * h;
    }
    return this.x;
  }

  set(x: number) {
    this.x = x;
    this.v = 0;
  }
}

/** Keep the cadence's remainder instead of rounding every deadline to a display frame. */
export function nextFrameAt(previous: number, now: number, interval: number): number {
  if (!Number.isFinite(interval)) return Infinity;
  if (!Number.isFinite(previous) || previous <= 0 || now - previous > interval) return now + interval;
  return previous + interval;
}

/** Canvas defaults are 300 by 150: checking width alone can silently crop a face. */
export function fitPaintCanvas(canvas: Pick<HTMLCanvasElement, "width" | "height">, px: number) {
  if (canvas.width < px || canvas.height < px) {
    const side = Math.max(canvas.width, canvas.height, px);
    canvas.width = side;
    canvas.height = side;
  }
}

const smooth = (x: number) => {
  const v = Math.min(1, Math.max(0, x));
  return v * v * (3 - 2 * v);
};

/**
 * How full a breath is (0 out .. 1 in) at `p` of its cycle: a slow inhale, a
 * longer exhale, then a short rest. Smooth at every joint, so the body never
 * ticks.
 */
export function breathCurve(p: number): number {
  const u = p - Math.floor(p);
  if (u < 0.4) return smooth(u / 0.4);
  if (u < 0.85) return 1 - smooth((u - 0.4) / 0.45);
  return 0;
}

/** Seconds a blink takes. */
export const BLINK = 0.21;

/** How open the eyes are (1 open .. 0 shut) `phase` seconds into a blink: a quick close, a beat shut, a slower open. */
export function blinkOpen(phase: number): number {
  if (phase < 0 || phase >= BLINK) return 1;
  if (phase < 0.07) return 1 - (phase / 0.07) ** 2;
  if (phase < 0.1) return 0;
  return 1 - (1 - (phase - 0.1) / 0.11) ** 2;
}

/** Seconds of the dance that ends a long stretch of work. */
export const DANCE = 1.8;
/** Seconds of rest between dances when the Dance state loops (Gem Studio's stage). */
const DANCE_REST = 0.7;

/**
 * The dance at `p` (0..1) of DANCE: four bounces with a squash on every
 * landing, a sway to one side and then the other, one full spin through the
 * middle, and the hat jigging a beat behind. `lift` is in body units, `squash`
 * above 0 is wider and shorter, `roll` a lean, `spin` the turn about the
 * vertical and `hat` the hat's tip, both in radians. Every channel starts and
 * ends at rest (the spin a full turn round), so it blends in and out of idle.
 */
export function dance(p: number): { lift: number; squash: number; roll: number; spin: number; hat: number } {
  if (p <= 0 || p >= 1) return { lift: 0, squash: 0, roll: 0, spin: 0, hat: 0 };
  const envelope = smooth(p / 0.08) * smooth((1 - p) / 0.2);
  const up = Math.abs(Math.sin(4 * Math.PI * p));
  return {
    lift: 0.1 * up * envelope,
    squash: (0.07 * (1 - up) ** 3 - 0.03 * up) * envelope,
    roll: 0.16 * Math.sin(2 * Math.PI * p) * envelope,
    spin: (2 * Math.PI * smooth((p - 0.2) / 0.55)) % (2 * Math.PI),
    hat: 0.18 * Math.sin(4 * Math.PI * p - 0.8) * envelope,
  };
}

/**
 * Seconds into the dance on show. After a long turn it is the one dance since
 * it started (`sinceDance`); the Dance state itself loops it with a rest
 * between, counted from when the state began (`since`), so it never stands still.
 */
export function danceClock(looping: boolean, since: number, sinceDance: number): number {
  return looping ? since % (DANCE + DANCE_REST) : sinceDance;
}

/**
 * Seconds between frames for a gem `size` CSS pixels across. Lively gems
 * (working, changing state, under the pointer) get full frames when big;
 * idle ones only need their breath and blinks, so small ones go slower.
 * A strained page halves every rate.
 */
export function frameInterval(size: number, lively: boolean, strained: boolean): number {
  const base = lively ? (size >= 72 ? 1 / 60 : 1 / 30) : size >= 72 ? 1 / 30 : size >= 40 ? 1 / 24 : 1 / 20;
  return strained ? base * 2 : base;
}
