import {
  Color,
  DataTexture,
  FloatType,
  IcosahedronGeometry,
  NearestFilter,
  RGBAFormat,
  ShaderMaterial,
  SRGBColorSpace,
  Vector3,
  Vector4,
  type BufferAttribute,
  type BufferGeometry,
} from "three";
import { mergeVertices } from "three/addons/utils/BufferGeometryUtils.js";
import type { GemCut, GemMaterial } from "./gem-specs";

/**
 * The gem's body: one sphere mesh that morphs between soft shapes. Each shape
 * is a radius along every direction from the center, so any mix of shapes is
 * a weighted sum per vertex. The vertex shader does the sum from per-shape
 * tables (the radius and an unnormalized normal, r·d − ∇r, which blends
 * exactly), so a morph costs a few uniforms. The same radial functions seat
 * the face and hat on the surface on the CPU (bodyPoint).
 *
 * The tables live in a float texture (`geometry.userData.shapes`, a texel per
 * vertex and shape, read with gl_VertexID) rather than vertex attributes: a
 * vertex attribute per shape ran past what GPUs allow (16, some fewer), and
 * the shader only reads the shapes in the current mix.
 *
 * Every shape must be star-shaped around the center.
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

const unit = (x: number, y: number, z: number, d: number): Plane => {
  const len = Math.hypot(x, y, z);
  return [x / len, y / len, z / len, d];
};

const superellipsoid =
  (a: number, b: number, c: number, p: number): Radial =>
  (x, y, z) =>
    (Math.abs(x / a) ** p + Math.abs(y / b) ** p + Math.abs(z / c) ** p) ** (-1 / p);

/**
 * Round in plan, with different halves: height `b` and roundness `p` above the
 * middle, `b2` and `p2` below. A low p points a half, a high one flattens it.
 * Both halves meet at radius `a`, so the seam is smooth.
 */
const halves =
  (a: number, b: number, p: number, b2: number, p2: number): Radial =>
  (x, y, z) => {
    const h = Math.hypot(x, z) / a;
    const [height, power] = y >= 0 ? [b, p] : [b2, p2];
    return (h ** power + Math.abs(y / height) ** power) ** (-1 / power);
  };

/** Where a ray from the center leaves a sphere that contains the center. */
const ball =
  (cx: number, cy: number, cz: number, r: number): Radial =>
  (x, y, z) => {
    const b = x * cx + y * cy + z * cz;
    return b + Math.sqrt(Math.max(0, b * b - (cx * cx + cy * cy + cz * cz) + r * r));
  };

/** A soft union of star-shaped parts: the farthest exit wins, with rounded seams. */
function union(parts: Radial[], k: number): Radial {
  const v = new Float64Array(parts.length);
  return (x, y, z) => {
    let m = -Infinity;
    for (let i = 0; i < parts.length; i += 1) {
      v[i] = parts[i](x, y, z);
      m = Math.max(m, v[i]);
    }
    let s = 0;
    for (let i = 0; i < v.length; i += 1) s += Math.exp(k * (v[i] - m));
    return m + Math.log(s) / k;
  };
}

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

const box = (x: number, top: number, bottom: number, z: number, sharp: number) =>
  polyhedron([[1, 0, 0, x], [-1, 0, 0, x], [0, 1, 0, top], [0, -1, 0, bottom], [0, 0, 1, z], [0, 0, -1, z]], sharp);

export const SHAPES = [
  "brilliant",
  "cushion",
  "emerald",
  "crystal",
  "dome",
  "cloud",
  "octa",
  "cabochon",
  "pear",
  "trillion",
  "heart",
  "hopper",
  "hex",
  "ingot",
  "person",
] as const;
export type Shape = (typeof SHAPES)[number];

const OCTA: Plane[] = [-1, 1].flatMap((a) =>
  [-1, 1].flatMap((b) => [-1, 1].map((c) => [a / Math.sqrt(3), b / Math.sqrt(3), c / Math.sqrt(3), 0.6] as const)),
);
// A hexagon facing front, flat at the top and bottom, with a beveled table.
const HEX_SIDES = [30, 90, 150, 210, 270, 330].map((deg) => (deg * Math.PI) / 180);
const RAW: Radial[] = [
  // brilliant: a crown over a pointed pavilion.
  polyhedron(
    [[0, 1, 0, 0.5], ...ring(8, TAU / 16, 0.38, 0.45, 0.434), ...ring(16, 0, 1, 0, 1), ...ring(8, 0, 1.03, -1, 0.95)],
    14,
  ),
  // cushion: a square pillow.
  superellipsoid(1, 0.92, 0.78, 3.2),
  // emerald: an upright octagon, step-cut toward its table.
  polyhedron(
    [
      [1, 0, 0, 0.7], [-1, 0, 0, 0.7], [0, 1, 0, 0.95], [0, -1, 0, 0.95],
      ...[-1, 1].flatMap((sx) => [-1, 1].map((sy) => unit(sx, sy, 0, 1))),
      [0, 0, 1, 0.55], [0, 0, -1, 0.55],
      unit(0.5, 0, 0.866, 0.76), unit(-0.5, 0, 0.866, 0.76), unit(0, 0.5, 0.866, 0.88), unit(0, -0.5, 0.866, 0.88),
    ],
    10,
  ),
  // crystal: a hexagonal point.
  polyhedron([...ring(6, TAU / 12, 1, 0, 0.66), ...ring(6, TAU / 12, 0.6, 0.66, 0.693), [0, -1, 0, 0.9]], 16),
  // dome: a ball.
  superellipsoid(1, 0.96, 0.96, 2),
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
  // cabochon: a gumdrop, domed over a flat back.
  halves(1, 0.9, 2.2, 0.52, 4.5),
  // pear: a drop, pointed at the top.
  halves(0.92, 1.05, 1.35, 0.9, 2.1),
  // trillion: a rounded triangle, point up.
  polyhedron([[0, -1, 0, 0.55], unit(0.866, 0.5, 0, 0.62), unit(-0.866, 0.5, 0, 0.62), [0, 0, 1, 0.62], [0, 0, -1, 0.62]], 5.5),
  // heart: two lobes over a wedge.
  union(
    [
      ball(-0.36, 0.26, 0, 0.52),
      ball(0.36, 0.26, 0, 0.52),
      polyhedron([unit(-0.766, -0.643, 0, 0.5), unit(0.766, -0.643, 0, 0.5), [0, 1, 0, 0.15], [0, 0, 1, 0.5], [0, 0, -1, 0.5]], 9),
    ],
    10,
  ),
  // hopper: bismuth's stepped block, the steps above the face.
  union([box(0.95, 0.34, 0.72, 0.8, 14), box(0.68, 0.64, 0.72, 0.58, 14), box(0.42, 0.9, 0.72, 0.36, 14)], 18),
  // hex: a garnet's hexagon.
  polyhedron(
    [
      ...HEX_SIDES.map((a) => [Math.cos(a), Math.sin(a), 0, 0.8] as const),
      ...HEX_SIDES.map((a) => unit(0.574 * Math.cos(a), 0.574 * Math.sin(a), 0.819, 0.88)),
      [0, 0, 1, 0.62],
      [0, 0, -1, 0.62],
    ],
    11,
  ),
  // ingot: a bar, narrower on top.
  polyhedron([[0, -1, 0, 0.52], [0, 1, 0, 0.5], unit(0.94, 0.34, 0, 0.8), unit(-0.94, 0.34, 0, 0.8), unit(0, 0.3, 0.954, 0.62), unit(0, 0.3, -0.954, 0.62)], 9),
  // person: a head, round over the brow and narrowing to the chin; hair grows from it (gem-fur.ts).
  halves(0.8, 0.98, 2.1, 1.02, 1.7),
];

/** The shape mix each cut rests in, by weight. An egg is a drop rounded out by a ball. */
const HOME: Record<GemCut, Partial<Record<Shape, number>>> = {
  brilliant: { brilliant: 1 },
  cushion: { cushion: 1 },
  emerald: { emerald: 1 },
  crystal: { crystal: 1 },
  sphere: { dome: 1 },
  cabochon: { cabochon: 1 },
  egg: { pear: 0.45, dome: 0.55 },
  pear: { pear: 1 },
  trillion: { trillion: 1 },
  heart: { heart: 1 },
  hopper: { hopper: 1 },
  hex: { hex: 1 },
  ingot: { ingot: 1 },
  octahedron: { octa: 1 },
  cloud: { cloud: 1 },
  person: { person: 1 },
};

/** The weights (one per SHAPES entry) of the shape a cut rests in. */
export function homeWeights(cut: GemCut): number[] {
  const mix = HOME[cut] ?? HOME.brilliant;
  return SHAPES.map((shape) => mix[shape] ?? 0);
}

/**
 * Height of the face's center on each shape: on a brilliant's girdle, a little
 * under the middle of a person's head (the hair takes the top), mid-body elsewhere.
 */
const FACE_Y = [0, 0.06, 0.04, -0.04, 0.04, 0, -0.04, 0.12, -0.02, -0.02, 0.06, 0, 0.02, 0.02, -0.06];

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
let compactGeometry: BufferGeometry | null = null;

/** Texels per row of the shape tables; a row of every shape for 2892 vertices would pass WebGL2's minimum texture size. */
export const SHAPE_ROW = 1024;

/** Samples every shape once per vertex, each normalized to the same outer radius. */
function build(detail = DETAIL, normalization?: Float64Array) {
  const ico = new IcosahedronGeometry(1, detail);
  ico.deleteAttribute("normal");
  ico.deleteAttribute("uv");
  const geometry = mergeVertices(ico);
  ico.dispose();
  const dirs = (geometry.getAttribute("position") as BufferAttribute).array as Float32Array;
  const count = dirs.length / 3;
  const rows = Math.ceil((count * SHAPES.length) / SHAPE_ROW);
  // Texel (vertex * SHAPES.length + shape): the vector r·d − ∇r and the radius r.
  const data = new Float32Array(SHAPE_ROW * rows * 4);
  const scale = new Float64Array(SHAPES.length);
  const d = new Vector3();
  const e1 = new Vector3();
  const e2 = new Vector3();
  const q = new Vector3();
  const h = 1e-3;
  RAW.forEach((radial, s) => {
    const sample = (k: number, e: Vector3) => {
      q.copy(e).multiplyScalar(k).add(d).normalize();
      return radial(q.x, q.y, q.z);
    };
    let max = 0;
    for (let i = 0; i < count; i += 1) {
      d.fromArray(dirs, 3 * i);
      e1.set(0, Math.abs(d.y) < 0.9 ? 1 : 0, Math.abs(d.y) < 0.9 ? 0 : 1).cross(d).normalize();
      e2.crossVectors(d, e1);
      const r = radial(d.x, d.y, d.z);
      // The radius's slope along the surface, from a small step each way.
      const g1 = (sample(h, e1) - r) / h;
      const g2 = (sample(h, e2) - r) / h;
      const k = (i * SHAPES.length + s) * 4;
      data[k] = r * d.x - g1 * e1.x - g2 * e2.x;
      data[k + 1] = r * d.y - g1 * e1.y - g2 * e2.y;
      data[k + 2] = r * d.z - g1 * e1.z - g2 * e2.z;
      data[k + 3] = r;
      max = Math.max(max, r);
    }
    // Both meshes use the same radii as CPU face/hat seating, including at a
    // size boundary. A coarser sample must not change the gem's proportions.
    scale[s] = normalization?.[s] ?? 1 / max;
    for (let i = 0; i < count; i += 1) {
      const k = (i * SHAPES.length + s) * 4;
      for (let j = 0; j < 4; j += 1) data[k + j] *= scale[s];
    }
  });
  const shapes = new DataTexture(data, SHAPE_ROW, rows, RGBAFormat, FloatType);
  shapes.magFilter = NearestFilter;
  shapes.minFilter = NearestFilter;
  shapes.generateMipmaps = false;
  shapes.needsUpdate = true;
  geometry.userData.shapes = shapes;
  return { geometry, scale };
}

export function bodyGeometry(compact = false): BufferGeometry {
  tables ??= build();
  // 812 vertices instead of 2892 for sidebar/composer gems. Hero previews
  // retain the full mesh; all avatars share these two immutable geometries.
  if (compact) return (compactGeometry ??= build(8, tables.scale).geometry);
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

const WEIGHT_VECTORS = Math.ceil(SHAPES.length / 4);
const WEIGHT_LIST = SHAPES.map((_, i) => `uW${i >> 2}.${"xyzw"[i & 3]}`).join(", ");

/** The body's surface, shared by its mesh and the coat that grows from it (gem-fur.ts). */
export const SURFACE_GLSL = /* glsl */ `
uniform highp sampler2D uShapes;
${Array.from({ length: WEIGHT_VECTORS }, (_, i) => `uniform vec4 uW${i};`).join("\n")}
uniform float uWob;
uniform float uTime;
uniform float uShear;
// The body toward d (a unit vector) from the shape tables' row for \`vertex\`:
// its point and its unnormalized normal, wobbled and sheared.
void surface(int vertex, vec3 d, out vec3 p, out vec3 n) {
  float w[${SHAPES.length}] = float[${SHAPES.length}](${WEIGHT_LIST});
  vec4 s = vec4(0.0);
  for (int k = 0; k < ${SHAPES.length}; k++) {
    if (w[k] != 0.0) {
      int i = vertex * ${SHAPES.length} + k;
      s += w[k] * texelFetch(uShapes, ivec2(i % ${SHAPE_ROW}, i / ${SHAPE_ROW}), 0);
    }
  }
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
  p = d * r;
  p.x += uShear * (p.y + 1.0) * 0.5;
  n = vec3(m.x, m.y - 0.5 * uShear * m.x, m.z);
}`;

/** The uniforms SURFACE_GLSL reads. */
export function surfaceUniforms() {
  return {
    uShapes: { value: null },
    ...Object.fromEntries(Array.from({ length: WEIGHT_VECTORS }, (_, i) => [`uW${i}`, { value: new Vector4() }])),
    uWob: { value: 0 },
    uTime: { value: 0 },
    uShear: { value: 0 },
  };
}

const BODY_VERT = /* glsl */ `
${SURFACE_GLSL}
varying vec3 vN;
varying vec3 vV;
varying vec3 vD;
varying float vY;
void main() {
  vec3 p;
  vec3 n;
  surface(gl_VertexID, position, p, n);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vN = normalize(normalMatrix * n);
  vV = normalize(-mv.xyz);
  vD = position;
  vY = p.y;
  gl_Position = projectionMatrix * mv;
}`;

const BODY_FRAG = /* glsl */ `
uniform vec3 uTop;
uniform vec3 uBottom;
uniform vec3 uRim;
uniform vec3 uGlowColor;
uniform float uGlow;
uniform float uGloss;
uniform float uClear;
uniform float uMetal;
uniform float uIris;
uniform float uFleck;
uniform vec3 uFleckColor;
uniform vec3 uSheen;
varying vec3 vN;
varying vec3 vV;
varying vec3 vD;
varying float vY;
float hash(vec3 p) {
  return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453);
}
void main() {
  vec3 n = normalize(vN);
  vec3 v = normalize(vV);
  // A key light up and to the left, a small fill from the right.
  vec3 key = normalize(vec3(-0.5, 0.8, 0.65));
  vec3 fill = normalize(vec3(0.75, 0.15, 0.65));
  float facing = max(dot(n, v), 0.0);
  float wrap = dot(n, key) * 0.5 + 0.5;
  vec3 base = mix(uBottom, uTop, smoothstep(-0.95, 0.85, vY));
  if (uFleck > 0.0) {
    // Specks fixed to the stone, so they turn with it.
    vec3 cell = vD * 11.0;
    vec3 id = floor(cell);
    vec3 jitter = vec3(hash(id + 1.7), hash(id + 3.1), hash(id + 5.3)) - 0.5;
    float speck = step(0.78, hash(id)) * smoothstep(0.3, 0.1, length(fract(cell) - 0.5 - 0.3 * jitter));
    base = mix(base, uFleckColor, speck * uFleck);
  }
  vec3 col = base * (0.62 + 0.42 * wrap) * (1.0 - 0.3 * uMetal);
  // Light carried through the body, brightest through the thick middle and on the shadow side.
  col += base * uClear * (0.16 * facing * facing + 0.12 * (1.0 - wrap));
  // Light bounced up from below in the gem's own color keeps the underside from going muddy.
  col += base * 0.07 * max(-n.y, 0.0);
  if (uIris > 0.0) {
    float edge = 1.0 - facing;
    vec3 film = 0.5 + 0.5 * cos(6.2832 * (vec3(0.0, 0.33, 0.67) + edge * 1.4 + vD.y * 0.6 + vD.x * 0.3));
    col = mix(col, col * 0.55 + film * 0.6, uIris * (0.18 + 0.55 * edge));
  }
  // A sheen floating under the surface, drifting as the stone turns.
  col += uSheen * pow(facing, 3.0) * smoothstep(0.1, 0.9, dot(n, normalize(vec3(-0.2, 0.5, 1.0))));
  float s1 = max(dot(reflect(-key, n), v), 0.0);
  float s2 = max(dot(reflect(-fill, n), v), 0.0);
  // Metal tints its highlights with its own color; glass and stone stay white.
  vec3 shine = mix(vec3(1.0), uTop * 1.5 + 0.1, uMetal);
  col += shine * (pow(s1, mix(8.0, 48.0, uGloss)) * mix(0.05, 0.14, uGloss) + pow(s1, 90.0) * 0.3 * uGloss + pow(s2, 40.0) * 0.07 * uGloss);
  float rim = pow(1.0 - facing, 3.0);
  col += uRim * rim * (0.16 + 0.15 * max(n.y, 0.0));
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
      ...surfaceUniforms(),
      uTop: { value: new Color() },
      uBottom: { value: new Color() },
      uRim: { value: new Color() },
      uGlowColor: { value: new Color() },
      uGlow: { value: 0 },
      uGloss: { value: 1 },
      uClear: { value: 0 },
      uMetal: { value: 0 },
      uIris: { value: 0 },
      uFleck: { value: 0 },
      uFleckColor: { value: new Color() },
      uSheen: { value: new Color(0, 0, 0) },
    },
  });
}

/** Loads a shape mix (one weight per SHAPES entry) into the material. */
export function setShapeWeights(material: ShaderMaterial, w: ArrayLike<number>) {
  for (let i = 0; i < WEIGHT_VECTORS; i += 1) {
    (material.uniforms[`uW${i}`].value as Vector4).set(w[4 * i] ?? 0, w[4 * i + 1] ?? 0, w[4 * i + 2] ?? 0, w[4 * i + 3] ?? 0);
  }
}

const hsl = { h: 0, s: 0, l: 0 };

/**
 * Colors the body from one gem color: a smooth gradient, lighter and a touch
 * warmer on top, deeper below. Near-grays go steel blue. `rest` (0..1) eases
 * toward an offline gem's look: nearly gray, keeping a hint of its hue, with
 * no finish. Lightness is capped so the shading always reads. `detail` (0..1)
 * fades specks that a small avatar could only draw as noise.
 */
export function paintBody(material: ShaderMaterial, color: Color, rest: number, finish?: GemMaterial, detail = 1) {
  color.getHSL(hsl, SRGBColorSpace);
  const icy = hsl.s < 0.15;
  const h = icy ? 0.62 : hsl.h;
  const lively = icy ? 0.22 : hsl.s;
  const s = lively + (Math.min(hsl.s, 0.12) - lively) * rest;
  const l = Math.min(hsl.l, 0.72);
  const tone = (out: Color, dl: number, dh: number, ds: number) =>
    out.setHSL((h + dh + 1) % 1, Math.min(1, Math.max(0, s + ds)), Math.min(0.96, Math.max(0.05, l + dl)), SRGBColorSpace);
  const u = material.uniforms;
  tone(u.uTop.value as Color, 0.08, 0.01, -0.02);
  tone(u.uBottom.value as Color, -0.17, -0.02, 0.05);
  tone(u.uRim.value as Color, 0.22, 0.02, -0.08);
  tone(u.uGlowColor.value as Color, 0.22, 0.01, 0.08);
  // A resting (offline) gem keeps its shape but loses its finish.
  const live = 1 - rest;
  u.uGloss.value = finish ? 0.3 + 0.7 * finish.gloss * live : 1;
  u.uClear.value = (finish?.clear ?? 0.5) * live;
  u.uMetal.value = (finish?.metal ?? 0) * live;
  u.uIris.value = (finish?.iris ?? 0) * live * (0.4 + 0.6 * detail);
  u.uFleck.value = (finish?.fleck ?? 0) * live * detail;
  const fleck = finish?.fleckColor;
  if (fleck) (u.uFleckColor.value as Color).setRGB(fleck[0] / 255, fleck[1] / 255, fleck[2] / 255, SRGBColorSpace);
  const sheen = finish?.sheen;
  if (sheen) (u.uSheen.value as Color).setRGB(sheen[0] / 255, sheen[1] / 255, sheen[2] / 255, SRGBColorSpace).multiplyScalar(0.22 * live);
  else (u.uSheen.value as Color).setRGB(0, 0, 0);
}
