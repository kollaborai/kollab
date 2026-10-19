import {
  BufferAttribute,
  Color,
  DoubleSide,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  Mesh,
  SRGBColorSpace,
  ShaderMaterial,
  Vector3,
} from "three";
import { COAT_GLSL, SURFACE_GLSL, bodyGeometry, setShapeWeights, surfaceUniforms } from "./gem-blob";
import type { FurStyle, HairStyle } from "./gem-face";

/**
 * Coats of strands grown from the gem's body, after the swaying-grass look:
 * thousands of thin blades swaying in gusts that roll across the coat. Hair
 * covers the head (a mohawk, a strip down its middle), fur the whole body
 * (COAT_GLSL says where); a gem can wear both. Each is the gem's own color or
 * one picked for it, darker at the root and lighter at the tip. Each blade
 * roots at a vertex of the body's shape tables, spread a little across the
 * surface, so it rides every morph, wobble and squash the body makes.
 */

const BLADES = 16000;
/** Segments along a blade: enough to bend smoothly as it sways. */
const LEVELS = 4;

const FUR_VERT = /* glsl */ `
${SURFACE_GLSL}
${COAT_GLSL}
uniform float uLength;
uniform float uWidth;
uniform float uLie;
uniform float uSway;
uniform float uClump;
uniform float uFall;
uniform float uBangs;
// A hat pulled down over the coat: its band's height and half width.
uniform float uHat;
uniform float uHatY;
uniform float uHatR;
attribute float aRoot;
attribute vec3 aDir;
attribute vec4 aRand;
varying float vP;
varying vec3 vN;
varying vec3 vT;
varying vec3 vV;
varying float vShade;
varying float vY;
float hash3(vec3 p) {
  return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453);
}
// Smooth noise over the surface, so neighbouring strands share a gust.
float gust(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  vec2 o = vec2(1.0, 0.0);
  return mix(
    mix(mix(hash3(i), hash3(i + o.xyy), f.x), mix(hash3(i + o.yxy), hash3(i + o.xxy), f.x), f.y),
    mix(mix(hash3(i + o.yyx), hash3(i + o.xyx), f.x), mix(hash3(i + o.yxx), hash3(i + o.xxx), f.x), f.y),
    f.z);
}
void main() {
  vec3 d = normalize(aDir);
  vec3 p;
  vec3 m;
  surface(int(aRoot + 0.5), d, p, m);
  vec3 n = normalize(m);
  // Off the coat a blade has no length, so it draws nothing; near its edge it thins out.
  float len = uLength * (0.7 + 0.6 * aRand.y) * smoothstep(0.0, 0.35, coat(d));
  // Over the face, long hair is cut to bangs, so it frames the face instead of covering it.
  len *= mix(1.0, uBangs, smoothstep(0.35, 0.85, d.z / max(length(d.xz), 1e-3)));
  // Under a hat pulled down over it, a strand lies pressed flat to the band and
  // comes out from under it with what is left of its length, hanging down.
  float lie = uLie;
  if (uHat > 0.5 && p.y > uHatY) {
    vec2 around = normalize(p.xz + 1e-4 * (aRand.xy - 0.5));
    vec3 exit = vec3(around.x * uHatR, uHatY, around.y * uHatR);
    len = max(0.0, len - distance(p, exit));
    p = exit;
    n = vec3(around.x, 0.0, around.y);
    m = n;
    lie = max(lie, 3.0);
  }
  // Strands lean down and away from the crown, each a little its own way.
  vec3 down = vec3(0.0, -1.0, 0.0) + n * n.y;
  down = dot(down, down) > 1e-6 ? normalize(down) : normalize(cross(n, vec3(1.0, 0.0, 0.0)));
  vec3 across = normalize(cross(n, down));
  vec3 dir = normalize(n + lie * down + (aRand.z - 0.5) * 0.6 * across);
  float t = position.y;
  // Gusts roll across the coat: each strand's phase comes from noise over the surface.
  float phase = uTime * 1.8 + 6.2832 * gust(d * 2.5 + uTime * 0.3) + aRand.x;
  vec3 swing = (across * sin(phase) + down * (0.6 + 0.4 * cos(phase))) * uSway;
  // Clumps: strands rooted in one cell of the surface meet at a point, so a spiky style reads as spikes.
  vec3 toward = (normalize((floor(d * 7.0) + 0.5) / 7.0) - d) * length(p);
  vec3 spine = p + n * 0.003 + (dir * t + swing * t * t) * len + toward * uClump * t;
  // Long hair falls, resting on the head instead of passing through it.
  spine.y -= uFall * t * t * len;
  float rest = length(p) + 0.004;
  spine *= max(1.0, rest / max(length(spine), 1e-4));
  vec4 mv = modelViewMatrix * vec4(spine, 1.0);
  vec3 grow = (dir + swing * 2.0 * t) * len + toward * uClump - vec3(0.0, 2.0 * uFall * t * len, 0.0);
  vec3 tv = normalize(mat3(modelViewMatrix) * grow);
  // A ribbon turned to face the camera, tapering to its tip.
  vec3 side = cross(tv, vec3(0.0, 0.0, 1.0));
  side = dot(side, side) > 1e-8 ? normalize(side) : vec3(1.0, 0.0, 0.0);
  mv.xyz += side * position.x * uWidth * (1.0 - t) * step(1e-4, len);
  vP = t;
  vN = normalize(normalMatrix * m);
  vT = tv;
  vV = normalize(-mv.xyz);
  vShade = aRand.w;
  vY = p.y;
  gl_Position = projectionMatrix * mv;
}`;

const FUR_FRAG = /* glsl */ `
uniform vec3 uTop;
uniform vec3 uBottom;
uniform vec3 uTip;
uniform float uDark;
uniform float uGloss;
varying float vP;
varying vec3 vN;
varying vec3 vT;
varying vec3 vV;
varying float vShade;
varying float vY;
void main() {
  // The body's key light, up and to the left.
  vec3 key = normalize(vec3(-0.5, 0.8, 0.65));
  vec3 n = normalize(vN);
  vec3 v = normalize(vV);
  vec3 t = normalize(vT);
  float wrap = dot(n, key) * 0.5 + 0.5;
  // The base is the gem's color where the strand leaves the body (darkened by uDark), lit as the body is; it turns to the tip color along the strand.
  vec3 gem = mix(uBottom, uTop, smoothstep(-0.95, 0.85, vY)) * (1.0 - uDark);
  vec3 col = mix(gem, uTip, smoothstep(0.0, 1.0, vP)) * mix(1.0, 0.8 + 0.4 * vShade, vP) * (0.62 + 0.42 * wrap);
  // Light running along each strand where it turns toward the light (Kajiya-Kay).
  float th = dot(t, normalize(key + v));
  col += uGloss * pow(sqrt(max(0.0, 1.0 - th * th)), 48.0) * vP * wrap;
  gl_FragColor = vec4(col, 1.0);
  #include <colorspace_fragment>
}`;

export type CoatStyle = {
  /** Where it grows (COAT_GLSL): 1 the head, 2 all over. */
  mode: number;
  length: number;
  /** How far strands lie down along the surface; 0 stands them straight out. */
  lie: number;
  sway: number;
  /** How far strands gather into pointed clumps (spikes), how far they fall (long hair), and its length over the face (1: the same). */
  clump: number;
  fall: number;
  bangs: number;
  /** Sheen along the strands. */
  gloss: number;
  /** How much darker than the coat's color its roots are, and how far its tips lighten toward white. */
  dark: number;
  tip: number;
  /** The hairline over the face's height, how far it drops toward the back, and a mohawk's half width (0: a full head). */
  hairline: number;
  back: number;
  strip: number;
};

/**
 * How each style grows. Read every frame, so the dev tuning page
 * (coat-tuner.html) can change them live.
 */
export const HAIR_COATS: Record<HairStyle, CoatStyle> = {
  short: { mode: 1, length: 0.2, lie: 2.2, sway: 0.1, clump: 0, fall: 0, bangs: 1, gloss: 0.3, dark: 0.65, tip: 0.05, hairline: 0.42, back: 0.3, strip: 0 },
  long: { mode: 1, length: 0.55, lie: 1.6, sway: 0.2, clump: 0.15, fall: 1, bangs: 0.3, gloss: 0.3, dark: 0.65, tip: 0.05, hairline: 0.42, back: 0.7, strip: 0 },
  spiky: { mode: 1, length: 0.3, lie: 0.4, sway: 0.07, clump: 0.9, fall: 0, bangs: 1, gloss: 0.25, dark: 0.65, tip: 0.1, hairline: 0.71, back: 0, strip: 0.6 },
  mohawk: { mode: 1, length: 0.6, lie: 0.1, sway: 0.05, clump: 0.8, fall: 0, bangs: 1, gloss: 0.25, dark: 0.6, tip: 0.08, hairline: 0.3, back: 0.7, strip: 0.32 },
};
export const FUR_COATS: Record<FurStyle, CoatStyle> = {
  short: { mode: 2, length: 0.16, lie: 0.3, sway: 0.35, clump: 0, fall: 0, bangs: 1, gloss: 0.15, dark: 0, tip: 0.25, hairline: 0, back: 0, strip: 0 },
  shaggy: { mode: 2, length: 0.3, lie: 1.2, sway: 0.45, clump: 0.35, fall: 0.3, bangs: 1, gloss: 0.1, dark: 0.15, tip: 0.2, hairline: 0, back: 0, strip: 0 },
};

/** How far a coat stands above the crown, in body units: where a perched hat sits on it. */
export function coatCrown(style: CoatStyle | undefined): number {
  return style ? style.length / Math.hypot(1, style.lie) : 0;
}

/** How thick a coat lies pressed under a hat pulled down over it, in body units. */
export function coatPress(style: CoatStyle | undefined): number {
  return style ? 0.12 * style.length : 0;
}

const blades = new Map<number, InstancedBufferGeometry>();

/**
 * One tapered blade, instanced over the body: x across it (-0.5..0.5), y
 * along it (0..1). Roots only where the coat can grow (`lowest`, the lowest
 * root direction's y), so hair spends its blades on the head.
 */
function bladeGeometry(lowest: number): InstancedBufferGeometry {
  const corners: number[] = [];
  for (let j = 0; j < LEVELS; j += 1) corners.push(-0.5, j / LEVELS, 0, 0.5, j / LEVELS, 0);
  corners.push(0, 1, 0);
  const index: number[] = [];
  for (let j = 0; j < LEVELS - 1; j += 1) index.push(2 * j, 2 * j + 1, 2 * j + 2, 2 * j + 1, 2 * j + 3, 2 * j + 2);
  index.push(2 * LEVELS - 2, 2 * LEVELS - 1, 2 * LEVELS);
  const geometry = new InstancedBufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(new Float32Array(corners), 3));
  geometry.setIndex(index);

  // Roots on the full body mesh's vertices, the rows of its shape tables, in a fixed order so a gem's coat never reshuffles.
  const all = bodyGeometry().getAttribute("position") as BufferAttribute;
  const rooted: number[] = [];
  for (let i = 0; i < all.count; i += 1) if (all.getY(i) >= lowest) rooted.push(i);
  const roots = new Float32Array(BLADES);
  const aim = new Float32Array(BLADES * 3);
  const rand = new Float32Array(BLADES * 4);
  const d = new Vector3();
  const e1 = new Vector3();
  const e2 = new Vector3();
  let seed = 20261018;
  const random = () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  for (let i = 0; i < BLADES; i += 1) {
    const vertex = rooted[Math.floor(random() * rooted.length)];
    d.fromBufferAttribute(all, vertex);
    e1.set(0, Math.abs(d.y) < 0.9 ? 1 : 0, Math.abs(d.y) < 0.9 ? 0 : 1).cross(d).normalize();
    e2.crossVectors(d, e1);
    // Spread over the patch around its vertex, about the mesh's spacing.
    d.addScaledVector(e1, (random() - 0.5) * 0.05).addScaledVector(e2, (random() - 0.5) * 0.05).normalize();
    roots[i] = vertex;
    d.toArray(aim, 3 * i);
    for (let j = 0; j < 4; j += 1) rand[4 * i + j] = random();
  }
  geometry.setAttribute("aRoot", new InstancedBufferAttribute(roots, 1));
  geometry.setAttribute("aDir", new InstancedBufferAttribute(aim, 3));
  geometry.setAttribute("aRand", new InstancedBufferAttribute(rand, 4));
  geometry.instanceCount = BLADES;
  return geometry;
}

/** A coat's mesh: a child of the body, so it takes the body's pose. Hidden until a gem wears it. */
export function coatMesh(kind: "hair" | "fur"): Mesh {
  const lowest = kind === "hair" ? -0.3 : -2;
  const geometry = blades.get(lowest) ?? bladeGeometry(lowest);
  blades.set(lowest, geometry);
  const material = new ShaderMaterial({
    vertexShader: FUR_VERT,
    fragmentShader: FUR_FRAG,
    side: DoubleSide,
    uniforms: {
      ...surfaceUniforms(),
      uCoat: { value: 0 },
      uHairline: { value: 0.4 },
      uBack: { value: 0 },
      uStrip: { value: 0 },
      uLength: { value: 0 },
      uWidth: { value: 0 },
      uLie: { value: 0 },
      uSway: { value: 0 },
      uClump: { value: 0 },
      uFall: { value: 0 },
      uBangs: { value: 1 },
      uHat: { value: 0 },
      uHatY: { value: 0 },
      uHatR: { value: 0 },
      uTop: { value: new Color() },
      uBottom: { value: new Color() },
      uTip: { value: new Color() },
      uDark: { value: 0 },
      uGloss: { value: 0 },
    },
  });
  const mesh = new Mesh(geometry, material);
  mesh.frustumCulled = false;
  mesh.userData.kind = kind;
  mesh.visible = false;
  return mesh;
}

const WHITE = new Color(1, 1, 1);
const picked = new Color();
const hsl = { h: 0, s: 0, l: 0 };

/**
 * Dresses one coat for one gem's frame, after the body's own uniforms are set
 * (its shape, wobble and colors): off without a style; otherwise in `color`,
 * or the body's colors when that is "gem" or unset. Offline, a picked color
 * goes nearly gray as the body does. A small gem gets fewer, wider strands,
 * so they still read as a coat. `face` is the face's height (gem-blob.ts
 * faceHeight); `unitPx` is pixels per body unit at this gem's size; `band` is
 * a hat pulled down over the coat (body units), which presses it flat.
 */
export function dressCoat(
  coat: Mesh,
  body: ShaderMaterial,
  style: CoatStyle | undefined,
  color: readonly number[] | "gem" | null | undefined,
  gem: { weights: ArrayLike<number>; rest: number; face: number; unitPx: number },
  band: { y: number; r: number } | null = null,
) {
  const b = body.uniforms;
  const hair = coat.userData.kind === "hair";
  // The body is shared by every gem: clear what the last one's coat left on it.
  if (hair) b.uScalpMix.value = 0;
  else b.uUnderFurMix.value = 0;
  coat.visible = !!style;
  if (!style) return;
  const material = coat.material as ShaderMaterial;
  const u = material.uniforms;
  u.uShapes.value = bodyGeometry().userData.shapes;
  setShapeWeights(material, gem.weights);
  u.uWob.value = b.uWob.value;
  u.uTime.value = b.uTime.value;
  u.uShear.value = b.uShear.value;
  u.uCoat.value = style.mode;
  u.uHairline.value = gem.face + style.hairline;
  u.uBack.value = style.back;
  u.uStrip.value = style.strip;
  u.uLength.value = style.length;
  u.uLie.value = style.lie;
  u.uSway.value = style.sway;
  u.uClump.value = style.clump;
  u.uFall.value = style.fall;
  u.uBangs.value = style.bangs;
  u.uHat.value = band ? 1 : 0;
  u.uHatY.value = band?.y ?? 0;
  u.uHatR.value = band?.r ?? 0;
  u.uGloss.value = style.gloss * (1 - gem.rest);
  u.uDark.value = style.dark;
  u.uWidth.value = Math.max(0.012, 1.1 / gem.unitPx);
  (coat.geometry as InstancedBufferGeometry).instanceCount = Math.round(BLADES * Math.min(1, Math.max(0.12, (gem.unitPx / 150) ** 1.2)));
  const top = u.uTop.value as Color;
  const bottom = u.uBottom.value as Color;
  if (Array.isArray(color)) {
    // The body's offline look: darker, nearly gray with a hint of its hue.
    picked.setRGB(color[0] / 255, color[1] / 255, color[2] / 255, SRGBColorSpace).multiplyScalar(1 - 0.45 * gem.rest);
    picked.getHSL(hsl, SRGBColorSpace);
    const s = hsl.s + (Math.min(hsl.s, 0.12) - hsl.s) * gem.rest;
    top.setHSL(hsl.h, s, Math.min(0.96, hsl.l + 0.06), SRGBColorSpace);
    bottom.setHSL(hsl.h, s, Math.max(0.02, hsl.l - 0.08), SRGBColorSpace);
  } else {
    top.copy(b.uTop.value as Color);
    bottom.copy(b.uBottom.value as Color);
  }
  (u.uTip.value as Color).copy(top).lerp(WHITE, style.tip);
  if (!Array.isArray(color)) return;
  const under = (hair ? b.uScalp.value : b.uUnderFur.value) as Color;
  under.copy(bottom).lerp(top, 0.5).multiplyScalar(1 - style.dark);
  if (hair) {
    b.uScalpMix.value = 0.9;
    b.uCoat.value = style.mode;
    b.uHairline.value = u.uHairline.value;
    b.uBack.value = style.back;
    b.uStrip.value = style.strip;
  } else {
    b.uUnderFurMix.value = 0.85;
  }
}
