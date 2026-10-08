/**
 * What a gem works with: tools it holds for the activity at hand (a magnifier
 * for searching, a communicator for messaging, a clipboard for a task) and the
 * laptop it turns side-on to for long jobs, like Schroeder at his piano.
 * Tools are summoned out of the gem the way the Gems summon their weapons.
 */

import { BLACK, TAU, WHITE, drawFace, eyeGap, mix, rgba, workPhase, type FaceFrame } from "./gem-face";

const GLYPHS = ["{", "}", ";", "</>", "=>", "()", "0", "1", "[]", "&&"];
const CODE_COLORS = ["#7ee787", "#79c0ff", "#d2a8ff", "#ffa657"];

// Where the two spare keyboards sit, out front of the laptop.
const EXTRA_KEYS = [
  [0.6, 1.0],
  [0.86, 1.12],
] as const;

function handAt(ctx: CanvasRenderingContext2D, x: number, y: number, s: number, color: string, angle = 0) {
  ctx.fillStyle = color;
  ctx.strokeStyle = "rgba(0,0,0,0.35)";
  ctx.lineWidth = Math.max(0.8, s * 0.006);
  ctx.beginPath();
  ctx.ellipse(x, y, s * 0.042, s * 0.027, angle, 0, TAU);
  ctx.fill();
  ctx.stroke();
}

function keyboard(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, glow: string, t: number) {
  const h = w * 0.3;
  ctx.fillStyle = "#23252d";
  ctx.strokeStyle = "rgba(255,255,255,0.18)";
  ctx.lineWidth = Math.max(0.6, w * 0.02);
  ctx.beginPath();
  ctx.roundRect(x - w / 2, y - h / 2, w, h, h * 0.2);
  ctx.fill();
  ctx.stroke();
  const cols = 7;
  const kw = (w * 0.86) / cols;
  for (let row = 0; row < 3; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const lit = Math.sin(t * 23 + row * 3.1 + col * 1.7) > 0.86;
      ctx.fillStyle = lit ? glow : "rgba(255,255,255,0.32)";
      ctx.fillRect(x - w * 0.43 + col * kw + kw * 0.15, y - h * 0.36 + row * h * 0.26, kw * 0.7, h * 0.16);
    }
  }
}

/** Grow a summoned tool in around (x, y) over its first moments. */
function summonIn(f: FaceFrame, x: number, y: number, start = 0) {
  const p = f.still ? 1 : Math.min(1, Math.max(0, (f.since - start) / 0.35));
  const e = 1 - (1 - p) ** 3;
  const k = 0.4 + 0.6 * e;
  f.ctx.globalAlpha *= e;
  f.ctx.translate(x, y);
  f.ctx.scale(k, k);
  f.ctx.translate(-x, -y);
}

function facePoint(f: FaceFrame) {
  const s = f.box.s;
  return { ax: f.box.x + f.anchor.x * s + f.shift.x, ay: f.box.y + f.anchor.y * s + f.shift.y, s };
}

/** Tools in hand. Drawn with the body, so they ride its moves. */
export function drawHeld(f: FaceFrame) {
  switch (f.activity) {
    case "searching":
      magnifier(f);
      return;
    case "messaging":
      communicator(f);
      return;
    case "tasking":
      if (f.since < 1.3) salute(f);
      else clipboard(f);
  }
}

function magnifier(f: FaceFrame) {
  const { ctx, t } = f;
  const { ax, ay, s } = facePoint(f);
  const lx = ax + eyeGap(f) / 2 + (f.still ? 0 : Math.sin(t * 1.6)) * s * 0.045;
  const ly = ay + (f.still ? 0 : Math.sin(t * 2.3)) * s * 0.02;
  const r = s * 0.1;
  ctx.save();
  summonIn(f, lx, ly);
  const hx = lx + r * 0.72;
  const hy = ly + r * 0.72;
  ctx.lineCap = "round";
  ctx.strokeStyle = "#5a3b22";
  ctx.lineWidth = s * 0.034;
  ctx.beginPath();
  ctx.moveTo(hx, hy);
  ctx.lineTo(hx + s * 0.11, hy + s * 0.11);
  ctx.stroke();
  handAt(ctx, hx + s * 0.1, hy + s * 0.1, s, rgba(mix(f.rgb, BLACK, 0.15)), Math.PI / 4);
  // Through the glass: the face beneath, magnified.
  ctx.save();
  ctx.beginPath();
  ctx.arc(lx, ly, r, 0, TAU);
  ctx.clip();
  ctx.fillStyle = rgba(mix(f.rgb, WHITE, 0.4), 0.5);
  ctx.fillRect(lx - r, ly - r, r * 2, r * 2);
  ctx.translate(lx, ly);
  ctx.scale(1.7, 1.7);
  ctx.translate(-lx, -ly);
  drawFace(f);
  ctx.restore();
  ctx.fillStyle = "rgba(255,255,255,0.35)";
  ctx.beginPath();
  ctx.ellipse(lx - r * 0.35, ly - r * 0.4, r * 0.32, r * 0.16, -0.6, 0, TAU);
  ctx.fill();
  ctx.strokeStyle = "#2a2d36";
  ctx.lineWidth = s * 0.022;
  ctx.beginPath();
  ctx.arc(lx, ly, r, 0, TAU);
  ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,0.45)";
  ctx.lineWidth = Math.max(0.6, s * 0.006);
  ctx.beginPath();
  ctx.arc(lx, ly, r * 0.9, 1.05 * Math.PI, 1.55 * Math.PI);
  ctx.stroke();
  ctx.restore();
}

/** A Homeworld communicator held to the side of the head, signal pulsing. */
function communicator(f: FaceFrame) {
  const { ctx, t } = f;
  const { ax, ay, s } = facePoint(f);
  const dx = ax + s * 0.27;
  const dy = ay + s * 0.03;
  const glow = rgba(mix(f.rgb, WHITE, 0.55));
  ctx.save();
  summonIn(f, dx, dy);
  const base = ctx.globalAlpha;
  if (!f.still) {
    ctx.strokeStyle = glow;
    ctx.lineWidth = Math.max(0.8, s * 0.012);
    for (let i = 0; i < 3; i += 1) {
      const p = (t * 1.1 + i / 3) % 1;
      ctx.globalAlpha = base * (1 - p) * 0.8;
      ctx.beginPath();
      ctx.arc(dx + s * 0.03, dy - s * 0.12, s * (0.03 + p * 0.11), -0.95, 0.05);
      ctx.stroke();
    }
    ctx.globalAlpha = base;
  }
  ctx.translate(dx, dy);
  ctx.rotate(0.2);
  const w = s * 0.085;
  const h = s * 0.16;
  ctx.strokeStyle = "#2a2c36";
  ctx.lineWidth = Math.max(1, s * 0.012);
  ctx.beginPath();
  ctx.moveTo(w * 0.22, -h / 2);
  ctx.lineTo(w * 0.22, -h / 2 - h * 0.32);
  ctx.stroke();
  ctx.fillStyle = f.still || Math.sin(t * 6) > 0 ? "#ff4d6d" : "#5a1a26";
  ctx.beginPath();
  ctx.arc(w * 0.22, -h / 2 - h * 0.34, s * 0.012, 0, TAU);
  ctx.fill();
  const body = ctx.createLinearGradient(-w / 2, 0, w / 2, 0);
  body.addColorStop(0, "#2b2e3a");
  body.addColorStop(1, "#14161d");
  ctx.fillStyle = body;
  ctx.strokeStyle = "rgba(255,255,255,0.25)";
  ctx.lineWidth = Math.max(0.8, s * 0.006);
  ctx.beginPath();
  ctx.roundRect(-w / 2, -h / 2, w, h, w * 0.3);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.roundRect(-w * 0.34, -h * 0.38, w * 0.68, h * 0.4, w * 0.12);
  ctx.fill();
  // The Diamond insignia on screen, and the speaker grille.
  ctx.fillStyle = "rgba(10,12,20,0.8)";
  ctx.beginPath();
  ctx.moveTo(0, -h * 0.33);
  ctx.lineTo(w * 0.13, -h * 0.18);
  ctx.lineTo(0, -h * 0.03);
  ctx.lineTo(-w * 0.13, -h * 0.18);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.25)";
  for (let i = 0; i < 3; i += 1) ctx.fillRect(-w * 0.25, h * 0.12 + i * h * 0.08, w * 0.5, h * 0.03);
  handAt(ctx, 0, h * 0.42, s, rgba(mix(f.rgb, BLACK, 0.15)));
  ctx.restore();
}

/** A new task: the Diamond salute, hands meeting over the chest in a diamond. */
function salute(f: FaceFrame) {
  const { ctx } = f;
  const { ax, ay, s } = facePoint(f);
  const cx = ax - s * 0.03;
  const cy = ay + s * 0.24;
  const p = f.still ? 1 : Math.min(1, f.since / 0.3);
  const r = s * 0.06;
  ctx.save();
  ctx.globalAlpha *= p;
  ctx.strokeStyle = rgba(mix(f.rgb, WHITE, 0.6));
  ctx.lineWidth = Math.max(1, s * 0.014);
  ctx.beginPath();
  ctx.moveTo(cx, cy - r);
  ctx.lineTo(cx + r * 0.8, cy);
  ctx.lineTo(cx, cy + r);
  ctx.lineTo(cx - r * 0.8, cy);
  ctx.closePath();
  ctx.stroke();
  for (const side of [-1, 1]) {
    handAt(ctx, cx + side * (r * 0.8 + s * 0.03 + (1 - p) * s * 0.12), cy, s, rgba(mix(f.rgb, BLACK, 0.15)), side * 0.6);
  }
  ctx.restore();
}

/** The task's checklist, ticked off item by item, then a fresh page. */
function clipboard(f: FaceFrame) {
  const { ctx } = f;
  const { ax, ay, s } = facePoint(f);
  const cx = ax - s * 0.03;
  const cy = ay + s * 0.3;
  const w = s * 0.3;
  const h = s * 0.32;
  ctx.save();
  summonIn(f, cx, cy, 1.3);
  ctx.translate(cx, cy);
  ctx.rotate(-0.08);
  ctx.fillStyle = "#a8743f";
  ctx.strokeStyle = "rgba(0,0,0,0.35)";
  ctx.lineWidth = Math.max(0.8, s * 0.006);
  ctx.beginPath();
  ctx.roundRect(-w / 2, -h / 2, w, h, s * 0.02);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#fbfaf5";
  ctx.beginPath();
  ctx.roundRect(-w / 2 + s * 0.02, -h / 2 + s * 0.035, w - s * 0.04, h - s * 0.05, s * 0.008);
  ctx.fill();
  ctx.fillStyle = "#c9ccd4";
  ctx.beginPath();
  ctx.roundRect(-w * 0.18, -h / 2 - s * 0.015, w * 0.36, s * 0.042, s * 0.012);
  ctx.fill();
  ctx.stroke();
  const done = f.still ? 2 : Math.floor(((f.since - 1.3) * 0.7) % 4);
  for (let row = 0; row < 3; row += 1) {
    const y = -h / 2 + s * 0.09 + row * s * 0.075;
    const bx = -w / 2 + s * 0.045;
    ctx.strokeStyle = "#6b7080";
    ctx.lineWidth = Math.max(0.7, s * 0.007);
    ctx.strokeRect(bx, y - s * 0.017, s * 0.034, s * 0.034);
    ctx.fillStyle = row < done ? "#b9bec9" : "#8e94a3";
    ctx.fillRect(bx + s * 0.055, y - s * 0.006, w * (row === 1 ? 0.42 : 0.52), s * 0.012);
    if (row < done) {
      ctx.strokeStyle = "#2fb36b";
      ctx.lineWidth = Math.max(1, s * 0.014);
      ctx.beginPath();
      ctx.moveTo(bx + s * 0.006, y);
      ctx.lineTo(bx + s * 0.015, y + s * 0.011);
      ctx.lineTo(bx + s * 0.034, y - s * 0.016);
      ctx.stroke();
    }
  }
  for (const side of [-1, 1]) handAt(ctx, side * w * 0.5, h * 0.1, s, rgba(mix(f.rgb, BLACK, 0.15)), side * 0.4);
  ctx.restore();
}

/**
 * Side-on at a laptop, like Schroeder at his piano: the gem has turned to face
 * right and scooted left, and the laptop sits in front of it with its screen
 * toward the gem. Not drawn with the body, so it stays put while the gem
 * bounces with the typing. `desk` slides it in as the gem turns.
 */
export function drawProps(f: FaceFrame) {
  if (f.desk < 0.02) return;
  const phase = workPhase(f.activity, f.since);
  const { ctx, box, t } = f;
  const s = box.s;
  const enter = (1 - f.desk) * s * 0.3;
  const X = (u: number) => box.x + u * s + enter;
  const Y = (v: number) => box.y + v * s;
  const typing = phase.laptop && f.activity !== "reading" && !f.still;
  const furious = typing && phase.furious;
  const glow = rgba(mix(f.rgb, WHITE, 0.55));
  const hand = rgba(mix(f.rgb, BLACK, 0.15));
  const flicker = typing ? 0.82 + 0.18 * Math.sin(t * (furious ? 31 : 9)) : 1;
  const baseTop = Y(0.86);
  const hingeX = X(1.1);
  const lidLen = s * 0.36;
  const tilt = 0.18;
  const lidTopX = hingeX + Math.sin(tilt) * lidLen;
  const lidTopY = baseTop - Math.cos(tilt) * lidLen;

  ctx.save();
  ctx.globalAlpha = Math.min(1, f.desk * 1.5);
  const base = ctx.globalAlpha;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  // The screen's light falling on the gem.
  const beam = ctx.createLinearGradient(hingeX, 0, X(0.45), 0);
  beam.addColorStop(0, `rgba(175,215,255,${(0.3 * flicker).toFixed(3)})`);
  beam.addColorStop(1, "rgba(175,215,255,0)");
  ctx.fillStyle = beam;
  ctx.beginPath();
  ctx.moveTo(lidTopX - s * 0.03, lidTopY + s * 0.03);
  ctx.lineTo(hingeX - s * 0.03, baseTop - s * 0.04);
  ctx.lineTo(X(0.45), Y(0.82));
  ctx.lineTo(X(0.45), Y(0.25));
  ctx.closePath();
  ctx.fill();

  // Three keyboards deep: two more out front.
  if (phase.triple) EXTRA_KEYS.forEach(([u, v], i) => keyboard(ctx, X(u), Y(v), s * 0.3, glow, t + i));

  // Base with its keys.
  const shell = ctx.createLinearGradient(0, baseTop, 0, baseTop + s * 0.034);
  shell.addColorStop(0, "#e6e9ef");
  shell.addColorStop(1, "#9aa0ab");
  ctx.fillStyle = shell;
  ctx.strokeStyle = "rgba(0,0,0,0.35)";
  ctx.lineWidth = Math.max(0.8, s * 0.006);
  ctx.beginPath();
  ctx.roundRect(X(0.62), baseTop, s * 0.5, s * 0.034, s * 0.014);
  ctx.fill();
  ctx.stroke();
  for (let i = 0; i < 8; i += 1) {
    ctx.fillStyle = typing && Math.sin(t * 23 + i * 1.7) > 0.8 ? glow : "#2a2d35";
    ctx.fillRect(X(0.66) + i * s * 0.042, baseTop - s * 0.011, s * 0.032, s * 0.011);
  }

  // Lid, leaning back, its screen toward the gem.
  ctx.save();
  ctx.translate(hingeX, baseTop);
  ctx.rotate(tilt);
  const lt = s * 0.032;
  const lid = ctx.createLinearGradient(-lt, 0, 0, 0);
  lid.addColorStop(0, "#c2c7d0");
  lid.addColorStop(1, "#eef0f4");
  ctx.fillStyle = lid;
  ctx.beginPath();
  ctx.roundRect(-lt, -lidLen, lt, lidLen, lt * 0.45);
  ctx.fill();
  ctx.stroke();
  // The lit screen, seen almost edge-on.
  ctx.fillStyle = `rgba(205,232,255,${(0.9 * flicker).toFixed(3)})`;
  ctx.beginPath();
  ctx.roundRect(-lt - s * 0.013, -lidLen + s * 0.03, s * 0.013, lidLen - s * 0.06, s * 0.006);
  ctx.fill();
  ctx.restore();

  if (phase.spareAt >= 0) {
    // A spare dropped onto the keys after the throw.
    const p = phase.spareAt / 0.8;
    const fall = p < 0.7 ? (p / 0.7) ** 2 : 1 - Math.sin(((p - 0.7) / 0.3) * Math.PI) * 0.1;
    const from = Y(0.3);
    keyboard(ctx, X(0.82), from + (baseTop - s * 0.04 - from) * fall, s * 0.3, glow, t);
  }

  {
    // Hands on the keys, fading with the laptop; furious typing blurs them. With three keyboards out
    // the same two hands dart between the laptop and the spares, out of step.
    const tap = furious ? 26 : 12;
    const ghosts = furious ? 3 : 1;
    [0.72, 0.88].forEach((u, i) => {
      let hx = X(u);
      let hy = baseTop - s * 0.026;
      if (phase.triple && typing) {
        const [ku, kv] = EXTRA_KEYS[i];
        const c = (t * 1.6 + i * 0.5) % 1;
        // Dwell, dash over in 8% of the beat, dwell, dash back.
        const away = c < 0.42 ? 0 : c < 0.5 ? (c - 0.42) / 0.08 : c < 0.92 ? 1 : 1 - (c - 0.92) / 0.08;
        const e = away * away * (3 - 2 * away);
        hx += (X(ku) - s * 0.03 - hx) * e;
        hy += (Y(kv) - s * 0.035 - hy) * e;
      }
      for (let g = ghosts - 1; g >= 0; g -= 1) {
        ctx.globalAlpha = base * (g === 0 ? 1 : 0.28);
        const lift = typing ? Math.max(0, Math.sin(t * tap + i * Math.PI - g * 0.7)) * s * 0.03 : 0;
        handAt(ctx, hx + g * s * 0.012, hy - lift, s, hand);
      }
    });
    ctx.globalAlpha = base;
  }

  if (typing && s >= 40) {
    // Keystrokes: code glyphs fly up off the keys.
    const count = furious ? 6 : 3;
    const rate = furious ? 2.4 : 1;
    ctx.font = `700 ${Math.round(s * 0.06)}px ui-monospace, SFMono-Regular, Menlo, monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let i = 0; i < count; i += 1) {
      const clock = t * rate + i / count;
      const n = Math.floor(clock);
      const p = clock - n;
      const r = Math.abs(Math.sin(n * 12.9898 + i * 78.233) * 43758.5453) % 1;
      ctx.globalAlpha = base * Math.sin(p * Math.PI);
      ctx.fillStyle = CODE_COLORS[(n + i) % CODE_COLORS.length];
      ctx.fillText(GLYPHS[Math.floor(r * GLYPHS.length)], X(0.66 + 0.3 * r) + p * s * 0.05, baseTop - s * 0.05 - p * s * 0.3);
    }
    ctx.globalAlpha = base;
  }
  if (f.activity === "reading" && !f.still && s >= 40) {
    // Lines of text drift off the screen into the gem's eyes.
    ctx.fillStyle = "rgba(200,225,255,0.9)";
    for (let i = 0; i < 3; i += 1) {
      const p = (t * 0.7 + i / 3) % 1;
      ctx.globalAlpha = base * Math.sin(p * Math.PI) * 0.8;
      ctx.fillRect(lidTopX - s * 0.06 - p * s * 0.3, Y(0.56 + i * 0.05) - p * s * 0.06, s * (0.05 + 0.02 * i), s * 0.01);
    }
    ctx.globalAlpha = base;
  }
  if (furious) {
    // Sweat flying off the back of the head.
    const p = (t * 1.6) % 1;
    const x = box.x + (0.15 - p * 0.12) * s;
    const y = box.y + (0.28 + p * p * 0.1) * s;
    ctx.fillStyle = "#8fd3ff";
    ctx.beginPath();
    ctx.moveTo(x, y - s * 0.035);
    ctx.quadraticCurveTo(x + s * 0.022, y, x, y + s * 0.012);
    ctx.quadraticCurveTo(x - s * 0.022, y, x, y - s * 0.035);
    ctx.fill();
  }
  ctx.restore();
}

/** The keyboard flung over the shoulder passes behind the gem. */
export function drawBackProps(f: FaceFrame) {
  const phase = workPhase(f.activity, f.since);
  if (phase.throwAt < 0 || f.desk < 0.5) return;
  const { ctx, box, t } = f;
  const s = box.s;
  const p = phase.throwAt / 0.9;
  ctx.save();
  ctx.globalAlpha = p > 0.75 ? 1 - (p - 0.75) / 0.25 : 1;
  ctx.translate(box.x + (0.82 - p) * s, box.y + (0.82 - Math.sin(p * Math.PI) * 0.85 + p * 0.22) * s);
  ctx.rotate(-p * TAU * 1.5);
  keyboard(ctx, 0, 0, s * 0.3, rgba(mix(f.rgb, WHITE, 0.55)), t);
  ctx.restore();
}
