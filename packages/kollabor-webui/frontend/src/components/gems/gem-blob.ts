import {
  Color,
  Float32BufferAttribute,
  IcosahedronGeometry,
  ShaderMaterial,
  SRGBColorSpace,
  Vector3,
  Vector4,
  type BufferAttribute,
  type BufferGeometry,
} from "three";
import { mergeVertices } from "three/addons/utils/BufferGeometryUtils.js";
import type { GemCut } from "./gem-specs";

/**
 * The gem's body: one sphere mesh that morphs between soft shapes. Each shape
 * is a radius along every direction from the center, so any mix of shapes is
 * a weighted sum per vertex. The vertex shader does the sum from per-shape
 * tables (the radius and an unnormalized normal, r·d − ∇r, which blends
 * exactly), so a morph costs a few uniforms. The same radial functions seat
 * the face on the surface on the CPU (bodyPoint).
 */

const TAU = Math.PI * 2;

type Radial = (x: number, y: number, z: number) => number;
type Plane = readonly [number, number, number, number];

/** A rounded polyhedron: a ray leaves through its nearest plane; the soft max rounds the edges. */
function polyhedron(planes: Plane[], sharp: number): Radial {
  const v = new Float64Array(planes.length);
  return (x, y, z) => {
    let m = -Infinity;
    for (let i = 0; i < planes.length; i += 1) {
      const [a, b, c, d] = planes[i];
      v[i] = (a * x + b * y + c * z) / d;
      m = Math.max(m, v[i]);
    }
    let s = 0;
    for (let i = 0; i < v.length; i += 1) s += Math.exp(sharp * (v[i] - m));
    return 1 / (m + Math.log(s) / sharp);
  };
}

/** `count` planes around the vertical axis; (nr, ny) is the normal in the radius-height plane. */
function ring(count: number, offset: number, nr: number, ny: number, d: number): Plane[] {
  const len = Math.hypot(nr, ny);
  return Array.from({ length: count }, (_, i) => {
    const a = offset + (i / count) * TAU;
    return [(Math.cos(a) * nr) / len, ny / len, (Math.sin(a) * nr) / len, d / len] as const;
  });
}

const superellipsoid =
  (a: number, b: number, c: number, p: number): Radial =>
  (x, y, z) =>
    (Math.abs(x / a) ** p + Math.abs(y / b) ** p + Math.abs(z / c) ** p) ** (-1 / p);

/** A cloud: a soft union of spheres with a flattened bottom. */
function puffs(spheres: [number, number, number, number][], floor: number): Radial {
  return (x, y, z) => {
    let m = -Infinity;
    const hits: number[] = [];
    for (const [cx, cy, cz, r] of spheres) {
      const b = x * cx + y * cy + z * cz;
      const disc = b * b - (cx * cx + cy * cy + cz * cz) + r * r;
      if (disc > 0) {
        hits.push(b + Math.sqrt(disc));
        m = Math.max(m, hits[hits.length - 1]);
      }
    }
    let s = 0;
    for (const h of hits) s += Math.exp(9 * (h - m));
    const out = m + Math.log(s) / 9;
    if (y > -0.05) return out;
    const f = floor / -y;
    return -Math.log(Math.exp(-12 * out) + Math.exp(-12 * f)) / 12;
  };
}

export const SHAPES = ["brilliant", "cushion", "emerald", "crystal", "dome", "pebble", "cloud", "octa"] as const;
export type Shape = (typeof SHAPES)[number];

const OCTA: Plane[] = [-1, 1].flatMap((a) =>
  [-1, 1].flatMap((b) => [-1, 1].map((c) => [a / Math.sqrt(3), b / Math.sqrt(3), c / Math.sqrt(3), 0.6] as const)),
);
const RAW: Radial[] = [
  polyhedron(
    [[0, 1, 0, 0.5], ...ring(8, TAU / 16, 0.38, 0.45, 0.434), ...ring(16, 0, 1, 0, 1), ...ring(8, 0, 1.03, -1, 0.95)],
    14,
  ),
  superellipsoid(1, 0.92, 0.78, 3.2),
  superellipsoid(0.74, 1, 0.62, 4.5),
  polyhedron([...ring(6, TAU / 12, 1, 0, 0.66), ...ring(6, TAU / 12, 0.6, 0.66, 0.693), [0, -1, 0, 0.9]], 16),
  superellipsoid(1, 0.96, 0.96, 2),
  superellipsoid(1.06, 0.7, 0.9, 2.3),
  puffs(
    [
      [0, -0.05, 0, 0.62],
      [-0.52, -0.12, 0, 0.46],
      [0.52, -0.12, 0, 0.46],
      [-0.26, 0.32, 0, 0.46],
      [0.24, 0.36, 0, 0.5],
      [0, 0, 0.2, 0.56],
      [0, 0, -0.2, 0.56],
    ],
    0.6,
  ),
  polyhedron(OCTA, 10),
];

/** The shape each cut rests in. */
export const HOME_SHAPE: Record<GemCut, Shape> = {
  brilliant: "brilliant",
  pear: "brilliant",
  trillion: "brilliant",
  cushion: "cushion",
  hopper: "cushion",
  emerald: "emerald",
  crystal: "crystal",
  cabochon: "dome",
  sphere: "dome",
  octahedron: "octa",
};

/** Height of the face's center on each shape: on a brilliant's girdle, mid-body elsewhere. */
const FACE_Y = [0, 0.06, 0.06, -0.04, 0.04, 0.02, 0, -0.04];

/** The face's height for a shape mix. */
export function faceHeight(w: ArrayLike<number>): number {
  let y = 0;
  let total = 0;
  for (let i = 0; i < FACE_Y.length; i += 1) {
    const k = Math.max(0, w[i]);
    y += k * FACE_Y[i];
    total += k;
  }
  return total ? y / total : 0;
}

// Detail 16: 2892 vertices, smooth at the Gem Studio's hero size.
const DETAIL = 16;
let tables: { geometry: BufferGeometry; scale: Float64Array } | null = null;

/** Samples every shape once per vertex, each normalized to the same outer radius. */
function build() {
  const ico = new IcosahedronGeometry(1, DETAIL);
  ico.deleteAttribute("normal");
  ico.deleteAttribute("uv");
  const geometry = mergeVertices(ico);
  ico.dispose();
  const dirs = (geometry.getAttribute("position") as BufferAttribute).array as Float32Array;
  const count = dirs.length / 3;
  const scale = new Float64Array(SHAPES.length);
  const d = new Vector3();
  const e1 = new Vector3();
  const e2 = new Vector3();
  const q = new Vector3();
  const h = 1e-3;
  RAW.forEach((radial, s) => {
    const at = (k: number, e: Vector3) => {
      q.copy(e).multiplyScalar(k).add(d).normalize();
      return radial(q.x, q.y, q.z);
    };
    const data = new Float32Array(count * 4);
    let max = 0;
    for (let i = 0; i < count; i += 1) {
      d.fromArray(dirs, 3 * i);
      e1.set(0, Math.abs(d.y) < 0.9 ? 1 : 0, Math.abs(d.y) < 0.9 ? 0 : 1).cross(d).normalize();
      e2.crossVectors(d, e1);
      const r = radial(d.x, d.y, d.z);
      // The radius's slope along the surface, from a small step each way.
      const g1 = (at(h, e1) - r) / h;
      const g2 = (at(h, e2) - r) / h;
      data[4 * i] = r * d.x - g1 * e1.x - g2 * e2.x;
      data[4 * i + 1] = r * d.y - g1 * e1.y - g2 * e2.y;
      data[4 * i + 2] = r * d.z - g1 * e1.z - g2 * e2.z;
      data[4 * i + 3] = r;
      max = Math.max(max, r);
    }
    for (let j = 0; j < data.length; j += 1) data[j] /= max;
    scale[s] = 1 / max;
    geometry.setAttribute(`aS${s}`, new Float32BufferAttribute(data, 4));
  });
  return { geometry, scale };
}

export function bodyGeometry(): BufferGeometry {
  tables ??= build();
  return tables.geometry;
}

const wobble = (x: number, y: number, z: number, t: number) =>
  2.4 * Math.sin(3.1 * x + 1.7 * t) * Math.sin(2.7 * y + 1.3 * t + 1.2) * Math.sin(2.3 * z + 2.1 * t + 2.4);

/** The surface toward (x, y, z) for the shape mix `w`, with the shader's wobble and shear. */
export function bodyPoint(
  w: ArrayLike<number>,
  x: number,
  y: number,
  z: number,
  wob: number,
  t: number,
  shear: number,
  out: Vector3,
): Vector3 {
  tables ??= build();
  const len = Math.hypot(x, y, z);
  x /= len;
  y /= len;
  z /= len;
  let r = 0;
  for (let i = 0; i < RAW.length; i += 1) if (Math.abs(w[i]) > 1e-3) r += w[i] * RAW[i](x, y, z) * tables.scale[i];
  r *= 1 + wob * wobble(x, y, z, t);
  const py = y * r;
  return out.set(x * r + shear * (py + 1) * 0.5, py, z * r);
}

const BODY_VERT = /* glsl */ `
attribute vec4 aS0;
attribute vec4 aS1;
attribute vec4 aS2;
attribute vec4 aS3;
attribute vec4 aS4;
attribute vec4 aS5;
attribute vec4 aS6;
attribute vec4 aS7;
uniform vec4 uW0;
uniform vec4 uW1;
uniform float uWob;
uniform float uTime;
uniform float uShear;
varying vec3 vN;
varying vec3 vV;
varying float vY;
void main() {
  vec4 s = uW0.x * aS0 + uW0.y * aS1 + uW0.z * aS2 + uW0.w * aS3
    + uW1.x * aS4 + uW1.y * aS5 + uW1.z * aS6 + uW1.w * aS7;
  vec3 d = position;
  float r = s.w;
  vec3 m = s.xyz;
  // Wobble scales the radius by g; the normal picks up g's slope along the surface.
  vec3 a = vec3(3.1, 2.7, 2.3) * d + vec3(1.7, 1.3, 2.1) * uTime + vec3(0.0, 1.2, 2.4);
  vec3 sn = sin(a);
  vec3 cs = cos(a);
  vec3 grad = 2.4 * vec3(3.1 * cs.x * sn.y * sn.z, 2.7 * sn.x * cs.y * sn.z, 2.3 * sn.x * sn.y * cs.z);
  float g = 1.0 + uWob * 2.4 * sn.x * sn.y * sn.z;
  m = g * m - r * uWob * (grad - dot(grad, d) * d);
  r *= g;
  vec3 p = d * r;
  p.x += uShear * (p.y + 1.0) * 0.5;
  vec3 n = vec3(m.x, m.y - 0.5 * uShear * m.x, m.z);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vN = normalize(normalMatrix * n);
  vV = normalize(-mv.xyz);
  vY = p.y;
  gl_Position = projectionMatrix * mv;
}`;

const BODY_FRAG = /* glsl */ `
uniform vec3 uTop;
uniform vec3 uBottom;
uniform vec3 uRim;
uniform vec3 uGlowColor;
uniform float uGlow;
varying vec3 vN;
varying vec3 vV;
varying float vY;
void main() {
  vec3 n = normalize(vN);
  vec3 v = normalize(vV);
  vec3 l = normalize(vec3(-0.5, 0.8, 0.65));
  vec3 col = mix(uBottom, uTop, smoothstep(-0.95, 0.85, vY));
  col *= 0.74 + 0.36 * (dot(n, l) * 0.5 + 0.5);
  col += pow(max(dot(reflect(-l, n), v), 0.0), 16.0) * 0.1;
  float facing = max(dot(n, v), 0.0);
  col += uRim * pow(1.0 - facing, 2.5) * 0.3;
  // Lit from inside, brightest where the gem faces you.
  col = mix(col, uGlowColor, uGlow * (0.15 + 0.5 * facing * facing)) + uGlowColor * uGlow * 0.08;
  gl_FragColor = vec4(col, 1.0);
  #include <colorspace_fragment>
}`;

export function bodyMaterial(): ShaderMaterial {
  return new ShaderMaterial({
    vertexShader: BODY_VERT,
    fragmentShader: BODY_FRAG,
    uniforms: {
      uW0: { value: new Vector4() },
      uW1: { value: new Vector4() },
      uWob: { value: 0 },
      uTime: { value: 0 },
      uShear: { value: 0 },
      uTop: { value: new Color() },
      uBottom: { value: new Color() },
      uRim: { value: new Color() },
      uGlowColor: { value: new Color() },
      uGlow: { value: 0 },
    },
  });
}

const hsl = { h: 0, s: 0, l: 0 };

/**
 * Colors the body from one gem color: a smooth gradient, lighter and a touch
 * warmer on top, deeper below. Near-grays go steel blue unless `neutral` (an
 * offline gem's gray). Lightness is capped so the shading always reads.
 */
export function paintBody(material: ShaderMaterial, color: Color, neutral: boolean) {
  color.getHSL(hsl, SRGBColorSpace);
  const icy = !neutral && hsl.s < 0.15;
  const h = icy ? 0.62 : hsl.h;
  const s = neutral ? 0 : icy ? 0.22 : hsl.s;
  const l = Math.min(hsl.l, 0.72);
  const tone = (out: Color, dl: number, dh: number, ds: number) =>
    out.setHSL((h + dh + 1) % 1, Math.min(1, Math.max(0, s + ds)), Math.min(0.96, Math.max(0.05, l + dl)), SRGBColorSpace);
  const u = material.uniforms;
  tone(u.uTop.value as Color, 0.08, 0.01, -0.02);
  tone(u.uBottom.value as Color, -0.17, -0.02, 0.05);
  tone(u.uRim.value as Color, 0.22, 0.02, -0.08);
  tone(u.uGlowColor.value as Color, 0.22, 0.01, 0.08);
}
