import { useState, type ChangeEvent, type ReactNode } from "react";
import { Dices, Loader2, RotateCcw } from "lucide-react";
import type { AgentPoolEntry, AvatarSpec, GemAppearance, GemLook, Session } from "@/api";
import { Avatar } from "@/components/agents/Avatar";
import { isGemAvatar } from "@/components/agents/avatar-model";
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
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import { agentName, formatSessionName } from "@/utils/session-display";
import { GemAvatar } from "./GemAvatar";
import {
  EVERYDAY_FACES,
  EVERYDAY_HATS,
  GEM_LOOK_VOCABULARY,
  GEM_SWATCHES,
  GemAppearancePreview,
  useGemAppearance,
  useLookKey,
} from "./gem-appearance";
import { EYE_STYLES, HAT_STYLES, SEASONS, type Activity, type EyeStyle } from "./gem-face";
import { resolveGemLook, withLook } from "./gem-look";
import { GEM_CUTS, gemSpec, isPoolGem, type GemCut } from "./gem-specs";

type Rgb = [number, number, number];

const STAGE_ACTIVITIES: Activity[] = ["idle", "thinking", "typing", "speaking", "dance", "error", "dreaming"];
const HAT_GROUPS = [...new Set(HAT_STYLES.map((hat) => hat.group))];

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
        "border-b-2 px-1.5 pt-1 pb-0.5 text-xs transition-colors",
        active ? "border-foreground text-foreground" : "text-muted-foreground hover:text-foreground border-transparent",
      )}
    >
      {children}
    </button>
  );
}

/**
 * A session's Properties: the studio on that session's agent alone, plus a
 * Chat tab for the session itself. `hub` is whether its agent is on the hub
 * mesh, null when no agent runs it (nothing to switch). Its Avatar tab swaps
 * the roster's picture for a gem, initials, an emoji or a picture of the
 * user's; eyes and hat are there only while it is a gem.
 */
export type StudioFocus = {
  gem: string;
  /** Where that agent runs (its folder, or its device): the look is that agent's alone. */
  where?: string | null;
  tab?: "eyes" | "hat" | "color" | "chat";
  chat?: { session: Session; hub: boolean | null; setHub: (enabled: boolean) => Promise<void> };
};

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
  const focused = focus ? agents.find((agent) => agent.name === focus.gem) : undefined;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "flex max-h-[92vh] flex-col gap-0 overflow-hidden p-0",
          focus ? "sm:max-w-[min(46rem,calc(100%-2rem))]" : "sm:max-w-[min(64rem,calc(100%-2rem))]",
        )}
      >
        {/* Mounted per open, so each visit starts from what is saved. */}
        {/* The list dresses gems; a chat's Properties dress its agent, whatever its roster drew it as. */}
        <StudioBody
          agents={agents.filter((agent) => isGemAvatar(agent.avatar))}
          pinned={focused ?? null}
          focus={focus}
          onDone={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

function StudioBody({
  agents,
  pinned: pooled,
  focus,
  onDone,
}: {
  agents: readonly AgentPoolEntry[];
  /** The focused agent, as the pool has it when the dialog opens. */
  pinned: AgentPoolEntry | null;
  focus: StudioFocus | null;
  onDone: () => void;
}) {
  const studio = useGemAppearance();
  // It opens as what the agent is then: one that exits while it is open leaves the pool.
  const [pinned] = useState(pooled);
  const [draft, setDraft] = useState<GemAppearance>(() => studio?.appearance ?? {});
  const [selected, setSelected] = useState(focus?.gem ?? "");
  const [activity, setActivity] = useState<Activity>("idle");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const chat = focus?.chat;
  const [hub, setHub] = useState(chat?.hub ?? true);

  const current: AgentPoolEntry | undefined =
    pinned ?? agents.find((agent) => agent.name === selected) ?? (focus ? { name: focus.gem } : agents[0]);
  const name = current ? agentName(current.name, current.display_name) : "No Gems";
  // A chat's Properties dress that agent; the list dresses the gems of the engine's own folder.
  const where = focus?.where;
  const key = useLookKey(current?.name, where);
  const target: GemLook = (key && draft.gems?.[key]) || {};
  // What it shows now: the user's pick over the roster's. Eyes and hat dress a gem only.
  const rosterAvatar = pinned?.avatar;
  const dressGem = isGemAvatar(target.avatar ?? rosterAvatar);
  const born = resolveGemLook({ born: draft.born }, key, GEM_LOOK_VOCABULARY);
  const bornHat = HAT_STYLES.find((hat) => hat.id === born.bornHat)?.label;
  const baseColor = current?.color ?? (isPoolGem(current?.name) ? undefined : born.bornColor);
  const set = (patch: Partial<GemLook>) => {
    if (key) setDraft((prev) => withLook(prev, key, patch));
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
      if (chat && chat.hub !== null && hub !== chat.hub) await chat.setHub(hub);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <DialogHeader className="px-5 py-4 text-left">
        <DialogTitle>{focus ? name : "Gem Studio"}</DialogTitle>
        <DialogDescription>
          {focus
            ? chat
              ? "Pick this agent's avatar and dress it, or change how it takes part in the hub."
              : "Pick this agent's avatar and dress it; every browser shows the same look."
            : "Agents are born with a random look that sticks. Dress them over it; every browser shows the same gems. An agent in another folder or on another computer has its own look: dress it from its chat's Properties."}
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
              className="bg-muted/20 flex shrink-0 gap-1 overflow-x-auto p-2 lg:min-h-0 lg:flex-col lg:overflow-x-visible lg:overflow-y-auto"
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
                    <span className="text-muted-foreground text-[0.6875rem]">
                      {draft.gems?.[agent.name] ? "Custom" : titleCase(agent.caste || "")}
                    </span>
                  </span>
                </button>
              ))}
            </nav>
          )}

          <section className="flex min-h-[19rem] min-w-0 shrink-0 flex-col items-center justify-center gap-4 px-4 py-10 sm:px-6">
            {current ? (
              <Avatar
                key={key}
                gem={current.name}
                avatar={rosterAvatar}
                displayName={current.display_name}
                folder={chat?.session.device ? undefined : chat?.session.workspace}
                where={where}
                caste={current.caste}
                color={current.color}
                state="idle"
                live
                activity={activity}
                season="auto"
                follow
                size={phone ? 120 : 150}
                label={name}
              />
            ) : null}
            <div className="text-center">
              <p className="text-lg font-semibold">{name}</p>
              <p className="text-muted-foreground text-xs">{titleCase(current?.caste || "")}</p>
            </div>
            {!dressGem ? null : (
              <>
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
              </>
            )}
          </section>

          <aside className="bg-muted/20 flex shrink-0 flex-col lg:min-h-0">
            {(
              <Tabs
                defaultValue={focus?.tab ?? (focus && !dressGem ? "avatar" : "eyes")}
                className="flex flex-col gap-0 lg:min-h-0 lg:flex-1"
              >
                <TabsList
                  className="mx-3 mt-3 grid shrink-0"
                  style={{
                    gridTemplateColumns: `repeat(${1 + (focus ? 1 : 0) + (dressGem ? 3 : 0) + (chat ? 1 : 0)}, minmax(0, 1fr))`,
                  }}
                >
                  {focus ? <TabsTrigger value="avatar">Avatar</TabsTrigger> : null}
                  {dressGem ? <TabsTrigger value="shape">Shape</TabsTrigger> : null}
                  {dressGem ? <TabsTrigger value="eyes">Eyes</TabsTrigger> : null}
                  {dressGem ? <TabsTrigger value="hat">Hat</TabsTrigger> : null}
                  <TabsTrigger value="color">Color</TabsTrigger>
                  {chat ? <TabsTrigger value="chat">Chat</TabsTrigger> : null}
                </TabsList>

                {focus && current ? (
                  <TabsContent value="avatar" className="p-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
                    <AvatarPanel
                      agent={current}
                      where={where}
                      roster={rosterAvatar}
                      picked={target.avatar}
                      color={target.color ?? baseColor}
                      onPick={(avatar) => set({ avatar })}
                    />
                  </TabsContent>
                ) : null}

                {chat ? (
                  <TabsContent value="chat" className="p-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
                    <ChatPanel chat={chat} hub={hub} onHub={setHub} />
                  </TabsContent>
                ) : null}

                <TabsContent value="shape" className="p-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
                  {current ? (
                    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6 lg:grid-cols-3">
                      <ShapeTile
                        active={!target.cut}
                        onClick={() => set({ cut: undefined })}
                        label="Born"
                        agent={current}
                        where={where}
                        cut={gemSpec(current.name, current.caste).cut}
                      />
                      {GEM_CUTS.map((cut) => (
                        <ShapeTile
                          key={cut}
                          active={target.cut === cut}
                          onClick={() => set({ cut })}
                          label={titleCase(cut)}
                          agent={current}
                          where={where}
                          cut={cut}
                        />
                      ))}
                    </div>
                  ) : null}
                </TabsContent>

                <TabsContent value="eyes" className="p-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
                  <div className="grid grid-cols-3 gap-2 sm:grid-cols-6 lg:grid-cols-3">
                    {current ? (
                      <EyeTile
                        active={!target.face}
                        onClick={() => set({ face: undefined })}
                        label="Born"
                        agent={current}
                        where={where}
                        face={(born.face ?? "pill") as EyeStyle}
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
                            where={where}
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
                        Born
                      </Chip>
                      <Chip active={target.hat === "auto"} onClick={() => set({ hat: "auto" })}>
                        Auto
                      </Chip>
                    </div>
                    <p className="text-muted-foreground text-[0.6875rem]">
                      Born hat: {bornHat ?? "None"}. Seasonal costumes cover it; Auto picks a hat for its caste.
                    </p>
                    {HAT_GROUPS.map((group) => (
                      <div key={group} className="flex flex-col gap-1.5">
                        <p className="text-muted-foreground text-[0.6875rem] font-medium">{group}</p>
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
                          rgb={baseColor ?? [128, 128, 128]}
                          label={current.color ? (dressGem ? "Pool Color" : "Roster Color") : "Born Color"}
                          onClick={() => set({ color: undefined })}
                          ring
                        />
                        {GEM_SWATCHES.map((rgb) => (
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
                          value={toHex(target.color ?? baseColor ?? [128, 128, 128])}
                          onChange={(event) => set({ color: fromHex(event.target.value) })}
                          className="size-8 cursor-pointer rounded-md bg-muted/60 p-0.5"
                        />
                        Custom
                      </label>
                      <p className="text-muted-foreground text-[0.6875rem]">
                        The ringed swatch is {name}'s {current.color ? (dressGem ? "pool" : "roster") : "born"} color.
                      </p>
                    </div>
                  ) : null}
                </TabsContent>
              </Tabs>
            )}

            {focus ? null : (
              <div className="bg-muted/30 shrink-0 p-3">
                <p className="text-muted-foreground mb-2 text-[0.6875rem] font-medium">Season · All Gems</p>
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
                <p className="text-muted-foreground mt-2 text-[0.6875rem]">
                  Auto: Halloween in October, Christmas in December.
                </p>
              </div>
            )}
          </aside>
        </div>
      </GemAppearancePreview>

      <DialogFooter className="bg-muted/30 flex-row items-center gap-2 px-5 py-3 sm:justify-between">
        <div className="min-w-0 flex-1">
          {error ? (
            <p className="text-destructive text-xs">{error}</p>
          ) : current ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => set({ face: undefined, hat: undefined, cut: undefined, color: undefined, avatar: undefined })}
              disabled={!draft.gems?.[key]}
            >
              <RotateCcw className="size-4" />
              Reset {name}
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

/** What a chat's Properties hold apart from the look: the hub switch, and the session's own facts. */
function ChatPanel({
  chat,
  hub,
  onHub,
}: {
  chat: NonNullable<StudioFocus["chat"]>;
  hub: boolean;
  onHub: (enabled: boolean) => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <label htmlFor="studio-hub" className="text-sm font-medium">
            Hub
          </label>
          <p className="text-muted-foreground text-[0.6875rem]">
            {chat.hub === null
              ? "This session has no running agent, so there is nothing to switch."
              : hub
                ? "On the hub: it can see and message your other agents."
                : "Off the hub: your other agents can't see it or message it. Your chat with it keeps working."}
          </p>
        </div>
        <Switch
          id="studio-hub"
          checked={hub}
          onCheckedChange={onHub}
          disabled={chat.hub === null}
        />
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 rounded-lg bg-muted/40 p-3 text-xs">
        <dt className="text-muted-foreground">Session</dt>
        <dd className="truncate">
          {formatSessionName(chat.session.name, chat.session.session_id)}
        </dd>
        <dt className="text-muted-foreground">Model</dt>
        <dd className="truncate">{chat.session.model || chat.session.profile || "Default"}</dd>
        <dt className="text-muted-foreground">Workspace</dt>
        <dd className="truncate" title={chat.session.workspace ?? undefined}>
          {chat.session.workspace || "Default"}
        </dd>
      </dl>
    </div>
  );
}

function ShapeTile({
  active,
  onClick,
  label,
  agent,
  where,
  cut,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  agent: AgentPoolEntry;
  where?: string | null;
  cut: GemCut;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`${label} Shape`}
      onClick={onClick}
      className={cn(
        "flex flex-col items-center gap-1 rounded-lg px-1 pt-3 pb-1.5 text-[0.6875rem] transition-colors",
        active ? "bg-accent" : "bg-muted/30 hover:bg-accent/50",
      )}
    >
      <GemAvatar gem={agent.name} where={where} caste={agent.caste} color={agent.color} cut={cut} state="idle" live season="auto" size={44} />
      <span className="mt-1.5">{label}</span>
    </button>
  );
}

function EyeTile({
  active,
  onClick,
  label,
  agent,
  where,
  face,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  agent: AgentPoolEntry;
  where?: string | null;
  face: EyeStyle;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`${label} Eyes`}
      onClick={onClick}
      className={cn(
        "flex flex-col items-center gap-1 rounded-lg px-1 pt-3 pb-1.5 text-[0.6875rem] transition-colors",
        active ? "bg-accent" : "bg-muted/30 hover:bg-accent/50",
      )}
    >
      <GemAvatar
        gem={agent.name}
        where={where}
        caste={agent.caste}
        color={agent.color}
        face={face}
        state="idle"
        live
        season="auto"
        size={44}
      />
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
        "aspect-square rounded-full transition-transform hover:scale-110",
        ring && "ring-muted-foreground/60 ring-2 ring-offset-2 ring-offset-background",
        active && "outline-primary outline-2 outline-offset-2",
      )}
    />
  );
}

/** A picked picture, shrunk to a square the appearance store keeps inline (the engine caps it at 200 KB). */
const PICTURE_SIZE = 256;

async function shrinkPicture(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const side = Math.min(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = PICTURE_SIZE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser cannot read pictures.");
  // Center crop to a square, as the tile shows it.
  ctx.drawImage(
    bitmap,
    (bitmap.width - side) / 2,
    (bitmap.height - side) / 2,
    side,
    side,
    0,
    0,
    PICTURE_SIZE,
    PICTURE_SIZE,
  );
  bitmap.close();
  const webp = canvas.toDataURL("image/webp", 0.85);
  // Safari draws no WebP and hands back a PNG: JPEG keeps it small.
  return webp.startsWith("data:image/webp") ? webp : canvas.toDataURL("image/jpeg", 0.85);
}

/** The Avatar tab: what the agent shows as, over what its roster drew it as. */
function AvatarPanel({
  agent,
  where,
  roster,
  picked,
  color,
  onPick,
}: {
  agent: AgentPoolEntry;
  where?: string | null;
  roster?: AvatarSpec | null;
  picked?: AvatarSpec;
  color?: readonly number[];
  onPick: (avatar: AvatarSpec | undefined) => void;
}) {
  const [problem, setProblem] = useState("");
  const [emoji, setEmoji] = useState(picked?.kind === "emoji" ? picked.value : "");
  const picture = picked?.kind === "image" ? picked : roster?.kind === "image" ? roster : null;
  const tile = (avatar: AvatarSpec | null | undefined) =>
    isGemAvatar(avatar) ? (
      <GemAvatar gem={agent.name} where={where} caste={agent.caste} color={agent.color} state="idle" live season="auto" size={44} />
    ) : (
      // No gem name: the tile draws what it is handed, not the agent's pick.
      <Avatar gem="" avatar={avatar} displayName={agentName(agent.name, agent.display_name)} color={color ? [...color] : agent.color} size={44} />
    );
  const options: { label: string; avatar: AvatarSpec | undefined; active: boolean }[] = [
    { label: "Roster", avatar: undefined, active: !picked },
    ...(isGemAvatar(roster) ? [] : [{ label: "Gem", avatar: { kind: "gem" } as AvatarSpec, active: picked?.kind === "gem" }]),
    { label: "Initials", avatar: { kind: "initials" }, active: picked?.kind === "initials" },
    { label: "Emoji", avatar: { kind: "emoji", value: emoji || "🙂" }, active: picked?.kind === "emoji" },
    ...(picture ? [{ label: "Picture", avatar: picture, active: picked?.kind === "image" }] : []),
  ];

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setProblem("");
    try {
      onPick({ kind: "image", url: await shrinkPicture(file) });
    } catch {
      setProblem("That file is not a picture this browser can read.");
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        {options.map((option) => (
          <button
            key={option.label}
            type="button"
            aria-pressed={option.active}
            onClick={() => onPick(option.avatar)}
            className={cn(
              "flex flex-col items-center gap-1 rounded-lg px-1 pt-3 pb-1.5 text-[0.6875rem] transition-colors",
              option.active ? "bg-accent" : "bg-muted/30 hover:bg-accent/50",
            )}
          >
            {tile(option.avatar ?? roster)}
            <span className="mt-1.5">{option.label}</span>
          </button>
        ))}
      </div>
      <label className="text-muted-foreground flex items-center gap-2 text-xs">
        <input
          aria-label="Emoji"
          value={emoji}
          maxLength={16}
          placeholder="🙂"
          onChange={(event) => {
            setEmoji(event.target.value);
            if (event.target.value.trim()) onPick({ kind: "emoji", value: event.target.value.trim() });
          }}
          className="bg-muted/60 text-foreground w-16 rounded-md px-2 py-1.5 text-center text-base"
        />
        Emoji
      </label>
      <label className="text-muted-foreground flex cursor-pointer items-center gap-2 text-xs">
        <span className="bg-muted/60 text-foreground hover:bg-accent shrink-0 rounded-md px-3 py-1.5 font-medium whitespace-nowrap transition-colors">
          Upload Picture
        </span>
        <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="sr-only" onChange={(event) => void upload(event)} />
        PNG, JPEG, WebP or GIF; cropped square.
      </label>
      {problem ? <p className="text-destructive text-[0.6875rem]">{problem}</p> : null}
      <p className="text-muted-foreground text-[0.6875rem]">
        The avatar shows wherever this agent does, in every browser. Roster is what {agentName(agent.name, agent.display_name)}'s roster drew.
      </p>
    </div>
  );
}
