// The glass K: the mark extruded with rounded bevels in refracting glass, with
// Kollab's electric mist swirling behind it. The mist plane draws only into the
// transmission pass, so it is seen through the glass and never directly.
// Loaded lazily by KollabLogo so three.js stays out of the main bundle.
import {
  Camera,
  DirectionalLight,
  ExtrudeGeometry,
  Mesh,
  MeshBasicMaterial,
  MeshPhysicalMaterial,
  NeutralToneMapping,
  PerspectiveCamera,
  PlaneGeometry,
  PMREMGenerator,
  PointLight,
  Scene,
  ShaderMaterial,
  Shape,
  Vector2,
  Vector3,
  WebGLRenderTarget,
  WebGLRenderer,
} from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { toCreasedNormals } from "three/addons/utils/BufferGeometryUtils.js";

// Electric mist, ported from Mentiko's page banner (electric-mist.tsx).
const MIST_FRAG = `
precision highp float;
varying vec2 vUv;
uniform float uTime, uFlare;
uniform vec3 uColor;
#define time uTime * 0.2
mat2 makem2(float t){ float c=cos(t), s=sin(t); return mat2(c,-s,s,c); }
float hash(vec2 p){ return fract(sin(dot(p, vec2(12.9898,78.233)))*43758.5453); }
float noise(vec2 x){
  x *= 1.5;
  vec2 p=floor(x), f=fract(x); f=f*f*(3.0-2.0*f);
  return mix(mix(hash(p),hash(p+vec2(1,0)),f.x), mix(hash(p+vec2(0,1)),hash(p+vec2(1,1)),f.x), f.y);
}
float fbm(vec2 p, int oct){
  mat2 m2 = mat2(0.8,0.6,-0.6,0.8);
  float z=2.0, rz=0.0;
  for (int i=0;i<5;i++){ if(i>=oct) break; rz+=abs((noise(p)-0.5)*4.0)/z; z*=2.0; p=m2*(p*2.0); }
  return rz;
}
void main(){
  vec2 p = (vUv * 2.0 - 1.0) * 1.7;
  p *= makem2(uTime * 0.15);
  vec2 bp = p;
  p = (p + 5.0) * 0.5;
  float rb = fbm(p*0.5 + time*0.17, 3) * 0.1;
  p *= makem2(rb*0.2 + atan(p.y,p.x) * 2.4);
  float rz = fbm(p*0.9 - time*0.7, 5) * 12.0;
  rz *= abs(sin(bp.x*0.5 - time*4.0 - 2.0));
  vec3 col = uColor * (1.0 + uFlare * 1.6) / (1.15 - rz);
  gl_FragColor = vec4(min(abs(col), vec3(4.0)), 1.0); // no sqrt: keeps the blacks deep
}`;
const MIST_VERT =
  "varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }";

const SOFTWARE_GPU = /swiftshader|llvmpipe|softpipe|software|basic render/i;
const VIEW_HEIGHT = 1.8; // canvas is 180% of the mark's box; the mark is 1 unit tall

export type GlassHooks = {
  /** Fires once, after the first frame has really been drawn (the canvas had a size). */
  onFrame?: () => void;
  /** Fires when the GPU drops the context; the canvas is blank from then on. */
  onLost?: () => void;
};

/** Starts the glass K on `canvas`; null means keep the flat mark. */
export function startGlass(
  canvas: HTMLCanvasElement,
  polys: ReadonlyArray<ReadonlyArray<readonly [number, number]>>,
  hooks: GlassHooks = {},
): { stop: () => void; flare: () => void } | null {
  let renderer: WebGLRenderer;
  let context: WebGL2RenderingContext | null = null;
  try {
    const attrs = {
      alpha: true,
      antialias: true,
      failIfMajorPerformanceCaveat: true,
    };
    context = canvas.getContext(
      "webgl2",
      attrs,
    ) as WebGL2RenderingContext | null;
    if (!context) return null;
    const info = context.getExtension("WEBGL_debug_renderer_info");
    const gpu = String(
      context.getParameter(
        info ? info.UNMASKED_RENDERER_WEBGL : context.RENDERER,
      ) ?? "",
    );
    if (SOFTWARE_GPU.test(gpu)) {
      context.getExtension("WEBGL_lose_context")?.loseContext();
      return null;
    }
    renderer = new WebGLRenderer({ canvas, context, ...attrs });
  } catch {
    try {
      context?.getExtension("WEBGL_lose_context")?.loseContext();
    } catch {
      // A failed context cleanup must not break the flat SVG fallback.
    }
    return null;
  }

  try {
    return build(renderer, canvas, polys, hooks);
  } catch {
    renderer.dispose();
    renderer.forceContextLoss();
    return null;
  }
}

function build(
  renderer: WebGLRenderer,
  canvas: HTMLCanvasElement,
  polys: ReadonlyArray<ReadonlyArray<readonly [number, number]>>,
  hooks: GlassHooks,
) {
  // The canvas is tiny (~40 css px), so supersample: render at 3x or the screen's ratio and let
  // the browser scale it down. Smooths the thin bevel highlights and the refracted backdrop.
  renderer.setPixelRatio(Math.min(Math.max(window.devicePixelRatio || 1, 3), 4));
  renderer.toneMapping = NeutralToneMapping;

  const scene = new Scene();
  const pmrem = new PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const env = pmrem.fromScene(room, 0.02).texture;
  scene.environment = env;
  room.dispose();
  pmrem.dispose();

  const camera = new PerspectiveCamera(26, 1, 0.1, 100);
  camera.position.z =
    VIEW_HEIGHT / (2 * Math.tan((camera.fov * Math.PI) / 360));

  const glass = new MeshPhysicalMaterial({
    transmission: 1,
    thickness: 1.4,
    ior: 2,
    roughness: 0.02,
    color: 0xffffff,
    attenuationColor: 0xf2f6ff,
    attenuationDistance: 3,
    metalness: 0,
    clearcoat: 1,
    clearcoatRoughness: 0.02,
    iridescence: 0.2,
    iridescenceIOR: 1.3,
    iridescenceThicknessRange: [120, 420],
    specularIntensity: 1,
    envMapIntensity: 1.6,
    dispersion: 24,
    transparent: true,
    opacity: 0.88,
  });

  // 1 unit = the mark's height (654 SVG units).
  const sl = 1 / 654;
  const shapes = polys.map(
    (poly) => new Shape(poly.map(([x, y]) => new Vector2(x, y))),
  );
  const extruded = new ExtrudeGeometry(shapes, {
    depth: 0.3 / sl,
    curveSegments: 64,
    bevelEnabled: true,
    bevelThickness: 0.06 / sl,
    bevelSize: 0.06 / sl,
    bevelOffset: -0.06 / sl,
    bevelSegments: 12,
  });
  const geo = toCreasedNormals(extruded, (30 * Math.PI) / 180);
  extruded.dispose();
  geo.scale(sl, sl, sl);
  geo.rotateX(Math.PI); // SVG y points down
  geo.center();
  const mark = new Mesh(geo, glass);
  scene.add(mark);

  const mistRT = new WebGLRenderTarget(256, 256);
  const mist = new ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uFlare: { value: 0 },
      uColor: { value: new Vector3(0.09, 0.1, 0.12) },
    },
    vertexShader: MIST_VERT,
    fragmentShader: MIST_FRAG,
    depthTest: false,
    depthWrite: false,
  });
  const quad = new PlaneGeometry(2, 2);
  const mistScene = new Scene();
  mistScene.add(new Mesh(quad, mist));
  const mistCam = new Camera();

  const backdropGeo = new PlaneGeometry(5, 5);
  const backdropMat = new MeshBasicMaterial({
    map: mistRT.texture,
    toneMapped: false,
  });
  const backdrop = new Mesh(backdropGeo, backdropMat);
  backdrop.position.z = -1.6;
  backdrop.onBeforeRender = (r) =>
    backdropGeo.setDrawRange(0, r.getRenderTarget() ? Infinity : 0);
  scene.add(backdrop);

  const key = new PointLight(0xffffff, 5, 10);
  key.position.set(1.8, 1.6, 3.4);
  const fill = new PointLight(0xffffff, 2, 10);
  fill.position.set(-2, -1, 3);
  const rim = new DirectionalLight(0xffffff, 2);
  rim.position.set(-3, 1, -2);
  scene.add(key, fill, rim);

  // Light theme: denser, less see-through glass so it holds up on white.
  let dark: boolean | null = null;
  const syncTheme = () => {
    const isDark = document.documentElement.classList.contains("dark");
    if (isDark === dark) return;
    dark = isDark;
    glass.attenuationColor.set(isDark ? 0xf2f6ff : 0xd6dde8);
    glass.attenuationDistance = isDark ? 3 : 1.3;
    glass.opacity = isDark ? 0.88 : 0.96;
    glass.envMapIntensity = isDark ? 1.6 : 1.2;
    key.intensity = isDark ? 5 : 3;
    fill.intensity = isDark ? 2 : 1.2;
    rim.intensity = isDark ? 2 : 0.8;
  };

  // Tilt toward the pointer, measured from the mark itself.
  let tx = 0;
  let ty = 0;
  let faceX = 0;
  let faceY = 0;
  const onPointer = (e: PointerEvent) => {
    const box = canvas.getBoundingClientRect();
    const clamp = (v: number) => Math.max(-1, Math.min(1, v));
    tx = clamp((e.clientX - (box.left + box.width / 2)) / 600);
    ty = clamp((e.clientY - (box.top + box.height / 2)) / 600);
  };
  window.addEventListener("pointermove", onPointer);

  let flare = 0;
  const motion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const start = performance.now();
  let last = start;
  let frame = 0;
  let stopped = false;
  let framed = false;

  const draw = (now: number) => {
    if (stopped) return;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (w && h) {
      const size = renderer.getSize(new Vector2());
      if (size.x !== w || size.y !== h) {
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
      syncTheme();
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const still = !!motion?.matches;
      const t = still ? 0 : (now - start) / 1000;
      faceX = still ? 0 : faceX + (tx - faceX) * 0.06;
      faceY = still ? 0 : faceY + (ty - faceY) * 0.06;
      flare *= Math.pow(0.12, dt);
      const r = 0.45 * t;
      mark.rotation.y = -0.1 + 0.45 * faceX + 0.16 * Math.cos(r);
      mark.rotation.x = 0.06 + 0.28 * faceY + 0.04 * Math.sin(r * 0.7);
      mark.rotation.z = 0.05 * Math.sin(r);
      mist.uniforms.uTime.value = t * 1.8;
      mist.uniforms.uFlare.value = flare;
      renderer.setRenderTarget(mistRT);
      renderer.render(mistScene, mistCam);
      renderer.setRenderTarget(null);
      renderer.render(scene, camera);
      if (!framed) {
        framed = true;
        hooks.onFrame?.();
      }
    }
    // Reduced motion renders this frame once; do not keep a GPU render loop alive.
    if (!motion?.matches) frame = requestAnimationFrame(draw);
  };
  // A lost context leaves a blank canvas: tell the caller so the flat mark returns.
  const onContextLost = (event: Event) => {
    event.preventDefault();
    hooks.onLost?.();
  };
  canvas.addEventListener?.("webglcontextlost", onContextLost);

  // Reduced motion draws one still frame, so redraw it when the canvas gets a size,
  // and restart (or hold) the loop when the preference changes mid-session.
  const redrawStill = () => {
    if (motion?.matches) draw(performance.now());
  };
  const resizeObserver =
    typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(redrawStill);
  resizeObserver?.observe(canvas);
  const onMotionChange = () => {
    cancelAnimationFrame(frame);
    draw(performance.now());
  };
  motion?.addEventListener?.("change", onMotionChange);
  const stop = () => {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(frame);
    window.removeEventListener("pointermove", onPointer);
    canvas.removeEventListener?.("webglcontextlost", onContextLost);
    resizeObserver?.disconnect();
    motion?.removeEventListener?.("change", onMotionChange);
    geo.dispose();
    glass.dispose();
    quad.dispose();
    mist.dispose();
    mistRT.dispose();
    backdropGeo.dispose();
    backdropMat.dispose();
    env.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  };

  // A failed first frame must release what was registered above: the caller gets no handle.
  try {
    draw(start);
  } catch (error) {
    stop();
    throw error;
  }

  return {
    flare: () => {
      if (!motion?.matches) flare = 1.4;
    },
    stop,
  };
}
