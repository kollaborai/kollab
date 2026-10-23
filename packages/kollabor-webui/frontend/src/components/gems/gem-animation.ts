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

/**
 * Seconds a tool's look stays up once shown. A call that lands in 50 ms
 * still reads as a beat at the laptop instead of yanking it in and out.
 */
export const TOOL_HOLD = 0.9;

/** Seconds in one round of the typing routine (each gem runs a little faster or slower). */
export const FRENZY = 3.4;

const ease = (a: number, b: number, p: number) => a + (b - a) * smooth(p);

/**
 * Typing as a performance, in rounds: nose to the screen reading what is
 * there, a jerk back with hands up ("wait..."), a slam back down onto the
 * keys, then hammering at them with a stomp on every keystroke. `lean` is
 * toward the screen (-1 pulled back .. 1 nose on it), `pitch` nods down onto
 * the keys, `lift` and `squash` are the stomps, `roll` the shake, `hands`
 * how hard the hands hammer (0..1) and `raise` how far they are up off the
 * keys. The first round starts from rest; later ones flow out of the last.
 */
export function frenzy(
  since: number,
  seed = 0,
): { lean: number; pitch: number; lift: number; squash: number; roll: number; hands: number; raise: number } {
  const span = FRENZY * (0.9 + 0.2 * ((seed / (2 * Math.PI)) % 1));
  const u = Math.max(0, since) % span;
  // A later round leans in from wherever the hammering left the body.
  const from = since < span ? REST : hammer(span - 1.45);
  if (u < 0.9) {
    const p = u / 0.35;
    return {
      lean: ease(from.lean, 1, p),
      pitch: ease(from.pitch, 0.04, p),
      lift: ease(from.lift, 0, p),
      squash: ease(from.squash, 0, p),
      roll: ease(from.roll, 0, p),
      hands: 0.25,
      raise: 0,
    };
  }
  if (u < 1.25) {
    const p = (u - 0.9) / 0.18;
    return { lean: ease(1, -0.7, p), pitch: ease(0.04, -0.2, p), lift: ease(0, 0.045, p), squash: ease(0, -0.03, p), roll: 0, hands: 0, raise: smooth(p) };
  }
  if (u < 1.45) {
    const p = (u - 1.25) / 0.2;
    const impact = p > 0.7 ? Math.sin(((p - 0.7) / 0.3) * Math.PI) : 0;
    return {
      lean: ease(-0.7, 0.35, p),
      pitch: ease(-0.2, 0.06, p),
      lift: ease(0.045, -0.02, p),
      squash: ease(-0.03, 0, p) + 0.09 * impact,
      roll: 0,
      hands: p,
      raise: 1 - smooth(p),
    };
  }
  return hammer(u - 1.45);
}

const REST = { lean: 0, pitch: 0, lift: 0, squash: 0, roll: 0, hands: 0, raise: 0 };

/** Hammering the keys `k` seconds in: a stomp about eight times a second. */
function hammer(k: number) {
  const up = Math.abs(Math.sin(Math.PI * 8 * k));
  return {
    lean: 0.35 + 0.08 * Math.sin(k * 5),
    pitch: 0.06 + 0.025 * up,
    lift: 0.02 * up - 0.02,
    squash: 0.04 * (1 - up) ** 3,
    roll: 0.025 * Math.sin(k * 31),
    hands: 1,
    raise: 0,
  };
}

/**
 * Thinking out loud with the whole body: the head rolls from side to side and
 * tips up at the ideas, the weight sways, and every few seconds a quick
 * "hmm!" double nod. Away from the laptop only; at it the gem peers instead.
 */
export function ponder(since: number, seed = 0): { pitch: number; roll: number; lift: number; slide: number } {
  const t = since + seed;
  const c = (since + 1.2) % 2.9;
  const hmm = c < 0.5 ? Math.sin((c / 0.5) * 2 * Math.PI) * Math.sin((c / 0.5) * Math.PI) : 0;
  return {
    pitch: -0.05 - 0.05 * Math.sin(t * 0.7) + 0.09 * hmm,
    roll: 0.14 * Math.sin(t * 1.1),
    lift: 0.022 * Math.sin(t * 2.2) ** 2,
    slide: 0.05 * Math.sin(t * 0.55),
  };
}

/**
 * At the laptop between keystrokes (reading, or thinking over a tool's
 * result): face pressed up close to the screen, nodding along line by line,
 * now and then sitting back to take it in before leaning in again.
 */
export function peer(since: number, seed = 0): { lean: number; pitch: number } {
  const t = since + seed;
  const c = (since + seed) % 4.2;
  const back = c > 3.3 ? Math.sin(((c - 3.3) / 0.9) * Math.PI) : 0;
  const into = smooth(since / 0.4);
  return {
    lean: into * (0.85 + 0.1 * Math.sin(t * 0.9)) - 1.2 * back,
    pitch: into * (0.03 + 0.025 * Math.sin(t * 3.4)) - 0.12 * back,
  };
}
