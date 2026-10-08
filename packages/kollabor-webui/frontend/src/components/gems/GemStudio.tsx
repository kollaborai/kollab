import { useState, type ReactNode } from "react";
import { Dices, Loader2, RotateCcw } from "lucide-react";
import type { AgentPoolEntry, GemAppearance, GemLook } from "@/api";
import { titleCase } from "@/components/panels/panel-model";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import { GemAvatar } from "./GemAvatar";
import { EVERYDAY_FACES, EVERYDAY_HATS, GemAppearancePreview, useGemAppearance } from "./gem-appearance";
import { EYE_STYLES, HAT_STYLES, SEASONS, type Activity, type EyeStyle } from "./gem-face";
import { withLook } from "./gem-look";

type Rgb = [number, number, number];

const STAGE_ACTIVITIES: Activity[] = ["idle", "thinking", "typing", "speaking", "dance", "error", "dreaming"];
const HAT_GROUPS = [...new Set(HAT_STYLES.map((hat) => hat.group))];
const SWATCHES: Rgb[] = [
  [200, 30, 50], [255, 120, 100], [240, 160, 40], [240, 220, 80],
  [120, 190, 33], [30, 160, 90], [40, 180, 170], [100, 200, 235],
  [15, 82, 186], [140, 80, 200], [230, 90, 160], [230, 225, 240],
];

const toHex = (rgb: readonly number[]) =>
  `#${rgb.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
const fromHex = (hex: string): Rgb => [
  parseInt(hex.slice(1, 3), 16),
  parseInt(hex.slice(3, 5), 16),
  parseInt(hex.slice(5, 7), 16),
];
const sameColor = (a?: readonly number[], b?: readonly number[]) =>
  Boolean(a && b && a.length === b.length && a.every((channel, i) => channel === b[i]));

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "rounded-md border px-2.5 py-1 text-xs transition-colors",
        active ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent",
      )}
    >
      {children}
    </button>
  );
}

/** One chat's gem: the studio opens on it alone (a session's Properties). */
export type StudioFocus = { gem: string; tab?: "eyes" | "hat" | "color" };

/**
 * Dress the gems over the random look each was born with: eyes, hat and color
 * per gem, and the season for all, on a live stage. Edits a draft; Save writes
 * it to the engine (GET/PUT /agents/appearance) and every gem in the app
 * follows. Born looks are the engine's: Reset goes back to one. With `focus`
 * it dresses that one gem: no gem list and no season.
 */
export function GemStudio({
  open,
  onOpenChange,
  agents,
  focus = null,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agents: readonly AgentPoolEntry[];
  focus?: StudioFocus | null;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "flex max-h-[92vh] flex-col gap-0 overflow-hidden p-0",
          focus ? "sm:max-w-[min(46rem,calc(100%-2rem))]" : "sm:max-w-[min(64rem,calc(100%-2rem))]",
        )}
      >
        {/* Mounted per open, so each visit starts from what is saved. */}
        <StudioBody agents={agents} focus={focus} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function StudioBody({
  agents,
  focus,
  onDone,
}: {
  agents: readonly AgentPoolEntry[];
  focus: StudioFocus | null;
  onDone: () => void;
}) {
  const studio = useGemAppearance();
  const [draft, setDraft] = useState<GemAppearance>(() => studio?.appearance ?? {});
  const [selected, setSelected] = useState(focus?.gem ?? "");
  const [activity, setActivity] = useState<Activity>("idle");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const current = agents.find((agent) => agent.name === selected) ?? (focus ? { name: focus.gem } : agents[0]);
  const target: GemLook = (current && draft.gems?.[current.name]) || {};
  const born = current ? draft.born?.[current.name] : undefined;
  const bornHat = HAT_STYLES.find((hat) => hat.id === born?.hat)?.label;
  const set = (patch: Partial<GemLook>) => {
    if (current) setDraft((prev) => withLook(prev, current.name, patch));
  };
  const pick = (pool: readonly string[]) => pool[Math.floor(Math.random() * pool.length)];
  const season = draft.season ?? "auto";
  const phone = useIsMobile();

  const save = async () => {
    if (!studio) return;
    setSaving(true);
    setError("");
    try {
      await studio.save(draft);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <DialogHeader className="border-b px-5 py-4 text-left">
        <DialogTitle>{focus ? titleCase(focus.gem) : "Gem Studio"}</DialogTitle>
        <DialogDescription>
          {focus
            ? "Dress this chat's gem over the look it was born with; every browser shows the same gem."
            : "Agents are born with a random look that sticks. Dress them over it; every browser shows the same gems."}
        </DialogDescription>
      </DialogHeader>

      <GemAppearancePreview appearance={draft}>
        <div
          className={cn(
            "flex min-h-0 flex-1 flex-col overflow-y-auto lg:grid lg:overflow-hidden",
            focus ? "lg:grid-cols-[minmax(0,1fr)_20rem]" : "lg:grid-cols-[13rem_minmax(0,1fr)_20rem]",
          )}
        >
          {focus ? null : (
            <nav
              aria-label="Gems"
              className="flex shrink-0 gap-1 overflow-x-auto border-b p-2 lg:min-h-0 lg:flex-col lg:overflow-x-visible lg:overflow-y-auto lg:border-r lg:border-b-0"
            >
              {agents.map((agent) => (
                <button
                  key={agent.name}
                  type="button"
                  aria-pressed={current?.name === agent.name}
                  onClick={() => setSelected(agent.name)}
                  className={cn(
                    "flex shrink-0 items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm transition-colors lg:w-full",
                    current?.name === agent.name ? "bg-accent text-accent-foreground" : "hover:bg-accent/50",
                  )}
                >
                  <GemAvatar gem={agent.name} caste={agent.caste} color={agent.color} state="idle" live season="auto" size={28} />
                  <span className="flex min-w-0 flex-col leading-tight">
                    <span className="font-medium">{titleCase(agent.name)}</span>
                    <span className="text-muted-foreground text-[11px]">
                      {draft.gems?.[agent.name] ? "Custom" : titleCase(agent.caste || "")}
                    </span>
                  </span>
                </button>
              ))}
            </nav>
          )}

          <section className="flex min-h-[19rem] min-w-0 shrink-0 flex-col items-center justify-center gap-4 border-b px-4 py-10 sm:px-6 lg:border-b-0">
            {current ? (
              <GemAvatar
                key={current.name}
                gem={current.name}
                caste={current.caste}
                color={current.color}
                state="idle"
                live
                activity={activity}
                season="auto"
                follow
                size={phone ? 120 : 150}
                label={titleCase(current.name)}
              />
            ) : null}
            <div className="text-center">
              <p className="text-lg font-semibold">{current ? titleCase(current.name) : "No Gems"}</p>
              <p className="text-muted-foreground text-xs">
                {[titleCase(current?.caste || ""), current && !born ? "Not Born Yet" : ""].filter(Boolean).join(" · ")}
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-1.5" role="group" aria-label="Preview activity">
              {STAGE_ACTIVITIES.map((option) => (
                <Chip key={option} active={option === activity} onClick={() => setActivity(option)}>
                  {titleCase(option)}
                </Chip>
              ))}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => set({ face: pick(EVERYDAY_FACES), hat: pick(EVERYDAY_HATS) })}
              disabled={!current}
            >
              <Dices className="size-4" />
              Random Look
            </Button>
          </section>

          <aside className="flex shrink-0 flex-col lg:min-h-0 lg:border-l">
            <Tabs defaultValue={focus?.tab ?? "eyes"} className="flex flex-col gap-0 lg:min-h-0 lg:flex-1">
              <TabsList className="mx-3 mt-3 grid shrink-0 grid-cols-3">
                <TabsTrigger value="eyes">Eyes</TabsTrigger>
                <TabsTrigger value="hat">Hat</TabsTrigger>
                <TabsTrigger value="color">Color</TabsTrigger>
              </TabsList>

              <TabsContent value="eyes" className="p-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
                <div className="grid grid-cols-3 gap-2 sm:grid-cols-6 lg:grid-cols-3">
                  {current ? (
                    <EyeTile
                      active={!target.face}
                      onClick={() => set({ face: undefined })}
                      label={born ? "Born" : "Default"}
                      agent={current}
                      face={(born?.face ?? "pill") as EyeStyle}
                    />
                  ) : null}
                  {current
                    ? EYE_STYLES.map((style) => (
                        <EyeTile
                          key={style.id}
                          active={target.face === style.id}
                          onClick={() => set({ face: style.id })}
                          label={style.label}
                          agent={current}
                          face={style.id}
                        />
                      ))
                    : null}
                </div>
              </TabsContent>

              <TabsContent value="hat" className="p-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap gap-1.5">
                    <Chip active={!target.hat} onClick={() => set({ hat: undefined })}>
                      {born ? "Born" : "Default"}
                    </Chip>
                    <Chip active={target.hat === "auto"} onClick={() => set({ hat: "auto" })}>
                      Auto
                    </Chip>
                  </div>
                  <p className="text-muted-foreground text-[11px]">
                    {bornHat
                      ? `Born hat: ${bornHat}. Seasonal costumes cover it; Auto picks a hat for its caste.`
                      : "Until it is born it wears Auto: a hat for its caste, or a seasonal costume."}
                  </p>
                  {HAT_GROUPS.map((group) => (
                    <div key={group} className="flex flex-col gap-1.5">
                      <p className="text-muted-foreground text-[11px] font-medium">{group}</p>
                      <div className="flex flex-wrap gap-1.5">
                        {HAT_STYLES.filter((hat) => hat.group === group).map((hat) => (
                          <Chip key={hat.id} active={target.hat === hat.id} onClick={() => set({ hat: hat.id })}>
                            {hat.label}
                          </Chip>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </TabsContent>

              <TabsContent value="color" className="p-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
                {current ? (
                  <div className="flex flex-col gap-3">
                    <div className="grid grid-cols-6 gap-2">
                      <Swatch
                        active={!target.color}
                        rgb={current.color ?? [128, 128, 128]}
                        label="Pool Color"
                        onClick={() => set({ color: undefined })}
                        ring
                      />
                      {SWATCHES.map((rgb) => (
                        <Swatch
                          key={rgb.join(",")}
                          active={sameColor(target.color, rgb)}
                          rgb={rgb}
                          label={toHex(rgb)}
                          onClick={() => set({ color: rgb })}
                        />
                      ))}
                    </div>
                    <label className="text-muted-foreground flex items-center gap-2 text-xs">
                      <input
                        type="color"
                        aria-label="Custom Color"
                        value={toHex(target.color ?? current.color ?? [128, 128, 128])}
                        onChange={(event) => set({ color: fromHex(event.target.value) })}
                        className="size-8 cursor-pointer rounded-md border bg-transparent p-0.5"
                      />
                      Custom
                    </label>
                    <p className="text-muted-foreground text-[11px]">
                      The ringed swatch is {titleCase(current.name)}'s pool color.
                    </p>
                  </div>
                ) : null}
              </TabsContent>
            </Tabs>

            {focus ? null : (
              <div className="shrink-0 border-t p-3">
                <p className="text-muted-foreground mb-2 text-[11px] font-medium">Season · All Gems</p>
                <div className="flex flex-wrap gap-1.5">
                  <Chip active={season === "auto"} onClick={() => setDraft((prev) => ({ ...prev, season: "auto" }))}>
                    Auto
                  </Chip>
                  {SEASONS.map((option) => (
                    <Chip
                      key={option.id}
                      active={season === option.id}
                      onClick={() => setDraft((prev) => ({ ...prev, season: option.id }))}
                    >
                      {option.label}
                    </Chip>
                  ))}
                </div>
                <p className="text-muted-foreground mt-2 text-[11px]">
                  Auto: Halloween in October, Christmas in December.
                </p>
              </div>
            )}
          </aside>
        </div>
      </GemAppearancePreview>

      <DialogFooter className="flex-row items-center gap-2 border-t px-5 py-3 sm:justify-between">
        <div className="min-w-0 flex-1">
          {error ? (
            <p className="text-destructive text-xs">{error}</p>
          ) : current ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => set({ face: undefined, hat: undefined, color: undefined })}
              disabled={!draft.gems?.[current.name]}
            >
              <RotateCcw className="size-4" />
              Reset {titleCase(current.name)}
            </Button>
          ) : null}
        </div>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="button" onClick={() => void save()} disabled={saving || !studio}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : null}
          Save
        </Button>
      </DialogFooter>
    </>
  );
}

function EyeTile({
  active,
  onClick,
  label,
  agent,
  face,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  agent: AgentPoolEntry;
  face: EyeStyle;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`${label} Eyes`}
      onClick={onClick}
      className={cn(
        "flex flex-col items-center gap-1 rounded-lg border px-1 pt-3 pb-1.5 text-[11px] transition-colors",
        active ? "border-primary bg-accent" : "border-transparent hover:bg-accent/50",
      )}
    >
      <GemAvatar gem={agent.name} caste={agent.caste} color={agent.color} face={face} state="idle" live season="auto" size={44} />
      <span className="mt-1.5">{label}</span>
    </button>
  );
}

function Swatch({
  active,
  rgb,
  label,
  onClick,
  ring = false,
}: {
  active: boolean;
  rgb: readonly number[];
  label: string;
  onClick: () => void;
  ring?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={label}
      title={label}
      onClick={onClick}
      style={{ background: `rgb(${rgb.join(",")})` }}
      className={cn(
        "aspect-square rounded-full border transition-transform hover:scale-110",
        ring && "ring-muted-foreground/60 ring-2 ring-offset-2 ring-offset-background",
        active && "outline-primary outline-2 outline-offset-2",
      )}
    />
  );
}
