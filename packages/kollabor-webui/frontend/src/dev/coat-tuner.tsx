import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import "../styles.css";
import { GemAvatar } from "@/components/gems/GemAvatar";
import { gemEngine } from "@/components/gems/gem-engine";
import { COATS, type CoatStyle } from "@/components/gems/gem-fur";
import type { GemCut } from "@/components/gems/gem-specs";
import type { HatStyle } from "@/components/gems/gem-face";

// Dev only (/coat-tuner.html; the build only takes index.html): tune hair and fur live on the real engine. The sliders write COATS, which the engine reads every frame.
const MOCKS: { title: string; cut: GemCut | null; hat: HatStyle }[] = [
  { title: "Person With Hair", cut: "person", hat: "hair" },
  { title: "Gem With Fur", cut: null, hat: "fur" },
  { title: "Gem With Hair", cut: null, hat: "hair" },
];
const GEMS = ["lapis", "ruby", "peridot", "topaz", "amethyst", "pearl", "jasper", "garnet"];
const KNOBS: { key: keyof CoatStyle; label: string; min: number; max: number; step: number }[] = [
  { key: "dark", label: "Root Darkness", min: 0, max: 1, step: 0.01 },
  { key: "tip", label: "Tip Lightness", min: 0, max: 1, step: 0.01 },
  { key: "length", label: "Length", min: 0.05, max: 0.6, step: 0.01 },
  { key: "lie", label: "Lie Flat", min: 0, max: 5, step: 0.05 },
  { key: "sway", label: "Sway", min: 0, max: 1, step: 0.01 },
  { key: "gloss", label: "Sheen", min: 0, max: 1, step: 0.01 },
];
// The engine and the coats, for reading the tuned values back.
Object.assign(window, { gemEngine, COATS });

function Knobs({ hat }: { hat: "hair" | "fur" }) {
  const coat = COATS[hat]!;
  const [, redraw] = useState(0);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "96px 160px 40px", alignItems: "center", gap: "4px 8px", fontSize: 12 }}>
      <div className="text-sm font-medium" style={{ gridColumn: "1 / -1" }}>{hat === "hair" ? "Hair" : "Fur"}</div>
      {KNOBS.map((knob) => (
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
              redraw((n) => n + 1);
            }}
          />
          <span style={{ fontVariantNumeric: "tabular-nums" }}>{coat[knob.key].toFixed(2)}</span>
        </label>
      ))}
    </div>
  );
}

function Tuner() {
  const [gem, setGem] = useState(new URLSearchParams(location.search).get("gem") ?? "lapis");
  const avatar = (cut: GemCut | null, hat: HatStyle, size: number) => (
    <GemAvatar gem={gem} caste="communication" face="disney" hat={hat} cut={cut} season="none" activity="idle" state="working" live size={size} />
  );
  return (
    <div className="dark">
      <div className="bg-background text-foreground min-h-svh" style={{ padding: 20 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 14, fontSize: 13, marginBottom: 14 }}>
          <span className="text-muted-foreground">Gem</span>
          {GEMS.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setGem(name)}
              className={name === gem ? "text-foreground underline underline-offset-4" : "text-muted-foreground hover:text-foreground"}
              style={{ textTransform: "capitalize" }}
            >
              {name}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 32, marginBottom: 12 }}>
          <Knobs hat="hair" />
          <Knobs hat="fur" />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))", gap: 12 }}>
          {MOCKS.map((mock) => (
            <div key={mock.title} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
              <div className="text-sm font-medium">{mock.title}</div>
              {avatar(mock.cut, mock.hat, 220)}
              <div style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
                {avatar(mock.cut, mock.hat, 40)}
                {avatar(mock.cut, mock.hat, 28)}
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
