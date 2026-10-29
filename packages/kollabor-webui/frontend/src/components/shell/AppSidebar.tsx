import { Fragment, useEffect, useMemo, useState, type ComponentProps, type ReactNode } from "react";
import { BookOpen, ChevronDown, Eye, EyeOff, LayoutGrid, Loader2, Newspaper, Palette, Plus, Settings2, SlidersHorizontal, Users } from "lucide-react";
import type { EngineApi, AgentBundleEntry, AgentNetwork, AgentPoolEntry, LiveAgent, OfflineAgent, Profile, Session } from "@/api";
import { AgentPicker, choiceLabel } from "@/components/agents/AgentPicker";
import { Avatar } from "@/components/agents/Avatar";
import type { LaunchChoice } from "@/components/agents/launch-choices";
import { presenceActivity } from "@/components/gems/activity";
import type { Activity } from "@/components/gems/gem-face";
import { agentStatus, bundleName, titleCase } from "@/components/panels/panel-model";
import { groupNetworkTree } from "@/components/shell/agent-network";
import { liveAgentOf } from "@/components/shell/live-agent";
import { KollabLogo } from "@/components/icons/kollab-logo";
import { CliBadge } from "@/components/shell/CliBadge";
import { AgentSidebarList, AgentSidebarRow, type AgentRowDrag } from "@/components/shell/AgentSidebarRow";
import { DeviceSidebarList } from "@/components/shell/DeviceSidebarList";
import { relayDetachedTouches } from "@/components/shell/touch-relay";
import { sidebarAgentKey } from "@/components/shell/hidden-agents";
import type { HiddenAgents } from "@/hooks/use-hidden-agents";
import type { SidebarOrder } from "@/hooks/use-sidebar-order";
import { HaltedLine } from "@/components/shell/HaltedLine";
import { buildLocalOrderRows, groupByFolder, orderedDevices, reorderDevicesWithinNetwork, reorderWithinFolder, orderKey, workspaceName } from "@/components/shell/sidebar-order";
import { WorkspacePicker } from "@/components/shell/WorkspacePicker";
import { HoverHelp } from "@/components/ui/hover-help";
import { lastRanLabel, since } from "@/last-ran";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { agentName, formatSessionName, sessionHeading } from "@/utils/session-display";
import { cliHarness } from "@/utils/cli-harness";
import { sessionFolder, sessionSubtitle } from "@/utils/session-subtitle";
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
  return <span className="text-destructive text-[0.8125rem] leading-snug break-words whitespace-normal">{text}</span>;
}

type LocalRow = { key: string; folder: string; session?: Session; offline?: OfflineAgent };

const FOLDED_KEY = "kollab.sidebar.folded";

/** A sidebar group its label folds away; the count stays in view while it is folded. */
function FoldGroup({
  label,
  count,
  open,
  onOpenChange,
  children,
}: {
  label: ReactNode;
  count?: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} className="group/fold">
      <SidebarGroup className="gap-1 py-1.5">
        <SidebarGroupLabel
          asChild
          className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground h-9 w-full cursor-pointer gap-1 px-2.5 text-xs text-sidebar-foreground/80 transition-colors"
        >
          <CollapsibleTrigger>
            <span className="flex min-w-0 flex-1 items-center overflow-hidden whitespace-nowrap">{label}</span>
            <span className="ml-auto flex shrink-0 items-center gap-1 pl-2">
              {count === undefined ? null : <span className="tabular-nums">{count}</span>}
              <ChevronDown className="size-3.5 transition-transform group-data-[state=closed]/fold:-rotate-90" />
            </span>
          </CollapsibleTrigger>
        </SidebarGroupLabel>
        <CollapsibleContent>{children}</CollapsibleContent>
      </SidebarGroup>
    </Collapsible>
  );
}

const FOOTER_BUTTON =
  "size-8 p-2 text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground md:size-7 md:p-1.5";

const ROW_CLASS = "h-auto gap-2.5 overflow-visible rounded-lg px-2.5 py-2.5 transition-colors";
const AVATAR_SIZE = 40;

export function AppSidebar({
  sessions,
  profiles,
  api,
  agents,
  liveAgents,
  launchChoices,
  network,
  offline,
  hiddenAgents,
  sidebarOrder,
  bundles,
  selectedProfile,
  selectedIdentity,
  selectedBundle,
  workspacePath,
  activeId,
  activeActivity,
  opening,
  busy,
  creationError,
  onProfileChange,
  onIdentityChange,
  onBundleChange,
  onWorkspaceChange,
  onApps,
  onAgents,
  onSettings,
  onHalt,
  onLearn,
  learnActive,
  onWhatsNew,
  whatsNewActive,
  onStudio,
  onManageProfiles,
  // Named `onSelectSession`, not `onSelect`: ComponentProps<typeof Sidebar>
  // already carries the DOM `onSelect` handler, and the collision widens the
  // callback argument to `string | SyntheticEvent`.
  onSelectSession,
  onSelectRemote,
  onStartOffline,
  onCreate,
  onDelete,
  onProperties,
  ...props
}: ComponentProps<typeof Sidebar> & {
  sessions: Session[];
  profiles: Profile[];
  /** For the workspace picker's folder lists. */
  api: EngineApi;
  agents: AgentPoolEntry[];
  /** Every running agent, one per process: a row finds its own by its instance, never by name. */
  liveAgents: LiveAgent[];
  /** What New Session can start in the chosen folder: its roster, then the other rosters (an agent running in another folder is free here). */
  launchChoices: LaunchChoice[];
  network: AgentNetwork;
  /** Agents that ran in this folder and are not running; a click starts one. */
  offline: OfflineAgent[];
  /** The rows hidden for every window of this engine, and how to change that. */
  hiddenAgents: HiddenAgents;
  /** The saved order of this computer's agents, kept by the engine. */
  sidebarOrder: SidebarOrder;
  bundles: AgentBundleEntry[];
  selectedProfile: string;
  selectedIdentity: string;
  selectedBundle: string;
  workspacePath: string;
  activeId: string | null;
  /** The open session's live action, read from its thread; other rows act out their presence's tool. */
  activeActivity?: Activity | null;
  /** The row being opened, and why it could not be. */
  opening?: { id: string; error?: string } | null;
  busy: boolean;
  creationError?: string | null;
  onProfileChange: (profile: string) => void;
  onIdentityChange: (identity: string) => void;
  onBundleChange: (bundle: string) => void;
  onWorkspaceChange: (path: string) => void;
  onApps: () => void;
  /** Opens the Agents view: every roster and team this folder can start. */
  onAgents: () => void;
  onSettings: () => void;
  /** Open Settings on the Halt and Resume screen (works with no chat open). */
  onHalt: () => void;
  onLearn: () => void;
  learnActive: boolean;
  onWhatsNew: () => void;
  whatsNewActive: boolean;
  /** Opens the Gem Studio; works without a session. */
  onStudio: () => void;
  onManageProfiles: () => void;
  /** Resolves true once the chat is open. */
  onSelectSession: (id: string) => Promise<boolean>;
  /** Opens an agent on another computer, first starting an agent here to carry the network if none is on it. */
  onSelectRemote: (handle: string) => Promise<boolean>;
  /** Starts an offline agent as itself; resolves true once its chat is open. */
  onStartOffline: (identity: string, workspace: string) => Promise<boolean>;
  onCreate: () => Promise<boolean>;
  onDelete: (id: string) => void;
  /** A session's Properties: right-click (long press) its row for the Chat tab, or double-click its gem. */
  onProperties: (id: string, tab?: "chat") => void;
}) {
  const { setOpenMobile } = useSidebar();
  const { hidden: hiddenList, error: hiddenError, hide, show, showAll } = hiddenAgents;
  const hiddenKeys = useMemo(() => new Set(hiddenList.map((agent) => agent.key)), [hiddenList]);
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

  useEffect(() => {
    if (learnActive || whatsNewActive) setOptionsOpen(false);
  }, [learnActive, whatsNewActive]);
  // A lifted row's clone replaces the element a touch started on; keep that touch reaching the drag.
  useEffect(() => relayDetachedTouches(window), []);
  // Every live agent on this computer is a session row (the engine lists them); these are the other computers'.
  // The #158 tree: NETWORKS > DEVICES > WORKSPACES > AGENTS across every network
  // this device belongs to; an engine without the aggregate synthesizes one
  // group from the flat rows, so the sidebar never regresses.
  const networkTree = useMemo(
    () => groupNetworkTree(network.networks, network.device, network.remote),
    [network.networks, network.device, network.remote],
  );
  const visibleNetworks = networkTree.map((net) => ({
    ...net,
    devices: orderedDevices(net.id, net.devices.map((dev) => ({
      ...dev,
      workspaces: dev.workspaces.map((ws) => {
        const agents = ws.agents.filter((agent) => !hiddenKeys.has(sidebarAgentKey({
          name: agent.name, device: agent.device || dev.device,
          workspace: agent.workspace || ws.label, network: agent.network || net.id,
        })));
        const folder = JSON.stringify(["remote", net.id, dev.device, ws.label || ""]);
        const rows = agents.map((agent) => ({
          key: orderKey(
            agent.workspace || ws.label,
            agent.handle || `${agent.name}@${agent.device || dev.device}`,
            ["remote", net.id, dev.device],
          ),
          folder,
          agent,
        }));
        const orderRows = groupByFolder(rows, sidebarOrder.order);
        return {
          ...ws,
          agents: orderRows.flatMap((group) => group.rows.map((row) => row.agent)),
          orderRows,
        };
      }).filter((ws) => ws.agents.length),
    })).filter((dev) => dev.workspaces.length), sidebarOrder.order),
  }));
  // Groups the viewer folded away, remembered in this browser only.
  const [folded, setFolded] = useState<Record<string, boolean>>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(FOLDED_KEY) || "{}");
      return saved && typeof saved === "object" ? saved : {};
    } catch {
      return {};
    }
  });
  const fold = (key: string, open: boolean) =>
    setFolded((prev) => {
      const next = { ...prev, [key]: !open };
      try {
        localStorage.setItem(FOLDED_KEY, JSON.stringify(next));
      } catch {
        // Blocked storage (a private window): the fold lasts this visit.
      }
      return next;
    });
  // An agent opened on another computer is a session too; it stays in its computer's group.
  const localSessions = useMemo(() => sessions.filter((session) => !session.device), [sessions]);
  // A running agent is hidden as the process it is, not as its name in a folder.
  const instanceOf = (session: Session) => session.instance || session.session_id;
  const sessionKey = (session: Session) => sidebarAgentKey({ instance: instanceOf(session) });
  const offlineKey = (agent: OfflineAgent) => sidebarAgentKey({ name: agent.identity, workspace: agent.workspace });
  // An offline row wears the persona the engine found for it, else the live pool's entry of that name.
  const offlineLook = (agent: OfflineAgent) => (agent.avatar ? agent : poolByName.get(agent.identity));
  const offlineName = (agent: OfflineAgent) => agentName(agent.identity, offlineLook(agent)?.display_name);
  const remoteName = (name: string) => agentName(name, poolByName.get(name)?.display_name);
  const visibleSessions = localSessions.filter((session) => !hiddenKeys.has(sessionKey(session)));
  const visibleOffline = offline.filter((agent) => !hiddenKeys.has(offlineKey(agent)));
  // This computer's rows grouped by folder, in the order the engine saved.
  // Duplicate live sessions have distinct session/conversation keys; persona
  // anchors keep order stable when a session becomes offline or its id rotates.
  const folderGroups = useMemo(
    () => groupByFolder<LocalRow>(buildLocalOrderRows(visibleSessions, visibleOffline, sidebarOrder.order), sidebarOrder.order),
    [visibleSessions, visibleOffline, sidebarOrder.order],
  );
  const moveRow = (key: string, to: number) => {
    const next = reorderWithinFolder(folderGroups, key, to, sidebarOrder.order);
    if (next) void sidebarOrder.save(next);
  };
  const pickedAgent = launchChoices.find((choice) => choice.ref === selectedIdentity);
  const identityLabel = pickedAgent ? choiceLabel(pickedAgent) : "Next Free Agent";
  const modelLabel =
    profiles.find((profile) => profile.name === selectedProfile)?.model ||
    selectedProfile ||
    "default";
  // On a phone the sidebar is a sheet over the chat: close it when the chat changes.
  const create = async () => {
    if (await onCreate()) {
      setOptionsOpen(false);
      setOpenMobile(false);
    } else {
      setOptionsOpen(true);
    }
  };

  const renderSession = (session: Session, drag: AgentRowDrag) => {
                  const gem = session.identity || session.agent || "";
                  // Hub presence when this very agent is live; otherwise the engine's
                  // own word on whether a daemon backs the session. Gem names repeat
                  // across folders (a koordinator per project), so presence is read
                  // by the row's instance and the pool only dresses the gem.
                  const agent = liveAgentOf(session, liveAgents);
                  const look = poolByName.get(session.identity || "");
                  const hubLive = Boolean(agent);
                  const live = hubLive || session.active !== false;
                  const task = agent?.state === "working" ? agent.current_task.trim() : "";
                  const folder = sessionFolder(session);
                  const heading = sessionHeading(session, poolByName.get(session.identity || "")?.display_name);
                  const harness = cliHarness(session);
                  const note = opening?.id === session.session_id ? opening : null;
                  const openProperties = (tab?: "chat") => {
                    setOpenMobile(false);
                    onProperties(session.session_id, tab);
                  };
                  return (
                  <AgentSidebarRow
                    key={drag.id}
                    name={heading}
                    onHide={() => hide({ key: sessionKey(session), label: heading, detail: session.workspace || workspacePath, instance: instanceOf(session) })}
                    onProperties={session.identity ? () => openProperties("chat") : undefined}
                    onDelete={session.external ? undefined : () => setPendingDelete(session)}
                    drag={drag}
                    busy={busy}
                  >
                        <SidebarMenuButton
                          isActive={session.session_id === activeId}
                          onClick={async () => {
                            // On a phone the menu stays open until the chat does, so
                            // the row's Opening… and any reason it failed stay in view.
                            if (await onSelectSession(session.session_id)) setOpenMobile(false);
                          }}
                          title={[heading, formatSessionName(session.name, session.session_id), session.harness, session.workspace, task].filter(Boolean).join(" · ")}
                          // overflow-visible: the gem canvas overhangs its box for hats and props.
                          className={ROW_CLASS}
                        >
                          <span
                            className="contents"
                            onDoubleClick={session.identity ? () => openProperties() : undefined}
                          >
                            <Avatar
                              gem={gem}
                              avatar={look?.avatar}
                              displayName={look?.display_name}
                              folder={session.device ? undefined : session.workspace}
                              where={session.device || session.workspace}
                              caste={look?.caste}
                              color={look?.color}
                              state={agent ? agent.state : live ? "idle" : "offline"}
                              live={live}
                              activity={session.session_id === activeId ? activeActivity : presenceActivity(agent?.current_tool)}
                              season="auto"
                              follow
                              size={AVATAR_SIZE}
                            />
                          </span>
                          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <span className="truncate text-sm leading-snug font-medium">
                              {heading}
                            </span>
                            <span className="text-muted-foreground flex min-w-0 items-center gap-1 truncate text-[0.8125rem] leading-snug">
                              {note && !note.error ? (
                                "Opening…"
                              ) : harness ? (
                                <CliBadge harness={harness} />
                              ) : task ? (
                                task
                              ) : (
                                sessionSubtitle(session, Boolean(agent?.solo))
                              )}
                            </span>
                            {task && harness ? (
                              <span className="text-muted-foreground truncate text-[0.8125rem] leading-snug" title={task}>{task}</span>
                            ) : null}
                            {session.acp_waiting && (
                              <span className="text-amber-600 dark:text-amber-400 truncate text-[0.8125rem] leading-snug">Waiting for Approval</span>
                            )}
                            {harness && (agent?.solo || folder) ? (
                              <span className="text-muted-foreground truncate text-[0.8125rem] leading-snug">
                                {[agent?.solo ? "Off Hub" : "", folder].filter(Boolean).join(" · ")}
                              </span>
                            ) : null}
                            {note?.error ? <OpenError text={note.error} /> : null}
                          </div>
                        </SidebarMenuButton>
                  </AgentSidebarRow>
                  );
                };
  const renderOffline = (agent: OfflineAgent, drag: AgentRowDrag) => (
                  <AgentSidebarRow
                    key={drag.id}
                    name={offlineName(agent)}
                    drag={drag}
                    onHide={() => hide({ key: offlineKey(agent), label: offlineName(agent), detail: agent.workspace })}
                  >
                    <SidebarMenuButton
                      disabled={busy}
                      onClick={async () => {
                        if (await onStartOffline(agent.identity, agent.workspace)) setOpenMobile(false);
                      }}
                      title={`Start ${offlineName(agent)} in ${agent.workspace}`}
                      className={ROW_CLASS}
                      data-testid={`offline-${agent.identity}`}
                    >
                      <Avatar
                        gem={agent.identity}
                        avatar={offlineLook(agent)?.avatar}
                        displayName={offlineLook(agent)?.display_name}
                        folder={agent.workspace}
                        where={agent.workspace}
                        caste={offlineLook(agent)?.caste}
                        color={offlineLook(agent)?.color}
                        state="offline"
                        live={false}
                        season="auto"
                        follow
                        size={AVATAR_SIZE}
                      />
                      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className="text-muted-foreground truncate text-sm leading-snug font-medium">
                          {offlineName(agent)}
                        </span>
                        <span className="text-muted-foreground truncate text-[0.8125rem] leading-snug">
                          Offline · {lastRanLabel(agent.last_ran)}
                        </span>
                      </div>
                    </SidebarMenuButton>
                  </AgentSidebarRow>
                );

  return (
    <Sidebar variant="floating" {...props}>
      {/* One row: the logo, then New Session and its options as icons. */}
      <SidebarHeader className="h-[var(--shell-header-height,3rem)] shrink-0 justify-center py-0">
        <div className="flex h-8 shrink-0 items-center gap-0.5 px-2">
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
              collisionPadding={16}
              className="w-80 p-0"
            >
              <div className="bg-foreground/[0.04] flex items-center gap-3 px-4 py-3">
                <Avatar
                  gem={pickedAgent?.name ?? ""}
                  avatar={pickedAgent?.avatar}
                  displayName={pickedAgent?.display_name}
                  folder={workspacePath.trim()}
                  caste={pickedAgent?.caste}
                  color={pickedAgent?.color}
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
                <Label htmlFor="new-session-gem" className="text-muted-foreground text-xs font-normal">
                  Agent
                </Label>
                <AgentPicker
                  id="new-session-gem"
                  choices={launchChoices}
                  value={selectedIdentity}
                  onChange={onIdentityChange}
                  folder={workspacePath.trim()}
                />
                <Label htmlFor="new-session-agent" className="text-muted-foreground text-xs font-normal">
                  Type
                </Label>
                <Select value={selectedBundle || "default"} onValueChange={onBundleChange}>
                  <SelectTrigger id="new-session-agent" size="sm" className="w-full">
                    <SelectValue placeholder={bundleName()} />
                  </SelectTrigger>
                  <SelectContent>
                    {bundleOptions.map((bundle) => (
                      <SelectItem key={bundle.name} value={bundle.name}>
                        {/* No profile suffix: a web session always sends the Model
                            field's profile, so the bundle's preferred one never
                            applies ("coder · default" also read as two agents). */}
                        {bundleName(bundle.name)}
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
                <WorkspacePicker api={api} value={workspacePath} onChange={onWorkspaceChange} />
              </div>
              {creationError ? (
                <div role="alert" className="text-destructive bg-destructive/10 px-4 py-3 text-xs leading-relaxed break-words whitespace-pre-wrap">
                  {creationError}
                </div>
              ) : null}
              <div className="bg-foreground/[0.04] flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
                <HoverHelp side="bottom" align="start" text="Opens your saved profiles. A profile picks the provider, model and endpoint a new session talks to.">
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
                </HoverHelp>
                <Button type="button" size="sm" disabled={busy} onClick={create}>
                  <Plus className="size-4" />
                  Start Session
                </Button>
              </div>
            </PopoverContent>
          </Popover>
        </div>
      </SidebarHeader>

      <SidebarContent className="gap-4">
        <FoldGroup
          label={
            <>
              <span className="shrink-0 whitespace-nowrap">This Computer</span>
              {network.device ? (
                <span className="text-muted-foreground ml-1 min-w-0 truncate font-normal" title={network.device}>
                  {`· ${network.device}`}
                </span>
              ) : null}
            </>
          }
          count={visibleSessions.length + visibleOffline.length}
          open={!folded.local}
          onOpenChange={(open) => fold("local", open)}
        >
          <SidebarGroupContent>
              {/* No folder picked yet: ask about the first listed folder (a halt holds the whole network). */}
              <HaltedLine api={api} folder={workspacePath.trim() || folderGroups[0]?.folder || ""} onOpen={onHalt} />
              {folderGroups.map((group) => (
                <Fragment key={group.folder || "-"}>
                  <div className="text-muted-foreground flex h-7 items-center gap-2 px-2.5 text-xs" title={group.folder}>
                    <span className="min-w-0 truncate">{workspaceName(group.folder)}</span>
                    <span className="ml-auto shrink-0 text-xs tabular-nums">{group.rows.length}</span>
                  </div>
                  <AgentSidebarList
                    id={JSON.stringify(["local", group.folder])}
                    beginDrag={sidebarOrder.beginDrag}
                    onReorder={moveRow}
                  >
                    {group.rows.map((row, index) => {
                      const drag: AgentRowDrag = { id: row.key, index, disabled: busy };
                      return row.session ? renderSession(row.session, drag) : renderOffline(row.offline!, drag);
                    })}
                  </AgentSidebarList>
                </Fragment>
              ))}
              {!visibleSessions.length && !visibleOffline.length ? (
                <p className="text-muted-foreground px-2 py-1 text-xs">
                  {localSessions.length || offline.length ? "No visible agents." : "No sessions yet."}
                </p>
              ) : null}
          </SidebarGroupContent>
        </FoldGroup>
        {visibleNetworks.map((net) => (
          <FoldGroup
            key={net.id}
            label={
              <>
                <span className="min-w-0 truncate">{net.name || "Network"}</span>
                <span className="text-muted-foreground ml-1 shrink-0 text-xs">
                  {net.name && net.trust ? ` ${net.trust}` : ""}
                </span>
              </>
            }
            count={net.devices.reduce((sum, dev) => sum + dev.workspaces.reduce((n, ws) => n + ws.agents.length, 0), 0)}
            open={!folded[`net:${net.id}`]}
            onOpenChange={(open) => fold(`net:${net.id}`, open)}
          >
            <SidebarGroupContent>
              {/* Full-width rows under small headers: nested sub-menus indented both sides and left the names a few letters wide. */}
              <DeviceSidebarList
                networkId={net.id}
                devices={net.devices}
                count={(dev) => dev.workspaces.reduce((sum, ws) => sum + ws.agents.length, 0)}
                beginDrag={sidebarOrder.beginDrag}
                onReorder={(key, to, devices) => {
                  const next = reorderDevicesWithinNetwork(net.id, devices, key, to, sidebarOrder.order);
                  if (next) void sidebarOrder.save(next);
                }}
              >
                {(dev) => dev.workspaces.map((ws) => (
                    <Fragment key={ws.label || "-"}>
                      {ws.label ? (
                        <p className="text-muted-foreground truncate px-2.5 text-xs">{ws.label}</p>
                      ) : null}
                      {ws.orderRows.map((group) => (
                      <AgentSidebarList
                        key={group.folder}
                        id={JSON.stringify(["remote", net.id, dev.device, ws.label, group.folder])}
                        beginDrag={sidebarOrder.beginDrag}
                        onReorder={(key, to) => {
                          const next = reorderWithinFolder(ws.orderRows, key, to, sidebarOrder.order);
                          if (next) void sidebarOrder.save(next);
                        }}
                      >
                        {group.rows.map((row, index) => {
                          const agent = row.agent;
                          // Opens through the network once that computer allows this one.
                          const id = agent.handle || "";
                          const note = id && opening?.id === id ? opening : null;
                          // Not up, or unknown: no local agent answered the network probe.
                          const up = (net.live !== false && network.live !== false) && agent.state !== "offline";
                          const seen = agent.last_seen ? `Seen ${since(agent.last_seen)}` : "";

                          return (
                          <AgentSidebarRow
                            key={row.key}
                            name={`${remoteName(agent.name)} on ${agent.device}`}
                            onHide={() => hide({
                              key: sidebarAgentKey({ name: agent.name, device: agent.device || dev.device, workspace: agent.workspace || ws.label, network: agent.network || net.id }),
                              label: remoteName(agent.name), detail: [agent.device || dev.device, ws.label, net.name].filter(Boolean).join(" · "),
                            })}
                            drag={{ id: row.key, index }}
                          >
                            <SidebarMenuButton
                              disabled={!id}
                              isActive={Boolean(id) && id === activeId}
                              onClick={async () => {
                                // Closes the phone menu only once it opened: a refusal shows here.
                                if (await onSelectRemote(id)) setOpenMobile(false);
                              }}
                              title={`Open ${remoteName(agent.name)} on ${agent.device}`}
                              className={ROW_CLASS}
                            >
                              <Avatar
                                gem={agent.name}
                                avatar={poolByName.get(agent.name)?.avatar}
                                displayName={poolByName.get(agent.name)?.display_name}
                                where={agent.device}
                                caste={poolByName.get(agent.name)?.caste}
                                color={poolByName.get(agent.name)?.color}
                                state={up ? agent.state || "idle" : "offline"}
                                live={up}
                                activity={id && id === activeId ? activeActivity : up ? presenceActivity(agent.current_tool) : null}
                                season="auto"
                                follow
                                size={AVATAR_SIZE}
                              />
                              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                                <span className="truncate text-sm leading-snug font-medium">
                                  {remoteName(agent.name)}
                                </span>
                                <span className="text-muted-foreground truncate text-[0.8125rem] leading-snug">
                                  {note && !note.error
                                    ? "Opening…"
                                    : agent.state === "offline"
                                      ? ["Offline", seen].filter(Boolean).join(" · ")
                                      : net.live === false || network.live === false
                                        ? seen || "Not Connected"
                                        : agentStatus(agent.state)}
                                </span>
                                {note?.error ? <OpenError text={note.error} /> : null}
                                <CliBadge harness={cliHarness({ harness: agent.harness })} className="text-muted-foreground text-[0.8125rem] leading-snug" />
                                {agent.acp_waiting && up ? (
                                  <span className="truncate text-[0.8125rem] leading-snug text-amber-600 dark:text-amber-400">Waiting for Approval</span>
                                ) : null}
                              </div>
                            </SidebarMenuButton>
                          </AgentSidebarRow>
                          );
                        })}
                      </AgentSidebarList>
                      ))}
                    </Fragment>
                ))}
              </DeviceSidebarList>
            </SidebarGroupContent>
          </FoldGroup>
        ))}
        {(network.contacts?.length ?? 0) > 0 ? (
          <FoldGroup
            label="Contacts"
            count={network.contacts?.length}
            open={!folded.contacts}
            onOpenChange={(open) => fold("contacts", open)}
          >
            <SidebarGroupContent>
              <SidebarMenu>
                {network.contacts?.map((contact) => (
                  <SidebarMenuItem key={contact.key}>
                    <SidebarMenuButton
                      disabled
                      className={`${ROW_CLASS} text-muted-foreground cursor-default`}
                      title={`Accepted contact (${contact.trust || "no trust"}): nothing is permitted until /connect allow`}
                    >
                      <Avatar
                        gem={contact.key}
                        where={contact.device || ""}
                        state="offline"
                        live={false}
                        season="auto"
                        follow
                        size={AVATAR_SIZE}
                      />
                      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className="truncate text-sm leading-snug font-medium">
                          {contact.label}
                        </span>
                        <span className="truncate text-[0.8125rem] leading-snug">
                          {contact.trust ? `${titleCase(contact.trust)} trust · ` : ""}
                          {"Ask /connect allow to work together"}
                        </span>
                      </div>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </FoldGroup>
        ) : null}
      </SidebarContent>

      <SidebarFooter className="@container p-2 md:px-1.5">
        {hiddenError ? (
          <p role="alert" className="text-destructive px-2 pb-1 text-xs break-words">{hiddenError}</p>
        ) : null}
        {sidebarOrder.warning ? (
          <p role="alert" className="text-destructive px-2 pb-1 text-xs break-words">
            Sidebar order needs recovery: {sidebarOrder.warning}
          </p>
        ) : null}
        {sidebarOrder.error ? (
          <p role="alert" className="text-destructive px-2 pb-1 text-xs break-words">
            Could not save sidebar order: {sidebarOrder.error}
          </p>
        ) : null}
        {/* Use one row when it fits, otherwise center four actions over three. */}
        <div
          data-sidebar="footer-actions"
          className="grid grid-cols-8 items-center justify-items-center gap-y-1 [&>button]:col-span-2 [&>button:nth-of-type(5)]:col-start-2 [&>button:nth-of-type(6)]:col-start-4 [&>button:nth-of-type(7)]:col-start-6 @min-[17rem]:flex @min-[17rem]:justify-between md:@min-[14rem]:flex md:@min-[14rem]:justify-between"
        >
          <Popover>
            <PopoverTrigger asChild>
              <TooltipIconButton data-sidebar-hide-target tooltip={hiddenList.length ? `Hidden Agents (${hiddenList.length})` : "Hidden Agents"} side="top" className={`${FOOTER_BUTTON} w-auto gap-1 px-2 md:px-1`}>
                <EyeOff className="size-4" />
                {hiddenList.length ? <span className="text-xs tabular-nums" aria-hidden>{hiddenList.length}</span> : null}
              </TooltipIconButton>
            </PopoverTrigger>
            <PopoverContent side="top" align="start" className="w-72 max-w-[calc(100vw-2rem)] p-3">
              <p className="text-sm font-semibold">Hidden Agents</p>
              <p className="text-muted-foreground mt-1 text-xs">Hidden in every window and browser.</p>
              {!hiddenList.length ? <p className="text-muted-foreground mt-2 text-xs">No hidden agents.</p> : null}
              <ul className="my-2 max-h-64 overflow-y-auto">
                {hiddenList.map((agent) => (
                  <li key={agent.key} className="flex items-center gap-2 py-1.5">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm">{agent.label}</p>
                      <p className="text-muted-foreground truncate text-xs" title={agent.detail}>{agent.detail}</p>
                    </div>
                    <Button size="sm" variant="ghost" aria-label={`Show ${agent.label}`} onClick={() => show(agent.key)}><Eye className="size-4" />Show</Button>
                  </li>
                ))}
              </ul>
              {hiddenList.length ? <Button size="sm" variant="outline" className="w-full" onClick={showAll}>Show All</Button> : null}
            </PopoverContent>
          </Popover>
          <TooltipIconButton
            tooltip="Apps"
            side="top"
            className={FOOTER_BUTTON}
            onClick={() => {
              setOpenMobile(false);
              onApps();
            }}
            disabled={!activeId}
          >
            <LayoutGrid className="size-4" />
          </TooltipIconButton>
          <TooltipIconButton
            tooltip="Agents"
            side="top"
            className={FOOTER_BUTTON}
            onClick={() => {
              setOpenMobile(false);
              onAgents();
            }}
          >
            <Users className="size-4" />
          </TooltipIconButton>
          <TooltipIconButton
            tooltip="Learn"
            side="top"
            className={`${FOOTER_BUTTON} ${learnActive ? "bg-sidebar-accent text-sidebar-accent-foreground" : ""}`}
            aria-current={learnActive ? "page" : undefined}
            onClick={() => {
              setOpenMobile(false);
              onLearn();
            }}
          >
            <BookOpen className="size-4" />
          </TooltipIconButton>
          <TooltipIconButton
            tooltip="What’s New"
            side="top"
            className={`${FOOTER_BUTTON} ${whatsNewActive ? "bg-sidebar-accent text-sidebar-accent-foreground" : ""}`}
            aria-current={whatsNewActive ? "page" : undefined}
            onClick={() => {
              setOpenMobile(false);
              onWhatsNew();
            }}
          >
            <Newspaper className="size-4" />
          </TooltipIconButton>
          <TooltipIconButton
            tooltip="Gem Studio"
            side="top"
            className={FOOTER_BUTTON}
            onClick={() => {
              // On a phone the sidebar is a sheet: close it under the studio.
              setOpenMobile(false);
              onStudio();
            }}
          >
            <Palette className="size-4" />
          </TooltipIconButton>
          <TooltipIconButton
            tooltip="Settings"
            side="top"
            className={FOOTER_BUTTON}
            onClick={() => {
              setOpenMobile(false);
              onSettings();
            }}
            disabled={!activeId}
          >
            <Settings2 className="size-4" />
          </TooltipIconButton>
        </div>
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
              {pendingDelete ? sessionHeading(pendingDelete, poolByName.get(pendingDelete.identity || "")?.display_name) : "This session"}{" "}
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
