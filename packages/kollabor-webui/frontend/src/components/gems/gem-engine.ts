import {
  AdditiveBlending,
  AmbientLight,
  BackSide,
  BufferGeometry,
  Color,
  DirectionalLight,
  DoubleSide,
  Float32BufferAttribute,
  Group,
  Mesh,
  MeshBasicMaterial,
  MeshPhysicalMaterial,
  NeutralToneMapping,
  PerspectiveCamera,
  PlaneGeometry,
  PMREMGenerator,
  PointLight,
  Points,
  PointsMaterial,
  SRGBColorSpace,
  Scene,
  Sprite,
  SpriteMaterial,
  type Texture,
  WebGLRenderer,
} from "three";
import { drawEffects, drawHat, drawSeasonBody } from "./gem-dress";
import {
  autoHat,
  drawFace,
  seasonFor,
  workPhase,
  type Activity,
  type EyeStyle,
  type FaceFrame,
  type HatStyle,
  type Season,
} from "./gem-face";
import { FACE_ANCHORS, gemGeometry } from "./gem-geometry";
import { drawBackProps, drawHeld, drawProps } from "./gem-props";
import { gemMood, gemSpec, type GemMood, type GemSpec } from "./gem-specs";
import { softDot, stoneTexture } from "./gem-textures";

/**
 * One WebGL context renders every gem avatar on the page.
 *
 * Browsers cap live WebGL contexts (Chrome at 16), so a canvas per sidebar row
 * would fall over with a busy hub. Instead a single offscreen renderer draws
 * each visible avatar in turn and blits the pixels into that avatar's own 2D
 * canvas, which then gets a 2D pass: halo, glints, and the personality layer
 * from gem-face.ts. The canvas is GEM_PAD times the layout box so hats,
 * laptops and flying keyboards have room around the stone.
 * Idle gems repaint at 30fps, busy gems every frame, offline gems once.
 */

export const GEM_PAD = 1.6;

export type GemProps = {
  gem?: string | null;
  caste?: string | null;
  /** sRGB [r, g, b] 0-255 from the engine's pool entry. */
  color?: readonly number[] | null;
  state?: string | null;
  /** False when no process backs the identity. */
  live?: boolean;
  /** CSS pixels of the layout box the gem fills. */
  size: number;
  /** What the agent is doing right now; derived from `state` when absent. */
  activity?: Activity | null;
  face?: EyeStyle | "none";
  /** "auto" dresses the gem for its caste. */
  hat?: HatStyle | "auto";
  /** Eyes and body turn toward the pointer. */
  follow?: boolean;
  /** Seasonal dressing; "auto" follows the calendar. */
  season?: Season | "auto";
};

type Glint = { x: number; y: number; age: number; ttl: number; size: number; hue: number | null };

type Avatar = {
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  props: GemProps;
  spec: GemSpec;
  base: Color;
  rgb: [number, number, number];
  mood: GemMood;
  activity: Activity;
  activitySince: number;
  prev: Activity;
  danceUntil: number;
  px: number;
  gemPx: number;
  seed: number;
  visible: boolean;
  dirty: boolean;
  lastRender: number;
  glow: number;
  burst: number;
  yaw: number;
  bob: number;
  slide: number;
  roll: number;
  /** Extra yaw from a dance twist or spin; the face wraps around with it. */
  spin: number;
  stretch: number;
  /** 0..1: turned side-on to the laptop. */
  desk: number;
  /** 0..1: how far the thought bubble is open. */
  bubble: number;
  blinkAt: number;
  blink: number;
  look: { x: number; y: number };
  talk: number;
  glints: Glint[];
  glintCarry: number;
};

type Rig = {
  group: Group;
  body: Mesh<BufferGeometry, MeshPhysicalMaterial>;
  inner?: Mesh<BufferGeometry, MeshPhysicalMaterial>;
  core?: Sprite;
  flecks?: Points<BufferGeometry, PointsMaterial>;
  light: PointLight;
  /** The stone's color comes from its pattern map, not the material color. */
  textured: boolean;
};

const FOV = 28;
const CAMERA_DISTANCE = 4.7;
// World units across the frame: a unit-radius gem fills ~85% of it.
const FRAME = 2 * Math.tan(((FOV / 2) * Math.PI) / 180) * CAMERA_DISTANCE;
const WORLD_TO_FRAME = 1 / FRAME;
const MAX_PIXEL_RATIO = 2;
// Milliseconds of drawing per frame. Past it the remaining gems wait a frame,
// so a crowded page animates a little slower instead of freezing input.
const FRAME_BUDGET_MS = 8;
const WHITE = new Color(1, 1, 1);
const ANGRY = new Color("#ff2d20");
const BUSY: ReadonlySet<Activity> = new Set([
  "thinking",
  "typing",
  "reading",
  "speaking",
  "searching",
  "messaging",
  "tasking",
]);

/**
 * A jeweler's light box: hard strip lights and hot spots on near-black. Facets
 * mirror it as crisp flashes against dark gaps, the contrast that makes a cut
 * stone read as a gem instead of tinted plastic. Rotating it sweeps the
 * flashes across the facets.
 */
function studioEnvironment(renderer: WebGLRenderer): Texture {
  const studio = new Scene();
  studio.background = new Color(0x030405);
  const panel = (w: number, h: number, x: number, y: number, z: number, intensity: number, tint = 0xffffff) => {
    const mesh = new Mesh(
      new PlaneGeometry(w, h),
      new MeshBasicMaterial({ color: new Color(tint).multiplyScalar(intensity), side: DoubleSide }),
    );
    mesh.position.set(x, y, z);
    mesh.lookAt(0, 0, 0);
    studio.add(mesh);
  };
  panel(7, 1.1, 0, 6, 2.5, 10);
  panel(1.1, 7, -6, 1, 3, 7);
  panel(1.1, 7, 6, 1, 3, 7);
  panel(4, 3, 0, 2, 7, 3);
  panel(1, 1, 3.5, -3, 5, 14);
  panel(1.2, 1.2, -4, 4, -4, 11, 0xcfe3ff);
  panel(9, 1, 0, -6, 0, 1.4);
  const pmrem = new PMREMGenerator(renderer);
  const texture = pmrem.fromScene(studio, 0.015).texture;
  pmrem.dispose();
  studio.traverse((object) => {
    if (object instanceof Mesh) {
      object.geometry.dispose();
      (object.material as MeshBasicMaterial).dispose();
    }
  });
  return texture;
}

function colorFrom(props: GemProps, spec: GemSpec): Color {
  const rgb = props.color;
  if (rgb && rgb.length >= 3 && rgb.every((value) => Number.isFinite(value))) {
    return new Color().setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, SRGBColorSpace);
  }
  return new Color(spec.color);
}

function srgb(color: Color): [number, number, number] {
  const c = color.clone().convertLinearToSRGB();
  return [Math.round(c.r * 255), Math.round(c.g * 255), Math.round(c.b * 255)];
}

function hashSeed(text: string): number {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (((hash >>> 0) % 10000) / 10000) * Math.PI * 2;
}

function rgbString(color: Color, alpha: number): string {
  const [r, g, b] = srgb(color);
  return `rgba(${r},${g},${b},${alpha.toFixed(3)})`;
}

function activityFor(props: GemProps, mood: GemMood): Activity {
  if (mood === "offline") return "offline";
  if (props.activity) return props.activity;
  switch (mood) {
    case "working":
      return "thinking";
    case "waiting":
      return "waiting";
    case "dreaming":
      return "dreaming";
    case "booting":
      return "booting";
    default:
      return "idle";
  }
}

class GemEngine {
  private renderer: WebGLRenderer | null = null;
  private scene = new Scene();
  private camera = new PerspectiveCamera(FOV, 1, 0.1, 20);
  private key = new DirectionalLight(0xffffff, 2.6);
  private rigs = new Map<string, Rig>();
  private shown: Rig | null = null;
  private avatars = new Set<Avatar>();
  private byCanvas = new WeakMap<HTMLCanvasElement, Avatar>();
  private observer: IntersectionObserver | null = null;
  private pointer = { x: 0, y: 0, at: -Infinity };
  private bufferPx = 0;
  private raf = 0;
  private lastTick = 0;
  /** Where the next frame starts, so skipped gems go first. */
  private cursor = 0;
  /** Smoothed milliseconds a tick spends drawing; over budget halves the frame rate. */
  private cost = 0;
  private lost = false;
  private still = false;
  private failed = false;

  /** False when WebGL is unavailable; GemAvatar then draws a CSS gem. */
  get supported(): boolean {
    return !this.failed && this.ensureRenderer();
  }

  private ensureRenderer(): boolean {
    if (this.renderer) return true;
    if (this.failed || typeof document === "undefined") return false;
    try {
      const canvas = document.createElement("canvas");
      const renderer = new WebGLRenderer({ canvas, alpha: true, antialias: true });
      renderer.setPixelRatio(1);
      renderer.setClearColor(0x000000, 0);
      renderer.autoClear = false;
      // Neutral keeps saturated stone colors true; ACES washes them toward pastel.
      renderer.toneMapping = NeutralToneMapping;
      renderer.toneMappingExposure = 1;
      renderer.outputColorSpace = SRGBColorSpace;
      canvas.addEventListener("webglcontextlost", (event) => {
        event.preventDefault();
        this.lost = true;
      });
      canvas.addEventListener("webglcontextrestored", () => {
        this.lost = false;
        this.avatars.forEach((avatar) => (avatar.dirty = true));
        this.kick();
      });
      this.scene.environment = studioEnvironment(renderer);
      this.camera.position.set(0, 0, CAMERA_DISTANCE);
      const rim = new DirectionalLight(0x9ecbff, 1.1);
      rim.position.set(-2.5, 1.8, -3);
      this.scene.add(new AmbientLight(0xffffff, 0.25), this.key, rim);
      this.renderer = renderer;
    } catch {
      this.failed = true;
      return false;
    }
    const motion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (motion) {
      this.still = motion.matches;
      motion.addEventListener?.("change", (event) => {
        this.still = event.matches;
        this.avatars.forEach((avatar) => (avatar.dirty = true));
        this.kick();
      });
    }
    window.addEventListener(
      "pointermove",
      (event) => {
        this.pointer = { x: event.clientX, y: event.clientY, at: performance.now() / 1000 };
      },
      { passive: true },
    );
    this.observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const avatar = this.byCanvas.get(entry.target as HTMLCanvasElement);
        if (!avatar) continue;
        avatar.visible = entry.isIntersecting;
        if (avatar.visible) avatar.dirty = true;
      }
      this.kick();
    });
    return true;
  }

  mount(canvas: HTMLCanvasElement, props: GemProps): boolean {
    if (!this.ensureRenderer()) return false;
    const ctx = canvas.getContext("2d");
    if (!ctx) return false;
    const spec = gemSpec(props.gem, props.caste);
    const base = colorFrom(props, spec);
    const mood = gemMood(props.state, props.live !== false);
    const now = performance.now() / 1000;
    const avatar: Avatar = {
      canvas,
      ctx,
      props,
      spec,
      base,
      rgb: srgb(base),
      mood,
      activity: activityFor(props, mood),
      activitySince: now,
      prev: activityFor(props, mood),
      danceUntil: 0,
      px: 0,
      gemPx: 0,
      seed: hashSeed(props.gem || "unassigned"),
      visible: false,
      dirty: true,
      lastRender: 0,
      glow: 0,
      burst: 0,
      yaw: 0,
      bob: 0,
      slide: 0,
      roll: 0,
      spin: 0,
      stretch: 0,
      desk: 0,
      bubble: 0,
      blinkAt: now + 1 + Math.random() * 3,
      blink: 1,
      look: { x: 0, y: 0 },
      talk: 0,
      glints: [],
      glintCarry: 0,
    };
    this.resize(avatar);
    this.avatars.add(avatar);
    this.byCanvas.set(canvas, avatar);
    this.observer?.observe(canvas);
    return true;
  }

  update(canvas: HTMLCanvasElement, props: GemProps) {
    const avatar = this.byCanvas.get(canvas);
    if (!avatar) return;
    const now = performance.now() / 1000;
    const mood = gemMood(props.state, props.live !== false);
    const activity = activityFor(props, mood);
    if (activity !== avatar.activity) {
      // A finished turn earns a flash and a little dance.
      if (BUSY.has(avatar.activity) && activity === "idle") {
        avatar.burst = 1;
        avatar.danceUntil = now + 2.4;
      }
      avatar.prev = avatar.activity;
      avatar.activity = activity;
      // Thinking between two tool calls stays at the laptop: the thought
      // bubble opens a turn, else the gem flips away and back on every call.
      avatar.activitySince = activity === "thinking" && avatar.desk > 0.5 ? now - 4 : now;
    }
    avatar.mood = mood;
    avatar.props = props;
    avatar.spec = gemSpec(props.gem, props.caste);
    avatar.base = colorFrom(props, avatar.spec);
    avatar.rgb = srgb(avatar.base);
    avatar.dirty = true;
    this.resize(avatar);
    this.kick();
  }

  unmount(canvas: HTMLCanvasElement) {
    const avatar = this.byCanvas.get(canvas);
    if (!avatar) return;
    this.observer?.unobserve(canvas);
    this.avatars.delete(avatar);
    this.byCanvas.delete(canvas);
  }

  private resize(avatar: Avatar) {
    const ratio = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
    const gemPx = Math.max(8, Math.round(avatar.props.size * ratio));
    const px = Math.round(gemPx * GEM_PAD);
    if (avatar.px !== px) {
      avatar.px = px;
      avatar.canvas.width = px;
      avatar.canvas.height = px;
    }
    avatar.gemPx = gemPx;
    if (gemPx > this.bufferPx && this.renderer) {
      this.bufferPx = gemPx;
      this.renderer.setSize(gemPx, gemPx, false);
    }
  }

  private kick() {
    if (!this.raf && this.avatars.size) this.raf = requestAnimationFrame(this.tick);
  }

  /** The activity being shown: a short dance takes over after a finished turn. */
  private shownActivity(avatar: Avatar, t: number): Activity {
    return avatar.activity === "idle" && t < avatar.danceUntil ? "dance" : avatar.activity;
  }

  private tick = (now: number) => {
    this.raf = 0;
    if (this.lost || !this.renderer) return;
    const t = now / 1000;
    const dt = this.lastTick ? Math.min(0.1, t - this.lastTick) : 1 / 60;
    this.lastTick = t;
    const started = performance.now();
    const strained = this.cost > 12;
    const list = [...this.avatars];
    const offset = list.length ? this.cursor % list.length : 0;
    let animating = false;
    let drawn = 0;
    let resume = -1;
    for (let i = 0; i < list.length; i += 1) {
      const index = (offset + i) % list.length;
      const avatar = list[index];
      if (!avatar.visible) continue;
      const activity = this.shownActivity(avatar, t);
      this.advance(avatar, activity, t, dt);
      // Transitions (a poof, a summon, the turn to the laptop) get full frames.
      const recent =
        t - avatar.activitySince < 1.4 ||
        (avatar.desk > 0 && avatar.desk < 1) ||
        (avatar.bubble > 0 && avatar.bubble < 1);
      const busy =
        activity !== "idle" && activity !== "offline" && activity !== "dreaming" && activity !== "waiting";
      const lively = busy || recent || avatar.burst > 0.01 || avatar.glints.length > 0;
      const interval =
        // Reduced motion: repaint only when something changes.
        (activity === "offline" && !recent) || this.still
          ? Infinity
          : lively
            ? strained ? 1 / 30 : 0
            : strained ? 1 / 15 : 1 / 30;
      if (interval !== Infinity) animating = true;
      if (!avatar.dirty && t - avatar.lastRender < interval) continue;
      if (drawn > 0 && performance.now() - started > FRAME_BUDGET_MS) {
        if (resume < 0) resume = index;
        animating = true;
        continue;
      }
      this.draw(avatar, activity, t);
      avatar.lastRender = t;
      avatar.dirty = false;
      drawn += 1;
    }
    this.cursor = resume >= 0 ? resume : offset;
    this.cost += (performance.now() - started - this.cost) * 0.1;
    if (animating) this.raf = requestAnimationFrame(this.tick);
  };

  /** Per-frame state: glow easing, the finish flash, blinks, gaze, glints. */
  private advance(avatar: Avatar, activity: Activity, t: number, dt: number) {
    const s = avatar.seed;
    let target = 0;
    if (BUSY.has(activity)) {
      // Breathing plus a faster flicker: a flashlight moving behind the stone.
      target = this.still ? 0.55 : 0.5 + 0.12 * Math.sin(t * 2.6 + s) + 0.04 * Math.sin(t * 7.3 + s * 3);
    } else if (activity === "error") {
      target = this.still ? 0.55 : 0.5 + 0.14 * Math.sin(t * 8);
    } else if (activity === "booting") {
      target = 0.6;
    } else if (activity === "dance") {
      target = 0.38 + 0.14 * Math.sin(t * 6);
    } else if (activity === "waiting") {
      target = this.still ? 0.28 : 0.24 + 0.1 * Math.sin(t * 1.6 + s);
    } else if (activity === "dreaming") {
      target = this.still ? 0.16 : 0.14 + 0.07 * Math.sin(t * 0.7 + s);
    } else if (activity === "idle") {
      target = 0.06;
    }
    avatar.glow += (target - avatar.glow) * (1 - Math.exp(-dt * 7));
    avatar.burst *= Math.exp(-dt * 3.2);
    if (avatar.burst < 0.01) avatar.burst = 0;

    // Turning side-on to the laptop and back, and the thought bubble, ease.
    const work = workPhase(activity, t - avatar.activitySince);
    const desk = work.laptop ? 1 : 0;
    avatar.desk = this.still ? desk : avatar.desk + (desk - avatar.desk) * (1 - Math.exp(-dt * 6));
    if (Math.abs(avatar.desk - desk) < 0.005) avatar.desk = desk;
    const bubble = work.bubble ? 1 : 0;
    avatar.bubble = this.still ? bubble : avatar.bubble + (bubble - avatar.bubble) * (1 - Math.exp(-dt * 6));
    if (Math.abs(avatar.bubble - bubble) < 0.005) avatar.bubble = bubble;

    // Blinks.
    if (t > avatar.blinkAt + 0.16) {
      const calm = BUSY.has(activity) ? 3.5 : 2.4;
      avatar.blinkAt = t + calm + Math.random() * 3.6;
    }
    const phase = t - avatar.blinkAt;
    avatar.blink = !this.still && phase >= 0 && phase <= 0.16 ? Math.abs(Math.cos((Math.PI * phase) / 0.16)) : 1;

    // Gaze: toward the pointer when it moved recently, else a slow wander.
    let tx = 0;
    let ty = 0;
    if (!this.still) {
      if (avatar.props.follow !== false && t - this.pointer.at < 4) {
        const rect = avatar.canvas.getBoundingClientRect();
        const dx = this.pointer.x - (rect.left + rect.width / 2);
        const dy = this.pointer.y - (rect.top + rect.height / 2);
        const dist = Math.hypot(dx, dy) || 1;
        const reach = Math.min(1, dist / 160);
        tx = (dx / dist) * reach;
        ty = (dy / dist) * reach;
      } else {
        tx = Math.sin(t * 0.37 + s) * 0.35;
        ty = Math.sin(t * 0.29 + s * 2) * 0.2;
      }
    }
    const ease = 1 - Math.exp(-dt * 10);
    avatar.look.x += (tx - avatar.look.x) * ease;
    avatar.look.y += (ty - avatar.look.y) * ease;

    // Syllable-ish mouth movement while streaming text.
    const talkTarget =
      (activity === "speaking" || activity === "messaging") && !this.still
        ? Math.abs(Math.sin(t * 11.5)) * (0.55 + 0.45 * Math.sin(t * 3.3 + s))
        : 0;
    avatar.talk += (Math.max(0, talkTarget) - avatar.talk) * (1 - Math.exp(-dt * 25));

    const rate = this.still
      ? 0
      : BUSY.has(activity)
        ? 1.8
        : ({ dance: 2, error: 0, waiting: 0.7, dreaming: 0.3, idle: 0.35, booting: 2, offline: 0 } as Record<
            string,
            number
          >)[activity] ?? 0.35;
    avatar.glintCarry += rate * dt + (avatar.burst > 0.9 ? 5 : 0);
    while (avatar.glintCarry >= 1) {
      avatar.glintCarry -= 1;
      const angle = Math.random() * Math.PI * 2;
      const radius = Math.sqrt(Math.random()) * 0.3;
      avatar.glints.push({
        // Biased up-left, toward the key light.
        x: 0.47 + Math.cos(angle) * radius,
        y: 0.44 + Math.sin(angle) * radius,
        age: 0,
        ttl: 0.45 + Math.random() * 0.45,
        size: 0.07 + Math.random() * 0.1,
        hue: avatar.spec.fire && Math.random() < 0.7 ? Math.floor(Math.random() * 360) : null,
      });
    }
    for (const glint of avatar.glints) glint.age += dt;
    avatar.glints = avatar.glints.filter((glint) => glint.age < glint.ttl);
  }

  private rig(avatar: Avatar): Rig {
    const key = `${avatar.props.gem || "unassigned"}|${avatar.base.getHexString()}`;
    let rig = this.rigs.get(key);
    if (rig) return rig;

    const { spec, base } = avatar;
    const geometry = gemGeometry(spec);
    const group = new Group();
    const light = new PointLight(base.clone().lerp(WHITE, 0.35), 0, 0, 2);
    group.add(light);

    let body: Mesh<BufferGeometry, MeshPhysicalMaterial>;
    let inner: Rig["inner"];
    let core: Rig["core"];
    let flecks: Rig["flecks"];
    let textured = false;

    if (spec.finish === "faceted") {
      // Back facets first, lit from inside by the point light, then a glassy
      // front shell over them: the cheap, convincing way to fake refraction.
      inner = new Mesh(
        geometry,
        new MeshPhysicalMaterial({
          color: base,
          emissive: base,
          roughness: 0.04,
          side: BackSide,
          flatShading: true,
          envMapIntensity: 1.8,
        }),
      );
      inner.renderOrder = 1;
      // The flashlight: a soft hot spot in the gem's own hue, pushed past 1.0
      // so tone mapping blooms it toward white at the center only.
      core = new Sprite(
        new SpriteMaterial({
          map: softDot(),
          color: base.clone().multiplyScalar(3),
          transparent: true,
          opacity: 0,
          blending: AdditiveBlending,
          depthWrite: false,
        }),
      );
      core.renderOrder = 2;
      body = new Mesh(
        geometry,
        new MeshPhysicalMaterial({
          color: base,
          emissive: base,
          metalness: 0,
          roughness: 0,
          clearcoat: 1,
          clearcoatRoughness: 0,
          ior: 1.76,
          iridescence: spec.iridescence ?? 0.12,
          iridescenceIOR: 1.6,
          flatShading: true,
          transparent: true,
          opacity: 0.5,
          depthWrite: false,
          envMapIntensity: 3.2,
        }),
      );
      body.renderOrder = 3;
      group.add(inner, core, body);
    } else {
      const metal = spec.finish === "metal";
      const pearl = spec.finish === "pearl";
      const map = spec.pattern ? stoneTexture(spec.pattern, base, avatar.seed) : null;
      textured = map !== null;
      body = new Mesh(
        geometry,
        new MeshPhysicalMaterial({
          color: textured ? WHITE : base,
          map,
          // The pattern doubles as the emissive map, so a glowing stone lights
          // up its own veins and play-of-color rather than a flat tint.
          emissive: textured ? WHITE : base,
          emissiveMap: map,
          metalness: metal ? 1 : 0,
          roughness: metal ? 0.3 : pearl ? 0.24 : 0.22,
          clearcoat: metal ? 0.5 : 1,
          clearcoatRoughness: 0.04,
          iridescence: spec.iridescence ?? 0,
          iridescenceIOR: pearl ? 1.45 : 1.8,
          iridescenceThicknessRange: metal ? [250, 1100] : [180, 820],
          sheen: pearl || spec.iridescence ? 0.8 : 0,
          sheenColor: new Color(pearl ? "#ffd6e8" : spec.pattern === "moonstone" ? "#a8ccff" : "#ffffff"),
          sheenRoughness: 0.4,
          flatShading: metal,
          envMapIntensity: metal ? 3.4 : 1.7,
        }),
      );
      group.add(body);
      if (spec.flecks) {
        // Pyrite specks that twinkle, spread evenly over the visible face of
        // the dome: sampled in the projected disc, then lifted onto the
        // surface, so they don't bunch up along the silhouette.
        const count = 28;
        const positions = new Float32Array(count * 3);
        const phases = new Float32Array(count);
        if (!geometry.boundingBox) geometry.computeBoundingBox();
        const box = geometry.boundingBox;
        const sx = box ? box.max.x : 1;
        const sy = box ? box.max.y : 1;
        const sz = box ? box.max.z : 0.62;
        for (let i = 0; i < count; i += 1) {
          const angle = Math.random() * Math.PI * 2;
          const radius = Math.sqrt(Math.random()) * 0.82;
          const x = Math.cos(angle) * radius;
          const y = Math.sin(angle) * radius;
          const z = Math.sqrt(Math.max(0, 1 - x * x - y * y));
          positions.set([x * sx * 1.01, y * sy * 1.01, z * sz * 1.01], i * 3);
          phases[i] = Math.random() * Math.PI * 2;
        }
        const fleckGeometry = new BufferGeometry();
        fleckGeometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
        fleckGeometry.setAttribute("color", new Float32BufferAttribute(new Float32Array(count * 3), 3));
        fleckGeometry.userData.phases = phases;
        flecks = new Points(
          fleckGeometry,
          new PointsMaterial({
            size: 2,
            sizeAttenuation: false,
            vertexColors: true,
            transparent: true,
            blending: AdditiveBlending,
            depthWrite: false,
          }),
        );
        flecks.renderOrder = 4;
        group.add(flecks);
      }
    }

    group.visible = false;
    this.scene.add(group);
    rig = { group, body, inner, core, flecks, light, textured };
    this.rigs.set(key, rig);
    return rig;
  }

  private draw(avatar: Avatar, activity: Activity, t: number) {
    const renderer = this.renderer;
    if (!renderer) return;
    const rig = this.rig(avatar);
    if (this.shown !== rig) {
      if (this.shown) this.shown.group.visible = false;
      rig.group.visible = true;
      this.shown = rig;
    }
    const glow = Math.min(1.1, avatar.glow + avatar.burst * 0.7);
    this.pose(avatar, rig, activity, t, glow);

    const g = avatar.gemPx;
    const px = avatar.px;
    // Whole pixels: at a half-pixel offset the copy below blends in the row and
    // column just outside the gem's square, where another gem's pixels remain.
    const o = Math.floor((px - g) / 2);
    renderer.setViewport(0, 0, g, g);
    renderer.setScissor(0, 0, g, g);
    renderer.setScissorTest(true);
    renderer.clear();
    renderer.render(this.scene, this.camera);

    const ctx = avatar.ctx;
    ctx.clearRect(0, 0, px, px);
    const glowColor = activity === "error" ? ANGRY.clone() : avatar.base.clone().lerp(WHITE, 0.3);
    const lift = avatar.bob * g * WORLD_TO_FRAME;
    const slideX = avatar.slide * g * WORLD_TO_FRAME;
    const center = o + g / 2;
    if (glow > 0.03) {
      const halo = ctx.createRadialGradient(center + slideX, center - lift, 0, center + slideX, center - lift, g * 0.58);
      halo.addColorStop(0, rgbString(glowColor, Math.min(0.32, 0.3 * glow)));
      halo.addColorStop(0.5, rgbString(glowColor, Math.min(0.14, 0.12 * glow)));
      halo.addColorStop(1, rgbString(glowColor, 0));
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, px, px);
    }

    const face = avatar.props.face ?? "pill";
    const anchor = FACE_ANCHORS[avatar.spec.cut];
    const sway = avatar.yaw * g * 0.09;
    const season = avatar.props.season === "auto" ? seasonFor() : (avatar.props.season ?? "none");
    const hatProp = avatar.props.hat ?? "auto";
    const base = avatar.base;
    const frame: FaceFrame = {
      ctx,
      t,
      since: t - avatar.activitySince,
      prev: avatar.prev,
      box: { x: o, y: o, s: g },
      anchor,
      shift: { x: sway, y: 0 },
      look: avatar.look,
      blink: avatar.blink,
      activity,
      style: face === "none" ? "pill" : face,
      // A bubbled (offline) gem rests bare-headed.
      hat:
        activity === "offline" ? "none" : hatProp === "auto" ? autoHat(avatar.props.caste, season, avatar.seed) : hatProp,
      season,
      rgb: avatar.rgb,
      dark: base.r * 0.2126 + base.g * 0.7152 + base.b * 0.0722 < 0.05,
      talk: avatar.talk,
      desk: avatar.desk,
      bubble: avatar.bubble,
      seed: avatar.seed,
      still: this.still,
    };
    if (face !== "none") drawBackProps(frame);
    // WebGL's viewport origin is bottom-left; the gem sits in the buffer's
    // bottom-left corner.
    ctx.drawImage(renderer.domElement, 0, this.bufferPx - g, g, g, o, o, g, g);
    this.drawGlints(avatar, o + slideX, o - lift, g);

    if (face !== "none") {
      // The face, hat and held tools ride the body: its hop, shake, lean and squash.
      ctx.save();
      ctx.translate(center + slideX, center - lift);
      ctx.rotate(-avatar.roll);
      ctx.scale(1 - avatar.stretch * 0.04, 1 + avatar.stretch * 0.06);
      ctx.translate(-center, -center);
      // When the gem turns (a spin, or side-on to its laptop) whatever is on
      // its front wraps around the body: it slides toward the edge as it
      // narrows, and hides while it is round the back.
      const turn = Math.cos(avatar.spin);
      if (turn > 0.04) {
        const offset = Math.sin(avatar.spin) * g * 0.24;
        const fx = o + anchor.x * g + sway + offset;
        const front = { ...frame, shift: { x: sway + offset, y: 0 } };
        ctx.save();
        ctx.translate(fx, 0);
        ctx.scale(turn, 1);
        ctx.translate(-fx, 0);
        drawFace(front);
        drawSeasonBody(front);
        ctx.restore();
      }
      drawHat(frame);
      drawHeld(frame);
      ctx.restore();
      drawProps(frame);
      drawEffects(frame);
    }
  }

  private pose(avatar: Avatar, rig: Rig, activity: Activity, t: number, glow: number) {
    const { mood, seed: s, spec, look } = avatar;
    let yaw = 0;
    let pitch = 0;
    let bob = 0;
    let roll = 0;
    let slide = 0;
    let stretch = 0;
    let spin = 0;
    if (!this.still && activity !== "offline") {
      const speed = BUSY.has(activity) ? 1.6 : activity === "dreaming" ? 0.45 : 1;
      yaw = 0.24 * Math.sin(t * 0.55 * speed + s) + look.x * 0.25;
      pitch = 0.09 * Math.sin(t * 0.41 * speed + s * 1.7) + look.y * 0.14;
      bob = 0.045 * Math.sin(t * 1.15 * speed + s * 2.3);
      // Hunched over the keys, bouncing with the typing, as far as the gem
      // has turned to the laptop (a snap here read as a jump cut).
      const work = workPhase(activity, t - avatar.activitySince);
      const typingBob = (work.furious ? 0.03 : 0.018) * Math.sin(t * (work.furious ? 26 : 13));
      pitch += 0.12 * avatar.desk;
      bob += (typingBob - bob) * avatar.desk;
      if (activity === "dance") {
        // A hop with a squash on every beat and a lean; twist for two beats,
        // then a full spin over the next two (both ends meet at zero).
        const u = ((t * 7) / Math.PI) % 4;
        bob = Math.abs(Math.sin(t * 7)) * 0.16;
        roll = Math.sin(t * 3.5) * 0.22;
        stretch = Math.sin(t * 14);
        spin = u < 2 ? 0.45 * Math.sin(Math.PI * u) : (u - 2) * Math.PI;
      }
      if (activity === "error") {
        const since = t - avatar.activitySince;
        const envelope = since < 0.7 ? 1 : 0.35 * Math.max(0, Math.sin(t * 2.2));
        slide = Math.sin(t * 38) * 0.04 * envelope;
      }
      if (activity === "waiting") {
        pitch += 0.05;
        bob += Math.max(0, Math.sin(t * 3.1)) ** 8 * 0.08;
      }
      if (activity === "tasking" && t - avatar.activitySince < 1.3) {
        // Standing at attention for the salute.
        yaw = look.x * 0.1;
        pitch = -0.06;
        bob = 0;
      }
      if (mood === "booting") {
        yaw = 0;
        spin = (t * 2.4 + s) % (Math.PI * 2);
      }
    }
    // At the laptop the gem turns side-on and scoots left to make room.
    spin += avatar.desk * 0.95;
    slide -= avatar.desk * 0.26;
    yaw *= 1 - avatar.desk * 0.7;
    rig.group.rotation.set(pitch + 0.08, yaw + spin - 0.22, roll);
    rig.group.position.set(slide, bob, 0);
    rig.group.scale.set(1 - stretch * 0.04, 1 + stretch * 0.06, 1 - stretch * 0.04);
    avatar.yaw = yaw - look.x * 0.25;
    avatar.bob = bob;
    avatar.slide = slide;
    avatar.roll = roll;
    avatar.spin = spin;
    avatar.stretch = stretch;

    // Light sweeping across the facets is what makes them flash.
    const busy = BUSY.has(activity) || activity === "dance";
    const sweep = this.still ? s : t * (busy ? 1.5 : 0.35) + s;
    this.scene.environmentRotation.set(0, sweep, 0);
    const orbit = this.still ? s : t * (busy ? 1.2 : 0.5) + s;
    this.key.position.set(Math.cos(orbit) * 3, 2.4, 2.2 + Math.sin(orbit) * 1.5);

    const offline = activity === "offline";
    const color = avatar.base.clone();
    if (offline) {
      const gray = color.r * 0.2126 + color.g * 0.7152 + color.b * 0.0722;
      color.lerp(new Color(gray, gray, gray), 0.72).multiplyScalar(0.55);
    } else if (activity === "error") {
      color.lerp(ANGRY, 0.55);
    }
    if (rig.textured) {
      // The map carries the color; dim, gray or redden it through the multiplier.
      if (activity === "error") rig.body.material.color.copy(ANGRY).lerp(WHITE, 0.45);
      else rig.body.material.color.setScalar(offline ? 0.32 : 1);
      rig.body.material.emissive.setScalar(offline ? 0 : 1);
    } else {
      rig.body.material.color.copy(color);
      rig.body.material.emissive.copy(color);
    }
    if (spec.finish === "faceted") {
      rig.body.material.emissiveIntensity = glow * 0.25;
      if (rig.inner) {
        rig.inner.material.color.copy(color);
        rig.inner.material.emissive.copy(color);
        rig.inner.material.emissiveIntensity = offline ? 0.01 : 0.04 + glow * 0.55;
      }
      // The light wanders inside the stone, so different facets catch it in
      // turn: a flashlight moving behind the gem.
      const wander = this.still ? 0 : 1;
      const lx = Math.cos(t * 2.1 + s) * 0.34 * wander;
      const ly = Math.sin(t * 1.7 + s * 1.3) * 0.26 * wander;
      rig.light.color.copy(activity === "error" ? ANGRY : avatar.base.clone().lerp(WHITE, 0.35));
      rig.light.intensity = glow * 16;
      rig.light.position.set(lx, ly, 0.1);
      if (rig.core) {
        rig.core.material.color.copy(color).multiplyScalar(3);
        rig.core.material.opacity = Math.min(0.8, glow * 0.7);
        rig.core.scale.setScalar(0.9 + 0.5 * Math.min(glow, 1.2));
        rig.core.position.set(lx * 0.6, ly * 0.6, 0.05);
      }
    } else {
      rig.body.material.emissiveIntensity = (offline ? 0 : rig.textured ? 0.03 : 0.04) + glow * 0.6;
      rig.light.intensity = 0;
    }
    if (rig.flecks) {
      const colors = rig.flecks.geometry.getAttribute("color") as Float32BufferAttribute;
      const phases = rig.flecks.geometry.userData.phases as Float32Array;
      const gold = new Color(spec.flecks);
      for (let i = 0; i < phases.length; i += 1) {
        const twinkle = this.still ? 0.6 : Math.max(0, Math.sin(t * 2.2 + phases[i])) ** 4;
        const level = offline ? 0.12 : 0.3 + 0.7 * twinkle + glow * 0.6;
        colors.setXYZ(i, gold.r * level, gold.g * level, gold.b * level);
      }
      colors.needsUpdate = true;
    }
  }

  private drawGlints(avatar: Avatar, ox: number, oy: number, g: number) {
    if (!avatar.glints.length) return;
    const ctx = avatar.ctx;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const glint of avatar.glints) {
      const k = Math.sin((Math.PI * glint.age) / glint.ttl);
      const r = glint.size * g * k;
      if (r < 0.5) continue;
      const x = ox + glint.x * g;
      const y = oy + glint.y * g;
      // A four-point sparkle: concave diamond from four quadratic curves.
      // Dispersive stones throw colored fire instead of white.
      ctx.fillStyle =
        glint.hue === null
          ? `rgba(255,255,255,${(0.9 * k).toFixed(3)})`
          : `hsla(${glint.hue},100%,78%,${(0.95 * k).toFixed(3)})`;
      ctx.beginPath();
      ctx.moveTo(x, y - r);
      ctx.quadraticCurveTo(x, y, x + r * 0.7, y);
      ctx.quadraticCurveTo(x, y, x, y + r);
      ctx.quadraticCurveTo(x, y, x - r * 0.7, y);
      ctx.quadraticCurveTo(x, y, x, y - r);
      ctx.fill();
    }
    ctx.restore();
  }
}

export const gemEngine = new GemEngine();
