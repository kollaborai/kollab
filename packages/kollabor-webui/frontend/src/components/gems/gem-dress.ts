/**
 * How a gem is dressed and what floats around it: hats (everyday, Halloween,
 * Christmas), seasonal dressing (holiday lights, bats, a dangling spider,
 * snow) and activity effects (thought bubble, alert badges, zzz, notes). The
 * A few gem-flavor touches live here too: a resting gem sits in a bubble, a
 * gem that drops offline poofs first, and a booting gem regenerates in a
 * column of light.
 */

import { TAU, WHITE, hue, mix, rgba, star, workPhase, type FaceFrame, type HatStyle } from "./gem-face";

/**
 * Where a hat goes, in canvas pixels (the engine reads it off the head's
 * silhouette, gem-engine.ts fitHat): the middle of its band, the head's up
 * direction and the hat's scale.
 */
export type HatFit = {
  x: number;
  y: number;
  /** Radians the head's up leans from the canvas's. */
  angle: number;
  /** Pixels per hat unit. */
  k: number;
  /** Below 1 when a tall hat is pressed down to stay inside the layout box. */
  squash: number;
  /** The head's sides at ear height and its crown, for headphones. */
  ears: { lx: number; ly: number; rx: number; ry: number; tx: number; ty: number };
};

/**
 * Each hat's band half-width in its own units, the share of the head's widest
 * half-width the band opens to (a beanie pulls down over the crown, a top hat
 * perches), and how far the hat rises above its band.
 */
export const HAT_SIZES: Record<Exclude<HatStyle, "none">, { band: number; fit: number; rise: number }> = {
  party: { band: 0.15, fit: 0.36, rise: 0.41 },
  top: { band: 0.125, fit: 0.42, rise: 0.27 },
  beanie: { band: 0.21, fit: 0.84, rise: 0.25 },
  crown: { band: 0.19, fit: 0.46, rise: 0.24 },
  wizard: { band: 0.17, fit: 0.42, rise: 0.48 },
  cap: { band: 0.18, fit: 0.8, rise: 0.15 },
  headphones: { band: 0.3, fit: 1, rise: 0.06 },
  hardhat: { band: 0.2, fit: 0.84, rise: 0.17 },
  beret: { band: 0.21, fit: 0.74, rise: 0.12 },
  witch: { band: 0.15, fit: 0.4, rise: 0.5 },
  horns: { band: 0.15, fit: 0.56, rise: 0.21 },
  pumpkin: { band: 0.17, fit: 0.4, rise: 0.26 },
  santa: { band: 0.19, fit: 0.78, rise: 0.34 },
  antlers: { band: 0.14, fit: 0.56, rise: 0.31 },
  elf: { band: 0.17, fit: 0.6, rise: 0.44 },
};

function headphones(f: FaceFrame, fit: HatFit) {
  const { ctx } = f;
  const { lx, ly, rx, ry, ty } = fit.ears;
  const w = Math.hypot(rx - lx, ry - ly);
  const cup = w * 0.13;
  // The band arcs over the crown from cup to cup.
  ctx.strokeStyle = "#1b1c22";
  ctx.lineWidth = Math.max(1.2, w * 0.06);
  ctx.beginPath();
  ctx.moveTo(lx, ly - cup * 0.9);
  ctx.bezierCurveTo(lx - cup * 0.2, ty - cup * 1.2, rx + cup * 0.2, ty - cup * 1.2, rx, ry - cup * 0.9);
  ctx.stroke();
  for (const [x, y, side] of [[lx, ly, -1], [rx, ry, 1]] as const) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(fit.angle);
    ctx.fillStyle = "#16171c";
    ctx.beginPath();
    ctx.roundRect(-cup * 0.55 + side * cup * 0.12, -cup * 0.95, cup * 1.1, cup * 1.9, cup * 0.45);
    ctx.fill();
    ctx.strokeStyle = rgba(mix(f.rgb, WHITE, 0.4));
    ctx.lineWidth = Math.max(0.8, cup * 0.16);
    ctx.beginPath();
    ctx.roundRect(-cup * 0.38 + side * cup * 0.12, -cup * 0.75, cup * 0.76, cup * 1.5, cup * 0.32);
    ctx.stroke();
    ctx.restore();
  }
}

export function drawHat(f: FaceFrame, fit: HatFit) {
  if (f.hat === "none") return;
  const { ctx } = f;
  if (f.hat === "headphones") {
    headphones(f, fit);
    return;
  }
  const k = fit.k;
  const accent = `hsl(${(hue(f.rgb) + 150) % 360}, 82%, 58%)`;
  ctx.save();
  ctx.translate(fit.x, fit.y);
  ctx.rotate(fit.angle);
  ctx.scale(1, fit.squash);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.lineWidth = Math.max(0.8, k * 0.012);
  ctx.strokeStyle = "rgba(0,0,0,0.35)";
  switch (f.hat) {
    case "party": {
      ctx.fillStyle = accent;
      ctx.beginPath();
      ctx.moveTo(-0.15 * k, 0);
      ctx.lineTo(0, -0.36 * k);
      ctx.lineTo(0.15 * k, 0);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.save();
      ctx.clip();
      ctx.strokeStyle = "rgba(255,255,255,0.75)";
      ctx.lineWidth = 0.035 * k;
      for (let i = 0; i < 4; i += 1) {
        ctx.beginPath();
        ctx.moveTo(-0.2 * k, -i * 0.1 * k);
        ctx.lineTo(0.2 * k, -i * 0.1 * k - 0.1 * k);
        ctx.stroke();
      }
      ctx.restore();
      ctx.fillStyle = "#fff6c9";
      ctx.beginPath();
      ctx.arc(0, -0.37 * k, 0.045 * k, 0, TAU);
      ctx.fill();
      break;
    }
    case "top": {
      ctx.fillStyle = "#141418";
      ctx.beginPath();
      ctx.ellipse(0, 0, 0.21 * k, 0.045 * k, 0, 0, TAU);
      ctx.fill();
      ctx.beginPath();
      ctx.roundRect(-0.125 * k, -0.27 * k, 0.25 * k, 0.27 * k, 0.02 * k);
      ctx.fill();
      ctx.fillStyle = rgba(f.rgb);
      ctx.fillRect(-0.125 * k, -0.07 * k, 0.25 * k, 0.05 * k);
      ctx.fillStyle = "rgba(255,255,255,0.12)";
      ctx.fillRect(-0.1 * k, -0.26 * k, 0.04 * k, 0.18 * k);
      break;
    }
    case "beanie": {
      ctx.fillStyle = `hsl(${(hue(f.rgb) + 30) % 360}, 62%, 50%)`;
      ctx.beginPath();
      ctx.ellipse(0, -0.02 * k, 0.19 * k, 0.18 * k, 0, Math.PI, TAU);
      ctx.fill();
      ctx.fillStyle = `hsl(${(hue(f.rgb) + 30) % 360}, 62%, 40%)`;
      ctx.beginPath();
      ctx.roundRect(-0.21 * k, -0.06 * k, 0.42 * k, 0.08 * k, 0.03 * k);
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.18)";
      for (let i = -4; i <= 4; i += 1) {
        ctx.beginPath();
        ctx.moveTo(i * 0.045 * k, -0.055 * k);
        ctx.lineTo(i * 0.045 * k, 0.015 * k);
        ctx.stroke();
      }
      ctx.fillStyle = "#f4f1ea";
      ctx.beginPath();
      ctx.arc(0, -0.22 * k, 0.05 * k, 0, TAU);
      ctx.fill();
      break;
    }
    case "crown": {
      const gold = ctx.createLinearGradient(0, -0.22 * k, 0, 0);
      gold.addColorStop(0, "#fff1a8");
      gold.addColorStop(1, "#d39b12");
      ctx.fillStyle = gold;
      ctx.strokeStyle = "#8a6400";
      ctx.beginPath();
      ctx.moveTo(-0.18 * k, 0);
      ctx.lineTo(-0.2 * k, -0.2 * k);
      ctx.lineTo(-0.09 * k, -0.1 * k);
      ctx.lineTo(0, -0.24 * k);
      ctx.lineTo(0.09 * k, -0.1 * k);
      ctx.lineTo(0.2 * k, -0.2 * k);
      ctx.lineTo(0.18 * k, 0);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      for (const [color, x] of [["#e0303c", -0.1], [rgba(f.rgb), 0], ["#2f6fe0", 0.1]] as const) {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(x * k, -0.045 * k, 0.026 * k, 0, TAU);
        ctx.fill();
      }
      break;
    }
    case "wizard": {
      ctx.fillStyle = "#4a2a8f";
      ctx.beginPath();
      ctx.moveTo(-0.17 * k, 0);
      ctx.quadraticCurveTo(-0.02 * k, -0.3 * k, 0.12 * k, -0.48 * k);
      ctx.quadraticCurveTo(0.06 * k, -0.24 * k, 0.17 * k, 0);
      ctx.closePath();
      ctx.fill();
      ctx.beginPath();
      ctx.ellipse(0, 0, 0.25 * k, 0.045 * k, 0, 0, TAU);
      ctx.fill();
      ctx.fillStyle = "#ffd84d";
      for (const [x, y, r] of [[-0.04, -0.12, 0.03], [0.05, -0.26, 0.022], [-0.08, -0.04, 0.018]]) {
        star(ctx, x * k, y * k, r * k);
      }
      break;
    }
    case "cap": {
      ctx.fillStyle = accent;
      ctx.beginPath();
      ctx.ellipse(0, 0, 0.18 * k, 0.14 * k, 0, Math.PI, TAU);
      ctx.fill();
      ctx.beginPath();
      ctx.ellipse(0.17 * k, -0.005 * k, 0.15 * k, 0.035 * k, 0.08, 0, TAU);
      ctx.fill();
      ctx.fillStyle = "rgba(0,0,0,0.25)";
      ctx.beginPath();
      ctx.arc(0, -0.14 * k, 0.022 * k, 0, TAU);
      ctx.fill();
      break;
    }
    case "hardhat": {
      ctx.fillStyle = "#f6c419";
      ctx.beginPath();
      ctx.ellipse(0, 0, 0.2 * k, 0.16 * k, 0, Math.PI, TAU);
      ctx.fill();
      ctx.stroke();
      ctx.beginPath();
      ctx.ellipse(0, 0, 0.26 * k, 0.035 * k, 0, 0, TAU);
      ctx.fill();
      ctx.stroke();
      ctx.strokeStyle = "rgba(0,0,0,0.25)";
      ctx.beginPath();
      ctx.moveTo(0, -0.16 * k);
      ctx.lineTo(0, -0.02 * k);
      ctx.stroke();
      break;
    }
    case "beret": {
      ctx.fillStyle = "#b3202a";
      ctx.beginPath();
      ctx.ellipse(-0.03 * k, -0.04 * k, 0.22 * k, 0.075 * k, -0.15, 0, TAU);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(-0.03 * k, -0.12 * k, 0.018 * k, 0, TAU);
      ctx.fill();
      break;
    }
    case "witch": {
      ctx.fillStyle = "#1d1630";
      ctx.strokeStyle = "rgba(170,140,230,0.55)";
      ctx.beginPath();
      ctx.ellipse(0, 0, 0.28 * k, 0.05 * k, 0, 0, TAU);
      ctx.fill();
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(-0.15 * k, -0.01 * k);
      ctx.quadraticCurveTo(-0.08 * k, -0.25 * k, 0.02 * k, -0.42 * k);
      ctx.quadraticCurveTo(0.12 * k, -0.5 * k, 0.2 * k, -0.44 * k);
      ctx.quadraticCurveTo(0.08 * k, -0.36 * k, 0.07 * k, -0.24 * k);
      ctx.quadraticCurveTo(0.1 * k, -0.1 * k, 0.15 * k, -0.01 * k);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#e8742a";
      ctx.beginPath();
      ctx.moveTo(-0.15 * k, -0.01 * k);
      ctx.lineTo(0.15 * k, -0.01 * k);
      ctx.lineTo(0.135 * k, -0.065 * k);
      ctx.lineTo(-0.13 * k, -0.065 * k);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = "#f6d24a";
      ctx.lineWidth = Math.max(0.8, 0.015 * k);
      ctx.strokeRect(-0.03 * k, -0.06 * k, 0.06 * k, 0.045 * k);
      break;
    }
    case "horns": {
      ctx.fillStyle = "#d3263a";
      for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.moveTo(side * 0.06 * k, 0.02 * k);
        ctx.quadraticCurveTo(side * 0.07 * k, -0.12 * k, side * 0.17 * k, -0.2 * k);
        ctx.quadraticCurveTo(side * 0.14 * k, -0.08 * k, side * 0.15 * k, 0.03 * k);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
      break;
    }
    case "pumpkin": {
      ctx.fillStyle = "#e8741c";
      for (const x of [-0.09, 0.09, 0]) {
        ctx.beginPath();
        ctx.ellipse(x * k, -0.1 * k, 0.085 * k, 0.1 * k, 0, 0, TAU);
        ctx.fill();
        ctx.stroke();
      }
      ctx.fillStyle = "rgba(255,255,255,0.22)";
      ctx.beginPath();
      ctx.ellipse(-0.02 * k, -0.15 * k, 0.02 * k, 0.04 * k, 0, 0, TAU);
      ctx.fill();
      ctx.fillStyle = "#3f7a2a";
      ctx.beginPath();
      ctx.roundRect(-0.015 * k, -0.25 * k, 0.03 * k, 0.07 * k, 0.01 * k);
      ctx.fill();
      ctx.strokeStyle = "#3f7a2a";
      ctx.lineWidth = Math.max(0.8, 0.012 * k);
      ctx.beginPath();
      ctx.moveTo(0.01 * k, -0.22 * k);
      ctx.bezierCurveTo(0.08 * k, -0.3 * k, 0.12 * k, -0.18 * k, 0.07 * k, -0.2 * k);
      ctx.stroke();
      break;
    }
    case "santa": {
      ctx.fillStyle = "#d42a36";
      ctx.beginPath();
      ctx.moveTo(-0.17 * k, -0.02 * k);
      ctx.quadraticCurveTo(-0.08 * k, -0.34 * k, 0.12 * k, -0.3 * k);
      ctx.quadraticCurveTo(0.24 * k, -0.27 * k, 0.26 * k, -0.12 * k);
      ctx.quadraticCurveTo(0.14 * k, -0.18 * k, 0.17 * k, -0.02 * k);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#f7f4ef";
      ctx.beginPath();
      ctx.roundRect(-0.2 * k, -0.06 * k, 0.4 * k, 0.08 * k, 0.04 * k);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(0.26 * k, -0.1 * k, 0.045 * k, 0, TAU);
      ctx.fill();
      break;
    }
    case "antlers": {
      ctx.strokeStyle = "#7a4a26";
      ctx.lineWidth = 0.035 * k;
      for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.moveTo(side * 0.08 * k, 0);
        ctx.quadraticCurveTo(side * 0.1 * k, -0.15 * k, side * 0.2 * k, -0.28 * k);
        ctx.moveTo(side * 0.11 * k, -0.12 * k);
        ctx.lineTo(side * 0.2 * k, -0.15 * k);
        ctx.moveTo(side * 0.15 * k, -0.2 * k);
        ctx.lineTo(side * 0.13 * k, -0.3 * k);
        ctx.stroke();
      }
      break;
    }
    case "elf": {
      ctx.fillStyle = "#1f9a4a";
      ctx.beginPath();
      ctx.moveTo(-0.16 * k, -0.02 * k);
      ctx.quadraticCurveTo(-0.02 * k, -0.2 * k, 0.05 * k, -0.42 * k);
      ctx.quadraticCurveTo(0.1 * k, -0.2 * k, 0.16 * k, -0.02 * k);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#d42a36";
      ctx.beginPath();
      ctx.roundRect(-0.18 * k, -0.06 * k, 0.36 * k, 0.065 * k, 0.03 * k);
      ctx.fill();
      ctx.fillStyle = "#f2c94c";
      ctx.beginPath();
      ctx.arc(0.05 * k, -0.43 * k, 0.03 * k, 0, TAU);
      ctx.fill();
      break;
    }
  }
  ctx.restore();
}

/** Seasonal dressing worn on the body: a string of holiday lights. */
export function drawSeasonBody(f: FaceFrame) {
  if (f.season !== "xmas" || f.activity === "offline") return;
  const { ctx, box, anchor, t } = f;
  const s = box.s;
  const half = 0.3 * Math.min(1, anchor.gap);
  const cx = box.x + anchor.x * s + f.shift.x;
  const top = box.y + (anchor.y + 0.13) * s;
  const p0 = [cx - half * s, top];
  const p1 = [cx, top + 0.2 * s];
  const p2 = [cx + half * s, top];
  const at = (u: number) => [
    (1 - u) * (1 - u) * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0],
    (1 - u) * (1 - u) * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1],
  ];
  ctx.save();
  ctx.strokeStyle = "#1f3b24";
  ctx.lineWidth = Math.max(0.8, s * 0.008);
  ctx.beginPath();
  ctx.moveTo(p0[0], p0[1]);
  ctx.quadraticCurveTo(p1[0], p1[1], p2[0], p2[1]);
  ctx.stroke();
  const bulbs = ["#ff4d4d", "#ffd23f", "#3ddc84", "#4da3ff", "#ff7ad9"];
  for (let i = 0; i < 6; i += 1) {
    const [x, y] = at((i + 0.5) / 6);
    // A slow chase, not a flicker.
    const on = f.still || Math.sin(t * 1.3 - i * 1.1) > -0.4;
    const color = bulbs[i % bulbs.length];
    if (on) {
      const glow = ctx.createRadialGradient(x, y + s * 0.018, 0, x, y + s * 0.018, s * 0.045);
      glow.addColorStop(0, `${color}88`);
      glow.addColorStop(1, `${color}00`);
      ctx.fillStyle = glow;
      ctx.fillRect(x - s * 0.045, y - s * 0.027, s * 0.09, s * 0.09);
    }
    ctx.fillStyle = on ? color : "#4a4a52";
    ctx.beginPath();
    ctx.ellipse(x, y + s * 0.018, Math.max(0.8, s * 0.013), Math.max(1, s * 0.019), 0, 0, TAU);
    ctx.fill();
    ctx.fillStyle = "#2a2d35";
    ctx.fillRect(x - s * 0.007, y - s * 0.002, s * 0.014, s * 0.008);
  }
  ctx.restore();
}

function bat(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, flap: number) {
  ctx.save();
  ctx.translate(x, y);
  ctx.fillStyle = "#16121f";
  ctx.strokeStyle = "rgba(170,140,230,0.6)";
  ctx.lineWidth = Math.max(0.6, r * 0.06);
  const lift = flap * r * 0.5;
  for (const side of [-1, 1]) {
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.quadraticCurveTo(side * r * 0.6, -r * 0.5 - lift, side * r * 1.4, -r * 0.2 - lift);
    ctx.quadraticCurveTo(side * r * 1.1, r * 0.05 - lift * 0.3, side * r * 0.95, r * 0.15);
    ctx.quadraticCurveTo(side * r * 0.7, -r * 0.05, side * r * 0.5, r * 0.2);
    ctx.quadraticCurveTo(side * r * 0.3, 0, 0, r * 0.2);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.ellipse(0, r * 0.05, r * 0.22, r * 0.3, 0, 0, TAU);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(-r * 0.15, -r * 0.15);
  ctx.lineTo(-r * 0.1, -r * 0.42);
  ctx.lineTo(-r * 0.02, -r * 0.2);
  ctx.lineTo(r * 0.02, -r * 0.2);
  ctx.lineTo(r * 0.1, -r * 0.42);
  ctx.lineTo(r * 0.15, -r * 0.15);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#ffcf4a";
  ctx.fillRect(-r * 0.11, -r * 0.06, r * 0.07, r * 0.06);
  ctx.fillRect(r * 0.04, -r * 0.06, r * 0.07, r * 0.06);
  ctx.restore();
}

function spider(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, t: number) {
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = "#141018";
  ctx.lineWidth = Math.max(0.7, r * 0.12);
  ctx.lineCap = "round";
  for (const side of [-1, 1]) {
    for (let i = 0; i < 4; i += 1) {
      const a = -0.6 + i * 0.4 + Math.sin(t * 6 + i) * 0.05;
      const kx = side * Math.cos(a) * r * 0.9;
      const ky = Math.sin(a) * r * 0.9;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.quadraticCurveTo(kx, ky - r * 0.35, kx * 1.4, ky + r * 0.3);
      ctx.stroke();
    }
  }
  ctx.fillStyle = "#141018";
  ctx.strokeStyle = "rgba(200,190,230,0.5)";
  ctx.lineWidth = Math.max(0.5, r * 0.05);
  ctx.beginPath();
  ctx.arc(0, r * 0.15, r * 0.45, 0, TAU);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(0, -r * 0.3, r * 0.28, 0, TAU);
  ctx.fill();
  ctx.fillStyle = "#ff3b3b";
  for (const side of [-1, 1]) {
    ctx.beginPath();
    ctx.arc(side * r * 0.1, -r * 0.33, r * 0.06, 0, TAU);
    ctx.fill();
  }
  ctx.restore();
}

function drawSeasonAmbient(f: FaceFrame) {
  const { ctx, box, t } = f;
  const s = box.s;
  const view = f.anchor.view;
  if (f.season === "halloween") {
    // Bats looping overhead, inside the layout box.
    const reach = (view.x1 - view.x0) / 2 - 0.16;
    for (let i = 0; i < 2; i += 1) {
      const a = f.still ? f.seed + i * 2.1 : t * (0.9 + i * 0.25) + f.seed + i * 2.1;
      bat(
        ctx,
        box.x + (0.5 + Math.cos(a) * reach) * s,
        box.y + (view.y0 + 0.14 + Math.sin(a * 2) * 0.07) * s,
        s * (0.11 - i * 0.025),
        f.still ? 0.5 : Math.sin(t * 16 + i),
      );
    }
    // Now and then a spider drops in on its thread.
    const cycle = f.still ? 99 : (t + f.seed * 3) % 11;
    if (cycle < 3.2) {
      const p = cycle < 0.8 ? cycle / 0.8 : cycle < 2.4 ? 1 : 1 - (cycle - 2.4) / 0.8;
      const x = box.x + 0.88 * s;
      const top = box.y + (view.y0 + 0.01) * s;
      const y = top + p * 0.62 * s + Math.sin(t * 3) * 0.015 * s;
      ctx.strokeStyle = "rgba(220,220,230,0.6)";
      ctx.lineWidth = Math.max(0.5, s * 0.004);
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, y);
      ctx.stroke();
      spider(ctx, x, y, s * 0.05, t);
    }
    // Low mist: a flattened glow that fades out inside the layout box.
    ctx.save();
    ctx.translate(box.x + (0.5 + (f.still ? 0 : Math.sin(t * 0.4 + f.seed) * 0.06)) * s, box.y + (view.y1 - 0.1) * s);
    ctx.scale(1, 0.17);
    const mist = ctx.createRadialGradient(0, 0, 0, 0, 0, s * 0.6);
    mist.addColorStop(0, "rgba(150,110,220,0.3)");
    mist.addColorStop(1, "rgba(150,110,220,0)");
    ctx.fillStyle = mist;
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.6, 0, TAU);
    ctx.fill();
    ctx.restore();
  } else if (f.season === "xmas") {
    ctx.fillStyle = "#ffffff";
    for (let i = 0; i < 12; i += 1) {
      const p = f.still ? (i * 0.173) % 1 : (t * (0.1 + (i % 4) * 0.025) + i * 0.173) % 1;
      ctx.globalAlpha = 0.85 * Math.sin(p * Math.PI);
      ctx.beginPath();
      ctx.arc(
        box.x + (view.x0 + 0.03 + ((i * 0.377) % 1) * (view.x1 - view.x0 - 0.06) + Math.sin(t * 0.9 + i) * 0.03) * s,
        box.y + (view.y0 + p * (view.y1 - view.y0)) * s,
        Math.max(0.7, s * (0.008 + (i % 3) * 0.004)),
        0,
        TAU,
      );
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}

/** Where each summoned tool lands, as fractions of the gem box. */
const SUMMON_TARGET: Partial<Record<FaceFrame["activity"], [number, number]>> = {
  searching: [0.66, 0.45],
  messaging: [0.82, 0.5],
  tasking: [0.5, 0.78],
};

/** A round badge by the gem's head: amber "?" for a question, red "!" for an error. */
function badge(f: FaceFrame, x: number, y: number, fill: string, ink: string, glyph: string) {
  const { ctx, box } = f;
  const s = box.s;
  ctx.fillStyle = fill;
  ctx.strokeStyle = "rgba(0,0,0,0.22)";
  ctx.lineWidth = Math.max(0.8, s * 0.008);
  ctx.beginPath();
  ctx.arc(x, y, s * 0.085, 0, TAU);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = ink;
  ctx.font = `800 ${Math.round(s * 0.12)}px ui-sans-serif, system-ui`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(glyph, x, y + s * 0.006);
}

export function drawEffects(f: FaceFrame) {
  const { ctx, box, t, activity, since } = f;
  const s = box.s;
  const rx = box.x + 0.86 * s;
  const ry = box.y + 0.06 * s;
  const cx = box.x + (0.5 - 0.11 * f.desk) * s;
  const cy = box.y + 0.5 * s;
  const work = workPhase(activity, f.atDesk);
  ctx.save();
  ctx.lineCap = "round";

  // A tool summoned out of the gem: a flash at its core and a streak of light.
  const target = SUMMON_TARGET[activity] ?? (work.laptop && !f.atDesk ? [0.9, 0.8] : null);
  const start = activity === "tasking" ? 1.3 : 0;
  const ps = (since - start) / 0.45;
  if (target && ps >= 0 && ps < 1 && !f.still) {
    const fade = 1 - ps;
    const flash = ctx.createRadialGradient(cx, cy, 0, cx, cy, s * 0.35);
    flash.addColorStop(0, `rgba(255,255,255,${(0.6 * fade).toFixed(3)})`);
    flash.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = flash;
    ctx.fillRect(cx - s * 0.35, cy - s * 0.35, s * 0.7, s * 0.7);
    const tx = box.x + target[0] * s;
    const ty = box.y + target[1] * s;
    ctx.strokeStyle = `rgba(255,255,255,${(0.8 * fade).toFixed(3)})`;
    ctx.lineWidth = Math.max(1, s * 0.02 * fade);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + (tx - cx) * Math.min(1, ps * 2), cy + (ty - cy) * Math.min(1, ps * 2));
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    star(ctx, tx, ty, s * 0.08 * Math.sin(ps * Math.PI));
  }

  if (f.bubble > 0.01) {
    // The bubble opens and closes instead of popping: it fades and grows out
    // of the small trailing circle by the gem.
    ctx.save();
    ctx.globalAlpha *= f.bubble;
    const ox = rx - 0.14 * s;
    const oy = ry + 0.2 * s;
    const grow = 0.55 + 0.45 * f.bubble;
    ctx.translate(ox, oy);
    ctx.scale(grow, grow);
    ctx.translate(-ox, -oy);
    // An outline strong enough to hold the white bubble on a light page.
    ctx.fillStyle = "rgba(255,255,255,0.95)";
    ctx.strokeStyle = "rgba(40,44,60,0.4)";
    ctx.lineWidth = Math.max(0.8, s * 0.008);
    for (const [dx, dy, r] of [[-0.14, 0.2, 0.025], [-0.08, 0.12, 0.04]]) {
      ctx.beginPath();
      ctx.arc(rx + dx * s, ry + dy * s, r * s, 0, TAU);
      ctx.fill();
      ctx.stroke();
    }
    ctx.beginPath();
    for (const [dx, dy, r] of [[0, 0, 0.1], [-0.08, 0.01, 0.07], [0.08, 0.01, 0.07], [0, -0.05, 0.075]]) {
      ctx.moveTo(rx + dx * s + r * s, ry + dy * s);
      ctx.arc(rx + dx * s, ry + dy * s, r * s, 0, TAU);
    }
    ctx.stroke();
    ctx.fill();
    ctx.fillStyle = "#3b3f4a";
    for (let i = -1; i <= 1; i += 1) {
      const bounce = f.still ? 0 : Math.max(0, Math.sin(t * 5 - i * 0.9)) * s * 0.018;
      ctx.beginPath();
      ctx.arc(rx + i * s * 0.045, ry - bounce, s * 0.016, 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  }
  drawActivityEffects(f, 1);
  if (f.big) drawSeasonAmbient(f);
  ctx.restore();
}

/**
 * What an activity puts around the gem. The engine draws a finished
 * activity's again at a falling `alpha`, so a badge or the zzz fades out
 * instead of vanishing.
 */
export function drawActivityEffects(f: FaceFrame, alpha: number) {
  const { ctx, box, t, activity, since } = f;
  const s = box.s;
  const rx = box.x + 0.86 * s;
  const ry = box.y + 0.06 * s;
  const cx = box.x + (0.5 - 0.11 * f.desk) * s;
  const cy = box.y + 0.5 * s;
  if (alpha <= 0.01) return;
  ctx.save();
  ctx.globalAlpha *= alpha;
  if (activity === "error") {
    // A red alert, and a few puffs of steam as it lands, then quiet.
    const pop = f.still ? 1 : Math.min(1, since / 0.25);
    const k = 1 + 0.25 * Math.sin(Math.min(1, pop) * Math.PI);
    ctx.save();
    ctx.translate(rx, ry);
    ctx.scale(k * pop, k * pop);
    badge(f, 0, 0, "#e5383b", "#ffffff", "!");
    ctx.restore();
    const steam = f.still ? 0 : 1 - Math.min(1, Math.max(0, (since - 1.6) / 0.8));
    if (steam > 0) {
      const base = ctx.globalAlpha;
      for (const side of [-1, 1]) {
        const p = (t * 0.9 + (side > 0 ? 0.5 : 0)) % 1;
        ctx.globalAlpha = base * steam * 0.75 * (1 - p);
        ctx.fillStyle = "rgb(235,235,240)";
        ctx.beginPath();
        ctx.arc(box.x + (0.5 + side * 0.3) * s, box.y + (0.12 - p * 0.18) * s, s * (0.03 + p * 0.05), 0, TAU);
        ctx.fill();
      }
      ctx.globalAlpha = base;
    }
  }
  if (activity === "dreaming") {
    // Pale with a dark edge, so they read on a dark page and a light one.
    ctx.fillStyle = "rgba(200,208,232,0.95)";
    ctx.strokeStyle = "rgba(30,34,52,0.45)";
    ctx.lineWidth = Math.max(0.6, s * 0.006);
    ctx.lineJoin = "round";
    const base = ctx.globalAlpha;
    for (let i = 0; i < 3; i += 1) {
      const p = f.still ? 0.3 + i * 0.2 : (t * 0.25 + i / 3) % 1;
      ctx.globalAlpha = base * Math.sin(p * Math.PI);
      ctx.font = `700 ${Math.round(s * (0.08 + p * 0.06))}px ui-sans-serif, system-ui`;
      const x = rx - s * 0.06 + p * s * 0.12;
      const y = ry + s * 0.1 - p * s * 0.2;
      ctx.strokeText("z", x, y);
      ctx.fillText("z", x, y);
    }
    ctx.globalAlpha = base;
  }
  if (activity === "waiting") {
    const bob = f.still ? 0 : Math.sin(t * 2.4) * s * 0.014;
    badge(f, rx, ry + bob, "#ffc021", "#2b2100", "?");
  }
  if (activity === "dance" && !f.still) {
    // Notes rise in turn, one side then the other, all inside the dance.
    ctx.fillStyle = rgba(mix(f.rgb, WHITE, 0.45));
    ctx.font = `700 ${Math.round(s * 0.13)}px ui-sans-serif, system-ui`;
    const base = ctx.globalAlpha;
    for (let i = 0; i < 3; i += 1) {
      const p = Math.min(1, Math.max(0, (since - 0.1 - i * 0.4) / 0.85));
      if (p <= 0 || p >= 1) continue;
      ctx.globalAlpha = base * Math.sin(p * Math.PI);
      const side = i === 1 ? 1 : -1;
      const drift = 0.04 * Math.sin(p * Math.PI * 2);
      ctx.fillText(i === 1 ? "♫" : "♪", box.x + (0.5 + side * (0.42 + drift)) * s, box.y + (0.32 - p * 0.3) * s);
    }
    ctx.globalAlpha = base;
  }
  if (activity === "booting") {
    // Regenerating: a column of light with sparkles rising through it, in
    // the gem's own color so it shows on a light page too.
    const light = mix(f.rgb, WHITE, 0.45);
    const col = ctx.createLinearGradient(cx - s * 0.3, 0, cx + s * 0.3, 0);
    col.addColorStop(0, rgba(light, 0));
    col.addColorStop(0.5, rgba(light, 0.3));
    col.addColorStop(1, rgba(light, 0));
    ctx.fillStyle = col;
    ctx.fillRect(cx - s * 0.3, box.y - s * 0.25, s * 0.6, s * 1.5);
    if (!f.still) {
      ctx.fillStyle = rgba(mix(f.rgb, WHITE, 0.7));
      const base = ctx.globalAlpha;
      for (let i = 0; i < 7; i += 1) {
        const p = (t * 0.6 + i / 7) % 1;
        ctx.globalAlpha = base * Math.sin(p * Math.PI);
        star(ctx, cx + Math.sin(i * 2.4 + t * 1.1) * s * 0.22, box.y + (1.05 - p * 1.2) * s, s * 0.03 * (1 - p * 0.5));
      }
      ctx.globalAlpha = base;
    }
  }
  if (activity === "offline") {
    const fresh = f.prev !== "offline" && !f.still;
    if (fresh && since < 0.9) {
      // Poof: a cloud bursts as the gem retreats.
      const p = since / 0.9;
      ctx.fillStyle = `rgba(236,236,244,${(0.8 * (1 - p)).toFixed(3)})`;
      for (let i = 0; i < 9; i += 1) {
        const a = (i / 9) * TAU + 0.4;
        const r = s * (0.12 + p * 0.38);
        ctx.beginPath();
        ctx.arc(cx + Math.cos(a) * r, cy + Math.sin(a) * r * 0.85, s * (0.09 + 0.05 * Math.sin(i * 1.7) + p * 0.05), 0, TAU);
        ctx.fill();
      }
    }
    // Resting, bubbled the way a treasured gem is kept safe.
    const appear = fresh ? Math.min(1, Math.max(0, (since - 0.35) / 0.4)) : 1;
    if (appear > 0) {
      const r = s * 0.52;
      const base = ctx.globalAlpha;
      ctx.globalAlpha = base * appear;
      const shell = ctx.createRadialGradient(cx, cy, r * 0.55, cx, cy, r);
      shell.addColorStop(0, "rgba(255,170,220,0)");
      shell.addColorStop(0.85, "rgba(255,170,220,0.16)");
      shell.addColorStop(1, "rgba(255,190,230,0.38)");
      ctx.fillStyle = shell;
      ctx.strokeStyle = "rgba(255,200,235,0.55)";
      ctx.lineWidth = Math.max(0.8, s * 0.01);
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, TAU);
      ctx.fill();
      ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,0.7)";
      ctx.lineWidth = Math.max(1, s * 0.018);
      ctx.beginPath();
      ctx.arc(cx, cy, r * 0.82, 1.1 * Math.PI, 1.4 * Math.PI);
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,0.8)";
      ctx.beginPath();
      ctx.arc(cx - r * 0.35, cy - r * 0.62, Math.max(0.8, s * 0.015), 0, TAU);
      ctx.fill();
      ctx.globalAlpha = base;
    }
  }
  ctx.restore();
}
