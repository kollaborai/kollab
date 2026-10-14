import { Color, Mesh, PerspectiveCamera, SRGBColorSpace, Scene, Vector3, Vector4, WebGLRenderer, type ShaderMaterial } from "three";
import { HOME_SHAPE, SHAPES, bodyGeometry, bodyMaterial, bodyPoint, faceHeight, paintBody, type Shape } from "./gem-blob";
import { drawEffects, drawHat, drawSeasonBody } from "./gem-dress";
import {
  autoHat,
  drawFace,
  eyeGap,
  eyeShape,
  gazeFor,
  mouthShape,
  seasonFor,
  workPhase,
  type Activity,
  type EyeStyle,
  type FaceAnchor,
  type FaceFrame,
  type HatStyle,
  type Season,
} from "./gem-face";
import { drawn } from "./gem-look";
import { drawBackProps, drawHeld, drawProps } from "./gem-props";
import { gemMood, gemSpec, type GemMood, type GemSpec } from "./gem-specs";

/**
 * One WebGL context renders every gem avatar on the page.
 *
 * Browsers cap live WebGL contexts (Chrome at 16), so a canvas per sidebar row
 * would fall over with a busy hub. Instead a single offscreen renderer draws
 * each visible avatar in turn and blits the pixels into that avatar's own 2D
 * canvas, which then gets a 2D pass: halo, glints, and the personality layer
 * from gem-face.ts. The gem and its hat sit inside the layout box (FIT, SINK);
 * the canvas is GEM_PAD times the box so laptops, thought bubbles and flying
 * keyboards have room around the stone.
 * Idle gems repaint at 30fps, busy gems every frame, offline gems once.
 *
 * The body is a soft gem (gem-blob.ts) that is always moving: it breathes,
 * turns toward the pointer a beat after its eyes do, and morphs its shape in
 * the way its motion says (GemMotion). The face is seated on the surface each
 * frame, so it turns, squashes and wobbles with the body.
 */

export const GEM_PAD = 1.6;

/**
 * The gem is drawn FIT of its layout box and SINK (a fraction of the box) lower
 * in it, so the stone and its hat sit inside the box and never reach into the
 * row above. The camera zooms and slides by the same amounts, and the 2D layers
 * draw into the matching frame.
 */
const FIT = 0.84;
const SINK = 0.13;

/**
 * How a gem's body moves: "mood" takes a shape for what it is doing (a cloud
 * while thinking, round while talking, sharp on an error), "jelly" keeps its
 * cut but wobbles and goes blobby when the pointer flies, "cycle" drifts
 * through shapes on its own and snaps back to its cut when the pointer comes
 * close. Each agent is born with one, drawn from its look key.
 */
export type GemMotion = "mood" | "jelly" | "cycle";
export const GEM_MOTIONS: readonly GemMotion[] = ["mood", "jelly", "cycle"];

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
  /** Whose look this is (gem-look.ts lookKey): two agents of one name move out of step. */
  lookKey?: string;
  /** How the body moves; absent, the agent's born motion. */
  motion?: GemMotion | null;
};

type Glint = { x: number; y: number; age: number; ttl: number; size: number; hue: number | null };

/** A damped spring, stepped at 120 Hz so a long frame cannot throw it. Low damping overshoots, which reads as soft. */
class Spring {
  v = 0;
  constructor(public x: number) {}
  to(target: number, k: number, c: number, dt: number): number {
    const steps = Math.max(1, Math.ceil(dt * 120));
    const h = dt / steps;
    for (let i = 0; i < steps; i += 1) {
      this.v += (k * (target - this.x) - c * this.v) * h;
      this.x += this.v * h;
    }
    return this.x;
  }
  set(x: number) {
    this.x = x;
    this.v = 0;
  }
}

type Avatar = {
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  props: GemProps;
  spec: GemSpec;
  base: Color;
  rgb: [number, number, number];
  mood: GemMood;
  motion: GemMotion;
  /** The shape its cut rests in, as an index into SHAPES. */
  home: number;
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
  /** Where the pupils look, -1..1, on springs so a dart overshoots. */
  look: { x: number; y: number };
  gaze: [Spring, Spring];
  /** Where the body is turning to: the eyes' target, a beat late. */
  lead: { x: number; y: number };
  turn: [Spring, Spring];
  /** The weight of each body shape (SHAPES). */
  shape: Spring[];
  /** Pill eye and shared mouth shape mixes (gem-face.ts eyeShape, mouthShape). */
  eyes: Spring[];
  lid: number;
  mouth: Spring[];
  mouthOpen: number;
  /** When the next passing smile starts. */
  cheerAt: number;
  /** 0..1: how stirred up a jelly gem is. */
  energy: number;
  /** A cycling gem holds its cut until then. */
  focusUntil: number;
  wob: number;
  shear: number;
  talk: number;
  glints: Glint[];
  glintCarry: number;
};

/** An affine from face pixels to canvas pixels, and 0..1 for how squarely the patch faces the camera. */
type Seat = { a: number; b: number; c: number; d: number; e: number; f: number; vis: number };

const FOV = 28;
const CAMERA_DISTANCE = 4.7;
// World units across the frame: a unit-radius gem fills ~85% of it.
const FRAME = 2 * Math.tan(((FOV / 2) * Math.PI) / 180) * CAMERA_DISTANCE;
// The body renders across the whole padded canvas, so a hop or the scoot to
// the laptop never clips it at the box's edge.
const CANVAS_FOV = (2 * Math.atan(Math.tan(((FOV / 2) * Math.PI) / 180) * GEM_PAD) * 180) / Math.PI;
const WORLD_TO_FRAME = 1 / FRAME;
const MAX_PIXEL_RATIO = 2;
// Milliseconds of drawing per frame. Past it the remaining gems wait a frame,
// so a crowded page animates a little slower instead of freezing input.
const FRAME_BUDGET_MS = 8;
const WHITE = new Color(1, 1, 1);
const ANGRY = new Color("#ff2d20");
// The layout box in frame fractions: the room a hat, bat or thread has.
const FRAME_TOP = 0.5 + SINK - FIT / 2;
const VIEW = {
  x0: (FIT - 1) / (2 * FIT),
  x1: (FIT + 1) / (2 * FIT),
  y0: -FRAME_TOP / FIT,
  y1: (1 - FRAME_TOP) / FIT,
};
/** Where the face, hat and props sit on the soft gem, as fractions of its box. */
const ANCHOR: FaceAnchor = { x: 0.5, y: 0.5, gap: 1.15, hatY: 0.15, hatScale: 0.66, view: VIEW };
// The hat is seated this far toward the face from the crown (body-local, of its height), so a
// turn, lean or hop carries it sideways the way it carries the face. 0 is the pole, which a turn
// never moves; past about 0.9 the hat slides down onto the brow and tilts off the head.
const HAT_FWD = 0.75;
/** How big the face is drawn against the body. */
const FACE_SCALE = 1.24;
const BUSY: ReadonlySet<Activity> = new Set([
  "thinking",
  "typing",
  "reading",
  "speaking",
  "searching",
  "messaging",
  "tasking",
]);
const shapeOf = (shape: Shape) => SHAPES.indexOf(shape);
/** Mood motion: the shape for what the gem is doing; anything else rests in its cut. */
const MOOD_SHAPES: Partial<Record<Activity, number>> = {
  thinking: shapeOf("cloud"),
  booting: shapeOf("cloud"),
  speaking: shapeOf("dome"),
  messaging: shapeOf("dome"),
  dance: shapeOf("dome"),
  waiting: shapeOf("dome"),
  dreaming: shapeOf("pebble"),
};

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

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

const whose = (props: GemProps) => props.lookKey || props.gem || "unassigned";
const motionFor = (props: GemProps): GemMotion => props.motion ?? drawn(whose(props), "motion", GEM_MOTIONS) ?? "mood";
const homeFor = (spec: GemSpec) => shapeOf(HOME_SHAPE[spec.cut] ?? "brilliant");

// Scratch space for seating the face.
const P = new Vector3();
const U = new Vector3();
const V = new Vector3();
const Q = new Vector3();
const N = new Vector3();
const c0 = { x: 0, y: 0 };
const c1 = { x: 0, y: 0 };
const c2 = { x: 0, y: 0 };
const weights = new Float32Array(SHAPES.length);
const tint = new Color();

class GemEngine {
  private renderer: WebGLRenderer | null = null;
  private scene = new Scene();
  private camera = new PerspectiveCamera(CANVAS_FOV, 1, 0.1, 20);
  private body: Mesh | null = null;
  private material: ShaderMaterial | null = null;
  /** Where the face is painted before it is trimmed to the body's silhouette. */
  private paint: CanvasRenderingContext2D | null = null;
  private avatars = new Set<Avatar>();
  private byCanvas = new WeakMap<HTMLCanvasElement, Avatar>();
  private observer: IntersectionObserver | null = null;
  private pointer = { x: 0, y: 0, at: -Infinity };
  /** Pointer speed in px/s, smoothed; it stirs jelly gems. */
  private speed = 0;
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
      this.camera.position.set(0, 0, CAMERA_DISTANCE);
      this.camera.updateMatrixWorld();
      // Zoom out to the gem's frame and slide it down, so the figure lands in the box.
      this.camera.zoom = FIT;
      this.camera.setViewOffset(1, 1, 0, -SINK / GEM_PAD, 1, 1);
      this.material = bodyMaterial();
      this.body = new Mesh(bodyGeometry(), this.material);
      this.body.frustumCulled = false;
      this.scene.add(this.body);
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
        const at = performance.now() / 1000;
        const moved = Math.hypot(event.clientX - this.pointer.x, event.clientY - this.pointer.y);
        const v = Math.min(6000, moved / Math.max(0.008, at - this.pointer.at));
        this.speed = this.speed * 0.7 + v * 0.3;
        this.pointer = { x: event.clientX, y: event.clientY, at };
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
    const home = homeFor(spec);
    const avatar: Avatar = {
      canvas,
      ctx,
      props,
      spec,
      base,
      rgb: srgb(base),
      mood,
      motion: motionFor(props),
      home,
      activity: activityFor(props, mood),
      activitySince: now,
      prev: activityFor(props, mood),
      danceUntil: 0,
      px: 0,
      gemPx: 0,
      seed: hashSeed(whose(props)),
      visible: false,
      dirty: true,
      lastRender: 0,
      glow: 0,
      burst: 0,
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
      gaze: [new Spring(0), new Spring(0)],
      lead: { x: 0, y: 0 },
      turn: [new Spring(0), new Spring(0)],
      shape: SHAPES.map((_, i) => new Spring(i === home ? 1 : 0)),
      eyes: [1, 0, 0, 0].map((w) => new Spring(w)),
      lid: 0,
      mouth: [1, 0, 0, 0, 0].map((w) => new Spring(w)),
      mouthOpen: 0,
      cheerAt: now + 4 + Math.random() * 4,
      energy: 0,
      focusUntil: 0,
      wob: 0,
      shear: 0,
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
    avatar.home = homeFor(avatar.spec);
    avatar.motion = motionFor(props);
    avatar.seed = hashSeed(whose(props));
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
    if (px > this.bufferPx && this.renderer) {
      this.bufferPx = px;
      this.renderer.setSize(px, px, false);
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
    if (t - this.pointer.at > 0.08) this.speed *= Math.exp(-dt * 5);
    const started = performance.now();
    const strained = this.cost > 12;
    const list = [...this.avatars];
    const offset = list.length ? this.cursor % list.length : 0;
    let animating = false;
    let painted = 0;
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
      if (painted > 0 && performance.now() - started > FRAME_BUDGET_MS) {
        if (resume < 0) resume = index;
        animating = true;
        continue;
      }
      this.draw(avatar, activity, t);
      avatar.lastRender = t;
      avatar.dirty = false;
      painted += 1;
    }
    this.cursor = resume >= 0 ? resume : offset;
    this.cost += (performance.now() - started - this.cost) * 0.1;
    if (animating) this.raf = requestAnimationFrame(this.tick);
  };

  /** Per-frame state: glow easing, the finish flash, blinks, gaze, the body's springs, glints. */
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

    // Blinks, sometimes twice.
    if (t > avatar.blinkAt + 0.16) {
      const calm = BUSY.has(activity) ? 3.5 : 2.4;
      avatar.blinkAt = t + (Math.random() < 0.15 ? 0.1 : calm + Math.random() * 3.6);
    }
    const phase = t - avatar.blinkAt;
    avatar.blink = !this.still && phase >= 0 && phase <= 0.16 ? Math.abs(Math.cos((Math.PI * phase) / 0.16)) : 1;

    // Gaze: toward the pointer when it moved recently, else a slow wander.
    let tx = 0;
    let ty = 0;
    let near = false;
    if (!this.still) {
      if (avatar.props.follow !== false && t - this.pointer.at < 4) {
        const rect = avatar.canvas.getBoundingClientRect();
        const dx = this.pointer.x - (rect.left + rect.width / 2);
        const dy = this.pointer.y - (rect.top + rect.height / 2);
        const dist = Math.hypot(dx, dy) || 1;
        const reach = Math.min(1, dist / 160);
        tx = (dx / dist) * reach;
        ty = (dy / dist) * reach;
        near = dist < Math.max(90, rect.width);
      } else {
        tx = Math.sin(t * 0.37 + s) * 0.35;
        ty = Math.sin(t * 0.29 + s * 2) * 0.2;
      }
    }
    // The eyes dart on a stiff spring; the body follows a beat later.
    avatar.look.x = avatar.gaze[0].to(tx, 260, 21, dt);
    avatar.look.y = avatar.gaze[1].to(ty, 260, 21, dt);
    const aim = gazeFor({ activity, look: { x: tx, y: ty }, t, desk: avatar.desk });
    const lag = this.still ? 1 : 1 - Math.exp(-dt / 0.12);
    avatar.lead.x += (aim.x - avatar.lead.x) * lag;
    avatar.lead.y += (aim.y - avatar.lead.y) * lag;
    if (this.still) {
      avatar.turn.forEach((spring) => spring.set(0));
    } else {
      // Looking up turns further than looking down, which would hide the face.
      avatar.turn[0].to(avatar.lead.x * 0.5, 55, 11, dt);
      avatar.turn[1].to(avatar.lead.y * (avatar.lead.y > 0 ? 0.16 : 0.38), 55, 11, dt);
    }

    // Syllable-ish mouth movement while streaming text.
    const talkTarget =
      (activity === "speaking" || activity === "messaging") && !this.still
        ? Math.abs(Math.sin(t * 11.5)) * (0.55 + 0.45 * Math.sin(t * 3.3 + s))
        : 0;
    avatar.talk += (Math.max(0, talkTarget) - avatar.talk) * (1 - Math.exp(-dt * 25));

    // The body's shape, the pill eyes and the mouth morph on springs.
    avatar.energy += (clamp(this.speed / 1600, 0, 1) + (BUSY.has(activity) ? 0.2 : 0) - avatar.energy) * (1 - Math.exp(-dt * 4));
    const goal = this.shapeGoal(avatar, activity, t, near);
    const [k, c] = avatar.motion === "jelly" ? [45, 7] : [70, 7.5];
    avatar.shape.forEach((spring, i) => (this.still ? spring.set(goal[i]) : spring.to(goal[i], k, c, dt)));
    if (t > avatar.cheerAt + 0.8) avatar.cheerAt = t + 4 + Math.random() * 5;
    const cheer = activity === "idle" && t > avatar.cheerAt && !this.still;
    const eyes = eyeShape(activity, t, cheer);
    avatar.eyes.forEach((spring, i) => (this.still ? spring.set(eyes[i]) : spring.to(eyes[i], 170, 19, dt)));
    const lid = activity === "error" ? 0.5 : work.furious ? 0.3 : 0;
    avatar.lid = this.still ? lid : avatar.lid + (lid - avatar.lid) * (1 - Math.exp(-dt * 12));
    const mouth = mouthShape(activity, t, avatar.talk, cheer);
    avatar.mouth.forEach((spring, i) => (this.still ? spring.set(mouth.mix[i]) : spring.to(mouth.mix[i], 150, 17, dt)));
    avatar.mouthOpen = mouth.open;

    const rate = this.still
      ? 0
      : BUSY.has(activity)
        ? 1
        : ({ dance: 2, error: 0, waiting: 0.5, dreaming: 0.2, idle: 0.12, booting: 2, offline: 0 } as Record<
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
        size: 0.045 + Math.random() * 0.06,
        hue: avatar.spec.fire && Math.random() < 0.7 ? Math.floor(Math.random() * 360) : null,
      });
    }
    for (const glint of avatar.glints) glint.age += dt;
    avatar.glints = avatar.glints.filter((glint) => glint.age < glint.ttl);
  }

  /** The shape mix the body is heading for, by its motion. */
  private shapeGoal(avatar: Avatar, activity: Activity, t: number, near: boolean): number[] {
    const goal = SHAPES.map(() => 0);
    const home = avatar.home;
    const mood = () =>
      activity === "error"
        ? home === shapeOf("octa") ? shapeOf("brilliant") : shapeOf("octa")
        : MOOD_SHAPES[activity] ?? home;
    if (activity === "offline" || (this.still && avatar.motion !== "mood")) {
      // Reduced motion keeps only the mood shapes, which snap: they say what the gem is doing.
      goal[home] = 1;
    } else if (avatar.motion === "jelly") {
      // A fast pointer or a busy turn stirs it out of its cut into a blob.
      const e = Math.min(1, avatar.energy);
      goal[home] += 1 - e * 0.6;
      goal[shapeOf("dome")] += e * 0.35;
      goal[shapeOf("cloud")] += e * 0.25;
    } else if (avatar.motion === "cycle" && activity === "idle") {
      if (near) avatar.focusUntil = t + 1.5;
      const loop = [home, shapeOf("dome"), shapeOf("cloud"), home === shapeOf("brilliant") ? shapeOf("crystal") : shapeOf("brilliant")];
      goal[t < avatar.focusUntil ? home : loop[Math.floor(t / 2.6 + avatar.seed) % loop.length]] = 1;
    } else {
      goal[mood()] = 1;
    }
    return goal;
  }

  /** A cleared scratch canvas at least px across, shared by every gem. */
  private paintLayer(px: number): CanvasRenderingContext2D | null {
    if (!this.paint) this.paint = document.createElement("canvas").getContext("2d");
    const paint = this.paint;
    if (!paint) return null;
    if (paint.canvas.width < px) {
      paint.canvas.width = px;
      paint.canvas.height = px;
    }
    paint.setTransform(1, 0, 0, 1, 0, 0);
    paint.clearRect(0, 0, px, px);
    return paint;
  }

  /** A body-local point on the canvas (px across), in pixels. */
  private project(local: Vector3, px: number, out: { x: number; y: number }) {
    if (!this.body) return;
    Q.copy(local).applyMatrix4(this.body.matrixWorld).project(this.camera);
    out.x = (Q.x + 1) * 0.5 * px;
    out.y = (1 - Q.y) * 0.5 * px;
  }

  /**
   * Seats the face on the front of the body, its eye line centered at (fx, fy):
   * the affine that draws face pixels (a front-facing face at box scale) onto
   * the canvas. It runs through three points pinned to the surface, either eye
   * and the mouth, so the face turns, foreshortens, squashes and wobbles with
   * the body and the far eye wraps toward the edge instead of hanging off it.
   */
  private seat(avatar: Avatar, fx: number, fy: number, eye: number, t: number, px: number, g: number): Seat {
    const { wob, shear } = avatar;
    const body = this.body;
    // Face pixels to surface directions: the eyes sit EYE (of the box) either
    // side of the center, the mouth MOUTH below it. The spread is angular, so a
    // narrow crystal wears a smaller face than a round dome.
    const EYE = eye;
    const MOUTH = 0.13;
    const k = (FACE_SCALE * FRAME) / 0.95;
    bodyPoint(weights, fx - EYE * k, fy, 1, wob, t, shear, U);
    bodyPoint(weights, fx + EYE * k, fy, 1, wob, t, shear, V);
    bodyPoint(weights, fx, fy - MOUTH * k, 1, wob, t, shear, P);
    this.project(U, px, c0);
    this.project(V, px, c1);
    this.project(P, px, c2);
    const e = (c0.x + c1.x) / 2;
    const f = (c0.y + c1.y) / 2;
    // Fade it out as it turns away, so it never shows through the back.
    let vis = 1;
    if (body) {
      U.applyMatrix4(body.matrixWorld);
      V.applyMatrix4(body.matrixWorld);
      P.applyMatrix4(body.matrixWorld);
      Q.addVectors(U, V).multiplyScalar(0.5);
      N.subVectors(V, U).cross(P.sub(Q).negate()).normalize();
      vis = clamp((N.dot(Q.sub(this.camera.position).negate().normalize()) - 0.15) / 0.3, 0, 1);
    }
    return {
      a: (c1.x - c0.x) / (2 * EYE * g),
      b: (c1.y - c0.y) / (2 * EYE * g),
      c: (c2.x - e) / (MOUTH * g),
      d: (c2.y - f) / (MOUTH * g),
      e,
      f,
      vis,
    };
  }

  private draw(avatar: Avatar, activity: Activity, t: number) {
    const renderer = this.renderer;
    const body = this.body;
    if (!renderer || !body) return;
    const glow = Math.min(1.1, avatar.glow + avatar.burst * 0.7);
    this.pose(avatar, activity, t, glow);

    // The gem's frame: FIT of the layout box and SINK lower, as the camera draws it.
    const g = avatar.gemPx * FIT;
    const px = avatar.px;
    const o = Math.floor((px - g) / 2);
    const oy = Math.floor((px - g) / 2 + SINK * avatar.gemPx);
    renderer.setViewport(0, 0, px, px);
    renderer.setScissor(0, 0, px, px);
    renderer.setScissorTest(true);
    renderer.clear();
    renderer.render(this.scene, this.camera);

    const ctx = avatar.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, px, px);
    const glowColor = activity === "error" ? ANGRY.clone() : avatar.base.clone().lerp(WHITE, 0.3);
    const lift = avatar.bob * g * WORLD_TO_FRAME;
    const slideX = avatar.slide * g * WORLD_TO_FRAME;
    const cx = o + g / 2;
    const cy = oy + g / 2;
    if (glow > 0.03) {
      const halo = ctx.createRadialGradient(cx + slideX, cy - lift, 0, cx + slideX, cy - lift, g * 0.58);
      halo.addColorStop(0, rgbString(glowColor, Math.min(0.32, 0.3 * glow)));
      halo.addColorStop(0.5, rgbString(glowColor, Math.min(0.14, 0.12 * glow)));
      halo.addColorStop(1, rgbString(glowColor, 0));
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, px, px);
    }

    const face = avatar.props.face ?? "pill";
    const season = avatar.props.season === "auto" ? seasonFor() : (avatar.props.season ?? "none");
    const hatProp = avatar.props.hat ?? "auto";
    const base = avatar.base;
    const frame: FaceFrame = {
      ctx,
      t,
      since: t - avatar.activitySince,
      prev: avatar.prev,
      box: { x: o, y: oy, s: g },
      anchor: ANCHOR,
      shift: { x: 0, y: 0 },
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
      eyeShape: avatar.eyes.map((spring) => spring.x),
      lid: avatar.lid,
      mouthShape: avatar.mouth.map((spring) => spring.x),
      mouthOpen: avatar.mouthOpen,
    };
    if (face !== "none") drawBackProps(frame);
    // WebGL's viewport origin is bottom-left; the canvas's render sits in the
    // buffer's bottom-left corner.
    ctx.drawImage(renderer.domElement, 0, this.bufferPx - px, px, px, 0, 0, px, px);
    this.drawGlints(avatar, o + slideX, oy - lift, g);
    if (face === "none") return;

    // The face, hat and held tools are seated on the surface, so they ride the
    // body: its turn toward the pointer, hop, shake, lean, squash and wobble.
    body.updateMatrixWorld(true);
    avatar.shape.forEach((spring, i) => (weights[i] = spring.x));
    const { lead } = avatar;
    const up = -lead.y * (lead.y < 0 ? 0.1 : 0.05);
    const local: FaceFrame = { ...frame, box: { x: -ANCHOR.x * g, y: -ANCHOR.y * g, s: g } };
    const seat = this.seat(avatar, lead.x * 0.08, faceHeight(weights) + 0.1 + up, eyeGap(local) / (2 * g), t, px, g);
    // A fast dart stretches the face along it.
    const vx = Math.abs(avatar.gaze[0].v);
    const vy = Math.abs(avatar.gaze[1].v);
    const sx = 1 + Math.min(vx * 0.04, 0.24) - Math.min(vy * 0.02, 0.12);
    const sy = 1 + Math.min(vy * 0.04, 0.24) - Math.min(vx * 0.02, 0.12);
    // The hat sits on the crown, toward the face while the face shows (it settles back to the
    // pole as the gem spins away), and tilts with the surface, but never turns flat. It keeps
    // the pole's size, so seating it forward does not swell it.
    // It goes on first, so a face turned toward a headphone cup passes in front.
    bodyPoint(weights, 0, 1, 0, avatar.wob, t, avatar.shear, P);
    const near = CAMERA_DISTANCE / (CAMERA_DISTANCE - P.applyMatrix4(body.matrixWorld).z);
    bodyPoint(weights, 0, 1, HAT_FWD * seat.vis, avatar.wob, t, avatar.shear, P);
    this.project(P, px, c0);
    this.project(U.copy(P).multiplyScalar(1.06), px, c1);
    const crown = Math.atan2(c1.x - c0.x, -(c1.y - c0.y));
    ctx.save();
    ctx.setTransform(Math.cos(crown) * near, Math.sin(crown) * near, -Math.sin(crown) * near, Math.cos(crown) * near, c0.x, c0.y);
    drawHat({ ...frame, box: { x: -0.5 * g, y: -ANCHOR.hatY * g, s: g } });
    ctx.restore();

    // The face and holiday lights are paint on the body: drawn apart, trimmed
    // to its silhouette (wide shades on a turned gem never stick out), then laid on.
    const paint = this.paintLayer(px);
    if (paint && seat.vis > 0.01) {
      paint.globalAlpha = seat.vis;
      paint.setTransform(seat.a, seat.b, seat.c, seat.d, seat.e, seat.f);
      paint.scale(sx, sy);
      drawFace({ ...local, ctx: paint });
      // Upright at the face, at the body's scale: the lights hang below the mouth.
      const tilt = Math.atan2(-seat.c, seat.d);
      const scale = Math.hypot(seat.c, seat.d) / FACE_SCALE;
      paint.setTransform(Math.cos(tilt) * scale, Math.sin(tilt) * scale, -Math.sin(tilt) * scale, Math.cos(tilt) * scale, seat.e, seat.f);
      drawSeasonBody({ ...frame, ctx: paint, box: { x: -ANCHOR.x * g, y: (0.09 - ANCHOR.y) * g, s: g } });
      paint.setTransform(1, 0, 0, 1, 0, 0);
      paint.globalAlpha = 1;
      paint.globalCompositeOperation = "destination-in";
      paint.drawImage(renderer.domElement, 0, this.bufferPx - px, px, px, 0, 0, px, px);
      paint.globalCompositeOperation = "source-over";
      ctx.drawImage(paint.canvas, 0, 0, px, px, 0, 0, px, px);
    }

    // Tools in hand ride the face.
    ctx.save();
    ctx.setTransform(seat.a, seat.b, seat.c, seat.d, seat.e, seat.f);
    drawHeld(local);
    ctx.restore();
    drawProps(frame);
    drawEffects(frame);
  }

  /** The body's pose, shape and colors for this frame. */
  private pose(avatar: Avatar, activity: Activity, t: number, glow: number) {
    const body = this.body;
    const material = this.material;
    if (!body || !material) return;
    const { mood, seed: s, look } = avatar;
    let yaw = 0;
    let pitch = 0;
    let bob = 0;
    let roll = 0;
    let slide = 0;
    let stretch = 0;
    let spin = 0;
    let breath = 0;
    if (!this.still && activity !== "offline") {
      const speed = BUSY.has(activity) ? 1.6 : activity === "dreaming" ? 0.45 : 1;
      yaw = avatar.turn[0].x;
      pitch = avatar.turn[1].x;
      bob = 0.045 * Math.sin(t * 1.15 * speed + s * 2.3);
      // Leaning into a fast turn.
      roll = clamp(-avatar.turn[0].v * 0.07, -0.18, 0.18);
      breath = Math.sin(t * (activity === "dreaming" ? 1.1 : 1.9) + s);
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
    // At the laptop the gem turns toward it and scoots left to make room; not
    // fully side-on, or the face (tongue out, furious) would wrap out of view.
    spin += avatar.desk * 0.75;
    slide -= avatar.desk * 0.26;
    yaw *= 1 - avatar.desk;
    const amp = activity === "dreaming" ? 0.05 : 0.03;
    const wide = (1 - amp * 0.5 * breath) * (1 - stretch * 0.04);
    body.rotation.set(0.12 + pitch, yaw + spin, roll);
    body.position.set(slide, bob, 0);
    body.scale.set(wide, (1 + amp * breath) * (1 + stretch * 0.06), wide);
    avatar.bob = bob;
    avatar.slide = slide;
    avatar.roll = roll;
    avatar.spin = spin;
    avatar.stretch = stretch;

    const moving = !this.still && activity !== "offline";
    const jelly = avatar.motion === "jelly" && moving;
    avatar.wob = !moving ? 0 : jelly ? 0.022 + avatar.energy * 0.05 : 0.008;
    avatar.shear = jelly ? clamp(-avatar.turn[0].v * 0.16, -0.3, 0.3) : 0;
    const u = material.uniforms;
    const w = avatar.shape;
    (u.uW0.value as Vector4).set(w[0].x, w[1].x, w[2].x, w[3].x);
    (u.uW1.value as Vector4).set(w[4].x, w[5].x, w[6].x, w[7].x);
    u.uWob.value = avatar.wob;
    u.uTime.value = t;
    u.uShear.value = avatar.shear;

    const offline = activity === "offline";
    tint.copy(avatar.base);
    if (offline) {
      const gray = tint.r * 0.2126 + tint.g * 0.7152 + tint.b * 0.0722;
      tint.setRGB(gray, gray, gray).multiplyScalar(0.55);
    } else if (activity === "error") {
      tint.lerp(ANGRY, 0.55);
    }
    paintBody(material, tint, offline);
    if (activity === "error") (u.uGlowColor.value as Color).copy(ANGRY);
    u.uGlow.value = offline ? 0 : Math.min(0.8, glow * 0.85);
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
