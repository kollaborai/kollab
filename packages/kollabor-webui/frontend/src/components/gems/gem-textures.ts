import { CanvasTexture, Color, SRGBColorSpace } from "three";

/**
 * Procedural surface patterns for the polished (cabochon) stones, painted on
 * a 2D canvas once per gem and wrapped around the dome: lapis calcite veins
 * and pyrite, jasper banding, jade clouds, opal play-of-color, moonstone
 * sheen. Patterns are derived from the gem's own color, so a custom pool color
 * still tints them, and seeded, so a gem looks the same on every load.
 */

export type StonePattern = "lapis" | "jasper" | "jade" | "opal" | "moonstone" | "coral";

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const css = (color: Color, alpha = 1) => {
  const c = color.clone().convertLinearToSRGB();
  return `rgba(${Math.round(c.r * 255)},${Math.round(c.g * 255)},${Math.round(c.b * 255)},${alpha})`;
};

const W = 512;
const H = 256;

export function stoneTexture(pattern: StonePattern, base: Color, seed: number): CanvasTexture | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const rand = mulberry32(Math.floor(seed * 1e6) + pattern.length);
  const blob = (fill: string | ((r: number) => string), count: number, minR: number, maxR: number) => {
    for (let i = 0; i < count; i += 1) {
      const r = minR + rand() * (maxR - minR);
      const x = rand() * W;
      const y = rand() * H;
      const gradient = ctx.createRadialGradient(x, y, 0, x, y, r);
      const color = typeof fill === "function" ? fill(r) : fill;
      gradient.addColorStop(0, color);
      gradient.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = gradient;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
    }
  };
  const blur = (px: number) => {
    const copy = document.createElement("canvas");
    copy.width = W;
    copy.height = H;
    copy.getContext("2d")?.drawImage(canvas, 0, 0);
    ctx.filter = `blur(${px}px)`;
    ctx.drawImage(copy, 0, 0);
    ctx.filter = "none";
  };
  const darker = base.clone().multiplyScalar(0.55);
  const lighter = base.clone().lerp(new Color(1, 1, 1), 0.3);

  ctx.fillStyle = css(base);
  ctx.fillRect(0, 0, W, H);

  switch (pattern) {
    case "lapis": {
      blob(css(darker, 0.55), 40, 12, 46);
      blob(css(lighter, 0.35), 30, 10, 36);
      blur(5);
      // Calcite veins.
      ctx.lineCap = "round";
      for (let i = 0; i < 6; i += 1) {
        ctx.strokeStyle = `rgba(214,224,240,${0.18 + rand() * 0.25})`;
        ctx.lineWidth = 1 + rand() * 3;
        ctx.beginPath();
        const y = rand() * H;
        ctx.moveTo(0, y);
        ctx.bezierCurveTo(W * 0.3, y + (rand() - 0.5) * 120, W * 0.6, y + (rand() - 0.5) * 120, W, y + (rand() - 0.5) * 60);
        ctx.stroke();
      }
      blur(1);
      // Pyrite specks.
      for (let i = 0; i < 160; i += 1) {
        const size = 1 + rand() * 2.2;
        ctx.fillStyle = `rgba(232,194,90,${0.55 + rand() * 0.45})`;
        ctx.fillRect(rand() * W, rand() * H, size, size);
      }
      break;
    }
    case "jasper": {
      for (let i = 0; i < 16; i += 1) {
        const shade = rand() < 0.5 ? darker : base.clone().lerp(new Color("#5a1f0a"), 0.5);
        ctx.strokeStyle = css(shade, 0.22 + rand() * 0.25);
        ctx.lineWidth = 3 + rand() * 12;
        ctx.beginPath();
        const y = rand() * H;
        const wave = 6 + rand() * 18;
        const period = 60 + rand() * 120;
        for (let x = 0; x <= W; x += 8) {
          const yy = y + Math.sin(x / period + i) * wave;
          if (x === 0) ctx.moveTo(x, yy);
          else ctx.lineTo(x, yy);
        }
        ctx.stroke();
      }
      blur(3);
      blob(css(lighter, 0.25), 18, 8, 26);
      break;
    }
    case "jade": {
      blob(css(darker, 0.4), 34, 16, 60);
      blob(css(lighter, 0.35), 30, 14, 50);
      blur(9);
      blob("rgba(20,40,20,0.5)", 26, 1, 3);
      break;
    }
    case "opal": {
      const hues = [205, 190, 160, 125, 50, 28, 330, 285];
      blob(() => {
        const hue = hues[Math.floor(rand() * hues.length)];
        return `hsla(${hue},95%,62%,0.6)`;
      }, 110, 8, 26);
      blur(3);
      ctx.fillStyle = "rgba(240,244,255,0.16)";
      ctx.fillRect(0, 0, W, H);
      break;
    }
    case "moonstone": {
      blob("hsla(212,90%,74%,0.42)", 22, 30, 80);
      blur(12);
      ctx.fillStyle = "rgba(255,255,255,0.12)";
      ctx.fillRect(0, 0, W, H);
      break;
    }
    case "coral": {
      blob(css(lighter, 0.3), 26, 10, 40);
      blur(4);
      for (let i = 0; i < 220; i += 1) {
        ctx.fillStyle = css(darker, 0.25 + rand() * 0.3);
        ctx.beginPath();
        ctx.arc(rand() * W, rand() * H, 0.6 + rand() * 1.4, 0, Math.PI * 2);
        ctx.fill();
      }
      break;
    }
  }

  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

let dot: CanvasTexture | null = null;

/** A soft round falloff, used for the light core inside a faceted gem. */
export function softDot(): CanvasTexture {
  if (dot) return dot;
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    gradient.addColorStop(0, "rgba(255,255,255,1)");
    gradient.addColorStop(0.35, "rgba(255,255,255,0.55)");
    gradient.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 64, 64);
  }
  dot = new CanvasTexture(canvas);
  return dot;
}
