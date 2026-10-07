/**
 * How a gem is dressed and what floats around it: hats (everyday, Halloween,
 * Christmas), seasonal dressing (holiday lights, bats, a dangling spider,
 * snow) and activity effects (thought bubble, steam, zzz, music notes). The
 * Steven Universe touches live here too: a resting gem sits in a bubble, a
 * gem that drops offline poofs first, and a booting gem regenerates in a
 * column of light.
 */

import { TAU, WHITE, hue, mix, rgba, star, workPhase, type FaceFrame } from "./gem-face";

export function drawHat(f: FaceFrame) {
  if (f.hat === "none") return;
  const { ctx, box, anchor, shift } = f;
  const s = box.s;
  const k = s * anchor.hatScale;
  const accent = `hsl(${(hue(f.rgb) + 150) % 360}, 82%, 58%)`;
  ctx.save();
  ctx.translate(box.x + 0.5 * s + shift.x, box.y + anchor.hatY * s + shift.y);
  ctx.rotate(f.activity === "dance" ? Math.sin(f.t * 8) * 0.2 : -0.12);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.lineWidth = Math.max(0.8, s * 0.008);
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
    case "headphones": {
      ctx.strokeStyle = "#1b1c22";
      ctx.lineWidth = 0.05 * k;
      ctx.beginPath();
      ctx.arc(0, 0.16 * k, 0.32 * k, 1.08 * Math.PI, 1.92 * Math.PI);
      ctx.stroke();
      for (const side of [-1, 1]) {
        ctx.fillStyle = "#16171c";
        ctx.beginPath();
        ctx.roundRect(side * 0.31 * k - 0.055 * k, 0.12 * k, 0.11 * k, 0.17 * k, 0.04 * k);
        ctx.fill();
        ctx.strokeStyle = rgba(mix(f.rgb, WHITE, 0.4));
        ctx.lineWidth = Math.max(0.8, 0.018 * k);
        ctx.beginPath();
        ctx.roundRect(side * 0.31 * k - 0.04 * k, 0.14 * k, 0.08 * k, 0.13 * k, 0.03 * k);
        ctx.stroke();
      }
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
    const on = f.still || Math.sin(t * 3 + i * 1.9) > -0.3;
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
  if (f.season === "halloween") {
    // Bats looping overhead.
    for (let i = 0; i < 2; i += 1) {
      const a = f.still ? f.seed + i * 2.1 : t * (0.9 + i * 0.25) + f.seed + i * 2.1;
      bat(
        ctx,
        box.x + (0.5 + Math.cos(a) * 0.62) * s,
        box.y + (-0.1 + Math.sin(a * 2) * 0.08) * s,
        s * (0.11 - i * 0.025),
        f.still ? 0.5 : Math.sin(t * 16 + i),
      );
    }
    // Now and then a spider drops in on its thread.
    const cycle = f.still ? 99 : (t + f.seed * 3) % 11;
    if (cycle < 3.2) {
      const p = cycle < 0.8 ? cycle / 0.8 : cycle < 2.4 ? 1 : 1 - (cycle - 2.4) / 0.8;
      const x = box.x + 0.88 * s;
      const top = box.y - 0.3 * s;
      const y = top + p * 0.62 * s + Math.sin(t * 3) * 0.015 * s;
      ctx.strokeStyle = "rgba(220,220,230,0.6)";
      ctx.lineWidth = Math.max(0.5, s * 0.004);
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, y);
      ctx.stroke();
      spider(ctx, x, y, s * 0.05, t);
    }
    // Low mist: a flattened glow that fades out well inside the canvas.
    ctx.save();
    ctx.translate(box.x + (0.5 + (f.still ? 0 : Math.sin(t * 0.4 + f.seed) * 0.06)) * s, box.y + 1.02 * s);
    ctx.scale(1, 0.32);
    const mist = ctx.createRadialGradient(0, 0, 0, 0, 0, s * 0.72);
    mist.addColorStop(0, "rgba(150,110,220,0.3)");
    mist.addColorStop(1, "rgba(150,110,220,0)");
    ctx.fillStyle = mist;
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.72, 0, TAU);
    ctx.fill();
    ctx.restore();
  } else if (f.season === "xmas") {
    ctx.fillStyle = "#ffffff";
    for (let i = 0; i < 12; i += 1) {
      const p = f.still ? (i * 0.173) % 1 : (t * (0.1 + (i % 4) * 0.025) + i * 0.173) % 1;
      ctx.globalAlpha = 0.85 * Math.sin(p * Math.PI);
      ctx.beginPath();
      ctx.arc(
        box.x + (-0.25 + ((i * 0.377) % 1) * 1.5 + Math.sin(t * 0.9 + i) * 0.03) * s,
        box.y + (-0.3 + p * 1.6) * s,
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

export function drawEffects(f: FaceFrame) {
  const { ctx, box, t, activity, since } = f;
  const s = box.s;
  const rx = box.x + 0.86 * s;
  const ry = box.y + 0.06 * s;
  const cx = box.x + (0.5 - 0.11 * f.desk) * s;
  const cy = box.y + 0.5 * s;
  const work = workPhase(activity, since);
  ctx.save();
  ctx.lineCap = "round";

  // A tool summoned out of the gem: a flash at its core and a streak of light.
  const target = SUMMON_TARGET[activity] ?? (work.laptop ? [0.9, 0.8] : null);
  const start = activity === "tasking" ? 1.3 : activity === "thinking" ? 4 : 0;
  const ps = (since - start) / 0.45;
  if (target && ps >= 0 && ps < 1 && !f.still) {
    const fade = 1 - ps;
    const flash = ctx.createRadialGradient(cx, cy, 0, cx, cy, s * 0.35);
    flash.addColorStop(0, `rgba(255,255,255,${(0.7 * fade).toFixed(3)})`);
    flash.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = flash;
    ctx.fillRect(cx - s * 0.35, cy - s * 0.35, s * 0.7, s * 0.7);
    const tx = box.x + target[0] * s;
    const ty = box.y + target[1] * s;
    ctx.strokeStyle = `rgba(255,255,255,${(0.85 * fade).toFixed(3)})`;
    ctx.lineWidth = Math.max(1, s * 0.02 * fade);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + (tx - cx) * Math.min(1, ps * 2), cy + (ty - cy) * Math.min(1, ps * 2));
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    star(ctx, tx, ty, s * 0.08 * Math.sin(ps * Math.PI));
  }

  if (work.bubble) {
    ctx.fillStyle = "rgba(255,255,255,0.95)";
    ctx.strokeStyle = "rgba(0,0,0,0.25)";
    ctx.lineWidth = Math.max(0.6, s * 0.006);
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
    ctx.fill();
    ctx.fillStyle = "#3b3f4a";
    for (let i = -1; i <= 1; i += 1) {
      const bounce = f.still ? 0 : Math.max(0, Math.sin(t * 6 - i * 0.9)) * s * 0.02;
      ctx.beginPath();
      ctx.arc(rx + i * s * 0.045, ry - bounce, s * 0.016, 0, TAU);
      ctx.fill();
    }
  }
  if (activity === "error") {
    // Anger mark and steam puffs.
    ctx.strokeStyle = "#ff3b30";
    ctx.lineWidth = Math.max(1.2, s * 0.025);
    const m = s * 0.05 * (1 + Math.sin(t * 10) * 0.12);
    for (const [qx, qy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      ctx.beginPath();
      ctx.moveTo(rx + qx * m * 0.3, ry + qy * m * 1.2);
      ctx.quadraticCurveTo(rx + qx * m * 0.35, ry + qy * m * 0.35, rx + qx * m * 1.2, ry + qy * m * 0.3);
      ctx.stroke();
    }
    for (const side of [-1, 1]) {
      const p = (t * 0.9 + (side > 0 ? 0.5 : 0)) % 1;
      ctx.fillStyle = `rgba(235,235,240,${(0.75 * (1 - p)).toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(box.x + (0.5 + side * 0.3) * s, box.y + (0.12 - p * 0.18) * s, s * (0.03 + p * 0.05), 0, TAU);
      ctx.fill();
    }
  }
  if (activity === "dreaming") {
    ctx.fillStyle = "rgba(220,226,240,0.9)";
    for (let i = 0; i < 3; i += 1) {
      const p = (t * 0.35 + i / 3) % 1;
      ctx.globalAlpha = Math.sin(p * Math.PI);
      ctx.font = `700 ${Math.round(s * (0.08 + p * 0.06))}px ui-sans-serif, system-ui`;
      ctx.fillText("z", rx - s * 0.06 + p * s * 0.12, ry + s * 0.1 - p * s * 0.2);
    }
    ctx.globalAlpha = 1;
  }
  if (activity === "waiting") {
    const bob = f.still ? 0 : Math.sin(t * 4) * s * 0.02;
    ctx.fillStyle = "rgba(255,255,255,0.95)";
    ctx.beginPath();
    ctx.arc(rx, ry + bob, s * 0.075, 0, TAU);
    ctx.fill();
    ctx.fillStyle = "#e0a100";
    ctx.font = `800 ${Math.round(s * 0.11)}px ui-sans-serif, system-ui`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("?", rx, ry + bob + s * 0.005);
  }
  if (activity === "dance") {
    ctx.fillStyle = rgba(mix(f.rgb, WHITE, 0.45));
    ctx.font = `700 ${Math.round(s * 0.13)}px ui-sans-serif, system-ui`;
    for (let i = 0; i < 2; i += 1) {
      const p = (t * 0.6 + i * 0.5) % 1;
      ctx.globalAlpha = Math.sin(p * Math.PI);
      const side = i === 0 ? -1 : 1;
      ctx.fillText(i === 0 ? "♪" : "♫", box.x + (0.5 + side * 0.42) * s, box.y + (0.3 - p * 0.3) * s);
    }
    ctx.globalAlpha = 1;
  }
  if (activity === "booting" && !f.still) {
    // Regenerating: a column of light with sparkles rising through it.
    const col = ctx.createLinearGradient(cx - s * 0.3, 0, cx + s * 0.3, 0);
    col.addColorStop(0, "rgba(255,255,255,0)");
    col.addColorStop(0.5, "rgba(255,255,255,0.22)");
    col.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = col;
    ctx.fillRect(cx - s * 0.3, box.y - s * 0.25, s * 0.6, s * 1.5);
    ctx.fillStyle = "#ffffff";
    for (let i = 0; i < 7; i += 1) {
      const p = (t * 0.7 + i / 7) % 1;
      ctx.globalAlpha = Math.sin(p * Math.PI);
      star(ctx, cx + Math.sin(i * 2.4 + t * 1.3) * s * 0.22, box.y + (1.05 - p * 1.2) * s, s * 0.03 * (1 - p * 0.5));
    }
    ctx.globalAlpha = 1;
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
    // Resting, bubbled the way the Crystal Gems keep a gem safe.
    const appear = fresh ? Math.min(1, Math.max(0, (since - 0.35) / 0.4)) : 1;
    if (appear > 0) {
      const r = s * 0.52;
      ctx.globalAlpha = appear;
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
      ctx.globalAlpha = 1;
    }
  }
  drawSeasonAmbient(f);
  ctx.restore();
}
