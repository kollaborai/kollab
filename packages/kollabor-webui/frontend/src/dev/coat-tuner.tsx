import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import "../styles.css";
import { GemAvatar } from "@/components/gems/GemAvatar";
import { gemEngine } from "@/components/gems/gem-engine";
import { FUR_COATS, HAIR_COATS, type CoatStyle } from "@/components/gems/gem-fur";
import type { GemCut } from "@/components/gems/gem-specs";
import { COAT_KNOBS, HAIR_COLORS, HAT_STYLES, type Activity, type Aura, type FurStyle, type HairStyle, type HatStyle, type Sparkle } from "@/components/gems/gem-face";

// Dev only (/coat-tuner.html; the build only takes index.html): tune hair and fur live on the real engine. The sliders write HAIR_COATS and FUR_COATS, which the engine reads every frame.
type Mock = { title: string; cut: GemCut | null; hair?: HairStyle; fur?: FurStyle; hat?: HatStyle; auras?: Aura[]; sparkles?: Sparkle };
const MOCKS: Mock[] = [
  // The curly styles first, then every other hair style the gems can be born with.
  { title: "Crop", cut: "person", hair: "crop" },
  { title: "Bob", cut: "person", hair: "bob" },
  { title: "Mane", cut: "person", hair: "mane" },
  { title: "Buzz", cut: "person", hair: "buzz" },
  { title: "Fringe", cut: "person", hair: "fringe" },
  { title: "Page", cut: "person", hair: "page" },
  { title: "Plume", cut: "person", hair: "plume" },
  { title: "Crest", cut: "person", hair: "crest" },
  { title: "Quiff", cut: "person", hair: "quiff" },
  { title: "Halo", cut: "person", hair: "halo" },
  { title: "Short", cut: "person", hair: "short" },
  { title: "Long", cut: "person", hair: "long" },
  { title: "Spiky", cut: "person", hair: "spiky" },
  { title: "Mohawk", cut: "person", hair: "mohawk" },
  { title: "Short Fur", cut: null, fur: "short" },
  { title: "Shaggy Fur", cut: null, fur: "shaggy" },
  { title: "Fur And Long Hair", cut: null, fur: "short", hair: "long" },
  { title: "Short Hair And Cap", cut: "person", hair: "short", hat: "cap" },
  { title: "Long Hair And Beanie", cut: "person", hair: "long", hat: "beanie" },
  { title: "Glow", cut: null, auras: ["glow"] },
  { title: "Flames", cut: null, auras: ["flames"] },
  { title: "Orbit", cut: null, auras: ["orbit"] },
  { title: "Lots Of Sparkles", cut: null, sparkles: "lots" },
  { title: "Every Aura", cut: null, auras: ["glow", "flames", "orbit"], sparkles: "lots" },
];
const GEMS = ["lapis", "ruby", "peridot", "topaz", "amethyst", "pearl", "jasper", "garnet"];
const STYLES: { label: string; coat: CoatStyle }[] = [
  ...Object.entries(HAIR_COATS).map(([id, coat]) => ({ label: `Hair ${id}`, coat })),
  ...Object.entries(FUR_COATS).map(([id, coat]) => ({ label: `Fur ${id}`, coat })),
];
// The engine and the coats, for reading the tuned values back.
Object.assign(window, { gemEngine, HAIR_COATS, FUR_COATS });
// Slider moves outlive a reload (every engine edit reloads this page).
const SAVED = "coat-tuner";
const saved: Record<string, Partial<CoatStyle>> = (() => {
  try {
    return JSON.parse(localStorage.getItem(SAVED) ?? "{}");
  } catch {
    return {};
  }
})();
for (const style of STYLES) Object.assign(style.coat, saved[style.label]);

const tab = (on: boolean) => (on ? "text-foreground underline underline-offset-4" : "text-muted-foreground hover:text-foreground");

function Knobs() {
  const [pick, setPick] = useState(0);
  const [, redraw] = useState(0);
  const coat = STYLES[pick].coat;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, fontSize: 13 }}>
        {STYLES.map((style, i) => (
          <button key={style.label} type="button" onClick={() => setPick(i)} className={tab(i === pick)} style={{ textTransform: "capitalize" }}>
            {style.label}
          </button>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "96px 160px 40px", alignItems: "center", gap: "4px 8px", fontSize: 12 }}>
        {COAT_KNOBS.map((knob) => (
          <label key={knob.key} style={{ display: "contents" }}>
            <span className="text-muted-foreground">{knob.label}</span>
            <input
              type="range"
              min={knob.min}
              max={knob.max}
              step={knob.step}
              value={coat[knob.key]}
              onChange={(event) => {
                coat[knob.key] = Number(event.target.value);
                saved[STYLES[pick].label] = { ...saved[STYLES[pick].label], [knob.key]: coat[knob.key] };
                try {
                  localStorage.setItem(SAVED, JSON.stringify(saved));
                } catch {
                  // Private windows: the moves just don't outlive a reload.
                }
                redraw((n) => n + 1);
              }}
            />
            <span style={{ fontVariantNumeric: "tabular-nums" }}>{coat[knob.key].toFixed(2)}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

function Swatches({ label, value, onPick }: { label: string; value: readonly number[] | "gem"; onPick: (rgb: readonly number[] | "gem") => void }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
      <span className="text-muted-foreground" style={{ width: 70 }}>{label}</span>
      <button type="button" onClick={() => onPick("gem")} className={tab(value === "gem")}>Gem</button>
      {HAIR_COLORS.map((color) => (
        <button
          key={color.label}
          type="button"
          aria-label={color.label}
          title={color.label}
          onClick={() => onPick(color.rgb)}
          style={{
            width: 18,
            height: 18,
            borderRadius: 9,
            background: `rgb(${color.rgb.join(",")})`,
            outline: value === color.rgb ? "2px solid white" : "none",
            outlineOffset: 2,
          }}
        />
      ))}
      <input
        type="color"
        aria-label={`Custom ${label}`}
        title="Custom"
        value={`#${(value === "gem" ? [128, 128, 128] : value).map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`}
        onChange={(event) => onPick([1, 3, 5].map((i) => parseInt(event.target.value.slice(i, i + 2), 16)))}
        style={{ width: 26, height: 22, cursor: "pointer", background: "transparent" }}
      />
    </div>
  );
}

function Tuner() {
  const [gem, setGem] = useState(new URLSearchParams(location.search).get("gem") ?? "lapis");
  const [hairColor, setHairColor] = useState<readonly number[] | "gem">("gem");
  const [furColor, setFurColor] = useState<readonly number[] | "gem">("gem");
  const [activity, setActivity] = useState<Activity>(new URLSearchParams(location.search).get("activity") === "typing" ? "typing" : "idle");
  // One hat on every gem, or each its own ("each").
  const [hat, setHat] = useState(new URLSearchParams(location.search).get("hat") ?? "each");
  const avatar = (mock: Mock, size: number) => (
    <GemAvatar
      gem={gem}
      caste="communication"
      face="disney"
      hat={hat === "each" ? (mock.hat ?? "none") : (hat as HatStyle)}
      cut={mock.cut}
      hair={mock.hair ?? "none"}
      hairColor={hairColor}
      fur={mock.fur ?? "none"}
      furColor={furColor}
      auras={mock.auras}
      sparkles={mock.sparkles}
      season="none"
      activity={activity}
      state="working"
      live
      size={size}
    />
  );
  return (
    <div className="dark">
      <div className="bg-background text-foreground min-h-svh" style={{ padding: 20 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 14, fontSize: 13, marginBottom: 14 }}>
          <span className="text-muted-foreground">Gem</span>
          {GEMS.map((name) => (
            <button key={name} type="button" onClick={() => setGem(name)} className={tab(name === gem)} style={{ textTransform: "capitalize" }}>
              {name}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 32, marginBottom: 12 }}>
          <Knobs />
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <Swatches label="Hair Color" value={hairColor} onPick={setHairColor} />
            <Swatches label="Fur Color" value={furColor} onPick={setFurColor} />
            <div style={{ display: "flex", gap: 12, fontSize: 12 }}>
              <span className="text-muted-foreground" style={{ width: 70 }}>Activity</span>
              {(["idle", "typing", "offline"] as Activity[]).map((option) => (
                <button key={option} type="button" onClick={() => setActivity(option)} className={tab(option === activity)} style={{ textTransform: "capitalize" }}>
                  {option}
                </button>
              ))}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px", fontSize: 12, maxWidth: 520 }}>
              <span className="text-muted-foreground" style={{ width: 70 }}>Hat</span>
              {[{ id: "each", label: "Each Own" }, ...HAT_STYLES].map((option) => (
                <button key={option.id} type="button" onClick={() => setHat(option.id)} className={tab(option.id === hat)}>
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
          {MOCKS.map((mock) => (
            <div key={mock.title} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
              <div className="text-sm font-medium">{mock.title}</div>
              {avatar(mock, 180)}
              <div style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
                {avatar(mock, 40)}
                {avatar(mock, 28)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Tuner />
  </StrictMode>,
);
