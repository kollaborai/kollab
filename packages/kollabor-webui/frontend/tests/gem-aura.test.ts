// Run: node --test tests/gem-aura.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

// gem-aura.ts imports "./gem-face" the way Vite resolves it; node needs the extension.
registerHooks({
  resolve: (specifier, context, next) => {
    try {
      return next(specifier, context);
    } catch (error) {
      if (!specifier.startsWith(".")) throw error;
      return next(`${specifier}.ts`, context);
    }
  },
});
const { drawAuraBehind, drawAuraFront } = await import("../src/components/gems/gem-aura.ts");

/** A 2D context that only counts what gets filled. */
function counting() {
  let fills = 0;
  const gradient = { addColorStop: () => undefined };
  const ctx = new Proxy(
    {},
    {
      get: (_, name) =>
        name === "fill"
          ? () => {
              fills += 1;
            }
          : name.toString().startsWith("create")
            ? () => gradient
            : () => undefined,
      set: () => true,
    },
  ) as CanvasRenderingContext2D;
  return { ctx, fills: () => fills };
}

const paint = (ctx: CanvasRenderingContext2D, strength: number, auras: ("glow" | "flames" | "orbit")[]) => ({
  ctx,
  t: 1.3,
  cx: 50,
  cy: 50,
  g: 80,
  rgb: [200, 30, 50] as [number, number, number],
  strength,
  auras,
  seed: 2,
});

test("an offline gem shows no aura", () => {
  const { ctx, fills } = counting();
  drawAuraBehind(paint(ctx, 0, ["glow", "flames", "orbit"]));
  drawAuraFront(paint(ctx, 0, ["glow", "flames", "orbit"]));
  assert.equal(fills(), 0);
});

test("each aura draws only when worn, and orbs split between behind and in front", () => {
  const glow = counting();
  drawAuraBehind(paint(glow.ctx, 0.35, ["glow"]));
  assert.equal(glow.fills(), 1);
  const none = counting();
  drawAuraBehind(paint(none.ctx, 1, []));
  drawAuraFront(paint(none.ctx, 1, []));
  assert.equal(none.fills(), 0);
  const behind = counting();
  const front = counting();
  drawAuraBehind(paint(behind.ctx, 1, ["orbit"]));
  drawAuraFront(paint(front.ctx, 1, ["orbit"]));
  // Three orbs, two fills each (glow and core), split by which side of the ring they are on.
  assert.equal(behind.fills() + front.fills(), 6);
  assert.ok(behind.fills() > 0 && front.fills() > 0);
});
