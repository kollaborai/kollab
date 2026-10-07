/**
 * The gem's face, drawn in 2D over the 3D render: eyes in many styles, brows,
 * and a mouth that talks while the agent streams. Held tools and the laptop
 * live in gem-props.ts; hats, seasonal dressing and effects in gem-dress.ts.
 * Everything is vector paths, so it stays crisp from a 32px sidebar row to
 * the hero gem.
 */

export type EyeStyle =
  | "pill"
  | "dot"
  | "disney"
  | "kawaii"
  | "diamond"
  | "heart"
  | "triclops"
  | "shades"
  | "visor"
  | "googly"
  | "pixel"
  | "lantern"
  | "hollow"
  | "buttons"
  | "spider"
  | "coal"
  | "sparkle";

export type HatStyle =
  | "none"
  | "party"
  | "top"
  | "beanie"
  | "crown"
  | "wizard"
  | "cap"
  | "headphones"
  | "hardhat"
  | "beret"
  | "witch"
  | "horns"
  | "pumpkin"
  | "santa"
  | "antlers"
  | "elf";

export type Season = "none" | "halloween" | "xmas";

export type Activity =
  | "idle"
  | "thinking"
  | "reading"
  | "typing"
  | "speaking"
  | "searching"
  | "messaging"
  | "tasking"
  | "error"
  | "waiting"
  | "dreaming"
  | "dance"
  | "booting"
  | "offline";

type Option<T> = { id: T; label: string; group: string };

export const EYE_STYLES: Option<EyeStyle>[] = [
  { id: "pill", label: "Pill", group: "Classic" },
  { id: "dot", label: "Adventure", group: "Classic" },
  { id: "disney", label: "Storybook", group: "Classic" },
  { id: "kawaii", label: "Kawaii", group: "Classic" },
  { id: "diamond", label: "Diamond", group: "Gem" },
  { id: "heart", label: "Hearts", group: "Gem" },
  { id: "triclops", label: "Third Eye", group: "Gem" },
  { id: "shades", label: "Shades", group: "Gem" },
  { id: "visor", label: "Visor", group: "Fun" },
  { id: "googly", label: "Googly", group: "Fun" },
  { id: "pixel", label: "Pixel", group: "Fun" },
  { id: "lantern", label: "Lantern", group: "Halloween" },
  { id: "hollow", label: "Hollow", group: "Halloween" },
  { id: "buttons", label: "Buttons", group: "Halloween" },
  { id: "spider", label: "Spider", group: "Halloween" },
  { id: "coal", label: "Snowman", group: "Christmas" },
  { id: "sparkle", label: "Sparkle", group: "Christmas" },
];

export const HAT_STYLES: Option<HatStyle>[] = [
  { id: "none", label: "None", group: "Everyday" },
  { id: "party", label: "Party", group: "Everyday" },
  { id: "top", label: "Top Hat", group: "Everyday" },
  { id: "beanie", label: "Beanie", group: "Everyday" },
  { id: "crown", label: "Crown", group: "Everyday" },
  { id: "wizard", label: "Wizard", group: "Everyday" },
  { id: "cap", label: "Cap", group: "Everyday" },
  { id: "headphones", label: "Headphones", group: "Everyday" },
  { id: "hardhat", label: "Hard Hat", group: "Everyday" },
  { id: "beret", label: "Beret", group: "Everyday" },
  { id: "witch", label: "Witch", group: "Halloween" },
  { id: "horns", label: "Horns", group: "Halloween" },
  { id: "pumpkin", label: "Pumpkin", group: "Halloween" },
  { id: "santa", label: "Santa", group: "Christmas" },
  { id: "antlers", label: "Antlers", group: "Christmas" },
  { id: "elf", label: "Elf", group: "Christmas" },
];

export const SEASONS: Option<Season>[] = [
  { id: "none", label: "None", group: "" },
  { id: "halloween", label: "Halloween", group: "" },
  { id: "xmas", label: "Christmas", group: "" },
];

export type Rgb = [number, number, number];
export const TAU = Math.PI * 2;
export const WHITE: Rgb = [255, 255, 255];
export const BLACK: Rgb = [10, 12, 20];
export const rgba = ([r, g, b]: Rgb, a = 1) => `rgba(${r},${g},${b},${a})`;
export const mix = (a: Rgb, b: Rgb, k: number): Rgb => [
  Math.round(a[0] + (b[0] - a[0]) * k),
  Math.round(a[1] + (b[1] - a[1]) * k),
  Math.round(a[2] + (b[2] - a[2]) * k),
];

export function hue([r, g, b]: Rgb): number {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  if (max === min) return 0;
  const d = max - min;
  let h = 0;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}

/** A four-point sparkle, filled with the current fill style. */
export function star(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x, y - r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.quadraticCurveTo(x, y, x, y + r);
  ctx.quadraticCurveTo(x, y, x - r, y);
  ctx.quadraticCurveTo(x, y, x, y - r);
  ctx.fill();
}

export function line(ctx: CanvasRenderingContext2D, x1: number, y1: number, x2: number, y2: number) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
}

const CASTE_HATS: Record<string, HatStyle> = {
  leadership: "crown",
  engineering: "hardhat",
  creative: "beret",
  intelligence: "wizard",
  communication: "headphones",
  defense: "cap",
};

const SEASON_HATS: Record<Exclude<Season, "none">, HatStyle[]> = {
  halloween: ["witch", "horns", "pumpkin"],
  xmas: ["santa", "antlers", "elf"],
};

/** Spooky all October, festive all December. */
export function seasonFor(date = new Date()): Season {
  const month = date.getMonth();
  return month === 9 ? "halloween" : month === 11 ? "xmas" : "none";
}

/**
 * "auto" dresses each gem for its caste (leaders get crowns, engineers hard
 * hats), or, in season, in a seasonal hat picked by its seed so a roster mixes.
 */
export function autoHat(caste: string | null | undefined, season: Season, seed: number): HatStyle {
  if (season !== "none") {
    const hats = SEASON_HATS[season];
    return hats[Math.floor((seed / TAU) * hats.length) % hats.length];
  }
  return (caste && CASTE_HATS[caste.toLowerCase()]) || "none";
}

export type FaceAnchor = { x: number; y: number; gap: number; hatY: number; hatScale: number };

export type FaceFrame = {
  ctx: CanvasRenderingContext2D;
  t: number;
  /** Seconds since the current activity started; drives escalation. */
  since: number;
  /** The activity before this one, for transitions such as the poof. */
  prev: Activity;
  /** The square the gem is rendered into, in canvas pixels. */
  box: { x: number; y: number; s: number };
  anchor: FaceAnchor;
  /** Body sway in canvas pixels, so the face rides along with the gem. */
  shift: { x: number; y: number };
  /** Gaze toward the pointer, -1..1 on each axis. */
  look: { x: number; y: number };
  /** 1 open, 0 shut. */
  blink: number;
  activity: Activity;
  style: EyeStyle;
  hat: HatStyle;
  season: Season;
  /** Gem color as sRGB 0-255. */
  rgb: Rgb;
  dark: boolean;
  /** Mouth openness while talking, 0..1. */
  talk: number;
  /** 0..1: how far the gem has turned side-on to its laptop. */
  desk: number;
  /** Stable per-gem value in [0, 2π), for variety across a roster. */
  seed: number;
  still: boolean;
};

/** Where activity escalation stands while thinking or working at length. */
export function workPhase(activity: Activity, since: number) {
  const thinking = activity === "thinking";
  const offset = thinking ? 4 : 0;
  const laptop = activity === "reading" || activity === "typing" || (thinking && since >= 4);
  const s = since - offset;
  const furious = laptop && activity !== "reading" && s >= 5 && s < 7;
  // A keyboard goes flying at 7s, then every 9s once three are out, and a
  // spare comes out right after each throw.
  let throwAt = -1;
  let spareAt = -1;
  if (laptop && activity !== "reading" && s >= 7) {
    const cycle = s >= 13 ? (s - 13) % 9 : s - 7;
    if (cycle < 0.9) throwAt = cycle;
    else if (cycle < 1.7) spareAt = cycle - 0.9;
  }
  const triple = laptop && activity !== "reading" && s >= 13;
  return { bubble: thinking && since < 4, laptop, furious: furious || triple, throwAt, spareAt, triple };
}

const EYE_GAP: Record<EyeStyle, number> = {
  pill: 0.14,
  dot: 0.2,
  disney: 0.19,
  kawaii: 0.17,
  diamond: 0.19,
  heart: 0.19,
  triclops: 0.25,
  shades: 0.14,
  visor: 0.14,
  googly: 0.2,
  pixel: 0.17,
  lantern: 0.22,
  hollow: 0.2,
  buttons: 0.2,
  spider: 0.15,
  coal: 0.2,
  sparkle: 0.19,
};

/** Big white eyes with an iris that moves inside the white. */
const BIG_EYES: ReadonlySet<EyeStyle> = new Set(["disney", "diamond", "heart", "triclops", "sparkle"]);

export function eyeGap(f: FaceFrame): number {
  return f.box.s * EYE_GAP[f.style] * f.anchor.gap;
}

type FaceMood = {
  closed: boolean;
  happy: boolean;
  angry: boolean;
  gaze: { x: number; y: number };
  squint: number;
  wide: number;
  ink: string;
};

/** Where the eyes look for each activity; otherwise they follow the pointer. */
function gazeFor(f: FaceFrame): { x: number; y: number } {
  const { activity, look, t } = f;
  if (f.desk > 0.5) {
    // Side-on at the laptop: on the screen, scanning down lines while reading.
    return activity === "reading" ? { x: 0.8, y: -0.2 + 0.55 * ((t * 0.55) % 1) } : { x: 0.85, y: 0.3 };
  }
  switch (activity) {
    case "thinking":
      return { x: 0.55, y: -0.8 };
    case "waiting":
      return { x: 0, y: 0 };
    case "searching":
      return { x: 0.45 + 0.35 * Math.sin(t * 1.6), y: 0.1 };
    case "messaging":
      return { x: 0.35 * Math.sin(t * 0.8), y: -0.3 };
    case "tasking":
      return { x: 0.15 + 0.3 * Math.sin(t * 0.9), y: 0.8 };
    default:
      return look;
  }
}

export function drawFace(f: FaceFrame) {
  const { ctx, box, anchor, shift, activity, style } = f;
  const s = box.s;
  const ax = box.x + anchor.x * s + shift.x;
  const ay = box.y + anchor.y * s + shift.y;
  const offline = activity === "offline";
  const work = workPhase(activity, f.since);
  const angry = activity === "error";
  const m: FaceMood = {
    closed: offline || activity === "dreaming" || activity === "booting",
    happy: activity === "dance",
    angry,
    gaze: gazeFor(f),
    squint: angry ? 0.72 : work.furious ? 0.6 : 1,
    wide: activity === "waiting" ? 1.15 : 1,
    ink: offline ? "rgba(120,124,136,0.9)" : f.dark ? "rgba(236,242,255,0.95)" : "rgba(8,10,18,0.92)",
  };
  const gap = eyeGap(f);

  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  switch (style) {
    case "visor":
      drawVisor(f, ax, ay, m);
      break;
    case "shades":
      drawShades(f, ax, ay, m);
      break;
    case "lantern":
      drawLantern(f, ax, ay, gap, m);
      break;
    case "spider":
      drawSpiderEyes(f, ax, ay, m);
      break;
    case "triclops": {
      // Garnet's three eyes: smaller, with the third set between and above.
      const k = 0.66 * Math.min(1, anchor.gap);
      drawEye(f, ax - gap / 2, ay, -1, k, m);
      drawEye(f, ax + gap / 2, ay, 1, k, m);
      drawEye(f, ax, ay - s * 0.05, 0, k, m);
      break;
    }
    default:
      drawEye(f, ax - gap / 2, ay, -1, 1, m);
      drawEye(f, ax + gap / 2, ay, 1, 1, m);
  }
  drawBrows(f, ax, ay, gap, m);

  const plain = style !== "visor" && style !== "shades" && style !== "lantern" && style !== "spider";
  if (f.season === "xmas" && !m.closed && plain && style !== "kawaii" && style !== "hollow" && style !== "buttons") {
    // Rosy cheeks.
    ctx.fillStyle = "rgba(255,105,140,0.38)";
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.ellipse(ax + side * (gap / 2 + s * 0.03), ay + s * 0.085, s * 0.04, s * 0.022, 0, 0, TAU);
      ctx.fill();
    }
  }
  if (activity === "reading" && plain) drawGlasses(f, ax, ay, gap);
  if (style === "coal") {
    // A carrot nose.
    ctx.fillStyle = "#f07a1a";
    ctx.beginPath();
    ctx.moveTo(ax - s * 0.012, ay + s * 0.048);
    ctx.lineTo(ax + s * 0.09, ay + s * 0.066);
    ctx.lineTo(ax - s * 0.008, ay + s * 0.078);
    ctx.closePath();
    ctx.fill();
  }
  if (f.hat === "antlers" && !offline) {
    // Antlers come with a red nose.
    const ny = ay + s * (BIG_EYES.has(style) ? 0.11 : 0.07);
    const glow = ctx.createRadialGradient(ax, ny, 0, ax, ny, s * 0.06);
    glow.addColorStop(0, `rgba(255,60,60,${f.still ? 0.35 : (0.25 + 0.15 * Math.sin(f.t * 4)).toFixed(3)})`);
    glow.addColorStop(1, "rgba(255,60,60,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(ax - s * 0.06, ny - s * 0.06, s * 0.12, s * 0.12);
    ctx.fillStyle = "#e3262f";
    ctx.beginPath();
    ctx.arc(ax, ny, s * 0.028, 0, TAU);
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.beginPath();
    ctx.arc(ax - s * 0.009, ny - s * 0.01, s * 0.008, 0, TAU);
    ctx.fill();
  }
  const mouthOffset = style === "triclops" || style === "coal" ? 0.13 : BIG_EYES.has(style) ? 0.155 : plain ? 0.12 : 0.16;
  drawMouth(f, ax, ay + s * mouthOffset, m.ink);
  ctx.restore();
}

function drawEye(f: FaceFrame, ex: number, ey: number, side: -1 | 0 | 1, k: number, m: FaceMood) {
  const { ctx, box, style } = f;
  const s = box.s;
  if (m.closed && style !== "buttons") {
    ctx.strokeStyle = m.ink;
    ctx.lineWidth = Math.max(1, s * 0.022 * Math.max(0.7, k));
    ctx.beginPath();
    if (f.activity === "offline") {
      ctx.moveTo(ex - s * 0.04 * k, ey + s * 0.01);
      ctx.lineTo(ex + s * 0.04 * k, ey + s * 0.01);
    } else ctx.arc(ex, ey, s * 0.05 * k, 0.15 * Math.PI, 0.85 * Math.PI);
    ctx.stroke();
    return;
  }
  if (m.happy && style !== "buttons") {
    ctx.strokeStyle = BIG_EYES.has(style) ? "rgba(8,10,18,0.92)" : m.ink;
    ctx.lineWidth = Math.max(1.2, s * 0.03 * Math.max(0.7, k));
    ctx.beginPath();
    ctx.arc(ex, ey + s * 0.025 * k, s * 0.045 * k, 1.15 * Math.PI, 1.85 * Math.PI);
    ctx.stroke();
    return;
  }
  const { gaze, squint, wide, ink } = m;
  switch (style) {
    case "pill": {
      const w = s * 0.07 * wide;
      const h = Math.max(s * 0.014, s * 0.125 * wide * f.blink * squint);
      const x = ex + gaze.x * s * 0.025 - w / 2;
      const y = ey + gaze.y * s * 0.02 - h / 2;
      ctx.fillStyle = ink;
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, w / 2);
      ctx.fill();
      if (s >= 90 && f.blink > 0.6) {
        ctx.fillStyle = f.dark ? "rgba(20,24,40,0.7)" : "rgba(255,255,255,0.85)";
        ctx.beginPath();
        ctx.arc(x + w * 0.66, y + h * 0.26, w * 0.18, 0, TAU);
        ctx.fill();
      }
      return;
    }
    case "dot": {
      const r = s * 0.033 * wide;
      const x = ex + gaze.x * s * 0.02;
      const y = ey + s * 0.015 + gaze.y * s * 0.015;
      ctx.fillStyle = ink;
      if (f.blink < 0.35) {
        ctx.strokeStyle = ink;
        ctx.lineWidth = Math.max(1, s * 0.018);
        line(ctx, x - r, y, x + r, y);
      } else {
        ctx.beginPath();
        ctx.ellipse(x, y, r, r * f.blink * squint, 0, 0, TAU);
        ctx.fill();
      }
      return;
    }
    case "kawaii": {
      const rx = s * 0.042 * wide;
      const ry = s * 0.058 * wide * Math.max(0.12, f.blink) * squint;
      const x = ex + gaze.x * s * 0.02;
      const y = ey + gaze.y * s * 0.018;
      ctx.fillStyle = "#0b0c12";
      ctx.beginPath();
      ctx.ellipse(x, y, rx, ry, 0, 0, TAU);
      ctx.fill();
      if (f.blink > 0.5) {
        ctx.fillStyle = "#ffffff";
        ctx.beginPath();
        ctx.arc(x - rx * 0.35, y - ry * 0.4, rx * 0.42, 0, TAU);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x + rx * 0.35, y + ry * 0.35, rx * 0.18, 0, TAU);
        ctx.fill();
      }
      ctx.fillStyle = "rgba(255,110,150,0.45)";
      ctx.beginPath();
      ctx.ellipse(ex + side * s * 0.05, ey + s * 0.075, s * 0.04, s * 0.022, 0, 0, TAU);
      ctx.fill();
      return;
    }
    case "googly": {
      const r = s * 0.068 * wide;
      ctx.fillStyle = "#ffffff";
      ctx.strokeStyle = "#0b0c12";
      ctx.lineWidth = Math.max(1, s * 0.013);
      ctx.beginPath();
      ctx.arc(ex, ey, r, 0, TAU);
      ctx.fill();
      ctx.stroke();
      // A loose pupil swinging under gravity, only loosely aimed.
      const t = f.still ? 0 : f.t;
      const swing = Math.sin(t * 4.3 + side * 1.9) * 0.6 + Math.sin(t * 7.7 + side * 3.1) * 0.18;
      const pr = r * 0.5;
      const reach = r - pr - s * 0.005;
      ctx.fillStyle = "#0b0c12";
      ctx.beginPath();
      ctx.arc(
        ex + (Math.sin(swing) * 0.7 + gaze.x * 0.3) * reach,
        ey + (Math.cos(swing) * 0.7 + gaze.y * 0.3) * reach,
        pr,
        0,
        TAU,
      );
      ctx.fill();
      // Glare on the plastic dome.
      ctx.fillStyle = "rgba(255,255,255,0.85)";
      ctx.beginPath();
      ctx.ellipse(ex - r * 0.38, ey - r * 0.42, r * 0.22, r * 0.12, -0.6, 0, TAU);
      ctx.fill();
      return;
    }
    case "pixel": {
      // Snapped to a pixel grid, so the eyes move in whole pixels.
      const p = Math.max(1, Math.round(s * 0.028));
      const x0 = Math.round(ex / p) * p - p + Math.round(gaze.x * 1.2) * p;
      const y0 = Math.round(ey / p) * p - 2 * p + Math.round(gaze.y) * p;
      ctx.fillStyle = ink;
      if (f.blink < 0.4) {
        ctx.fillRect(x0, y0 + p, p * 2, p);
        return;
      }
      const rows = squint < 0.8 ? 2 : 3;
      const top = y0 + (3 - rows) * p;
      ctx.fillRect(x0, top, p * 2, p * rows);
      if (s >= 48) {
        ctx.fillStyle = f.dark ? "rgba(20,24,40,0.85)" : "#ffffff";
        ctx.fillRect(x0, top, p, p);
      }
      return;
    }
    case "hollow": {
      // Empty sockets with a pinprick of red light that follows you.
      const rx = s * 0.06 * wide;
      const ry = s * 0.075 * wide * Math.max(0.12, f.blink) * squint;
      const g = ctx.createRadialGradient(ex, ey, 0, ex, ey, rx * 1.25);
      g.addColorStop(0, "rgba(0,0,0,1)");
      g.addColorStop(0.7, "rgba(0,0,0,0.95)");
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.ellipse(ex, ey, rx * 1.25, ry * 1.25, 0, 0, TAU);
      ctx.fill();
      if (f.blink > 0.4 && f.activity !== "offline") {
        const flare = m.angry ? 1.7 : 1;
        const px = ex + gaze.x * rx * 0.45;
        const py = ey + gaze.y * ry * 0.4;
        const r = s * 0.035 * flare;
        const halo = ctx.createRadialGradient(px, py, 0, px, py, r);
        halo.addColorStop(0, "rgba(255,42,42,0.55)");
        halo.addColorStop(1, "rgba(255,42,42,0)");
        ctx.fillStyle = halo;
        ctx.fillRect(px - r, py - r, r * 2, r * 2);
        ctx.fillStyle = "#ff3b3b";
        ctx.beginPath();
        ctx.arc(px, py, Math.max(0.8, s * 0.011 * flare), 0, TAU);
        ctx.fill();
      }
      return;
    }
    case "buttons": {
      // Sewn-on buttons. They never blink and never look away.
      const r = s * 0.05;
      const dim = f.activity === "offline";
      ctx.fillStyle = dim ? "#3a3c44" : "#17151f";
      ctx.strokeStyle = dim ? "#55585f" : "#3b3550";
      ctx.lineWidth = Math.max(1, s * 0.012);
      ctx.beginPath();
      ctx.arc(ex, ey, r, 0, TAU);
      ctx.fill();
      ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,0.18)";
      ctx.beginPath();
      ctx.arc(ex, ey, r * 0.78, 1.1 * Math.PI, 1.6 * Math.PI);
      ctx.stroke();
      const h = r * 0.34;
      ctx.fillStyle = "rgba(0,0,0,0.85)";
      for (const [dx, dy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
        ctx.beginPath();
        ctx.arc(ex + dx * h, ey + dy * h, r * 0.11, 0, TAU);
        ctx.fill();
      }
      ctx.strokeStyle = "#d8cdb4";
      ctx.lineWidth = Math.max(0.8, s * 0.009);
      line(ctx, ex - h, ey - h, ex + h, ey + h);
      line(ctx, ex + h, ey - h, ex - h, ey + h);
      return;
    }
    case "coal": {
      // Lumps of coal: irregular and matte, with one dull glint.
      const r = s * 0.034 * wide;
      const open = Math.max(0.15, f.blink) * squint;
      const x = ex + gaze.x * s * 0.015;
      const y = ey + gaze.y * s * 0.012;
      ctx.fillStyle = "#16161a";
      ctx.beginPath();
      for (let i = 0; i < 7; i += 1) {
        const a = (i / 7) * TAU;
        const lump = 0.8 + 0.25 * Math.abs(Math.sin(i * 2.7 + side * 1.3 + f.seed));
        const px = x + Math.cos(a) * r * lump;
        const py = y + Math.sin(a) * r * lump * open;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = "rgba(255,255,255,0.35)";
      ctx.beginPath();
      ctx.arc(x - r * 0.35, y - r * 0.35 * open, r * 0.2, 0, TAU);
      ctx.fill();
      return;
    }
    default: {
      // Big white eyes: Storybook, Diamond, Hearts, Third Eye, Sparkle.
      const rx = s * 0.085 * wide * k;
      const ry = s * 0.1 * wide * k;
      const open = Math.max(0.06, f.blink) * Math.max(0.7, squint);
      ctx.fillStyle = "#ffffff";
      ctx.strokeStyle = "rgba(10,12,20,0.4)";
      ctx.lineWidth = Math.max(0.8, s * 0.008);
      ctx.beginPath();
      ctx.ellipse(ex, ey, rx, ry * open, 0, 0, TAU);
      ctx.fill();
      ctx.stroke();
      if (open <= 0.3) return;
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(ex, ey, rx, ry * open, 0, 0, TAU);
      ctx.clip();
      drawPupil(f, side, ex + gaze.x * rx * 0.42, ey + gaze.y * ry * 0.38, s * k);
      ctx.restore();
    }
  }
}

function rhombus(ctx: CanvasRenderingContext2D, x: number, y: number, rx: number, ry: number) {
  ctx.beginPath();
  ctx.moveTo(x, y - ry);
  ctx.lineTo(x + rx, y);
  ctx.lineTo(x, y + ry);
  ctx.lineTo(x - rx, y);
  ctx.closePath();
}

function heart(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x, y + r * 0.9);
  ctx.bezierCurveTo(x - r * 1.3, y + r * 0.1, x - r * 0.9, y - r, x, y - r * 0.35);
  ctx.bezierCurveTo(x + r * 0.9, y - r, x + r * 1.3, y + r * 0.1, x, y + r * 0.9);
  ctx.closePath();
}

/** `s` is the gem size already scaled for this eye. */
function drawPupil(f: FaceFrame, side: -1 | 0 | 1, ix: number, iy: number, s: number) {
  const { ctx, style } = f;
  switch (style) {
    case "diamond": {
      // Diamond-cut irises, like Homeworld's Diamonds.
      ctx.fillStyle = rgba(mix(f.rgb, BLACK, 0.25));
      rhombus(ctx, ix, iy, s * 0.055, s * 0.075);
      ctx.fill();
      ctx.fillStyle = "#07080d";
      rhombus(ctx, ix, iy, s * 0.026, s * 0.036);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(ix - s * 0.02, iy - s * 0.028, s * 0.014, 0, TAU);
      ctx.fill();
      return;
    }
    case "heart": {
      ctx.fillStyle = rgba(mix(f.rgb, [255, 60, 130], 0.65));
      heart(ctx, ix, iy, s * 0.05);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(ix - s * 0.018, iy - s * 0.016, s * 0.012, 0, TAU);
      ctx.fill();
      return;
    }
    case "sparkle": {
      ctx.fillStyle = rgba(mix(f.rgb, BLACK, 0.3));
      ctx.beginPath();
      ctx.arc(ix, iy, s * 0.064, 0, TAU);
      ctx.fill();
      ctx.fillStyle = "#07080d";
      ctx.beginPath();
      ctx.arc(ix, iy, s * 0.032, 0, TAU);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      star(ctx, ix - s * 0.02, iy - s * 0.024, s * 0.03 * (f.still ? 1 : 0.85 + 0.15 * Math.sin(f.t * 3)));
      ctx.beginPath();
      ctx.arc(ix + s * 0.022, iy + s * 0.022, s * 0.01, 0, TAU);
      ctx.fill();
      return;
    }
    default: {
      // Storybook and Third Eye: the iris takes the gem's color, sapphire
      // gets blue eyes; the Third Eye's outer irises are Sapphire blue and
      // Ruby red, as on Garnet.
      const iris: Rgb =
        style === "triclops" && side !== 0 ? (side < 0 ? [60, 110, 230] : [225, 45, 70]) : mix(f.rgb, BLACK, 0.35);
      ctx.fillStyle = rgba(iris);
      ctx.beginPath();
      ctx.arc(ix, iy, s * 0.06, 0, TAU);
      ctx.fill();
      ctx.fillStyle = "#07080d";
      ctx.beginPath();
      ctx.arc(ix, iy, s * 0.034, 0, TAU);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(ix - s * 0.022, iy - s * 0.026, s * 0.02, 0, TAU);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(ix + s * 0.02, iy + s * 0.02, s * 0.009, 0, TAU);
      ctx.fill();
    }
  }
}

function drawBrows(f: FaceFrame, ax: number, ay: number, gap: number, m: FaceMood) {
  const { ctx, box, style, activity } = f;
  const s = box.s;
  if (m.closed || style === "lantern") return;
  const big = BIG_EYES.has(style);
  const banded = style === "visor" || style === "shades";
  const browY =
    ay -
    s *
      (style === "triclops"
        ? 0.1
        : big
          ? 0.115
          : banded || style === "googly"
            ? 0.1
            : style === "spider"
              ? 0.13
              : 0.085);
  const browGap = banded ? s * 0.22 * f.anchor.gap : gap;
  const color = big ? "rgba(8,10,18,0.95)" : m.ink;
  if (m.angry) {
    // Brows slanting down toward the middle.
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(1.4, s * 0.03);
    for (const side of [-1, 1] as const) {
      const ex = ax + (side * browGap) / 2;
      line(ctx, ex + side * s * 0.055, browY - s * 0.03, ex - side * s * 0.03, browY + s * 0.012);
    }
  } else if (activity === "thinking" && f.desk < 0.5 && !big && style !== "visor") {
    // One raised brow.
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(1, s * 0.018);
    ctx.beginPath();
    ctx.arc(ax + browGap / 2, browY + s * 0.035, s * 0.04, 1.2 * Math.PI, 1.8 * Math.PI);
    ctx.stroke();
  }
}

function drawVisor(f: FaceFrame, ax: number, ay: number, m: FaceMood) {
  const { ctx, box, t } = f;
  const s = box.s;
  const w = s * 0.5 * Math.max(0.8, f.anchor.gap);
  const h = s * 0.15;
  const x = ax - w / 2;
  const y = ay - h / 2;
  const band = ctx.createLinearGradient(x, y, x, y + h);
  band.addColorStop(0, "#1d2433");
  band.addColorStop(1, "#06080e");
  ctx.fillStyle = band;
  ctx.strokeStyle = "rgba(255,255,255,0.28)";
  ctx.lineWidth = Math.max(0.8, s * 0.008);
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, h / 2);
  ctx.fill();
  ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,0.22)";
  line(ctx, x + h * 0.5, y + h * 0.22, x + w - h * 0.5, y + h * 0.22);

  const led = m.angry ? "#ff3b30" : rgba(mix(f.rgb, WHITE, 0.55));
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, h / 2);
  ctx.clip();
  ctx.fillStyle = led;
  ctx.strokeStyle = led;
  ctx.lineWidth = Math.max(1.2, s * 0.022);
  if (f.activity === "thinking" && f.desk < 0.5) {
    // A scanner sweeping the visor.
    const sweep = (Math.sin(t * 3.2) * 0.5 + 0.5) * (w - h) + x + h / 2;
    ctx.beginPath();
    ctx.roundRect(sweep - s * 0.05, y + h * 0.3, s * 0.1, h * 0.4, h * 0.2);
    ctx.fill();
  } else {
    for (const side of [-1, 1] as const) {
      const ex = ax + side * w * 0.24 + m.gaze.x * w * 0.12;
      const ey = ay + m.gaze.y * h * 0.18;
      if (m.closed) {
        line(ctx, ex - s * 0.04, ey, ex + s * 0.04, ey);
      } else if (m.happy) {
        ctx.beginPath();
        ctx.arc(ex, ey + s * 0.02, s * 0.035, 1.15 * Math.PI, 1.85 * Math.PI);
        ctx.stroke();
      } else if (m.angry) {
        const r = s * 0.028;
        line(ctx, ex - r, ey - r, ex + r, ey + r);
        line(ctx, ex + r, ey - r, ex - r, ey + r);
      } else {
        const lh = Math.max(s * 0.012, h * 0.42 * f.blink);
        ctx.beginPath();
        ctx.roundRect(ex - s * 0.045, ey - lh / 2, s * 0.09, lh, lh / 2);
        ctx.fill();
      }
    }
  }
  ctx.restore();
}

/** Garnet's shades: one wraparound lens, a passing glint, future vision behind. */
function drawShades(f: FaceFrame, ax: number, ay: number, m: FaceMood) {
  const { ctx, box, t } = f;
  const s = box.s;
  const w = s * 0.42 * Math.max(0.8, f.anchor.gap);
  const h = s * 0.11;
  const x = ax - w / 2;
  const y = ay - h / 2 - (m.happy ? s * 0.012 : 0);
  const lens = () => {
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + w, y);
    ctx.lineTo(x + w * 0.97, y + h * 0.82);
    ctx.quadraticCurveTo(x + w * 0.76, y + h * 1.12, x + w * 0.56, y + h * 0.66);
    ctx.lineTo(x + w * 0.44, y + h * 0.66);
    ctx.quadraticCurveTo(x + w * 0.24, y + h * 1.12, x + w * 0.03, y + h * 0.82);
    ctx.closePath();
  };
  const fill = ctx.createLinearGradient(x, y, x, y + h);
  fill.addColorStop(0, "#3d2f63");
  fill.addColorStop(0.45, "#141022");
  fill.addColorStop(1, "#05040a");
  ctx.fillStyle = fill;
  ctx.strokeStyle = "rgba(0,0,0,0.6)";
  ctx.lineWidth = Math.max(0.8, s * 0.008);
  lens();
  ctx.fill();
  ctx.stroke();
  ctx.save();
  lens();
  ctx.clip();
  const cycle = f.still ? 0.2 : (t * 0.4 + f.seed) % 1;
  if (cycle < 0.3) {
    const gx = x - w * 0.2 + (cycle / 0.3) * w * 1.4;
    ctx.fillStyle = "rgba(255,255,255,0.5)";
    ctx.beginPath();
    ctx.moveTo(gx, y);
    ctx.lineTo(gx + w * 0.07, y);
    ctx.lineTo(gx - w * 0.05, y + h);
    ctx.lineTo(gx - w * 0.12, y + h);
    ctx.closePath();
    ctx.fill();
  }
  if ((f.activity === "thinking" && f.desk < 0.5) || m.angry) {
    // Thinking lights the third eye behind the lens; anger floods it red.
    const pulse = f.still ? 1 : 0.55 + 0.45 * Math.sin(t * 3);
    const color = m.angry ? "255,60,60" : "255,110,220";
    const g = ctx.createRadialGradient(ax, y + h * 0.4, 0, ax, y + h * 0.4, s * (m.angry ? 0.2 : 0.07));
    g.addColorStop(0, `rgba(${color},${(0.85 * pulse).toFixed(3)})`);
    g.addColorStop(1, `rgba(${color},0)`);
    ctx.fillStyle = g;
    ctx.fillRect(x, y, w, h);
  }
  ctx.restore();
  ctx.strokeStyle = "rgba(255,255,255,0.28)";
  ctx.lineWidth = Math.max(0.7, s * 0.006);
  line(ctx, x + w * 0.04, y + s * 0.006, x + w * 0.96, y + s * 0.006);
}

function flicker(f: FaceFrame) {
  return f.still ? 1 : 0.84 + 0.09 * Math.sin(f.t * 13 + f.seed) + 0.07 * Math.sin(f.t * 29 + f.seed * 2);
}

/** Candlelight through a carved shape, or a dark hole when the candle is out. */
function carved(f: FaceFrame, x: number, y: number, r: number, lit: boolean): string | CanvasGradient {
  if (!lit) return "#2a1405";
  const g = f.ctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, "#fff4a8");
  g.addColorStop(0.45, "#ffb02e");
  g.addColorStop(1, "#d4500e");
  return g;
}

function candle(f: FaceFrame, lit: boolean) {
  if (lit) f.ctx.globalAlpha *= 0.75 + 0.25 * flicker(f);
}

function drawLantern(f: FaceFrame, ax: number, ay: number, gap: number, m: FaceMood) {
  const { ctx, box } = f;
  const s = box.s;
  const lit = !m.closed;
  if (lit) {
    // Candlelight spilling out through the carving.
    const glow = ctx.createRadialGradient(ax, ay + s * 0.05, 0, ax, ay + s * 0.05, s * 0.26);
    glow.addColorStop(0, `rgba(255,150,40,${(0.32 * flicker(f)).toFixed(3)})`);
    glow.addColorStop(1, "rgba(255,150,40,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(ax - s * 0.26, ay - s * 0.21, s * 0.52, s * 0.52);
  }
  for (const side of [-1, 1] as const) {
    const w = s * 0.07;
    const h = s * 0.085 * Math.max(0.3, f.blink) * m.squint;
    ctx.save();
    candle(f, lit);
    ctx.translate(ax + (side * gap) / 2, ay);
    if (m.angry) ctx.rotate(side * 0.35);
    ctx.fillStyle = carved(f, 0, m.happy ? -h * 0.2 : h * 0.2, w * 1.1, lit);
    ctx.beginPath();
    if (m.happy) {
      ctx.moveTo(-w, -h / 2);
      ctx.lineTo(w, -h / 2);
      ctx.lineTo(0, h / 2);
    } else {
      ctx.moveTo(-w, h / 2);
      ctx.lineTo(w, h / 2);
      ctx.lineTo(0, -h / 2);
    }
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
  ctx.save();
  candle(f, lit);
  ctx.fillStyle = carved(f, ax, ay + s * 0.075, s * 0.03, lit);
  ctx.beginPath();
  ctx.moveTo(ax, ay + s * 0.055);
  ctx.lineTo(ax + s * 0.022, ay + s * 0.09);
  ctx.lineTo(ax - s * 0.022, ay + s * 0.09);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

// Two big front eyes and six small ones, as offsets from the face anchor in
// fractions of the gem box, with radii.
const SPIDER_EYES: [number, number, number][] = [
  [-0.075, 0, 0.048],
  [0.075, 0, 0.048],
  [-0.035, -0.082, 0.02],
  [0.035, -0.082, 0.02],
  [-0.115, -0.065, 0.017],
  [0.115, -0.065, 0.017],
  [-0.152, -0.005, 0.013],
  [0.152, -0.005, 0.013],
];

function drawSpiderEyes(f: FaceFrame, ax: number, ay: number, m: FaceMood) {
  const { ctx, box, t, anchor } = f;
  const s = box.s;
  SPIDER_EYES.forEach(([u, v, r], i) => {
    const x = ax + u * s * anchor.gap;
    const y = ay + v * s;
    const big = i < 2;
    if (m.closed) {
      ctx.strokeStyle = m.ink;
      ctx.lineWidth = Math.max(0.8, s * (big ? 0.02 : 0.01));
      line(ctx, x - r * s, y, x + r * s, y);
      return;
    }
    if (m.happy && big) {
      ctx.strokeStyle = m.ink;
      ctx.lineWidth = Math.max(1.2, s * 0.026);
      ctx.beginPath();
      ctx.arc(x, y + s * 0.02, s * 0.04, 1.15 * Math.PI, 1.85 * Math.PI);
      ctx.stroke();
      return;
    }
    // Each small eye blinks on its own clock.
    const own = f.still ? 0.5 : (t * 0.35 + i * 0.137 + f.seed) % 1;
    const open = (big ? f.blink : own < 0.035 ? 0.15 : 1) * m.squint;
    ctx.fillStyle = "#07070a";
    ctx.strokeStyle = "rgba(255,255,255,0.16)";
    ctx.lineWidth = Math.max(0.6, s * 0.005);
    ctx.beginPath();
    ctx.ellipse(x, y, r * s, Math.max(0.5, r * s * open), 0, 0, TAU);
    ctx.fill();
    if (big) ctx.stroke();
    if (open > 0.5) {
      ctx.fillStyle = m.angry ? "rgba(255,70,70,0.95)" : "rgba(255,255,255,0.9)";
      ctx.beginPath();
      ctx.arc(
        x - r * s * 0.3 + m.gaze.x * r * s * 0.25,
        y - r * s * 0.35 + m.gaze.y * r * s * 0.2,
        r * s * (big ? 0.28 : 0.32),
        0,
        TAU,
      );
      ctx.fill();
    }
  });
}

function drawGlasses(f: FaceFrame, ax: number, ay: number, gap: number) {
  const { ctx, box } = f;
  const s = box.s;
  const r = s * (BIG_EYES.has(f.style) ? 0.1 : 0.065);
  ctx.strokeStyle = "rgba(20,20,26,0.95)";
  ctx.lineWidth = Math.max(1, s * 0.016);
  ctx.fillStyle = "rgba(190,230,255,0.18)";
  for (const side of [-1, 1] as const) {
    ctx.beginPath();
    ctx.arc(ax + (side * gap) / 2, ay, r, 0, TAU);
    ctx.fill();
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.moveTo(ax - gap / 2 + r, ay);
  ctx.quadraticCurveTo(ax, ay - r * 0.4, ax + gap / 2 - r, ay);
  ctx.stroke();
}

function teeth(ctx: CanvasRenderingContext2D, mx: number, my: number, s: number) {
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "rgba(8,10,18,0.9)";
  ctx.lineWidth = Math.max(1, s * 0.012);
  ctx.beginPath();
  ctx.roundRect(mx - s * 0.05, my - s * 0.018, s * 0.1, s * 0.036, s * 0.012);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(mx - s * 0.05, my);
  ctx.lineTo(mx + s * 0.05, my);
  for (let i = -1; i <= 1; i += 1) {
    ctx.moveTo(mx + i * s * 0.025, my - s * 0.018);
    ctx.lineTo(mx + i * s * 0.025, my + s * 0.018);
  }
  ctx.stroke();
}

function fangs(ctx: CanvasRenderingContext2D, mx: number, y: number, s: number) {
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "rgba(8,10,18,0.6)";
  ctx.lineWidth = Math.max(0.6, s * 0.005);
  for (const side of [-1, 1]) {
    const x = mx + side * s * 0.018;
    ctx.beginPath();
    ctx.moveTo(x - s * 0.007, y);
    ctx.lineTo(x + s * 0.007, y);
    ctx.lineTo(x, y + s * 0.02);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
}

function drawMouth(f: FaceFrame, mx: number, my: number, ink: string) {
  const { ctx, box, activity, style, t } = f;
  const s = box.s;
  if (style === "lantern") {
    carvedMouth(f, mx, my);
    return;
  }
  const visor = style === "visor";
  const stroke = visor ? rgba(mix(f.rgb, WHITE, 0.55)) : BIG_EYES.has(style) ? "rgba(8,10,18,0.92)" : ink;
  ctx.strokeStyle = stroke;
  ctx.fillStyle = stroke;
  ctx.lineWidth = Math.max(1, s * 0.017);
  const vampire = f.season === "halloween" && !visor;
  const work = workPhase(activity, f.since);

  if (work.laptop) {
    if (activity === "reading") line(ctx, mx - s * 0.015, my, mx + s * 0.015, my);
    else if (work.furious) teeth(ctx, mx, my, s);
    else {
      // Tongue out in concentration.
      line(ctx, mx - s * 0.022, my, mx + s * 0.02, my);
      ctx.fillStyle = "#ff7a93";
      ctx.beginPath();
      ctx.ellipse(mx + s * 0.022, my + s * 0.009, s * 0.012, s * 0.009, 0.3, 0, TAU);
      ctx.fill();
    }
    return;
  }
  if (style === "pixel" && pixelMouth(f, mx, my, stroke)) return;
  if (style === "coal" && coalMouth(f, mx, my)) return;

  switch (activity) {
    case "speaking":
    case "messaging": {
      if (visor) {
        // An equalizer reading the speech.
        for (let i = -2; i <= 2; i += 1) {
          const level = 0.25 + 0.75 * Math.abs(Math.sin(t * (9 + i * 1.7) + i));
          const bh = s * 0.06 * level * (0.4 + f.talk);
          ctx.fillRect(mx + i * s * 0.026 - s * 0.008, my - bh / 2, s * 0.016, bh);
        }
        return;
      }
      const ry = s * (0.006 + 0.032 * f.talk);
      const rx = s * (0.042 - 0.01 * f.talk);
      ctx.fillStyle = "#3a0d16";
      ctx.beginPath();
      ctx.ellipse(mx, my, rx, ry, 0, 0, TAU);
      ctx.fill();
      if (ry > s * 0.02) {
        ctx.fillStyle = "#ff7a93";
        ctx.beginPath();
        ctx.ellipse(mx, my + ry * 0.5, rx * 0.55, ry * 0.4, 0, 0, TAU);
        ctx.fill();
      }
      if (vampire && ry > s * 0.015) fangs(ctx, mx, my - ry * 0.85, s);
      return;
    }
    case "dance": {
      ctx.fillStyle = "#3a0d16";
      ctx.beginPath();
      ctx.arc(mx, my - s * 0.01, s * 0.045, 0, Math.PI);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = "#ff7a93";
      ctx.beginPath();
      ctx.ellipse(mx, my + s * 0.018, s * 0.022, s * 0.012, 0, 0, TAU);
      ctx.fill();
      if (vampire) fangs(ctx, mx, my - s * 0.01, s);
      return;
    }
    case "error": {
      if (BIG_EYES.has(style)) teeth(ctx, mx, my, s);
      else {
        ctx.beginPath();
        ctx.arc(mx, my + s * 0.04, s * 0.04, 1.2 * Math.PI, 1.8 * Math.PI);
        ctx.stroke();
      }
      return;
    }
    case "thinking": {
      // "Hmm": a small wavy line pushed to one side.
      ctx.beginPath();
      ctx.moveTo(mx - s * 0.015, my);
      ctx.quadraticCurveTo(mx + s * 0.005, my - s * 0.012, mx + s * 0.025, my);
      ctx.quadraticCurveTo(mx + s * 0.04, my + s * 0.01, mx + s * 0.05, my - s * 0.004);
      ctx.stroke();
      return;
    }
    case "searching": {
      ctx.beginPath();
      ctx.ellipse(mx, my, s * 0.014, s * 0.018, 0, 0, TAU);
      ctx.stroke();
      return;
    }
    case "waiting":
    case "dreaming":
    case "booting": {
      ctx.beginPath();
      ctx.arc(mx, my, s * (activity === "waiting" ? 0.013 : 0.009), 0, TAU);
      ctx.stroke();
      return;
    }
    case "offline": {
      line(ctx, mx - s * 0.02, my, mx + s * 0.02, my);
      return;
    }
    default: {
      if (style === "kawaii") {
        // A cat mouth.
        ctx.beginPath();
        ctx.arc(mx - s * 0.013, my - s * 0.004, s * 0.013, 0.1 * Math.PI, 0.9 * Math.PI);
        ctx.arc(mx + s * 0.013, my - s * 0.004, s * 0.013, 0.1 * Math.PI, 0.9 * Math.PI);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.arc(mx, my - s * 0.03, s * 0.042, 0.22 * Math.PI, 0.78 * Math.PI);
        ctx.stroke();
      }
      if (vampire) fangs(ctx, mx, my + s * 0.006, s);
    }
  }
}

/** The pixel style's mouths; false hands the activity to the shared mouths. */
function pixelMouth(f: FaceFrame, mx: number, my: number, color: string): boolean {
  const { ctx, box, activity } = f;
  const p = Math.max(1, Math.round(box.s * 0.028));
  const x0 = Math.round(mx / p) * p - p;
  const y0 = Math.round(my / p) * p;
  const px = (cx: number, cy: number, w = 1, h = 1) => ctx.fillRect(x0 + cx * p, y0 + cy * p, w * p, h * p);
  ctx.fillStyle = color;
  switch (activity) {
    case "speaking":
    case "messaging":
      px(0, -1, 3, 1 + Math.round(f.talk * 2));
      return true;
    case "error":
      px(-1, 1);
      px(3, 1);
      px(0, 0, 3, 1);
      return true;
    case "idle":
    case "dance":
    case "tasking":
      px(-1, -1);
      px(3, -1);
      px(0, 0, 3, 1);
      return true;
    default:
      return false;
  }
}

/** A snowman's coal smile; false hands the activity to the shared mouths. */
function coalMouth(f: FaceFrame, mx: number, my: number): boolean {
  const { ctx, box, activity } = f;
  const s = box.s;
  const dot = (x: number, y: number, r: number) => {
    ctx.beginPath();
    ctx.arc(x, y, Math.max(0.6, s * r), 0, TAU);
    ctx.fill();
  };
  ctx.fillStyle = "#16161a";
  if (activity === "speaking" || activity === "messaging") {
    const r = s * (0.018 + f.talk * 0.014);
    for (let i = 0; i < 6; i += 1) {
      const a = (i / 6) * TAU;
      dot(mx + Math.cos(a) * r, my + Math.sin(a) * r * 0.8, 0.007);
    }
    return true;
  }
  if (activity === "idle" || activity === "dance" || activity === "error" || activity === "tasking") {
    const flip = activity === "error" ? -1 : 1;
    for (let i = -2; i <= 2; i += 1) dot(mx + i * s * 0.022, my + flip * (s * 0.012 - i * i * s * 0.004), 0.009);
    return true;
  }
  return false;
}

/** A jack-o'-lantern's jagged grin, lit by the same candle as the eyes. */
function carvedMouth(f: FaceFrame, mx: number, my: number) {
  const { ctx, box, activity } = f;
  const s = box.s;
  const lit = !(activity === "offline" || activity === "dreaming" || activity === "booting");
  const open = activity === "speaking" || activity === "messaging" ? 0.6 + f.talk * 0.7 : 1;
  const w = s * 0.1;
  const h = s * 0.06 * open;
  ctx.save();
  candle(f, lit);
  ctx.translate(mx, my);
  if (activity === "error") ctx.scale(1, -1);
  ctx.fillStyle = carved(f, 0, 0, w, lit);
  ctx.beginPath();
  ctx.moveTo(-w, -h * 0.3);
  ctx.quadraticCurveTo(0, h * 0.25, w, -h * 0.3);
  ctx.lineTo(w * 0.75, h * 0.2);
  ctx.lineTo(w * 0.5, h * 0.05);
  ctx.lineTo(w * 0.3, h * 0.55);
  ctx.lineTo(0, h * 0.7);
  ctx.lineTo(-w * 0.3, h * 0.55);
  ctx.lineTo(-w * 0.5, h * 0.05);
  ctx.lineTo(-w * 0.75, h * 0.2);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}
