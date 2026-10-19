// Run: node --test tests/gem-fur.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import { Color, type InstancedBufferGeometry, type ShaderMaterial } from "three";
import { bodyMaterial, homeWeights, paintBody } from "../src/components/gems/gem-blob.ts";

// gem-fur.ts imports "./gem-blob" the way Vite resolves it; node needs the extension.
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
const { FUR_COATS, HAIR_COATS, coatCrown, coatMesh, dressCoat } = await import("../src/components/gems/gem-fur.ts");

type Style = Parameters<typeof dressCoat>[2];
function dressed(style: Style, unitPx = 150, rest = 0, color: Parameters<typeof dressCoat>[3] = undefined, kind: "hair" | "fur" = "hair") {
  const body = bodyMaterial();
  paintBody(body, new Color("#2f5fd0"), rest);
  const coat = coatMesh(kind);
  dressCoat(coat, body, style, color, { weights: homeWeights("person"), rest, face: 0.1, unitPx });
  const u = (coat.material as ShaderMaterial).uniforms;
  return { body: body.uniforms, coat, u, geometry: coat.geometry as InstancedBufferGeometry };
}

test("a coat grows only with a style: hair on the head over its hairline, fur all over", () => {
  assert.equal(dressed(undefined).coat.visible, false);
  const hair = dressed(HAIR_COATS.mohawk);
  assert.equal(hair.coat.visible, true);
  assert.equal(hair.u.uCoat.value, 1);
  assert.equal(hair.u.uHairline.value, 0.1 + HAIR_COATS.mohawk.hairline);
  assert.equal(hair.u.uStrip.value, HAIR_COATS.mohawk.strip);
  assert.equal(dressed(FUR_COATS.short, 150, 0, undefined, "fur").u.uCoat.value, 2);
});

test("a coat in the gem's color roots in the body's colors; a picked one in its own, the body under it too", () => {
  const gem = dressed(HAIR_COATS.short, 150, 0, "gem");
  assert.ok((gem.u.uTop.value as Color).equals(gem.body.uTop.value));
  assert.ok((gem.u.uBottom.value as Color).equals(gem.body.uBottom.value));
  assert.equal(gem.body.uScalpMix.value, 0, "the body keeps its own color under gem-colored hair");
  assert.ok((gem.u.uTip.value as Color).equals(gem.body.uTop.value.clone().lerp(new Color(1, 1, 1), HAIR_COATS.short.tip)));

  const brown = dressed(HAIR_COATS.short, 150, 0, [95, 60, 35]);
  assert.ok(!(brown.u.uTop.value as Color).equals(brown.body.uTop.value));
  assert.equal(brown.body.uScalpMix.value, 0.9);
  const fur = dressed(FUR_COATS.short, 150, 0, [225, 190, 120], "fur");
  assert.equal(fur.body.uUnderFurMix.value, 0.85);
  assert.equal(fur.body.uScalpMix.value, 0, "fur leaves the hair's tint alone");
});

test("offline, a coat loses its sheen and a picked color goes nearly gray", () => {
  const live = dressed(HAIR_COATS.short, 150, 0, [150, 60, 35]);
  const offline = dressed(HAIR_COATS.short, 150, 1, [150, 60, 35]);
  assert.equal(offline.u.uGloss.value, 0);
  const hsl = { h: 0, s: 0, l: 0 };
  const saturation = (u: typeof live.u) => (u.uTop.value as Color).getHSL(hsl).s;
  assert.ok(saturation(offline.u) < saturation(live.u) / 2);
});

test("a small gem draws fewer, wider strands", () => {
  const all = dressed(FUR_COATS.short, 150, 0, undefined, "fur").geometry.getAttribute("aRoot").count;
  assert.equal(dressed(FUR_COATS.short, 600, 0, undefined, "fur").geometry.instanceCount, all);
  assert.equal(dressed(FUR_COATS.short, 5, 0, undefined, "fur").geometry.instanceCount, Math.round(all * 0.12));
  assert.ok(dressed(FUR_COATS.short, 20).u.uWidth.value > dressed(FUR_COATS.short, 150).u.uWidth.value);
  assert.equal(dressed(FUR_COATS.short, 600).u.uWidth.value, 0.012);
});

test("a hat rides higher on standing hair than on hair lying flat", () => {
  assert.equal(coatCrown(undefined), 0);
  assert.ok(coatCrown(HAIR_COATS.mohawk) > coatCrown(HAIR_COATS.short));
});

test("every knob the Studio shows is a field of every coat style", async () => {
  const { COAT_KNOBS } = await import("../src/components/gems/gem-face.ts");
  for (const style of [...Object.values(HAIR_COATS), ...Object.values(FUR_COATS)]) {
    for (const knob of COAT_KNOBS) assert.equal(typeof style[knob.key], "number", knob.key);
  }
});
