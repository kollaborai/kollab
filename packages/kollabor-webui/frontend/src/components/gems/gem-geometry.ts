import {
  BoxGeometry,
  BufferGeometry,
  ExtrudeGeometry,
  LatheGeometry,
  OctahedronGeometry,
  Shape,
  SphereGeometry,
  Vector2,
} from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { GemCut, GemSpec } from "./gem-specs";

/**
 * Procedural gem cuts, each normalized to a unit bounding sphere so every gem
 * fills the avatar the same way. Faceted cuts rely on `flatShading` in the
 * material for their crisp facets, so they stay low-poly on purpose.
 */

function lathe(profile: [number, number][], segments: number) {
  return new LatheGeometry(
    profile.map(([radius, height]) => new Vector2(radius, height)),
    segments,
  );
}

function extrude(shape: Shape, depth: number, bevel: number, bevelSegments: number, curveSegments: number) {
  return new ExtrudeGeometry(shape, {
    depth,
    bevelEnabled: true,
    bevelThickness: bevel,
    bevelSize: bevel,
    bevelSegments,
    curveSegments,
  });
}

/** A polygon whose corners are rounded with quadratic curves. */
function roundedPolygon(points: [number, number][], corner: number) {
  const shape = new Shape();
  const at = (i: number) => points[(i + points.length) % points.length];
  const toward = (from: [number, number], to: [number, number]): [number, number] => {
    const dx = to[0] - from[0];
    const dy = to[1] - from[1];
    const length = Math.hypot(dx, dy);
    return [from[0] + (dx / length) * corner, from[1] + (dy / length) * corner];
  };
  points.forEach((point, i) => {
    const enter = toward(point, at(i - 1));
    const exit = toward(point, at(i + 1));
    if (i === 0) shape.moveTo(...enter);
    else shape.lineTo(...enter);
    shape.quadraticCurveTo(point[0], point[1], ...exit);
  });
  shape.closePath();
  return shape;
}

function regular(sides: number, radius: number, rotation: number): [number, number][] {
  return Array.from({ length: sides }, (_, i) => {
    const angle = rotation + (i * Math.PI * 2) / sides;
    return [Math.cos(angle) * radius, Math.sin(angle) * radius];
  });
}

function build(spec: GemSpec): BufferGeometry {
  switch (spec.cut) {
    case "brilliant": {
      // Culet, pavilion, girdle, crown, table: tilted so the table faces the
      // camera at three-quarters, the way a gem is drawn on a ring box.
      const geometry = lathe(
        [[0, -0.9], [0.5, -0.48], [1, -0.04], [1, 0.03], [0.8, 0.24], [0.54, 0.38], [0, 0.38]],
        12,
      );
      geometry.rotateX(0.3);
      return geometry;
    }
    case "crystal": {
      const geometry = lathe([[0, -1], [0.44, -0.64], [0.47, 0.34], [0, 1]], 6);
      geometry.rotateY(Math.PI / 6);
      geometry.rotateZ(-0.16);
      return geometry;
    }
    case "emerald": {
      const shape = roundedPolygon(
        [[-0.62, -0.84], [0.62, -0.84], [0.62, 0.84], [-0.62, 0.84]],
        0.2,
      );
      return extrude(shape, 0.16, 0.22, 2, 1);
    }
    case "cushion": {
      return extrude(roundedPolygon(regular(4, 1, Math.PI / 4), 0.34), 0.18, 0.26, 3, 2);
    }
    case "trillion": {
      return extrude(roundedPolygon(regular(3, 1, Math.PI / 2), 0.24), 0.16, 0.24, 2, 2);
    }
    case "pear": {
      const shape = new Shape();
      shape.moveTo(0, 1);
      shape.quadraticCurveTo(0.7, 0.3, 0.7, -0.3);
      shape.absarc(0, -0.3, 0.7, 0, Math.PI, true);
      shape.quadraticCurveTo(-0.7, 0.3, 0, 1);
      return extrude(shape, 0.14, 0.24, 2, 5);
    }
    case "cabochon": {
      const [width, height] = spec.aspect ?? [1, 1];
      const geometry = new SphereGeometry(1, 48, 32);
      geometry.scale(width, height, 0.62);
      return geometry;
    }
    case "sphere":
      return new SphereGeometry(1, 48, 32);
    case "octahedron": {
      const geometry = new OctahedronGeometry(1, 0);
      geometry.scale(0.86, 1.1, 0.86);
      geometry.rotateY(0.55);
      return geometry;
    }
    case "hopper": {
      // Bismuth's stair-stepped hopper crystal: nested square frames rising
      // toward the camera.
      const parts: BufferGeometry[] = [];
      const frame = (outer: number, bar: number, z: number) => {
        const half = outer / 2 - bar / 2;
        const add = (w: number, h: number, x: number, y: number) => {
          const box = new BoxGeometry(w, h, 0.3);
          box.translate(x, y, z);
          parts.push(box);
        };
        add(outer, bar, 0, half);
        add(outer, bar, 0, -half);
        add(bar, outer - bar * 2, half, 0);
        add(bar, outer - bar * 2, -half, 0);
      };
      frame(1.5, 0.26, -0.2);
      frame(1.02, 0.22, 0.05);
      frame(0.6, 0.18, 0.28);
      const core = new BoxGeometry(0.22, 0.22, 0.3);
      core.translate(0, 0, 0.46);
      parts.push(core);
      const geometry = mergeGeometries(parts) ?? new BoxGeometry(1, 1, 1);
      parts.forEach((part) => part.dispose());
      geometry.rotateZ(Math.PI / 4);
      return geometry;
    }
  }
}

const cache = new Map<string, BufferGeometry>();

export function gemGeometry(spec: GemSpec): BufferGeometry {
  const key = `${spec.cut}:${spec.aspect?.join("x") ?? ""}`;
  let geometry = cache.get(key);
  if (!geometry) {
    geometry = build(spec);
    geometry.center();
    geometry.computeBoundingSphere();
    const radius = geometry.boundingSphere?.radius || 1;
    geometry.scale(1 / radius, 1 / radius, 1 / radius);
    geometry.computeBoundingSphere();
    cache.set(key, geometry);
  }
  return geometry;
}

/**
 * Where the face sits on each cut, as fractions of the gem's box: eye center,
 * eye spacing, and where a hat rests (the top of the silhouette) and how big
 * it is (pointy crystals wear small hats on their tips).
 */
export const FACE_ANCHORS: Record<GemCut, { x: number; y: number; gap: number; hatY: number; hatScale: number }> = {
  brilliant: { x: 0.54, y: 0.47, gap: 1, hatY: 0.31, hatScale: 0.9 },
  emerald: { x: 0.54, y: 0.45, gap: 0.92, hatY: 0.13, hatScale: 0.85 },
  crystal: { x: 0.52, y: 0.47, gap: 0.78, hatY: 0.12, hatScale: 0.6 },
  pear: { x: 0.53, y: 0.58, gap: 0.95, hatY: 0.13, hatScale: 0.62 },
  trillion: { x: 0.52, y: 0.57, gap: 0.9, hatY: 0.16, hatScale: 0.6 },
  cushion: { x: 0.54, y: 0.46, gap: 1, hatY: 0.15, hatScale: 0.9 },
  cabochon: { x: 0.55, y: 0.46, gap: 1, hatY: 0.13, hatScale: 0.95 },
  sphere: { x: 0.55, y: 0.46, gap: 1, hatY: 0.12, hatScale: 1 },
  octahedron: { x: 0.53, y: 0.49, gap: 0.86, hatY: 0.12, hatScale: 0.6 },
  hopper: { x: 0.54, y: 0.47, gap: 0.95, hatY: 0.12, hatScale: 0.7 },
};
