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
const { COATS, coatMesh, dressCoat } = await import("../src/components/gems/gem-fur.ts");

function dressed(hat: Parameters<typeof dressCoat>[2], unitPx = 150, rest = 0) {
  const body = bodyMaterial();
  paintBody(body, new Color("#2f5fd0"), rest);
  const coat = coatMesh();
  dressCoat(coat, body, hat, { weights: homeWeights("person"), rest, hairline: 0.5, unitPx });
  const u = (coat.material as ShaderMaterial).uniforms;
  return { body: body.uniforms, coat, u, geometry: coat.geometry as InstancedBufferGeometry };
}

test("only hair and fur grow a coat: hair above the hairline, fur everywhere", () => {
  for (const hat of [undefined, "auto", "none", "crown"] as const) assert.equal(dressed(hat).coat.visible, false, hat);
  const hair = dressed("hair");
  assert.equal(hair.coat.visible, true);
  assert.equal(hair.u.uCoat.value, 1);
  assert.equal(hair.u.uHairline.value, 0.5);
  assert.equal(dressed("fur").u.uCoat.value, 2);
});

test("strands root in the gem's own colors and tip toward white", () => {
  const { body, u } = dressed("hair");
  assert.ok((u.uTop.value as Color).equals(body.uTop.value));
  assert.ok((u.uBottom.value as Color).equals(body.uBottom.value));
  assert.equal(u.uDark.value, COATS.hair?.dark);
  assert.ok((u.uTip.value as Color).equals(body.uTop.value.clone().lerp(new Color(1, 1, 1), COATS.hair?.tip)));
  assert.equal(dressed("hair", 150, 1).u.uGloss.value, 0, "an offline gem's coat loses its sheen");
});

test("a small gem draws fewer, wider strands", () => {
  const all = dressed("fur").geometry.getAttribute("aRoot").count;
  assert.equal(dressed("fur", 600).geometry.instanceCount, all);
  assert.equal(dressed("fur", 5).geometry.instanceCount, Math.round(all * 0.12));
  assert.ok(dressed("fur", 40).geometry.instanceCount < all);
  assert.ok(dressed("fur", 20).u.uWidth.value > dressed("fur", 150).u.uWidth.value);
  assert.equal(dressed("fur", 600).u.uWidth.value, 0.012);
});
