import { Color, Mesh, PerspectiveCamera, SRGBColorSpace, Scene, Vector3, WebGLRenderer, type ShaderMaterial } from "three";
import {
  SHAPES,
  bodyGeometry,
  bodyMaterial,
  bodyPoint,
  faceHeight,
  homeWeights,
  paintBody,
  setShapeWeights,
  type Shape,
} from "./gem-blob";
import { SPARKS_PER_SECOND, drawAuraBehind, drawAuraFront, drawSparks, type AuraPaint, type Spark } from "./gem-aura";
import { HAT_SIZES, drawActivityEffects, drawEffects, drawHat, drawSeasonBody, type HatFit } from "./gem-dress";
import {
  autoHat,
  drawFace,
  eyeGap,
  eyeShape,
  gazeFor,
  mix,
  mouthShape,
  seasonFor,
  workPhase,
  type Activity,
  type Aura,
  type CoatKnobs,
  type EyeStyle,
  type FaceAnchor,
  type FaceFrame,
  type FurStyle,
  type HairStyle,
  type HatStyle,
  type Rgb,
  type Season,
  type Sparkle,
} from "./gem-face";
import { FUR_COATS, HAIR_COATS, coatCrown, coatMesh, coatPress, dressCoat, type CoatStyle } from "./gem-fur";
import { drawn } from "./gem-look";
import { HOLDS, drawHeld, drawProps } from "./gem-props";
import { gemMood, gemSpec, type GemCut, type GemMood, type GemSpec } from "./gem-specs";
import {
  BLINK,
  DANCE,
  TOOL_HOLD,
  blinkOpen,
  breathCurve,
  dance,
  danceClock,
  fitPaintCanvas,
  frameInterval,
  frenzy,
  nextFrameAt,
  peer,
  ponder,
  Spring,
} from "./gem-animation";

/**
 * One WebGL context renders every gem avatar on the page.
 *
 * Browsers cap live WebGL contexts (Chrome at 16), so a canvas per sidebar row
 * would fall over with a busy hub. Instead a single offscreen renderer draws
 * each visible avatar in turn and blits the pixels into that avatar's own 2D
 * canvas, which then gets a 2D pass: halo, glints, and the personality layer
 * from gem-face.ts. The gem and its hat sit inside the layout box (FIT, SINK);
 * the canvas is GEM_PAD times the box so the laptop and thought bubble have
 * room around the stone.
 *
 * Pacing (gem-animation.ts frameInterval): a working or changing gem repaints
 * at up to 60fps, an idle one at 20 to 30 by its size, an offline one only
 * until its poof settles, and with reduced motion only when something
 * changes. Hidden gems do no work. Simulation advances only for frames drawn.
 *
 * At rest a gem breathes at its own pace, blinks on an uneven rhythm (now and
 * then twice), holds its gaze and glances elsewhere, and turns toward the
 * pointer a beat after its eyes do. Each activity has a posture, a face and a
 * prop that read at a glance; a change eases over with a blink, and a long
 * stretch of work that lands ends in a short dance. The face and hat are
 * seated on the body every frame, so they turn, squash and breathe with it.
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
 * How a gem's body moves on top of its activity's mood shape: "mood" leans
 * into it fully, "jelly" keeps its cut but goes blobby when the pointer flies,
 * "cycle" strolls into another shape now and then and comes home when the
 * pointer comes close. Each agent is born with one, drawn from its look key.
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
  /** The body shape the user picked; absent, the one its name draws. */
  cut?: GemCut | null;
  /** How the body moves; absent, the agent's born motion. */
  motion?: GemMotion | null;
  /** Hair on its head and fur all over (gem-fur.ts), under any hat; "none" or absent, bare. */
  hair?: HairStyle | "none";
  fur?: FurStyle | "none";
  /** Their colors: sRGB 0-255, or "gem" / absent for the body's own. */
  hairColor?: readonly number[] | "gem" | null;
  furColor?: readonly number[] | "gem" | null;
  /** The user's settings of their styles' knobs (gem-face.ts COAT_KNOBS), over each style's own. */
  hairKnobs?: CoatKnobs;
  furKnobs?: CoatKnobs;
  /** Auras around it (gem-aura.ts), how much it sparkles (absent: as it always has), and their color. */
  auras?: readonly Aura[];
  sparkles?: Sparkle;
  auraColor?: readonly number[] | "gem" | null;
};

type Glint = { x: number; y: number; age: number; ttl: number; size: number; hue: number | null };

type Avatar = {
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  props: GemProps;
  spec: GemSpec;
  base: Color;
  rgb: [number, number, number];
  /** Dark enough that its face is drawn in light ink. */
  dark: boolean;
  mood: GemMood;
  motion: GemMotion;
  /** The shape mix its cut rests in (gem-blob.ts homeWeights). */
  home: number[];
  activity: Activity;
  activitySince: number;
  prev: Activity;
  /** The activity waiting out a tool's hold, and when it takes over. */
  queued: Activity | null;
  queuedAt: number;
  /** The working moves (frenzy, peer, ponder) as last drawn, and as they were when the activity changed, to blend from. */
  moves: Moves;
  carry: Moves;
  /** When the current stretch of work began. */
  busySince: number;
  /** When the dance after a long stretch of work started. */
  danceAt: number;
  /** Thinking between two tool calls: it stays at the laptop. */
  atDesk: boolean;
  px: number;
  gemPx: number;
  seed: number;
  /** Radians its hat sits askew. */
  tilt: number;
  /** Radians the hat tips this frame while it dances. */
  jig: number;
  /** Radians of the booting whirl; once booted it winds on to face front. */
  whirl: number;
  visible: boolean;
  dirty: boolean;
  lastRender: number;
  nextRender: number;
  bounds: DOMRect | null;
  boundsAt: number;
  glow: number;
  burst: number;
  /** Where it is in a breath (0..1), the pace it eases toward, how deep it breathes, and how full it is now. */
  breath: number;
  tempo: number;
  depth: number;
  inhale: number;
  /** Pitch, roll, lift and squash, eased toward the activity's posture. */
  pose: Spring[];
  bob: number;
  slide: number;
  /** 0..1: turned side-on to the laptop. */
  desk: number;
  /** 0..1: how far the thought bubble is open. */
  bubble: number;
  /** When the next blink starts (or the current one started), when the last one did, and whether a second follows. */
  blinkAt: number;
  blinked: number;
  twice: boolean;
  blink: number;
  /** Where the eyes rest when nothing calls them, and when they next look elsewhere. */
  glance: { x: number; y: number };
  glanceAt: number;
  /** Where the pupils look, -1..1, on springs so a dart overshoots. */
  look: { x: number; y: number };
  /** Last measured pointer influence; distant gems keep their idle cadence. */
  attention: number;
  gaze: [Spring, Spring];
  /** Where the body is turning to: the eyes' target, a beat late. */
  lead: { x: number; y: number };
  turn: [Spring, Spring];
  /** A smile for the pointer arriving: until when, and when it may smile again. */
  greetUntil: number;
  greetAgain: number;
  /** The weight of each body shape (SHAPES). */
  shape: Spring[];
  /** Pill eye and shared mouth shape mixes (gem-face.ts eyeShape, mouthShape). */
  eyes: Spring[];
  lid: number;
  mouth: Spring[];
  mouthOpen: number;
  /** 0..1: how stirred up a jelly gem is. */
  stir: number;
  /** A cycling gem's next stroll into another shape, and that shape. */
  strollAt: number;
  stroll: number;
  /** 0..1, eased toward offline and toward an error, so neither switches. */
  rest: number;
  anger: number;
  wob: number;
  shear: number;
  talk: number;
  glints: Glint[];
  glintCarry: number;
  /** Sparkles around the gem (its aura), apart from the glints on it. */
  sparks: Spark[];
  sparkCarry: number;
};

/** An affine from face pixels to canvas pixels, and 0..1 for how squarely the patch faces the camera. */
type Seat = { a: number; b: number; c: number; d: number; e: number; f: number; vis: number };

/** What a gem's 2D pass needs from its 3D one (render3d), held until the atlas is read back. */
type Pending = {
  avatar: Avatar;
  frame: FaceFrame;
  /** Its slot in the atlas, canvas pixels from the top left. */
  sx: number;
  sy: number;
  glow: number;
  /** Null when it draws no face. */
  seat: Seat | null;
  hat: HatFit | null;
  faceScale: number;
  /** The face's stretch along a fast dart. */
  stretch: [number, number];
};

const FOV = 28;
const CAMERA_DISTANCE = 4.7;
// World units across the frame: a unit-radius gem fills ~85% of it.
const FRAME = 2 * Math.tan(((FOV / 2) * Math.PI) / 180) * CAMERA_DISTANCE;
// The body renders across the whole padded canvas, so a dance or the scoot to
// the laptop never clips it at the box's edge.
const CANVAS_FOV = (2 * Math.atan(Math.tan(((FOV / 2) * Math.PI) / 180) * GEM_PAD) * 180) / Math.PI;
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
/** Where the face and props sit on the soft gem, as fractions of its box. */
const ANCHOR: FaceAnchor = { x: 0.5, y: 0.5, gap: 1.15, view: VIEW };
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
/** Activities a tool call shows; each holds the stage for at least TOOL_HOLD. */
const TOOLS: ReadonlySet<Activity> = new Set(["typing", "reading", "searching", "messaging", "tasking"]);
/** Seconds a thinking blip between two tool calls waits before it shows. */
const TOOL_GAP = 0.35;
/** Seconds the working moves take to blend from one activity's into the next. */
const MOVE_BLEND = 0.3;
type Moves = { slide: number; roll: number; pitch: number; lift: number; squash: number; spin: number };
const NO_MOVES: Moves = { slide: 0, roll: 0, pitch: 0, lift: 0, squash: 0, spin: 0 };
/**
 * The tilt that goes with leaning toward the laptop (lean > 0) or jerking back
 * from it. Mostly a slide in: tipping far toward the screen turns the face
 * out of view, so only the jerk back tips the head right over.
 */
const leanRoll = (lean: number) => (lean > 0 ? 0.04 : 0.18) * lean;
/** Sliding in toward the screen turns the face away from the viewer; turn back that much so it stays readable. */
const leanTurn = (lean: number) => -0.35 * Math.max(0, lean);
/** What cuts a tool's hold short. */
const URGENT: ReadonlySet<Activity> = new Set(["error", "offline"]);
/** Seconds of work before landing it earns the dance; a quick reply does not. */
const CELEBRATE_AFTER = 2.5;
const TAU = Math.PI * 2;
const shapeOf = (shape: Shape) => SHAPES.indexOf(shape);
/** How far an activity pulls the body toward a mood shape: a thought cloud, round while talking, sharp on an error. */
const MOOD_MORPH: Partial<Record<Activity, readonly [Shape, number]>> = {
  thinking: ["cloud", 0.4],
  booting: ["cloud", 0.5],
  speaking: ["dome", 0.3],
  messaging: ["dome", 0.3],
  dance: ["dome", 0.35],
  waiting: ["dome", 0.25],
  error: ["octa", 0.5],
};
/** Breathing pace against rest: quicker at work, slower asleep. */
const TEMPO: Partial<Record<Activity, number>> = {
  thinking: 1.35,
  reading: 1.2,
  typing: 1.35,
  speaking: 1.25,
  searching: 1.3,
  messaging: 1.25,
  tasking: 1.3,
  error: 1.6,
  waiting: 1.1,
  dreaming: 0.7,
  dance: 1.4,
};
type Posture = readonly [pitch: number, roll: number, lift: number, squash: number];
/**
 * Each activity's posture: pitch (nodding down), roll (a head tilt), lift and
 * squash (wider and shorter). Small on purpose: the face and prop say what
 * the gem is doing, the posture only leans into it.
 */
const POSE: Partial<Record<Activity, Posture>> = {
  thinking: [-0.06, 0.06, 0.015, 0],
  reading: [0.06, 0, 0, 0],
  typing: [0.1, 0, 0, 0.01],
  speaking: [-0.03, 0, 0.01, 0],
  searching: [0.02, -0.04, 0, 0],
  messaging: [-0.02, 0.03, 0, 0],
  tasking: [0.06, 0, 0, 0],
  waiting: [-0.05, 0.05, 0.015, 0],
  dreaming: [0.12, 0.08, -0.03, 0.05],
  error: [0.04, 0, 0, -0.03],
  booting: [0, 0, 0.03, -0.02],
  offline: [0.05, 0, -0.02, 0.04],
};
const REST_POSE: Posture = [0, 0, 0, 0];
/** Standing to attention for the salute that opens a task. */
const SALUTE_POSE: Posture = [-0.06, 0, 0, -0.01];
/** Hats that close over the crown, so a head rising inside one is hidden by it. Crowns, horns, antlers and headphones leave the top bare. */
const COVERS: ReadonlySet<HatStyle> = new Set(["party", "top", "beanie", "wizard", "cap", "hardhat", "beret", "witch", "pumpkin", "santa", "elf"]);
/** Sparkles a second; working gems use 0.5. */
const GLINTS: Partial<Record<Activity, number>> = { dance: 3, waiting: 0.25, dreaming: 0.1, idle: 0.12, booting: 1.5 };

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const smoothstep = (x: number) => x * x * (3 - 2 * x);
const luminance = (c: Color) => c.r * 0.2126 + c.g * 0.7152 + c.b * 0.0722;

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

function rgbString([r, g, b]: readonly number[], alpha: number): string {
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

// Scratch space for seating the face and hat.
const P = new Vector3();
const U = new Vector3();
const V = new Vector3();
const Q = new Vector3();
const N = new Vector3();
const H = new Vector3();
const c0 = { x: 0, y: 0 };
const c1 = { x: 0, y: 0 };
const c2 = { x: 0, y: 0 };
const weights = new Float32Array(SHAPES.length);
const tint = new Color();
const haloTint = new Color();

/**
 * Where a hat goes on a head (body-local, before the build is applied): its
 * band's half-width and height, the sides at eye height (for headphones) and
 * the top of the crown.
 */
type Head = { bx: number; by: number; ex: number; ey: number; top: number };
const heads = new Map<string, Head>();

/**
 * Reads a head off its front silhouette, from the crown down the side. The
 * band opens to `fit` of the head's widest half-width, but never down a wall
 * (a hopper's step) or over the brows, so a hat rests where it would on that
 * head. Cached by the shape mix in steps of 0.05, so a morph passes through
 * a few dozen heads, each read once.
 */
function headFor(w: ArrayLike<number>, build: readonly [number, number], fit: number): Head {
  let key = `${fit}|${build[0]}|${build[1]}`;
  for (let i = 0; i < w.length; i += 1) key += `|${Math.round(w[i] * 20)}`;
  const known = heads.get(key);
  if (known) return known;
  const [bw, bh] = build;
  const xs: number[] = [];
  const ys: number[] = [];
  for (let i = 0; i <= 32; i += 1) {
    const a = (i / 32) * Math.PI * 0.75;
    bodyPoint(w, Math.sin(a), Math.cos(a), 0, 0, 0, 0, H);
    xs.push(H.x * bw);
    ys.push(H.y * bh);
  }
  const widest = Math.max(...xs);
  const top = Math.max(...ys);
  bodyPoint(w, 0, faceHeight(w) + 0.1, 1, 0, 0, 0, H);
  const eye = H.y * bh;
  const brow = eye + 0.3 * bh;
  const target = fit * widest;
  let bx = xs[0];
  let by = ys[0];
  for (let i = 1; i < xs.length; i += 1) {
    const [x0, y0, x, y] = [xs[i - 1], ys[i - 1], xs[i], ys[i]];
    if (y < brow) break;
    if (x0 > 0.15 * widest && x > x0 && (y0 - y) / (x - x0) > 2.2) break;
    if (x >= target) {
      const u = x > x0 ? (target - x0) / (x - x0) : 1;
      bx = target;
      by = y0 + (y - y0) * u;
      break;
    }
    bx = x;
    by = y;
  }
  bx = Math.max(bx, 0.12 * widest);
  let ex = widest;
  for (let i = 1; i < xs.length; i += 1) {
    if (ys[i] <= eye) {
      const u = (ys[i - 1] - eye) / (ys[i - 1] - ys[i] || 1);
      ex = xs[i - 1] + (xs[i] - xs[i - 1]) * u;
      break;
    }
  }
  const head = { bx: bx / bw, by: by / bh, ex: ex / bw, ey: eye / bh, top: top / bh };
  if (heads.size > 512) heads.clear();
  heads.set(key, head);
  return head;
}

/** The hair and fur a gem wears: each style's look with the user's knob settings over it. */
function coats(props: GemProps): { hair?: CoatStyle; fur?: CoatStyle } {
  const hair = props.hair && props.hair !== "none" ? HAIR_COATS[props.hair] : undefined;
  const fur = props.fur && props.fur !== "none" ? FUR_COATS[props.fur] : undefined;
  return {
    hair: hair && props.hairKnobs ? { ...hair, ...props.hairKnobs } : hair,
    fur: fur && props.furKnobs ? { ...fur, ...props.furKnobs } : fur,
  };
}

/**
 * Where a hat's band sits over a gem's coat (body-local). A hat sits on the
 * head over a thin layer of the coat: one that covers the crown presses the
 * coat under it flat (`pressed`), one open on top (a crown, horns) lets it
 * stand around it. Headphones arc over the coat.
 */
function hatBand(props: GemProps, hat: Exclude<HatStyle, "none">, head: Head) {
  const { hair, fur } = coats(props);
  const over = hat === "headphones";
  const lift = over ? Math.max(coatCrown(hair), coatCrown(fur)) : Math.max(coatPress(hair), coatPress(fur));
  return { by: head.by + lift, bx: head.bx + (over ? 0.6 : 1) * lift, lift, pressed: COVERS.has(hat) && !!(hair || fur) };
}

class GemEngine {
  private renderer: WebGLRenderer | null = null;
  private scene = new Scene();
  private camera = new PerspectiveCamera(CANVAS_FOV, 1, 0.1, 20);
  private body: Mesh | null = null;
  /** Hair or fur grown from the body (gem-fur.ts), a child of it. */
  private hairCoat: Mesh | null = null;
  private furCoat: Mesh | null = null;
  private material: ShaderMaterial | null = null;
  /** Where the face is painted before it is trimmed to the body's silhouette. */
  private paint: CanvasRenderingContext2D | null = null;
  private avatars = new Set<Avatar>();
  private byCanvas = new WeakMap<HTMLCanvasElement, Avatar>();
  private observer: IntersectionObserver | null = null;
  private pointer = { x: 0, y: 0, at: -Infinity };
  /** Pointer speed in px/s, smoothed; it stirs jelly gems. */
  private speed = 0;
  /** Side of the square WebGL buffer each frame's gems are rendered into, side by side. */
  private atlas = 1024;
  /** The atlas read back once a frame; each gem's 2D pass copies its body from here. */
  private atlasCopy: CanvasRenderingContext2D | null = null;
  /** Smoothed milliseconds of one gem's 2D pass, counted against the frame budget. */
  private paintCost = 0.2;
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
      renderer.setSize(this.atlas, this.atlas, false);
      renderer.setScissorTest(true);
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
      this.hairCoat = coatMesh("hair");
      this.furCoat = coatMesh("fur");
      this.body.add(this.hairCoat, this.furCoat);
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
        this.avatars.forEach((avatar) => {
          avatar.dirty = true;
          avatar.danceAt = -Infinity;
          avatar.jig = 0;
          avatar.whirl = 0;
        });
        this.kick();
      });
    }
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        cancelAnimationFrame(this.raf);
        this.raf = 0;
        this.lastTick = 0;
      } else {
        this.avatars.forEach((avatar) => {
          avatar.dirty = true;
          avatar.lastRender = 0;
          avatar.bounds = null;
        });
        this.kick();
      }
    });
    window.addEventListener("resize", () => {
      this.avatars.forEach((avatar) => {
        this.resize(avatar);
        avatar.bounds = null;
        avatar.dirty = true;
      });
      this.kick();
    }, { passive: true });
    window.addEventListener("scroll", () => {
      this.avatars.forEach((avatar) => (avatar.bounds = null));
    }, { passive: true, capture: true });
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
        if (avatar.visible) {
          avatar.dirty = true;
          avatar.lastRender = 0;
          avatar.bounds = null;
        }
      }
      this.kick();
    });
    return true;
  }

  mount(canvas: HTMLCanvasElement, props: GemProps): boolean {
    if (!this.ensureRenderer()) return false;
    const ctx = canvas.getContext("2d");
    if (!ctx) return false;
    const now = performance.now() / 1000;
    const spec = gemSpec(props.gem, props.caste, props.cut);
    const mood = gemMood(props.state, props.live !== false);
    const activity = activityFor(props, mood);
    const home = homeWeights(spec.cut);
    const seed = hashSeed(whose(props));
    const work = workPhase(activity);
    const offline = activity === "offline";
    const avatar: Avatar = {
      canvas,
      ctx,
      props,
      spec,
      base: new Color(),
      rgb: [0, 0, 0],
      dark: false,
      mood,
      motion: "mood",
      home,
      activity,
      // Already in its state when the page opens, so no entrance replays.
      activitySince: now - 10,
      prev: activity,
      queued: null,
      queuedAt: 0,
      moves: { ...NO_MOVES },
      carry: { ...NO_MOVES },
      busySince: now,
      danceAt: -Infinity,
      atDesk: false,
      px: 0,
      gemPx: 0,
      seed,
      tilt: 0,
      jig: 0,
      whirl: 0,
      visible: false,
      dirty: true,
      lastRender: 0,
      nextRender: 0,
      bounds: null,
      boundsAt: 0,
      glow: 0,
      burst: 0,
      // Out of step with its neighbours from the first frame.
      breath: seed / (Math.PI * 2),
      tempo: TEMPO[activity] ?? 1,
      depth: offline ? 0 : 1,
      inhale: 0,
      pose: (POSE[activity] ?? REST_POSE).map((v) => new Spring(v)),
      bob: 0,
      slide: 0,
      desk: work.laptop ? 1 : 0,
      bubble: work.bubble ? 1 : 0,
      blinkAt: now + 1 + Math.random() * 3,
      blinked: now,
      twice: false,
      blink: 1,
      glance: { x: 0, y: 0 },
      glanceAt: now + 1 + Math.random() * 3,
      look: { x: 0, y: 0 },
      attention: 0,
      gaze: [new Spring(0), new Spring(0)],
      lead: { x: 0, y: 0 },
      turn: [new Spring(0), new Spring(0)],
      greetUntil: 0,
      greetAgain: 0,
      shape: home.map((w) => new Spring(w)),
      eyes: eyeShape(activity).map((w) => new Spring(w)),
      lid: activity === "error" ? 0.5 : 0,
      mouth: mouthShape(activity, now, 0).mix.map((w) => new Spring(w)),
      mouthOpen: 0,
      stir: 0,
      strollAt: now + 8 + Math.random() * 10,
      stroll: shapeOf("dome"),
      rest: offline ? 1 : 0,
      anger: activity === "error" ? 1 : 0,
      wob: 0,
      shear: 0,
      talk: 0,
      glints: [],
      glintCarry: 0,
      sparks: [],
      sparkCarry: 0,
    };
    this.identify(avatar, props);
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
    if (activity === avatar.activity) {
      // Back to the tool still on show: the blip in between never happened.
      avatar.queued = null;
    } else if (activity !== avatar.queued) {
      // A tool's look holds the stage a moment, and a breath of thinking
      // between two calls waits to see whether the next call follows.
      const until = TOOLS.has(avatar.activity) && !URGENT.has(activity)
        ? Math.max(avatar.activitySince + TOOL_HOLD, activity === "thinking" ? now + TOOL_GAP : 0)
        : 0;
      avatar.queued = now < until ? activity : null;
      avatar.queuedAt = until;
      if (!avatar.queued) this.changeActivity(avatar, activity, now);
    }
    avatar.mood = mood;
    this.identify(avatar, props);
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
    if (!this.avatars.size) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
      this.lastTick = 0;
    }
  }

  /** Who the gem is: its cut, color, motion and the quirks drawn from its name. */
  private identify(avatar: Avatar, props: GemProps) {
    avatar.props = props;
    avatar.spec = gemSpec(props.gem, props.caste, props.cut);
    avatar.home = homeWeights(avatar.spec.cut);
    avatar.motion = motionFor(props);
    avatar.seed = hashSeed(whose(props));
    // Each gem wears its hat a little askew its own way.
    avatar.tilt = 0.08 * Math.sin(avatar.seed * 5.3);
    avatar.base = colorFrom(props, avatar.spec);
    avatar.rgb = srgb(avatar.base);
    avatar.dark = luminance(avatar.base) < 0.12;
  }

  private changeActivity(avatar: Avatar, activity: Activity, now: number) {
    const was = avatar.activity;
    if (BUSY.has(activity) && !BUSY.has(was)) avatar.busySince = now;
    // A real stretch of work that lands earns a short dance; a quick reply does not.
    if (!this.still && BUSY.has(was) && activity === "idle" && now - avatar.busySince >= CELEBRATE_AFTER) {
      avatar.danceAt = now;
      avatar.burst = 0.6;
      avatar.glintCarry += 3;
    }
    // Thinking between two tool calls stays at the laptop instead of turning away and back.
    avatar.atDesk = activity === "thinking" && (avatar.desk > 0.5 || workPhase(was).laptop);
    avatar.prev = was;
    avatar.carry = { ...avatar.moves };
    avatar.activity = activity;
    avatar.activitySince = now;
    // A blink marks the change, unless one just happened.
    const blinking = now >= avatar.blinkAt && now < avatar.blinkAt + BLINK;
    if (!blinking && now - avatar.blinked > 1.2) avatar.blinkAt = now;
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
    if (px > this.atlas && this.renderer) {
      this.atlas = px;
      this.renderer.setSize(px, px, false);
    }
  }

  private kick() {
    if (!this.raf && this.avatars.size && !document.hidden) this.raf = requestAnimationFrame(this.tick);
  }

  /** The activity being shown: the short dance takes over idle after a long stretch of work. */
  private shownActivity(avatar: Avatar, t: number): Activity {
    return avatar.activity === "idle" && t - avatar.danceAt < DANCE ? "dance" : avatar.activity;
  }

  /** Seconds into the dance on show: the one after a long turn, or the Dance state's loop. */
  private danceSince(avatar: Avatar, t: number): number {
    return danceClock(avatar.activity === "dance", t - avatar.activitySince, t - avatar.danceAt);
  }

  /** An offline gem that has finished going to rest: nothing about it moves. */
  private settled(avatar: Avatar, activity: Activity, t: number): boolean {
    return activity === "offline" && avatar.rest > 0.995 && t - avatar.activitySince > 1.2;
  }

  /** Working, changing, celebrating or under the pointer: worth full frames. */
  private lively(avatar: Avatar, activity: Activity, t: number): boolean {
    return (
      BUSY.has(activity) ||
      activity === "dance" ||
      activity === "booting" ||
      avatar.whirl > 0 ||
      activity === "offline" ||
      t - avatar.activitySince < 1.4 ||
      (avatar.desk > 0 && avatar.desk < 1) ||
      (avatar.bubble > 0 && avatar.bubble < 1) ||
      avatar.burst > 0.01 ||
      t < avatar.greetUntil ||
      (t - this.pointer.at < 0.25 && avatar.attention > 0.1 && avatar.props.follow !== false)
    );
  }

  private tick = (now: number) => {
    this.raf = 0;
    if (this.lost || !this.renderer || document.hidden) return;
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
    // Due gems are rendered into the atlas in shelves, then painted together.
    const batch: Pending[] = [];
    let x = 0;
    let y = 0;
    let shelf = 0;
    for (let i = 0; i < list.length; i += 1) {
      const index = (offset + i) % list.length;
      const avatar = list[index];
      if (!avatar.visible) continue;
      if (avatar.queued && t >= avatar.queuedAt) {
        this.changeActivity(avatar, avatar.queued, t);
        avatar.queued = null;
        avatar.dirty = true;
      }
      const activity = this.shownActivity(avatar, t);
      // Reduced motion and a settled offline gem repaint only when something changes.
      const interval =
        this.still || this.settled(avatar, activity, t)
          ? Infinity
          : frameInterval(avatar.props.size, this.lively(avatar, activity, t), strained);
      if (interval !== Infinity) animating = true;
      if (!avatar.dirty && (interval === Infinity || t + 0.001 < avatar.nextRender)) continue;
      if (painted > 0 && performance.now() - started + batch.length * this.paintCost > FRAME_BUDGET_MS) {
        if (resume < 0) resume = index;
        animating = true;
        continue;
      }
      // A skipped frame does no physics or layout work. Each avatar integrates
      // its own elapsed time when its turn comes, bounded after long absences.
      this.advance(avatar, activity, t, avatar.lastRender ? Math.min(0.1, t - avatar.lastRender) : 1 / 60);
      const px = avatar.px;
      if (x + px > this.atlas) {
        x = 0;
        y += shelf;
        shelf = 0;
      }
      if (y + px > this.atlas) {
        this.flush(batch);
        x = 0;
        y = 0;
        shelf = 0;
      }
      const pending = this.render3d(avatar, activity, t, x, y);
      if (pending) batch.push(pending);
      x += px;
      shelf = Math.max(shelf, px);
      avatar.nextRender = nextFrameAt(avatar.dirty ? 0 : avatar.nextRender, t, interval);
      avatar.lastRender = t;
      avatar.dirty = false;
      painted += 1;
    }
    this.flush(batch);
    this.cursor = resume >= 0 ? resume : offset;
    this.cost += (performance.now() - started - this.cost) * 0.1;
    if (animating) this.raf = requestAnimationFrame(this.tick);
  };

  /** Per-frame state: breath, glow, blinks, glances, gaze, posture, the body's springs, glints. */
  private advance(avatar: Avatar, activity: Activity, t: number, dt: number) {
    const s = avatar.seed;
    const still = this.still;
    const { energy, pace } = avatar.spec;
    const offline = activity === "offline";
    const since = t - avatar.activitySince;
    const ease = (from: number, to: number, rate: number) => {
      const v = still ? to : from + (to - from) * (1 - Math.exp(-dt * rate));
      return Math.abs(v - to) < 0.002 ? to : v;
    };

    // Going offline and an error both ease in rather than switching.
    avatar.rest = ease(avatar.rest, offline ? 1 : 0, 5);
    avatar.anger = ease(avatar.anger, activity === "error" ? 1 : 0, 6);

    // Breathing. The phase integrates its pace, so quickening for work or
    // slowing into a dream never jumps; an offline gem stops breathing.
    avatar.tempo = ease(avatar.tempo, TEMPO[activity] ?? 1, 1.5);
    avatar.depth = ease(avatar.depth, still || offline ? 0 : activity === "dreaming" ? 1.5 : 1, 2);
    avatar.breath = (avatar.breath + (dt * avatar.tempo) / pace) % 1;
    const full = still ? 0.5 : breathCurve(avatar.breath);
    avatar.inhale = full * avatar.depth;

    // The glow inside: calm, and breathing with the gem rather than flickering.
    const target = BUSY.has(activity)
      ? 0.42 + 0.08 * full
      : activity === "error"
        ? 0.5 + 0.08 * full
        : activity === "booting"
          ? 0.55
          : activity === "dance"
            ? 0.4 + 0.12 * Math.abs(Math.sin((4 * Math.PI * this.danceSince(avatar, t)) / DANCE))
            : activity === "waiting"
              ? 0.24 + 0.06 * full
              : activity === "dreaming"
                ? 0.12 + 0.05 * full
                : activity === "idle"
                  ? 0.06
                  : 0;
    avatar.glow = ease(avatar.glow, target, 4);
    avatar.burst = still ? 0 : avatar.burst * Math.exp(-dt * 4);
    if (avatar.burst < 0.01) avatar.burst = 0;

    // Booting: a slow whirl. Once booted it winds on to the next full turn,
    // slowing as it comes round, and lands facing front instead of snapping back.
    if (activity === "booting" && !still) avatar.whirl = (avatar.whirl + dt * 2.4) % TAU;
    else if (avatar.whirl > 0) {
      const left = TAU - avatar.whirl;
      avatar.whirl = still || left < 0.01 ? 0 : Math.min(TAU, avatar.whirl + dt * Math.min(2.4, 0.4 + 3 * left));
    }

    // Turning side-on to the laptop and back, and the thought bubble, ease.
    const work = workPhase(activity, avatar.atDesk);
    avatar.desk = ease(avatar.desk, work.laptop ? 1 : 0, 6);
    avatar.bubble = ease(avatar.bubble, work.bubble ? 1 : 0, 6);

    // Blinks on an uneven rhythm, now and then twice, fewer while reading.
    if (t >= avatar.blinkAt + BLINK) {
      avatar.blinked = avatar.blinkAt;
      avatar.twice = !avatar.twice && Math.random() < 0.12;
      const focus = activity === "reading" || activity === "typing" ? 1.3 : 1;
      avatar.blinkAt = t + (avatar.twice ? 0.12 : (5.2 - 2.6 * energy) * (0.75 + 0.5 * Math.random()) * focus);
    }
    avatar.blink = still || offline ? 1 : blinkOpen(t - avatar.blinkAt);

    // At rest the eyes hold a spot, then glance elsewhere, often back to the
    // middle; a restless gem glances more often.
    if (t >= avatar.glanceAt) {
      const center = Math.random() < 0.45;
      avatar.glance.x = center ? 0 : (Math.random() * 2 - 1) * 0.5;
      avatar.glance.y = center ? 0 : (Math.random() * 2 - 1) * 0.3;
      avatar.glanceAt = t + (2.5 + (1 - energy) * 5) * (0.6 + 0.8 * Math.random());
    }
    let tx = still ? 0 : avatar.glance.x;
    let ty = still ? 0 : avatar.glance.y;
    let near = false;
    let attention = 0;
    if (!still && !offline && avatar.props.follow !== false && t - this.pointer.at < 4) {
      // Refresh occasionally for layout shifts, immediately after a scroll or resize.
      if (!avatar.bounds || t - avatar.boundsAt > 0.15) {
        avatar.bounds = avatar.canvas.getBoundingClientRect();
        avatar.boundsAt = t;
      }
      const rect = avatar.bounds;
      const dx = this.pointer.x - (rect.left + rect.width / 2);
      const dy = this.pointer.y - (rect.top + rect.height / 2);
      const dist = Math.hypot(dx, dy) || 1;
      const reach = Math.min(1, dist / 160);
      attention = (1 - clamp((dist - 80) / Math.max(220, rect.width * 3), 0, 1)) *
        (1 - clamp((t - this.pointer.at - 2.5) / 1.5, 0, 1));
      tx += ((dx / dist) * reach - tx) * attention;
      ty += ((dy / dist) * reach - ty) * attention;
      near = dist < Math.max(90, rect.width);
    }
    // A smile when the pointer arrives, not every time it wanders by.
    if (activity === "idle" && attention > 0.65 && avatar.attention <= 0.65 && t > avatar.greetAgain) {
      avatar.greetUntil = t + 1.4;
      avatar.greetAgain = t + 14;
    }
    avatar.attention = attention;
    const cheer = activity === "idle" && t < avatar.greetUntil ? 1 : 0;

    // The eyes dart on a stiff spring; the body follows a beat later.
    if (still) avatar.gaze.forEach((spring) => spring.set(0));
    avatar.look.x = avatar.gaze[0].to(tx, 300, 26, dt);
    avatar.look.y = avatar.gaze[1].to(ty, 300, 26, dt);
    const aim = gazeFor({ activity, look: { x: tx, y: ty }, t, desk: avatar.desk });
    const lag = still ? 1 : 1 - Math.exp(-dt / 0.12);
    avatar.lead.x += (aim.x - avatar.lead.x) * lag;
    avatar.lead.y += (aim.y - avatar.lead.y) * lag;
    if (still) {
      avatar.turn.forEach((spring) => spring.set(0));
    } else {
      // Close to critically damped: a composed gem turns slowly and settles,
      // a restless one turns quicker. Looking up turns further than looking
      // down, which would hide the face.
      const k = 40 + 30 * energy;
      const c = 1.8 * Math.sqrt(k);
      avatar.turn[0].to(avatar.lead.x * 0.42, k, c, dt);
      avatar.turn[1].to(avatar.lead.y * (avatar.lead.y > 0 ? 0.16 : 0.32), k, c, dt);
    }

    // Syllable-ish mouth movement while streaming text.
    const talking = (activity === "speaking" || activity === "messaging") && !still;
    const talkTarget = talking ? Math.abs(Math.sin(t * 11.5)) * (0.55 + 0.45 * Math.sin(t * 3.3 + s)) : 0;
    avatar.talk = still ? 0 : avatar.talk + (Math.max(0, talkTarget) - avatar.talk) * (1 - Math.exp(-dt * 25));

    // The body's shape, the pill eyes, the mouth and the posture morph on springs.
    avatar.stir += (clamp(this.speed / 1600, 0, 1) * attention + (BUSY.has(activity) ? 0.2 : 0) - avatar.stir) *
      (1 - Math.exp(-dt * 4));
    const goal = this.shapeGoal(avatar, activity, t, near);
    const [k, c] = avatar.motion === "jelly" ? [45, 10] : [60, 14];
    avatar.shape.forEach((spring, i) => (still ? spring.set(goal[i]) : spring.to(goal[i], k, c, dt)));
    const eyes = eyeShape(activity, cheer);
    avatar.eyes.forEach((spring, i) => (still ? spring.set(eyes[i]) : spring.to(eyes[i], 170, 19, dt)));
    avatar.lid = ease(avatar.lid, activity === "error" ? 0.5 : 0, 12);
    const mouth = mouthShape(activity, t, avatar.talk, cheer);
    avatar.mouth.forEach((spring, i) => (still ? spring.set(mouth.mix[i]) : spring.to(mouth.mix[i], 150, 17, dt)));
    avatar.mouthOpen = mouth.open;
    const posture = activity === "tasking" && since < 1.3 ? SALUTE_POSE : (POSE[activity] ?? REST_POSE);
    avatar.pose.forEach((spring, i) => (still ? spring.set(posture[i]) : spring.to(posture[i], 30, 11, dt)));

    if (still) {
      avatar.glints.length = 0;
      avatar.glintCarry = 0;
      avatar.sparks.length = 0;
      avatar.sparkCarry = 0;
    }
    const sparkles = avatar.props.sparkles;
    const rate =
      still || offline || activity === "error" || sparkles === "none" ? 0 : BUSY.has(activity) ? 0.5 : (GLINTS[activity] ?? 0.12);
    avatar.glintCarry += rate * dt;
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
    // A picked amount of sparkle also twinkles around the gem, more while it works.
    const around = sparkles === "few" || sparkles === "lots" ? SPARKS_PER_SECOND[sparkles] : null;
    avatar.sparkCarry += around && !still && !offline ? around[BUSY.has(activity) || activity === "dance" ? 1 : 0] * dt : 0;
    while (avatar.sparkCarry >= 1) {
      avatar.sparkCarry -= 1;
      const angle = Math.random() * Math.PI * 2;
      const reach = 0.36 + Math.random() * 0.26;
      avatar.sparks.push({
        x: Math.cos(angle) * reach,
        y: Math.sin(angle) * reach * 0.95,
        age: 0,
        ttl: 0.6 + Math.random() * 0.6,
        size: 0.04 + Math.random() * 0.045,
      });
    }
    for (const spark of avatar.sparks) spark.age += dt;
    avatar.sparks = avatar.sparks.filter((spark) => spark.age < spark.ttl);
  }

  /** The shape mix the body is heading for: its cut, leaning into its activity's mood shape and its motion. */
  private shapeGoal(avatar: Avatar, activity: Activity, t: number, near: boolean): number[] {
    const goal = avatar.home.slice();
    const pull = (shape: number, amount: number) => {
      for (let i = 0; i < goal.length; i += 1) goal[i] *= 1 - amount;
      goal[shape] += amount;
    };
    if (activity === "offline") return goal;
    const morph = MOOD_MORPH[activity];
    if (morph) {
      let shape = shapeOf(morph[0]);
      // An octahedron's error goes brilliant: it has to change to read.
      if (activity === "error" && avatar.home[shape] > 0.5) shape = shapeOf("brilliant");
      pull(shape, avatar.motion === "mood" ? morph[1] : morph[1] * 0.6);
    }
    // Reduced motion keeps only the mood shapes, which say what the gem is doing.
    if (this.still) return goal;
    if (avatar.motion === "jelly") {
      const e = Math.min(1, avatar.stir);
      pull(shapeOf("dome"), e * 0.3);
      pull(shapeOf("cloud"), e * 0.2);
    } else if (avatar.motion === "cycle" && activity === "idle") {
      // Now and then it strolls into another shape for a few seconds, and
      // comes home early when the pointer arrives.
      if (t > avatar.strollAt && (near || t > avatar.strollAt + 3)) {
        avatar.strollAt = t + 14 + 10 * Math.random();
        const crystal = avatar.home[shapeOf("crystal")] > 0.5;
        const options = [shapeOf("dome"), shapeOf("cloud"), shapeOf(crystal ? "brilliant" : "crystal")];
        avatar.stroll = options[Math.floor(Math.random() * options.length)];
      }
      if (t >= avatar.strollAt) pull(avatar.stroll, 0.7);
    }
    return goal;
  }

  /** A cleared scratch canvas at least px across, shared by every gem. */
  private paintLayer(px: number): CanvasRenderingContext2D | null {
    if (!this.paint) this.paint = document.createElement("canvas").getContext("2d");
    const paint = this.paint;
    if (!paint) return null;
    fitPaintCanvas(paint.canvas, px);
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
  private seat(avatar: Avatar, fx: number, fy: number, eye: number, t: number, px: number, g: number, scale: number): Seat {
    const { wob, shear } = avatar;
    const body = this.body;
    // Face pixels to surface directions: the eyes sit EYE (of the box) either
    // side of the center, the mouth MOUTH below it. The spread is angular, so a
    // narrow crystal wears a smaller face than a round dome.
    const EYE = eye;
    const MOUTH = 0.13;
    const k = (scale * FRAME) / 0.95;
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

  /**
   * Where the hat sits this frame: its band on the head's silhouette (headFor),
   * a little toward the face while it shows so a turn carries it, tilted with
   * the head, and pressed down or shrunk to stay inside the layout box.
   */
  private hatFit(avatar: Avatar, hat: Exclude<HatStyle, "none">, vis: number, px: number, g: number): HatFit {
    const body = this.body as Mesh;
    const size = HAT_SIZES[hat];
    const head = headFor(weights, avatar.spec.build, size.fit);
    // Over hair or fur the hat rides up and opens out by the coat's depth under it.
    const { by, bx, lift: coat } = hatBand(avatar.props, hat, head);
    P.set(0, by, 0.5 * bx * vis);
    this.project(P, px, c0);
    U.copy(P).applyMatrix4(body.matrixWorld);
    const near = CAMERA_DISTANCE / (CAMERA_DISTANCE - U.z);
    P.y += 0.25;
    this.project(P, px, c1);
    const angle = Math.atan2(c1.x - c0.x, -(c1.y - c0.y)) + (hat === "headphones" ? 0 : avatar.tilt + avatar.jig);
    const unit = g / FRAME;
    let k = (bx * body.scale.x * unit * near) / size.band;
    // The room above the band at rest (without a bounce) up to the box's top.
    const room = c0.y + avatar.bob * unit - ((px - avatar.gemPx) / 2 - 0.04 * avatar.gemPx);
    let squash = 1;
    if (size.rise * k > room) {
      squash = Math.max(0.72, room / (size.rise * k));
      k = Math.min(k, Math.max(0, room) / (size.rise * squash));
    }
    const ears = { lx: 0, ly: 0, rx: 0, ry: 0, tx: 0, ty: 0 };
    if (hat === "headphones") {
      P.set(-head.ex - 0.5 * coat, head.ey, 0);
      this.project(P, px, c2);
      [ears.lx, ears.ly] = [c2.x, c2.y];
      P.set(head.ex + 0.5 * coat, head.ey, 0);
      this.project(P, px, c2);
      [ears.rx, ears.ry] = [c2.x, c2.y];
      P.set(0, head.top + coat, 0);
      this.project(P, px, c2);
      [ears.tx, ears.ty] = [c2.x, c2.y];
    }
    return { x: c0.x, y: c0.y, angle, k, squash, ears };
  }

  /**
   * The 3D half of a gem's frame: poses the body, renders it into its slot of
   * the atlas (sx, sy: canvas pixels from the atlas's top left) and seats the
   * face and hat on it while the shared mesh still holds this gem's pose.
   */
  private render3d(avatar: Avatar, activity: Activity, t: number, sx: number, sy: number): Pending | null {
    const renderer = this.renderer;
    const body = this.body;
    if (!renderer || !body) return null;
    const glow = Math.min(1.1, avatar.glow + avatar.burst * 0.6);
    const face = avatar.props.face ?? "pill";
    const season = avatar.props.season === "auto" ? seasonFor() : (avatar.props.season ?? "none");
    const hatProp = avatar.props.hat ?? "auto";
    // A bubbled (offline) gem rests bare-headed.
    const hat = activity === "offline" ? "none" : hatProp === "auto" ? autoHat(avatar.props.caste, season, avatar.seed) : hatProp;
    // Without a face nothing is seated, the hat included.
    this.pose(avatar, activity, t, glow, face === "none" ? "none" : hat);
    const px = avatar.px;
    // WebGL counts rows from the bottom.
    renderer.setViewport(sx, this.atlas - sy - px, px, px);
    renderer.setScissor(sx, this.atlas - sy - px, px, px);
    renderer.clear();
    renderer.render(this.scene, this.camera);

    // The gem's frame: FIT of the layout box and SINK lower, as the camera draws it.
    const g = avatar.gemPx * FIT;
    const o = Math.floor((px - g) / 2);
    const oy = Math.floor((px - g) / 2 + SINK * avatar.gemPx);
    const frame: FaceFrame = {
      ctx: avatar.ctx,
      t,
      since: activity === "dance" ? this.danceSince(avatar, t) : t - avatar.activitySince,
      prev: avatar.prev,
      box: { x: o, y: oy, s: g },
      anchor: ANCHOR,
      shift: { x: 0, y: 0 },
      look: avatar.look,
      blink: avatar.blink,
      activity,
      style: face === "none" ? "pill" : face,
      hat,
      season,
      rgb: avatar.rgb,
      dark: avatar.dark,
      talk: avatar.talk,
      desk: avatar.desk,
      atDesk: avatar.atDesk,
      bubble: avatar.bubble,
      big: avatar.props.size >= 96,
      seed: avatar.seed,
      still: this.still || this.settled(avatar, activity, t),
      eyeShape: avatar.eyes.map((spring) => spring.x),
      lid: avatar.lid,
      mouthShape: avatar.mouth.map((spring) => spring.x),
      mouthOpen: avatar.mouthOpen,
    };
    const pending: Pending = { avatar, frame, sx, sy, glow, seat: null, hat: null, faceScale: FACE_SCALE, stretch: [1, 1] };
    if (face === "none") return pending;

    // The face, hat and held tools are seated on the surface, so they ride the
    // body: its turn toward the pointer, dance, lean, squash and breath.
    body.updateMatrixWorld(true);
    const { lead } = avatar;
    const up = -lead.y * (lead.y < 0 ? 0.1 : 0.05);
    const local: FaceFrame = { ...frame, box: { x: -ANCHOR.x * g, y: -ANCHOR.y * g, s: g } };
    // A small gem gets a bigger face, so it still reads in a sidebar row.
    pending.faceScale = FACE_SCALE * (1 + 0.18 * clamp((64 - avatar.props.size) / 40, 0, 1));
    pending.seat = this.seat(avatar, lead.x * 0.08, faceHeight(weights) + 0.1 + up, eyeGap(local) / (2 * g), t, px, g, pending.faceScale);
    // A fast dart stretches the face a little along it.
    const vx = Math.abs(avatar.gaze[0].v);
    const vy = Math.abs(avatar.gaze[1].v);
    pending.stretch = [1 + Math.min(vx * 0.01, 0.04) - Math.min(vy * 0.005, 0.02), 1 + Math.min(vy * 0.01, 0.04) - Math.min(vx * 0.005, 0.02)];
    if (frame.hat !== "none") pending.hat = this.hatFit(avatar, frame.hat, pending.seat.vis, px, g);
    return pending;
  }

  /**
   * Reads the atlas back once (copying straight from WebGL into each gem's
   * canvas forced a GPU sync per gem, which held a crowded page near 13fps)
   * and runs each rendered gem's 2D half.
   */
  private flush(batch: Pending[]) {
    const renderer = this.renderer;
    if (!batch.length || !renderer) return;
    let rows = 0;
    for (const p of batch) rows = Math.max(rows, p.sy + p.avatar.px);
    if (!this.atlasCopy) this.atlasCopy = document.createElement("canvas").getContext("2d");
    const copy = this.atlasCopy;
    if (!copy) return;
    if (copy.canvas.width < this.atlas || copy.canvas.height < this.atlas) {
      copy.canvas.width = this.atlas;
      copy.canvas.height = this.atlas;
    }
    copy.clearRect(0, 0, this.atlas, rows);
    copy.drawImage(renderer.domElement, 0, 0, this.atlas, rows, 0, 0, this.atlas, rows);
    const started = performance.now();
    for (const p of batch) this.paint2d(p, copy.canvas);
    this.paintCost += ((performance.now() - started) / batch.length - this.paintCost) * 0.1;
    batch.length = 0;
  }

  /**
   * The canvas less the inside of a covering hat, as an even-odd clip: from the
   * band up, across the middle of the band only, where every hat reaches down
   * to it (a beret's sides curve up, and a heart's lobes stand at its edges).
   */
  private crownClip(p: Pending, px: number): Path2D | null {
    const fit = p.hat;
    const hat = p.frame.hat;
    if (!fit || hat === "none" || !COVERS.has(hat)) return null;
    const half = 0.55 * HAT_SIZES[hat].band * fit.k;
    const cos = Math.cos(fit.angle);
    const sin = Math.sin(fit.angle);
    const path = new Path2D();
    path.rect(0, 0, px, px);
    const corners: [number, number][] = [[-half, 0], [half, 0], [half, -px], [-half, -px]];
    corners.forEach(([u, v], i) => {
      const x = fit.x + u * cos - v * sin;
      const y = fit.y + u * sin + v * cos;
      if (i) path.lineTo(x, y);
      else path.moveTo(x, y);
    });
    path.closePath();
    return path;
  }

  /** The 2D half of a gem's frame: halo, body, hat, face, held tools and effects. */
  private paint2d(p: Pending, atlas: HTMLCanvasElement) {
    const { avatar, frame, sx, sy, glow, seat } = p;
    const { t } = frame;
    const ctx = avatar.ctx;
    const px = avatar.px;
    const g = frame.box.s;
    const o = frame.box.x;
    const oy = frame.box.y;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, px, px);
    const unit = g / FRAME;
    const lift = avatar.bob * unit;
    const slideX = avatar.slide * unit;
    // Idle's tiny internal glow needs no extra full-canvas gradient.
    if (glow > 0.1) {
      const cx = o + g / 2 + slideX;
      const cy = oy + g / 2 - lift;
      haloTint.copy(avatar.base).lerp(WHITE, 0.3);
      if (avatar.anger > 0) haloTint.lerp(ANGRY, avatar.anger);
      const glowColor = srgb(haloTint);
      const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, g * 0.58);
      halo.addColorStop(0, rgbString(glowColor, Math.min(0.32, 0.3 * glow)));
      halo.addColorStop(0.5, rgbString(glowColor, Math.min(0.14, 0.12 * glow)));
      halo.addColorStop(1, rgbString(glowColor, 0));
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, px, px);
    }
    // Auras: what goes behind the body now, the rest once it is drawn.
    const aura = this.auraPaint(avatar, ctx, t, o + g / 2 + slideX, oy + g / 2 - lift, g, glow, frame.activity);
    if (aura) drawAuraBehind(aura);
    // A pointed head (a pear's tip, an octahedron's point) rises inside its
    // hat rather than poking through it.
    const crown = this.crownClip(p, px);
    ctx.save();
    if (crown) ctx.clip(crown, "evenodd");
    ctx.drawImage(atlas, sx, sy, px, px, 0, 0, px, px);
    ctx.restore();
    if (!seat) {
      this.drawGlints(ctx, avatar, o + slideX, oy - lift, g);
      if (aura) this.auraFront(aura, avatar);
      return;
    }

    // The hat goes on first, so a face turned toward a headphone cup passes in front.
    if (p.hat) drawHat(frame, p.hat);

    // The face, holiday lights and glints are paint on the body: drawn apart,
    // trimmed to its silhouette (wide shades on a turned gem never stick out),
    // then laid on.
    const local: FaceFrame = { ...frame, box: { x: -ANCHOR.x * g, y: -ANCHOR.y * g, s: g } };
    const paint = this.paintLayer(px);
    if (paint) {
      if (seat.vis > 0.01) {
        paint.globalAlpha = seat.vis;
        paint.setTransform(seat.a, seat.b, seat.c, seat.d, seat.e, seat.f);
        paint.scale(p.stretch[0], p.stretch[1]);
        drawFace({ ...local, ctx: paint });
        // Upright at the face, at the body's scale: the lights hang below the
        // mouth. Too small to read under 40px, so they stay off there.
        if (avatar.props.size >= 40) {
          const tilt = Math.atan2(-seat.c, seat.d);
          const scale = Math.hypot(seat.c, seat.d) / p.faceScale;
          paint.setTransform(Math.cos(tilt) * scale, Math.sin(tilt) * scale, -Math.sin(tilt) * scale, Math.cos(tilt) * scale, seat.e, seat.f);
          drawSeasonBody({ ...frame, ctx: paint, box: { x: -ANCHOR.x * g, y: (0.09 - ANCHOR.y) * g, s: g } });
        }
        paint.globalAlpha = 1;
      }
      paint.setTransform(1, 0, 0, 1, 0, 0);
      this.drawGlints(paint, avatar, o + slideX, oy - lift, g);
      paint.globalCompositeOperation = "destination-in";
      paint.drawImage(atlas, sx, sy, px, px, 0, 0, px, px);
      paint.globalCompositeOperation = "source-over";
      ctx.save();
      if (crown) ctx.clip(crown, "evenodd");
      ctx.drawImage(paint.canvas, 0, 0, px, px, 0, 0, px, px);
      ctx.restore();
    }

    // Tools in hand ride the face; a finished one goes back into the gem.
    const away = t - avatar.activitySince;
    const leaving = avatar.prev !== avatar.activity && !frame.still;
    ctx.save();
    ctx.setTransform(seat.a, seat.b, seat.c, seat.d, seat.e, seat.f);
    drawHeld(local);
    if (leaving && away < 0.3 && HOLDS.has(avatar.prev)) {
      drawHeld({ ...local, activity: avatar.prev, since: 10, leave: away / 0.3 });
    }
    ctx.restore();
    drawProps(frame);
    drawEffects(frame);
    // What the last activity put around the gem fades out instead of vanishing.
    if (leaving && away < 0.4) drawActivityEffects({ ...frame, activity: avatar.prev, since: 10 }, 1 - away / 0.4);
    if (aura) this.auraFront(aura, avatar);
  }

  /**
   * This frame's auras, in the picked aura color or the gem's own lightened:
   * a third strength at rest, full while it works, fading out offline.
   */
  private auraPaint(avatar: Avatar, ctx: CanvasRenderingContext2D, t: number, cx: number, cy: number, g: number, glow: number, activity: Activity): AuraPaint | null {
    const { auras = [], auraColor } = avatar.props;
    if (!auras.length && !avatar.sparks.length) return null;
    const strength = activity === "offline" ? 0 : (0.35 + 0.65 * clamp(glow, 0, 1)) * (1 - avatar.rest);
    const rgb: Rgb = Array.isArray(auraColor) && auraColor.length === 3 ? [auraColor[0], auraColor[1], auraColor[2]] : mix(avatar.rgb, [255, 255, 255], 0.25);
    return { ctx, t, cx, cy, g, rgb, strength, auras, seed: avatar.seed };
  }

  private auraFront(aura: AuraPaint, avatar: Avatar) {
    drawAuraFront(aura);
    drawSparks(aura.ctx, avatar.sparks, aura.cx, aura.cy, aura.g, aura.rgb);
  }

  /** The body's pose, shape and colors for this frame. */
  private pose(avatar: Avatar, activity: Activity, t: number, glow: number, hat: HatStyle) {
    const body = this.body;
    const material = this.material;
    if (!body || !material) return;
    body.geometry = bodyGeometry(avatar.props.size <= 64);
    material.uniforms.uShapes.value = body.geometry.userData.shapes;
    const still = this.still;
    let pitch = avatar.pose[0].x;
    let roll = avatar.pose[1].x;
    let lift = avatar.pose[2].x;
    let squash = avatar.pose[3].x;
    let yaw = 0;
    let slide = 0;
    let spin = 0;
    avatar.jig = 0;
    if (!still) {
      const since = t - avatar.activitySince;
      yaw = avatar.turn[0].x;
      pitch += avatar.turn[1].x * (1 - avatar.desk);
      // Leaning into a fast turn, and a slow shift of weight.
      roll += clamp(-avatar.turn[0].v * 0.05, -0.12, 0.12) +
        (0.008 + 0.012 * avatar.spec.energy) * Math.sin(t * 0.4 + avatar.seed);
      // At the laptop: a frenzy at the keys, or nose to the screen reading
      // (and thinking over what a tool sent back). Away from it, thinking
      // rolls the head and nods along. Blended in from the last activity's.
      const want = { ...NO_MOVES };
      if (activity === "typing") {
        const m = frenzy(since, avatar.seed);
        want.slide = 0.14 * m.lean * avatar.desk;
        want.roll = (m.roll - leanRoll(m.lean)) * avatar.desk;
        want.spin = leanTurn(m.lean) * avatar.desk;
        want.pitch = m.pitch * avatar.desk;
        want.lift = m.lift * avatar.desk;
        want.squash = m.squash * avatar.desk;
      } else if (activity === "reading" || (activity === "thinking" && avatar.atDesk)) {
        const m = peer(since, avatar.seed);
        want.slide = 0.14 * m.lean * avatar.desk;
        want.roll = -leanRoll(m.lean) * avatar.desk;
        want.spin = leanTurn(m.lean) * avatar.desk;
        want.pitch = m.pitch * avatar.desk;
      } else if (activity === "thinking") {
        const m = ponder(since, avatar.seed);
        const away = 1 - avatar.desk;
        want.slide = m.slide * away;
        want.roll = m.roll * away;
        want.pitch = m.pitch * away;
        want.lift = m.lift * away;
      }
      const k = since >= MOVE_BLEND ? 1 : smoothstep(since / MOVE_BLEND);
      for (const key of Object.keys(want) as (keyof Moves)[]) {
        avatar.moves[key] = avatar.carry[key] + (want[key] - avatar.carry[key]) * k;
      }
      slide += avatar.moves.slide;
      roll += avatar.moves.roll;
      pitch += avatar.moves.pitch;
      lift += avatar.moves.lift;
      squash += avatar.moves.squash;
      spin += avatar.moves.spin;
      // A lift with each syllable.
      if (activity === "speaking" || activity === "messaging") lift += 0.008 * avatar.talk;
      // The dance plays out even if work starts again mid-way, so it never
      // cuts off; the Dance state loops it. Each gem turns its own way.
      const move = dance(this.danceSince(avatar, t) / DANCE);
      const dir = avatar.seed < Math.PI ? 1 : -1;
      lift += move.lift;
      squash += move.squash;
      roll += move.roll * dir;
      spin += move.spin * dir;
      avatar.jig = move.hat * dir;
      // An error lands with one short shake, then holds still.
      if (activity === "error" && since < 0.55) slide = Math.sin(since * 40) * 0.035 * (1 - since / 0.55) ** 2;
      if (activity === "waiting") {
        // Every few seconds a small hop: still here, still waiting.
        const c = (since + 3.5) % 4.5;
        if (c < 0.45) lift += 0.045 * Math.sin((Math.PI * c) / 0.45);
      }
    }
    // At the laptop the gem turns toward it and scoots left to make room; not
    // fully side-on, so its face stays in view.
    spin += avatar.desk * 0.75 + avatar.whirl;
    slide -= avatar.desk * 0.26;
    yaw *= 1 - avatar.desk;
    const [bw, bh] = avatar.spec.build;
    const breath = 0.018 * avatar.inhale;
    const tall = (1 + breath) * (1 - squash);
    const wide = (1 - breath * 0.5) * (1 + squash * 0.6);
    // Squashed and stretched from the ground up: the base stays where it stands.
    lift += (tall - 1) * bh * 0.85;
    body.rotation.set(0.12 + pitch, yaw + spin, roll);
    body.position.set(slide, lift, 0);
    body.scale.set(bw * wide, bh * tall, bw * wide);
    avatar.bob = lift;
    avatar.slide = slide;

    const moving = !still && activity !== "offline";
    const jelly = avatar.motion === "jelly" && moving;
    avatar.wob = !moving ? 0 : jelly ? 0.012 + avatar.stir * 0.032 : 0.004;
    avatar.shear = jelly ? clamp(-avatar.turn[0].v * 0.16, -0.3, 0.3) : 0;
    for (let i = 0; i < SHAPES.length; i += 1) weights[i] = avatar.shape[i].x;
    setShapeWeights(material, weights);
    const u = material.uniforms;
    u.uWob.value = avatar.wob;
    u.uTime.value = t;
    u.uShear.value = avatar.shear;

    // Offline: darker, nearly gray with a hint of its hue, no finish. An error: flushed red.
    tint.copy(avatar.base).multiplyScalar(1 - 0.45 * avatar.rest);
    if (avatar.anger > 0) tint.lerp(ANGRY, 0.55 * avatar.anger);
    paintBody(material, tint, avatar.rest, avatar.spec.material, clamp((avatar.props.size - 24) / 40, 0, 1));
    if (avatar.anger > 0) (u.uGlowColor.value as Color).lerp(ANGRY, avatar.anger);
    u.uGlow.value = Math.min(0.8, glow * 0.85) * (1 - avatar.rest);
    if (this.hairCoat && this.furCoat) {
      const { hairColor, furColor } = avatar.props;
      const { hair, fur } = coats(avatar.props);
      const gem = { weights, rest: avatar.rest, face: faceHeight(weights), unitPx: (avatar.gemPx * FIT) / FRAME };
      // A hat over the crown presses the coat flat under its band.
      const pulled = hat === "none" ? null : hatBand(avatar.props, hat, headFor(weights, avatar.spec.build, HAT_SIZES[hat].fit));
      const band = pulled?.pressed ? { y: pulled.by, r: pulled.bx } : null;
      dressCoat(this.hairCoat, material, hair, hairColor, gem, band);
      dressCoat(this.furCoat, material, fur, furColor, gem, band);
    }
  }

  private drawGlints(ctx: CanvasRenderingContext2D, avatar: Avatar, ox: number, oy: number, g: number) {
    if (!avatar.glints.length) return;
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
