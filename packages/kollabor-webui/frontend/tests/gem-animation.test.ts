// Run: node --test tests/gem-animation.test.ts
import assert from "node:assert/strict";
import test from "node:test";
import { Vector3 } from "three";
import { fitPaintCanvas, nextFrameAt, Spring } from "../src/components/gems/gem-animation.ts";
import { bodyGeometry, bodyPoint, SHAPES } from "../src/components/gems/gem-blob.ts";

test("the initial canvas fits the entire face even when only its height is too small", () => {
  const canvas = { width: 300, height: 150 };
  fitPaintCanvas(canvas, 277); // A 96px avatar on a 1.8 DPR display.
  assert.deepEqual(canvas, { width: 300, height: 300 });
  fitPaintCanvas(canvas, 448);
  fitPaintCanvas(canvas, 102); // A sidebar draw after a hero must not shrink the scratch layer.
  assert.deepEqual(canvas, { width: 448, height: 448 });
});

test("a paint canvas that already fits is not cleared by assigning its dimensions again", () => {
  const canvas = Object.freeze({ width: 300, height: 300 });
  assert.doesNotThrow(() => fitPaintCanvas(canvas, 184));
});

test("30fps deadlines retain their remainder on 60, 90, 120 and 144 Hz displays", () => {
  for (const hz of [60, 90, 120, 144]) {
    let deadline = 0;
    let frames = 0;
    for (let tick = 0; tick < hz * 10; tick += 1) {
      const now = 1 + tick / hz;
      if (now + 0.001 < deadline) continue;
      frames += 1;
      deadline = nextFrameAt(deadline, now, 1 / 30);
    }
    assert.ok(Math.abs(frames - 300) <= 1, `${hz}Hz drew ${frames} frames instead of 300`);
  }
});

test("missed frames and static poses resume without a catch-up burst", () => {
  assert.equal(nextFrameAt(1, 30, 1 / 30), 30 + 1 / 30);
  assert.equal(nextFrameAt(Infinity, 30, 1 / 60), 30 + 1 / 60);
  assert.equal(nextFrameAt(30, 30, Infinity), Infinity);
});

test("springs remain consistent when only drawn frames advance and bound long absences", () => {
  const run = (hz: number) => {
    const spring = new Spring(0);
    for (let i = 0; i < hz; i += 1) spring.to(1, 55, 14, 1 / hz);
    return spring.x;
  };
  assert.ok(Math.abs(run(30) - run(120)) < 0.01);
  const resumed = new Spring(0);
  const bounded = new Spring(0);
  assert.equal(resumed.to(1, 55, 14, 60), bounded.to(1, 55, 14, 0.1));
  resumed.set(0);
  assert.equal(resumed.v, 0);
  assert.equal(resumed.to(1, 55, 14, 0), 0);
});

test("compact and hero meshes are shared, finite and agree with CPU surface seating", () => {
  const full = bodyGeometry();
  const compact = bodyGeometry(true);
  assert.equal(full.getAttribute("position").count, 2892);
  assert.equal(compact.getAttribute("position").count, 812);
  assert.equal(bodyGeometry(), full);
  assert.equal(bodyGeometry(true), compact);
  for (const geometry of [full, compact]) {
    const position = geometry.getAttribute("position");
    for (let shape = 0; shape < SHAPES.length; shape += 1) {
      const weights = SHAPES.map((_, i) => Number(i === shape));
      const samples = geometry.getAttribute(`aS${shape}`);
      assert.ok([...samples.array].every(Number.isFinite));
      for (let i = 0; i < position.count; i += 23) {
        const direction = new Vector3().fromBufferAttribute(position, i);
        const surface = bodyPoint(weights, direction.x, direction.y, direction.z, 0, 0, 0, new Vector3());
        assert.ok(Math.abs(surface.length() - samples.getW(i)) < 1e-6, `shape ${shape}, vertex ${i}`);
      }
    }
  }
});
