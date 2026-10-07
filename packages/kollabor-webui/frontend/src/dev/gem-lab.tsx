import { StrictMode, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "../styles.css";
import { GemAvatar } from "@/components/gems/GemAvatar";
import {
  EYE_STYLES,
  HAT_STYLES,
  SEASONS,
  type Activity,
  type EyeStyle,
  type HatStyle,
  type Season,
} from "@/components/gems/gem-face";

// Dev-only gem studio (vite serves /gem-lab.html; the build only takes
// index.html): compare eye styles, hats, seasons and activities on the pool.
const POOL: [string, number[], string][] = [
  ["lapis", [30, 90, 180], "communication"], ["sapphire", [15, 82, 186], "communication"],
  ["aquamarine", [100, 200, 235], "communication"], ["zircon", [70, 130, 200], "communication"],
  ["bismuth", [200, 100, 150], "engineering"], ["peridot", [120, 190, 33], "engineering"],
  ["jasper", [210, 120, 50], "engineering"], ["nephrite", [80, 160, 80], "engineering"],
  ["ruby", [200, 30, 50], "defense"], ["garnet", [140, 20, 60], "defense"],
  ["topaz", [240, 200, 50], "defense"], ["hessonite", [200, 140, 60], "defense"],
  ["pearl", [230, 220, 240], "intelligence"], ["moonstone", [200, 210, 230], "intelligence"],
  ["opal", [180, 200, 255], "intelligence"], ["padparadscha", [240, 170, 140], "intelligence"],
  ["amethyst", [140, 80, 200], "creative"], ["quartz", [240, 150, 170], "creative"],
  ["spinel", [230, 50, 120], "creative"], ["citrine", [240, 230, 100], "creative"],
  ["diamond", [245, 245, 250], "leadership"], ["aureate", [255, 230, 50], "leadership"],
  ["cobalt", [70, 100, 200], "leadership"], ["coral", [255, 130, 170], "leadership"],
];
const SHOWCASE = ["diamond", "sapphire", "ruby", "peridot", "lapis", "amethyst"];
const ACTIVITIES: Activity[] = [
  "idle", "thinking", "typing", "reading", "speaking", "searching", "messaging",
  "tasking", "error", "waiting", "dreaming", "dance", "booting", "offline",
];
const EYE_GROUPS = [...new Set(EYE_STYLES.map((e) => e.group))];

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md border px-2.5 py-1 text-xs capitalize transition-colors ${
        active ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent"
      }`}
    >
      {children}
    </button>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      <span className="text-muted-foreground mr-1 w-16 shrink-0 text-xs">{label}</span>
      {children}
    </div>
  );
}

function Lab() {
  const [size, setSize] = useState(96);
  const [activity, setActivity] = useState<Activity>("idle");
  const [hat, setHat] = useState<HatStyle | "auto">("auto");
  const [season, setSeason] = useState<Season | "auto">("auto");
  const [face, setFace] = useState<EyeStyle>("disney");
  const [group, setGroup] = useState("All");
  const [light, setLight] = useState(false);
  const [view, setView] = useState<"styles" | "pool">("styles");
  const gem = (name: string, style: EyeStyle) => {
    const [, color, caste] = POOL.find(([n]) => n === name)!;
    return (
      <div key={name} className="flex flex-col items-center gap-2">
        <GemAvatar
          gem={name}
          color={color}
          caste={caste}
          face={style}
          hat={hat}
          season={season}
          activity={activity === "offline" || activity === "booting" ? null : activity}
          state={activity === "booting" ? "booting" : activity === "offline" ? "" : "working"}
          live={activity !== "offline"}
          size={size}
        />
        <span className="text-muted-foreground text-[11px] capitalize">{name}</span>
      </div>
    );
  };
  const styles = EYE_STYLES.filter((e) => group === "All" || e.group === group);
  return (
    <div className={light ? "" : "dark"}>
      <div className="bg-background text-foreground min-h-svh p-6">
        <Row label="View">
          <Chip active={view === "styles"} onClick={() => setView("styles")}>Eye Styles</Chip>
          <Chip active={view === "pool"} onClick={() => setView("pool")}>Whole Pool</Chip>
          <span className="mx-2" />
          {[40, 64, 96, 140].map((s) => (
            <Chip key={s} active={s === size} onClick={() => setSize(s)}>{s}px</Chip>
          ))}
          <Chip active={light} onClick={() => setLight((v) => !v)}>Light</Chip>
        </Row>
        <Row label="Activity">
          {ACTIVITIES.map((a) => (
            <Chip key={a} active={a === activity} onClick={() => setActivity(a)}>{a}</Chip>
          ))}
        </Row>
        <Row label="Season">
          <Chip active={season === "auto"} onClick={() => setSeason("auto")}>Auto</Chip>
          {SEASONS.map((o) => (
            <Chip key={o.id} active={o.id === season} onClick={() => setSeason(o.id)}>{o.label}</Chip>
          ))}
        </Row>
        <Row label="Hat">
          <Chip active={hat === "auto"} onClick={() => setHat("auto")}>Auto</Chip>
          {HAT_STYLES.map((o, i) => (
            <span key={o.id} className="contents">
              {o.group !== HAT_STYLES[i - 1]?.group ? (
                <span className="text-muted-foreground ml-2 text-[10px] uppercase tracking-wide">{o.group}</span>
              ) : null}
              <Chip active={o.id === hat} onClick={() => setHat(o.id)}>{o.label}</Chip>
            </span>
          ))}
        </Row>
        {view === "pool" ? (
          <Row label="Eyes">
            {EYE_STYLES.map((o) => (
              <Chip key={o.id} active={o.id === face} onClick={() => setFace(o.id)}>{o.label}</Chip>
            ))}
          </Row>
        ) : (
          <Row label="Eyes">
            {["All", ...EYE_GROUPS].map((g) => (
              <Chip key={g} active={g === group} onClick={() => setGroup(g)}>{g}</Chip>
            ))}
          </Row>
        )}
        {view === "styles" ? (
          <div className="mt-8 flex flex-col gap-10">
            {styles.map((style) => (
              <div key={style.id} className="flex items-center gap-8">
                <div className="w-24 shrink-0">
                  <div className="text-sm font-medium">{style.label}</div>
                  <div className="text-muted-foreground text-[10px] uppercase tracking-wide">{style.group}</div>
                </div>
                <div className="flex flex-wrap gap-12">{SHOWCASE.map((name) => gem(name, style.id))}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-6 gap-x-6 gap-y-12 pt-6">{POOL.map(([name]) => gem(name, face))}</div>
        )}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Lab />
  </StrictMode>,
);
