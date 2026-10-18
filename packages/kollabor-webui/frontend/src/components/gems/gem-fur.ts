import {
  BufferAttribute,
  Color,
  DoubleSide,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  Mesh,
  ShaderMaterial,
  Vector3,
} from "three";
import { SURFACE_GLSL, bodyGeometry, setShapeWeights, surfaceUniforms } from "./gem-blob";
import type { HatStyle } from "./gem-face";

/**
 * A coat of strands grown from the gem's body, after the swaying-grass look:
 * thousands of thin blades swaying in gusts that roll across the coat, all in
 * the gem's own hue: hair starts dark at the root and brightens to the gem's
 * color at the tip; fur starts in the gem's color and lightens. Hair covers
 * the top of the head, fur the whole body (COAT_GLSL says where). Each blade
 * roots at a vertex of the body's shape tables, spread a little across the
 * surface, so it rides every morph, wobble and squash the body makes.
 */

const BLADES = 16000;
/** Segments along a blade: enough to bend smoothly as it sways. */
const LEVELS = 4;

/**
 * Where a coat grows: nowhere (uCoat 0), the top of the head (1) above a
 * hairline over the brow that drops toward the back, or all over (2).
 */
const COAT_GLSL = /* glsl */ `
uniform float uCoat;
uniform float uHairline;
float coat(vec3 d) {
  if (uCoat < 0.5) return 0.0;
  if (uCoat > 1.5) return 1.0;
  float front = d.z / max(length(d.xz), 1e-3);
  float line = uHairline - 0.38 * (1.0 - front);
  return smoothstep(line, line + 0.12, d.y);
}`;

const FUR_VERT = /* glsl */ `
${SURFACE_GLSL}
${COAT_GLSL}
uniform float uLength;
uniform float uWidth;
uniform float uLie;
uniform float uSway;
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
  // Strands lean down and away from the crown, each a little its own way.
  vec3 down = vec3(0.0, -1.0, 0.0) + n * n.y;
  down = dot(down, down) > 1e-6 ? normalize(down) : normalize(cross(n, vec3(1.0, 0.0, 0.0)));
  vec3 across = normalize(cross(n, down));
  vec3 dir = normalize(n + uLie * down + (aRand.z - 0.5) * 0.6 * across);
  // Off the coat a blade has no length, so it draws nothing; near its edge it thins out.
  float len = uLength * (0.7 + 0.6 * aRand.y) * smoothstep(0.0, 0.35, coat(d));
  float t = position.y;
  // Gusts roll across the coat: each strand's phase comes from noise over the surface.
  float phase = uTime * 1.8 + 6.2832 * gust(d * 2.5 + uTime * 0.3) + aRand.x;
  vec3 swing = (across * sin(phase) + down * (0.6 + 0.4 * cos(phase))) * uSway;
  vec3 spine = p + n * 0.003 + (dir * t + swing * t * t) * len;
  vec4 mv = modelViewMatrix * vec4(spine, 1.0);
  vec3 tv = normalize(mat3(modelViewMatrix) * (dir + swing * 2.0 * t));
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

export type CoatStyle = { mode: number; length: number; lie: number; sway: number; gloss: number; dark: number; tip: number };

/**
 * How each coat grows: its region (COAT_GLSL), strand length, how far strands
 * lie down, sway, sheen, how much darker than the gem its roots are, and how
 * far its tips lighten toward white. Read every frame, so the dev tuning page
 * (coat-tuner.html) can change them live.
 */
export const COATS: Partial<Record<HatStyle, CoatStyle>> = {
  hair: { mode: 1, length: 0.36, lie: 2.6, sway: 0.18, gloss: 0.3, dark: 0.65, tip: 0.05 },
  fur: { mode: 2, length: 0.16, lie: 0.3, sway: 0.35, gloss: 0.15, dark: 0, tip: 0.25 },
};

let blades: InstancedBufferGeometry | null = null;

/** One tapered blade, instanced over the body: x across it (-0.5..0.5), y along it (0..1). */
function bladeGeometry(): InstancedBufferGeometry {
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
  const dirs = bodyGeometry().getAttribute("position") as BufferAttribute;
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
    const vertex = Math.floor(random() * dirs.count);
    d.fromBufferAttribute(dirs, vertex);
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

/** The coat's mesh: a child of the body, so it takes the body's pose. Hidden until a gem wears hair or fur. */
export function coatMesh(): Mesh {
  blades ??= bladeGeometry();
  const material = new ShaderMaterial({
    vertexShader: FUR_VERT,
    fragmentShader: FUR_FRAG,
    side: DoubleSide,
    uniforms: {
      ...surfaceUniforms(),
      uCoat: { value: 0 },
      uHairline: { value: 0.4 },
      uLength: { value: 0 },
      uWidth: { value: 0 },
      uLie: { value: 0 },
      uSway: { value: 0 },
      uTop: { value: new Color() },
      uBottom: { value: new Color() },
      uTip: { value: new Color() },
      uDark: { value: 0 },
      uGloss: { value: 0 },
    },
  });
  const mesh = new Mesh(blades, material);
  mesh.frustumCulled = false;
  mesh.visible = false;
  return mesh;
}

const WHITE = new Color(1, 1, 1);

/**
 * Dresses the coat for one gem's frame, after the body's own uniforms are set
 * (its shape, wobble and colors): off without hair or fur; otherwise rooted in
 * the body's colors, so an offline gem's coat goes gray with it. A small gem
 * gets fewer, wider strands, so they still read as a coat. `unitPx` is pixels
 * per world unit at this gem's size.
 */
export function dressCoat(
  coat: Mesh,
  body: ShaderMaterial,
  hat: HatStyle | "auto" | undefined,
  gem: { weights: ArrayLike<number>; rest: number; hairline: number; unitPx: number },
) {
  const style = hat && hat !== "auto" ? COATS[hat] : undefined;
  const b = body.uniforms;
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
  u.uHairline.value = gem.hairline;
  u.uLength.value = style.length;
  u.uLie.value = style.lie;
  u.uSway.value = style.sway;
  u.uGloss.value = style.gloss * (1 - gem.rest);
  u.uDark.value = style.dark;
  u.uWidth.value = Math.max(0.012, 1.1 / gem.unitPx);
  (coat.geometry as InstancedBufferGeometry).instanceCount = Math.round(BLADES * Math.min(1, Math.max(0.12, (gem.unitPx / 150) ** 1.2)));
  (u.uTop.value as Color).copy(b.uTop.value as Color);
  (u.uBottom.value as Color).copy(b.uBottom.value as Color);
  (u.uTip.value as Color).copy(b.uTop.value as Color).lerp(WHITE, style.tip);
}
