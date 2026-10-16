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
