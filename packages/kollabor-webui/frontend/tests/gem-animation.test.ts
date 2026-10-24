// Run: node --test tests/gem-animation.test.ts
import assert from "node:assert/strict";
import test from "node:test";
import { Vector3 } from "three";
import {
  BLINK,
  blinkOpen,
  breathCurve,
  DANCE,
  dance,
  danceClock,
  fitPaintCanvas,
  frameInterval,
  FRENZY,
  frenzy,
  nextFrameAt,
  peer,
  ponder,
  Spring,
} from "../src/components/gems/gem-animation.ts";
import { bodyGeometry, bodyPoint, SHAPE_ROW, SHAPES } from "../src/components/gems/gem-blob.ts";

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

test("breath, blink and dance curves never jump and start and end at rest", () => {
  const worstStep = (f: (p: number) => number, to: number) => {
    let worst = 0;
    for (let i = 1; i <= 4000; i += 1) worst = Math.max(worst, Math.abs(f((to * i) / 4000) - f((to * (i - 1)) / 4000)));
    return worst;
  };
  assert.ok(worstStep(breathCurve, 3) < 0.01);
  assert.equal(breathCurve(0), 0);
  assert.equal(breathCurve(0.4), 1);
  assert.ok(worstStep(blinkOpen, BLINK) < 0.01);
  assert.deepEqual([blinkOpen(0), blinkOpen(0.085), blinkOpen(BLINK)], [1, 0, 1]);
  for (const channel of ["lift", "squash", "roll", "hat"] as const) {
    assert.ok(worstStep((p) => dance(p)[channel], 1) < 0.01, channel);
    assert.equal(dance(0)[channel], 0);
    assert.ok(Math.abs(dance(1 - 1e-9)[channel]) < 1e-6, channel);
  }
  // The spin is one full turn: it leaves and lands facing front, with no jump on the way round.
  assert.ok(worstStep((p) => Math.cos(dance(p).spin), 1) < 0.01);
  assert.ok(worstStep((p) => Math.sin(dance(p).spin), 1) < 0.01);
  for (const p of [0, 1 - 1e-9]) assert.ok(Math.cos(dance(p).spin) > 1 - 1e-9);
});

test("the dance moves, and the Dance state loops it instead of standing still", () => {
  const samples = Array.from({ length: 400 }, (_, i) => dance(i / 400));
  const peak = (channel: "lift" | "roll" | "hat") => Math.max(...samples.map((move) => Math.abs(move[channel])));
  assert.ok(peak("lift") > 0.05);
  assert.ok(peak("roll") > 0.08);
  assert.ok(peak("hat") > 0.08);
  assert.ok(Math.min(...samples.map((move) => Math.cos(move.spin))) < -0.99, "it turns all the way round");
  // Shown on its own (Gem Studio's stage) it keeps dancing: most of any stretch is mid-dance.
  let dancing = 0;
  for (let since = 0; since < 30; since += 0.05) {
    const clock = danceClock(true, since, -Infinity);
    if (clock > 0 && clock < DANCE) dancing += 1;
  }
  assert.ok(dancing / 600 > 0.6);
  // After a turn it plays once, from when it started.
  assert.equal(danceClock(false, 12, 0.5), 0.5);
});

test("only big lively gems get full frames and a strained page halves every rate", () => {
  assert.equal(frameInterval(96, true, false), 1 / 60);
  assert.equal(frameInterval(28, true, false), 1 / 30);
  for (const size of [18, 28, 40, 56, 72, 140]) {
    assert.ok(frameInterval(size, false, false) >= frameInterval(size, true, false));
    assert.ok(frameInterval(size, false, false) <= 1 / 20);
    for (const lively of [true, false]) assert.equal(frameInterval(size, lively, true), 2 * frameInterval(size, lively, false));
  }
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
    // The shader reads texel (vertex * SHAPES.length + shape), SHAPE_ROW texels a row.
    const table = geometry.userData.shapes.image;
    assert.equal(table.width, SHAPE_ROW);
    assert.ok(table.width * table.height >= position.count * SHAPES.length);
    const data = table.data as Float32Array;
    assert.ok(data.every(Number.isFinite));
    for (let shape = 0; shape < SHAPES.length; shape += 1) {
      const weights = SHAPES.map((_, i) => Number(i === shape));
      for (let i = 0; i < position.count; i += 23) {
        const direction = new Vector3().fromBufferAttribute(position, i);
        const surface = bodyPoint(weights, direction.x, direction.y, direction.z, 0, 0, 0, new Vector3());
        const radius = data[(i * SHAPES.length + shape) * 4 + 3];
        assert.ok(Math.abs(surface.length() - radius) < 1e-6, `shape ${shape}, vertex ${i}`);
      }
    }
  }
});

test("the typing routine starts from rest and every channel stays continuous", () => {
  const rest = frenzy(0);
  assert.equal(rest.lean, 0);
  assert.equal(rest.pitch, 0);
  for (const seed of [0, 2.1, 5.9]) {
    let last = frenzy(0, seed);
    for (let t = 0.004; t < FRENZY * 3; t += 0.004) {
      const now = frenzy(t, seed);
      for (const key of ["lean", "pitch", "lift", "squash"] as const) {
        assert.ok(Math.abs(now[key] - last[key]) < 0.06, `${key} jumps at ${t.toFixed(3)}s (seed ${seed})`);
      }
      last = now;
    }
  }
});

test("each round leans in, jerks back with hands up, then hammers the keys", () => {
  assert.ok(frenzy(0.6).lean > 0.9, "nose to the screen");
  const back = frenzy(1.15);
  assert.ok(back.lean < -0.5 && back.pitch < 0 && back.raise > 0.9, "head back, hands up");
  const hammer = frenzy(2.4);
  assert.equal(hammer.hands, 1);
  const stomps = Array.from({ length: 60 }, (_, i) => frenzy(1.5 + i * 0.02).squash);
  assert.ok(Math.max(...stomps) > 0.03, "a stomp lands on the keys");
});

test("thinking moves the whole body and nods hmm every few seconds", () => {
  const rolls = Array.from({ length: 100 }, (_, i) => ponder(i * 0.05).roll);
  assert.ok(Math.max(...rolls) - Math.min(...rolls) > 0.2, "the head rolls side to side");
  const nod = Array.from({ length: 30 }, (_, i) => ponder(1.7 + i * 0.02).pitch);
  assert.ok(Math.max(...nod) - Math.min(...nod) > 0.1, "a hmm nod");
});

test("peering at the screen eases in from upright and sits back now and then", () => {
  assert.equal(peer(0).lean, 0);
  assert.ok(peer(1).lean > 0.7);
  const leans = Array.from({ length: 100 }, (_, i) => peer(i * 0.05).lean);
  assert.ok(Math.min(...leans.slice(20)) < 0, "sits back");
});
