import { useMemo, useState, type ComponentProps } from "react";
import { Loader2, Palette, Plus, Settings2, SlidersHorizontal, Trash2 } from "lucide-react";
import type { AgentBundleEntry, AgentNetwork, AgentPoolEntry, Profile, Session } from "@/api";
import { GemAvatar } from "@/components/gems/GemAvatar";
import type { Activity } from "@/components/gems/gem-face";
import { titleCase } from "@/components/panels/panel-model";
import { groupRemoteByDevice } from "@/components/shell/agent-network";
import { KollabLogo } from "@/components/icons/kollab-logo";
import { Button } from "@/components/ui/button";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatSessionName } from "@/utils/session-display";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";

/**
 * kollab session sidebar, built from the same shadcn `Sidebar` primitives the
 * assistant-ui kit uses for its own `threadlist-sidebar`. The kit's `ThreadList`
 * is driven by assistant-ui's ThreadListRuntime, which kollab does not use --
 * sessions come from the engine's /sessions endpoint -- so the data layer is
 * ours while the design language stays the kit's.
 */
/** Why a row did not open, in full: it can name the command that allows it. */
function OpenError({ text }: { text: string }) {
  return <span className="text-destructive text-[11px] leading-snug break-words whitespace-normal">{text}</span>;
}

export function AppSidebar({
  sessions,
  profiles,
  agents,
  network,
  bundles,
  selectedProfile,
  selectedIdentity,
  selectedBundle,
  workspacePath,
  activeId,
  activeActivity,
  opening,
  busy,
  onProfileChange,
  onIdentityChange,
  onBundleChange,
  onWorkspaceChange,
  onSettings,
  onStudio,
  onManageProfiles,
  // Named `onSelectSession`, not `onSelect`: ComponentProps<typeof Sidebar>
  // already carries the DOM `onSelect` handler, and the collision widens the
  // callback argument to `string | SyntheticEvent`.
  onSelectSession,
  onCreate,
  onDelete,
  onProperties,
  ...props
}: ComponentProps<typeof Sidebar> & {
  sessions: Session[];
  profiles: Profile[];
  agents: AgentPoolEntry[];
  network: AgentNetwork;
  bundles: AgentBundleEntry[];
  selectedProfile: string;
  selectedIdentity: string;
  selectedBundle: string;
  workspacePath: string;
  activeId: string | null;
  /** The open session's live action, read from its thread. */
  activeActivity?: Activity | null;
  /** The row being opened, and why it could not be. */
  opening?: { id: string; error?: string } | null;
  busy: boolean;
  onProfileChange: (profile: string) => void;
  onIdentityChange: (identity: string) => void;
  onBundleChange: (bundle: string) => void;
  onWorkspaceChange: (path: string) => void;
  onSettings: () => void;
  /** Opens the Gem Studio; works without a session. */
  onStudio: () => void;
  onManageProfiles: () => void;
  /** Resolves true once the chat is open. */
  onSelectSession: (id: string) => Promise<boolean>;
  onCreate: () => void;
  onDelete: (id: string) => void;
  /** A session's Properties: right-click (long press) its row for the Chat tab, or double-click its gem. */
  onProperties: (id: string, tab?: "chat") => void;
}) {
  const { setOpenMobile } = useSidebar();
  // Deleting a session stops its daemon and is irreversible, so it goes behind
  // an AlertDialog rather than the bare "x" the previous shell shipped.
  const [pendingDelete, setPendingDelete] = useState<Session | null>(null);
  const bundleOptions = useMemo(() => {
    const seen = new Set<string>();
    const options = bundles.filter((bundle) => {
      if (seen.has(bundle.name)) return false;
      seen.add(bundle.name);
      return true;
    });
    if (!seen.has("default")) options.unshift({ name: "default" });
    return options;
  }, [bundles]);
  const poolByName = useMemo(
    () => new Map(agents.map((agent) => [agent.name, agent])),
    [agents],
  );
  const [optionsOpen, setOptionsOpen] = useState(false);
  // Free gems first; the ones a live session holds stay listed, disabled.
  // Live agents outside the pool (pool: false) are shown, never launched as.
  const gemOptions = useMemo(
    () =>
      agents
        .filter((agent) => agent.pool !== false)
        .sort((a, b) => Number(Boolean(a.active)) - Number(Boolean(b.active))),
    [agents],
  );
  // Every live agent on this computer is a session row (the engine lists them); these are the other computers'.
  const remoteGroups = useMemo(() => groupRemoteByDevice(network.remote), [network.remote]);
  // An agent opened on another computer is a session too; it stays in its computer's group.
  const localSessions = useMemo(() => sessions.filter((session) => !session.device), [sessions]);
  const pickedGem = poolByName.get(selectedIdentity);
  const identityLabel = selectedIdentity ? titleCase(selectedIdentity) : "Next Free Gem";
  const modelLabel =
    profiles.find((profile) => profile.name === selectedProfile)?.model ||
    selectedProfile ||
    "default";
  // On a phone the sidebar is a sheet over the chat: close it when the chat changes.
  const create = () => {
    setOptionsOpen(false);
    setOpenMobile(false);
    onCreate();
  };

  return (
    <Sidebar {...props}>
      {/* One row: the logo, then New Session and its options as icons. */}
      <SidebarHeader className="border-b">
        <div className="flex h-12 items-center gap-0.5 px-2">
          <KollabLogo className="h-6 w-auto shrink-0" />
          <span className="sr-only">kollab</span>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={create}
                disabled={busy}
                aria-label="New Session"
                data-testid="new-session"
                className="bg-sidebar-accent text-sidebar-accent-foreground hover:bg-sidebar-accent/80 ml-auto size-8"
              >
                {busy ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {busy ? "Starting…" : `New Session: ${identityLabel} on ${modelLabel}`}
            </TooltipContent>
          </Tooltip>
          <Popover open={optionsOpen} onOpenChange={setOptionsOpen}>
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                disabled={busy}
                aria-label="Session Options"
                title="Session Options"
                data-testid="new-session-options"
                className="text-muted-foreground hover:text-foreground size-8 shrink-0"
              >
                <SlidersHorizontal className="size-4" />
              </Button>
            </PopoverTrigger>
            <PopoverContent
              side="bottom"
              align="start"
              collisionPadding={12}
              className="w-80 p-0"
            >
              <div className="flex items-center gap-3 border-b px-4 py-3">
                <GemAvatar
                  gem={selectedIdentity}
                  caste={pickedGem?.caste}
                  color={pickedGem?.color}
                  state="idle"
                  live
                  season="auto"
                  follow
                  size={40}
                />
                <div className="min-w-0">
                  <p className="text-sm font-medium">New Session</p>
                  <p className="text-muted-foreground truncate text-xs">
                    {identityLabel} · {modelLabel}
                  </p>
                </div>
              </div>
              <div className="grid grid-cols-[4.75rem_minmax(0,1fr)] items-center gap-x-3 gap-y-2.5 px-4 py-3">
                <Label htmlFor="new-session-agent" className="text-muted-foreground text-xs font-normal">
                  Agent
                </Label>
                <Select value={selectedBundle || "default"} onValueChange={onBundleChange}>
                  <SelectTrigger id="new-session-agent" size="sm" className="w-full">
                    <SelectValue placeholder="default" />
                  </SelectTrigger>
                  <SelectContent>
                    {bundleOptions.map((bundle) => (
                      <SelectItem key={bundle.name} value={bundle.name}>
                        {/* No profile suffix: a web session always sends the Model
                            field's profile, so the bundle's preferred one never
                            applies ("coder · default" also read as two agents). */}
                        {bundle.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Label htmlFor="new-session-gem" className="text-muted-foreground text-xs font-normal">
                  Gem
                </Label>
                <Select
                  value={selectedIdentity}
                  onValueChange={onIdentityChange}
                  disabled={!agents.length}
                >
                  <SelectTrigger id="new-session-gem" size="sm" className="w-full">
                    <SelectValue placeholder="Next Free Gem" />
                  </SelectTrigger>
                  <SelectContent>
                    {gemOptions.map((agent) => (
                      <SelectItem key={agent.name} value={agent.name} disabled={agent.active}>
                        {titleCase(agent.name)}
                        {agent.active ? " · In Use" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Label htmlFor="new-session-model" className="text-muted-foreground text-xs font-normal">
                  Model
                </Label>
                <Select
                  value={selectedProfile}
                  onValueChange={onProfileChange}
                  disabled={!profiles.length}
                >
                  <SelectTrigger id="new-session-model" size="sm" className="w-full">
                    <SelectValue placeholder="default" />
                  </SelectTrigger>
                  <SelectContent>
                    {profiles.map((profile) => (
                      <SelectItem key={profile.name} value={profile.name}>
                        <span className="min-w-0 truncate">
                          {profile.model || profile.name}
                          {profile.model && profile.name !== profile.model ? (
                            <span className="text-muted-foreground"> · {profile.name}</span>
                          ) : null}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Label htmlFor="new-session-workspace" className="text-muted-foreground text-xs font-normal">
                  Workspace
                </Label>
                <Input
                  id="new-session-workspace"
                  placeholder="Project folder"
                  value={workspacePath}
                  onChange={(event) => onWorkspaceChange(event.target.value)}
                  className="h-8 text-xs"
                />
              </div>
              <div className="flex items-center justify-between gap-2 border-t px-3 py-2.5">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setOptionsOpen(false);
                    onManageProfiles();
                  }}
                >
                  Manage Profiles
                </Button>
                <Button type="button" size="sm" disabled={busy} onClick={create}>
                  <Plus className="size-4" />
                  Start Session
                </Button>
              </div>
            </PopoverContent>
          </Popover>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>
            This Computer
            {network.device ? (
              <span className="text-muted-foreground ml-1 min-w-0 truncate font-normal">
                {`· ${network.device}`}
              </span>
            ) : null}
            <span className="ml-auto tabular-nums">{localSessions.length}</span>
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {localSessions.map((session) => {
                const gem = session.identity || session.agent || "";
                // Hub presence when the identity is live; otherwise the engine's
                // own word on whether a daemon backs the session.
                const pool = poolByName.get(session.identity || "");
                // Gem names repeat across folders (a koordinator per project), so
                // an agent started elsewhere reads only its own presence entry.
                const ownPool = !session.external || pool?.agent_id === session.session_id;
                const hubLive = ownPool && Boolean(pool?.active);
                const live = hubLive || session.active !== false;
                const task = hubLive && pool?.state === "working" ? pool.current_task?.trim() : "";
                const folder = session.workspace?.split("/").filter(Boolean).pop();
                const note = opening?.id === session.session_id ? opening : null;
                const openProperties = (tab?: "chat") => {
                  setOpenMobile(false);
                  onProperties(session.session_id, tab);
                };
                return (
                <ContextMenu key={session.session_id}>
                  <ContextMenuTrigger asChild>
                    <SidebarMenuItem>
                      <SidebarMenuButton
                        isActive={session.session_id === activeId}
                        onClick={async () => {
                          // On a phone the menu stays open until the chat does, so
                          // the row's Opening… and any reason it failed stay in view.
                          if (await onSelectSession(session.session_id)) setOpenMobile(false);
                        }}
                        title={
                          task
                            ? `${titleCase(gem)}: ${task}`
                            : (session.external && session.workspace) || session.session_id
                        }
                        // overflow-visible: the gem canvas overhangs its box for hats and props.
                        className="h-auto gap-3 overflow-visible py-2 pl-2.5"
                      >
                        <span
                          className="contents"
                          onDoubleClick={session.identity ? () => openProperties() : undefined}
                        >
                          <GemAvatar
                            gem={gem}
                            where={session.device || session.workspace}
                            caste={pool?.caste}
                            color={pool?.color}
                            state={hubLive ? pool?.state : live ? "idle" : "offline"}
                            live={live}
                            activity={session.session_id === activeId ? activeActivity : null}
                            season="auto"
                            follow
                            size={56}
                          />
                        </span>
                        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                          <span className="truncate text-[13px] leading-tight font-medium">
                            {formatSessionName(session.name, session.session_id)}
                          </span>
                          <span className="text-muted-foreground truncate text-[11px] leading-tight">
                            {note && !note.error ? (
                              "Opening…"
                            ) : task ? (
                              task
                            ) : session.external ? (
                              [titleCase(session.identity || ""), folder ? `In ${folder}` : "Running"]
                                .filter(Boolean)
                                .join(" · ")
                            ) : (
                              <>
                                {titleCase(session.identity || "") || session.agent || "Unassigned"} ·{" "}
                                {pool?.active && pool.solo ? "Off Hub · " : ""}
                                {session.model || session.profile || "default"}
                              </>
                            )}
                          </span>
                          {note?.error ? <OpenError text={note.error} /> : null}
                        </div>
                      </SidebarMenuButton>
                      {/* The engine only detaches from an agent it did not start: no Delete. */}
                      {session.external ? null : (
                        <SidebarMenuAction
                          onClick={() => setPendingDelete(session)}
                          disabled={busy}
                          showOnHover
                          aria-label={`Delete session ${session.session_id}`}
                        >
                          <Trash2 />
                        </SidebarMenuAction>
                      )}
                    </SidebarMenuItem>
                  </ContextMenuTrigger>
                  <ContextMenuContent className="w-44">
                    <ContextMenuItem disabled={!session.identity} onSelect={() => openProperties("chat")}>
                      <SlidersHorizontal />
                      Properties
                    </ContextMenuItem>
                    {session.external ? null : (
                      <>
                        <ContextMenuSeparator />
                        <ContextMenuItem
                          variant="destructive"
                          disabled={busy}
                          onSelect={() => setPendingDelete(session)}
                        >
                          <Trash2 />
                          Delete Session
                        </ContextMenuItem>
                      </>
                    )}
                  </ContextMenuContent>
                </ContextMenu>
                );
              })}
              {!localSessions.length ? (
                <p className="text-muted-foreground px-2 py-1 text-xs">
                  No sessions yet.
                </p>
              ) : null}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        {remoteGroups.map((group) => (
          <SidebarGroup key={group.device}>
            <SidebarGroupLabel>
              {group.device}
              <span className="ml-auto tabular-nums">{group.agents.length}</span>
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.agents.map((agent) => {
                  // Opens through the network once that computer allows this one.
                  const id = agent.handle || "";
                  const note = id && opening?.id === id ? opening : null;
                  return (
                  <SidebarMenuItem key={id || `${agent.name}@${agent.device}`}>
                    <SidebarMenuButton
                      disabled={!id}
                      isActive={Boolean(id) && id === activeId}
                      onClick={async () => {
                        // Closes the phone menu only once it opened: a refusal shows here.
                        if (await onSelectSession(id)) setOpenMobile(false);
                      }}
                      title={`Open ${titleCase(agent.name)} on ${agent.device}`}
                      className="h-auto gap-3 overflow-visible py-2 pl-2.5"
                    >
                      <GemAvatar
                        gem={agent.name}
                        where={agent.device}
                        caste={poolByName.get(agent.name)?.caste}
                        color={poolByName.get(agent.name)?.color}
                        state="idle"
                        live
                        activity={id && id === activeId ? activeActivity : null}
                        season="auto"
                        follow
                        size={56}
                      />
                      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className="truncate text-[13px] leading-tight font-medium">
                          {titleCase(agent.name)}
                        </span>
                        <span className="text-muted-foreground truncate text-[11px] leading-tight">
                          {note && !note.error ? "Opening…" : agent.state ? titleCase(agent.state) : "Online"}
                        </span>
                        {note?.error ? <OpenError text={note.error} /> : null}
                      </div>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter className="border-t p-2">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={() => {
                // On a phone the sidebar is a sheet: close it under the studio.
                setOpenMobile(false);
                onStudio();
              }}
            >
              <Palette className="size-4" />
              <span>Gem Studio</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={() => {
                setOpenMobile(false);
                onSettings();
              }}
              disabled={!activeId}
            >
              <Settings2 className="size-4" />
              <span>Settings</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete This Session?</AlertDialogTitle>
            <AlertDialogDescription>
              {formatSessionName(
                pendingDelete?.name,
                pendingDelete?.session_id,
              )}{" "}
              will be stopped and its conversation removed. This cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                // Read the id before clearing state; the dialog unmounts its
                // content on close and `pendingDelete` is null by the time an
                // async handler would otherwise read it.
                const id = pendingDelete?.session_id;
                setPendingDelete(null);
                if (id) onDelete(id);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sidebar>
  );
}
