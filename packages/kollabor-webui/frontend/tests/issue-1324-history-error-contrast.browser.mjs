import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const assetsDir = path.join(frontendRoot, "dist", "assets");
const stylesheet = fs
  .readdirSync(assetsDir)
  .find((name) => name.startsWith("index-") && name.endsWith(".css"));
assert.ok(stylesheet, "build the web UI before running this proof");

const outputDir = process.argv[2] ?? path.join(os.tmpdir(), "kollab-issue-1324");
fs.mkdirSync(outputDir, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });

try {
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
    isMobile: true,
    hasTouch: true,
  });
  await page.setContent(`
    <main class="min-h-screen bg-background p-4 text-foreground">
      <h1 class="mb-4 text-lg font-semibold">Conversation</h1>
      <div data-slot="history-status" role="alert"
        class="mx-auto flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-xs bg-destructive/10 text-destructive">
        <span>Couldn't load older messages. Check the connection and retry.</span>
        <button class="rounded-md border px-2 py-1 text-xs">Retry</button>
      </div>
    </main>
  `);
  await page.addStyleTag({ path: path.join(assetsDir, stylesheet) });

  const contrast = await page.locator('[data-slot="history-status"]').evaluate((banner) => {
    const parseOklch = (value) => {
      const [, lightness, chroma, hue] = value.match(/oklch\(\s*([\d.]+)%?\s+([\d.]+)\s+([\d.]+)/) ?? [];
      if (!lightness) throw new Error(`unexpected destructive token: ${value}`);
      const L = Number(lightness) / (value.includes(`${lightness}%`) ? 100 : 1);
      const C = Number(chroma);
      const angle = (Number(hue) * Math.PI) / 180;
      const a = C * Math.cos(angle);
      const b = C * Math.sin(angle);
      const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
      const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
      const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
      return [
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
      ].map((channel) => {
        const clipped = Math.max(0, Math.min(1, channel));
        return clipped <= 0.0031308 ? 12.92 * clipped : 1.055 * clipped ** (1 / 2.4) - 0.055;
      });
    };
    const srgbToLinear = (value) =>
      value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    const luminance = (rgb) =>
      rgb.reduce((total, channel, index) => total + srgbToLinear(channel) * [0.2126, 0.7152, 0.0722][index], 0);
    const foreground = parseOklch(getComputedStyle(banner).color);
    const background = parseOklch(getComputedStyle(document.documentElement).getPropertyValue("--destructive"));
    // The banner tint is destructive at 10% over the light page background.
    const panel = background.map((channel) => channel * 0.1 + 0.9);
    const foregroundLuminance = luminance(foreground);
    const panelLuminance = luminance(panel);
    const ratio = (Math.max(foregroundLuminance, panelLuminance) + 0.05) /
      (Math.min(foregroundLuminance, panelLuminance) + 0.05);
    return { ratio, fontSize: getComputedStyle(banner).fontSize };
  });

  assert.ok(contrast.ratio >= 4.5, `history error contrast ${contrast.ratio.toFixed(2)}:1 is below 4.5:1`);
  const screenshot = path.join(outputDir, "proof-history-error-phone-light.png");
  await page.screenshot({ path: screenshot, fullPage: true, animations: "disabled" });
  console.log(`PASS: history error contrast ${contrast.ratio.toFixed(2)}:1 at ${contrast.fontSize}`);
  console.log(`Screenshot: ${path.basename(screenshot)}`);
} finally {
  await browser.close();
}
