import { refreshesOnEvent } from "./live-follow";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuiState } from "@assistant-ui/react";
import { Loader2, Plus, SlidersHorizontal, Waypoints } from "lucide-react";
import { Thread } from "./components/Thread";
import { TrajectoryView } from "./components/trajectory/TrajectoryView";
import { AppsDialog } from "@/components/apps/AppsDialog";
import { liveActivity, nextLiveActivity, NO_LIVE_ACTIVITY, useThreadActivity } from "@/components/gems/activity";
import { Avatar, AvatarImageProvider } from "@/components/agents/Avatar";
import { AgentLibrary } from "@/components/agents/AgentLibrary";
import { buildLaunchChoices, keepPick } from "@/components/agents/launch-choices";
import { GemAppearanceProvider } from "@/components/gems/gem-appearance";
import { GemStudio, type StudioFocus } from "@/components/gems/GemStudio";
import { KollabLogo } from "@/components/icons/kollab-logo";
import { titleCase } from "@/components/panels/panel-model";
import type { Activity } from "@/components/gems/gem-face";

import {
  contextCompactionStatusTitle,
  isUrgentContextCompactionStatus,
  parseContextCompactionStatus,
  type ContextCompactionStatus,
} from "./context-compaction-status";
import { AppSidebar } from "@/components/shell/AppSidebar";
import { networkOf, targetsOn } from "@/components/shell/agent-network";
import { liveAgentOf, poolSeenBy } from "@/components/shell/live-agent";
import { PanelHost } from "@/components/panels/PanelHost";
import type {
  PanelIntent,
  PanelOpenRequest,
  SettingsTab,
} from "@/components/panels/panel-model";
import { ProfilesDialog } from "@/components/shell/ProfilesDialog";
import { SetupGuide } from "@/setup/SetupGuide";
import { SessionPanel, type SessionView } from "@/components/shell/SessionPanel";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import {
  EngineApi,

  HISTORY_PAGE_SIZE,
  type HistoryPage,
  type AgentBundleEntry,
  type AgentNetwork,
  type OfflineAgent,
  type AgentPoolEntry,
  type LiveAgent,
  type NetworkAgent,
  DEFAULT_SLASH_COMMANDS,
  errorText,
  type Profile,
  type SlashCommand,
  type Session,
} from "./api";
import {
  EngineRuntimeProvider,
  buildInitialState,
  useEngineRuntimeState,
  type EngineState,
} from "./runtime";

import {
  historyPageCache,
  loadLatestHistoryPage,

  loadOlderHistoryPage,
  mergeOlderHistoryPage,
} from "./history-cache";
import { agentName, sessionHeading } from "@/utils/session-display";
import { useHiddenAgents } from "@/hooks/use-hidden-agents";
import { useSidebarOrder } from "@/hooks/use-sidebar-order";
import { chatToRestore } from "@/restore-chat";
import { useRosters } from "@/hooks/use-rosters";
import { LearnDialog } from "@/components/learn/LearnDialog";
import { WhatsNewPage } from "@/components/updates/WhatsNewPage";
import { navigateToLearn } from "@/learn/catalog";
import { navigateToWhatsNew, pagePath, readMainPageRoute } from "@/utils/page-route";

function waitForRetry(signal: AbortSignal, delayMs: number, timerRef: { current: number | null }) {
  return new Promise<void>((resolve) => {
    const finish = () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      signal.removeEventListener("abort", finish);
      resolve();
    };
    timerRef.current = window.setTimeout(finish, delayMs);
    signal.addEventListener("abort", finish, { once: true });
  });
}

const api = new EngineApi();

/** True until this browser recorded a guided-setup dismissal. */
const noGuideYet = () => {
  try {
    return window.localStorage.getItem("kollab.setup-guide.v1") === null;
  } catch {
    return false;
  }
};
const RESTART_COMMAND = /^\/(restart|new|clear)(\s|$)/i;

/** Keep context counts compact without rounding up the amount used. */
function formatContextTokens(value: number): string {
  return value < 1_000 ? String(Math.trunc(value)) : `${Math.trunc(value / 100) / 10}k`;
}

/** Harness cost, same rule as the terminal status bar (`_fmt_cost` in core_widgets.py). */
function formatCost(cost: { amount?: string | number; currency?: string }): string {
  const amount = Number(cost.amount);
  if (cost.currency !== "USD" || !Number.isFinite(amount)) return `${cost.amount} ${cost.currency ?? ""}`.trim();
  if (amount < 0.01) return "$0.00";
  return amount < 100 ? `$${amount.toFixed(2)}` : `$${amount.toFixed(0)}`;
}

/** True when the restored state still carries an unanswered permission prompt. */
function hasPendingPermission(state: EngineState): boolean {
  return state.messages.some(
    (message) =>
      message.role === "assistant" &&
      Array.isArray(message.content) &&
      message.content.some(
        (part) =>
          part.type === "tool-call" && part.toolName === "request_permission",
      ),
  );
}

function RuntimeShell({
  session,
  profiles,
  agents,
  liveAgents,
  remoteAgents,
  onSessionUpdated,
  onOpenSettings,
  onHistoryCleared,
  onActivity,
  eventActivity,
  onOpenProperties,
  refreshSignal,
  view,
  onViewChange: setView,
  locked,
  historyStatus,
  onLoadOlder,
  onRetryHistory,
  historyStartIndex,
}: {
  session: Session;
  profiles: Profile[];
  agents: AgentPoolEntry[];
  /** Every running agent, one per process: the chat reads its own by its instance, never by name. */
  liveAgents: LiveAgent[];
  /** Agents on the network's other computers (GET /sessions `network.remote`). */
  remoteAgents: NetworkAgent[];
  onSessionUpdated: (session: Session) => void;
  onOpenSettings: (request?: PanelOpenRequest) => void;
  /** Reloads this session's conversation after the engine cleared it. */
  onHistoryCleared: () => Promise<void>;
  /** Reports what this session's gem should act out in the sidebar. */
  onActivity: (activity: Activity | null) => void;
  /** What a turn this page did not start is doing, from the session's event stream. */
  eventActivity: Activity | null;
  /** This session's Properties (its gem, double-clicked). */
  onOpenProperties: () => void;
  refreshSignal: number;
  /** Held by App so a runtime remount (Clear history) keeps the open view. */
  view: SessionView;
  onViewChange: (view: SessionView) => void;
  /** Why the composer is closed while another chat starts or loads; empty when open. */
  locked: string;
  historyStatus: { kind: string; message: string } | undefined;
  onLoadOlder: () => Promise<void>;
  onRetryHistory: () => void;
  historyStartIndex: number;
}) {
  const runtimeState = useEngineRuntimeState();
  const [status, setStatus] = useState<string | null>(null);
  const [panelSheetOpen, setPanelSheetOpen] = useState(false);
  // An agent on another computer reads @names in that computer's mesh.
  // The #158 signature also narrows by network/workspace once rows carry them;
  // rows without those fields (pre-aggregate engines) ignore the filters.
  const targets = useMemo(
    () =>
      session.device
        ? targetsOn(session.device, remoteAgents, session.network, session.workspace ?? undefined)
        : { agents: poolSeenBy(agents, liveAgents, session), remote: remoteAgents },
    // The chat's own fields, not `session`: its object is new on every poll.
    [session.device, agents, liveAgents, remoteAgents, session.network, session.workspace, session.identity, session.instance],
  );
  const profile = profiles.find((item) => item.name === session.profile);
  const sessionLabel = sessionHeading(
    session,
    targets.agents.find((agent) => agent.name === session.identity)?.display_name,
  );
  // A typed /restart (/new, /clear) empties the daemon's conversation; once that
  // run ends, reset the thread the way the toolbar's Clear does. Only a run
  // seen in this mount counts, so the reloaded thread cannot loop.
  const running = useAuiState((s) => s.thread.isRunning);
  const lastPrompt = useAuiState((s) => {
    const part = s.thread.messages
      .filter((message) => message.role === "user")
      .at(-1)
      ?.parts.find((item) => item.type === "text");
    return part?.type === "text" ? part.text.trim() : "";
  });
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running && RESTART_COMMAND.test(lastPrompt)) {
      void onHistoryCleared();
    }
    wasRunning.current = running;
  }, [running, lastPrompt, onHistoryCleared]);
  const [commands, setCommands] = useState<SlashCommand[]>(
    DEFAULT_SLASH_COMMANDS,
  );
  // `thread.extras` is absent on first render; runtime.tsx guards the hook, and
  // this optional chain keeps App.tsx safe even if that guard is ever removed.
  const transportError = runtimeState?.state?.error;
  const activity = useThreadActivity(Boolean(transportError), eventActivity);
  const usageLabel = session.harness && session.acp_usage?.size !== undefined
    ? `${formatContextTokens(session.acp_usage.used ?? 0)}/${formatContextTokens(session.acp_usage.size)} Tokens${session.acp_usage.cost?.amount !== undefined ? ` · ${formatCost(session.acp_usage.cost)}` : ""}`
    : null;

  useEffect(() => onActivity(activity), [activity, onActivity]);
  useEffect(() => () => onActivity(null), [onActivity]);

  useEffect(() => {
    let mounted = true;
    setCommands(DEFAULT_SLASH_COMMANDS);
    void api
      .listCommands(session.session_id)
      .then((result) => {
        if (!mounted) return;
        setCommands(
          result.commands?.length ? result.commands : DEFAULT_SLASH_COMMANDS,
        );
      })
      .catch(() => {
        // Keep the first-paint compatibility catalog when an older daemon does
        // not expose the live registry yet.
      });
    return () => {
      mounted = false;
    };
  }, [session.session_id]);

  return (
    <>
      {/* Phones only: the sidebar and the session panel are sheets there. */}
      <ShellHeader className="bg-background flex min-h-12 shrink-0 items-center gap-2 px-3 py-1.5 md:hidden">
        <SidebarTrigger className="-ml-1 shrink-0" />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold tracking-tight" title={sessionLabel}>
          {sessionLabel}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label="Session Controls"
          className="h-8 shrink-0 gap-1.5 px-2"
          onClick={() => setPanelSheetOpen(true)}
        >
          <SlidersHorizontal className="size-4" />
          Session
        </Button>
      </ShellHeader>
      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {session.harness && Boolean(session.acp_plan?.entries?.length) && (
            <details className="bg-muted/30 px-3 py-2 text-xs">
              <summary className="cursor-pointer font-medium">Harness Plan</summary>
              <ol className="mt-2 space-y-1">
                {session.acp_plan?.entries?.map((entry, index) => (
                  <li key={index} className="flex gap-2">
                    <span className="text-muted-foreground shrink-0">{entry.status}</span>
                    <span className="min-w-0 break-words">{entry.content}</span>
                  </li>
                ))}
              </ol>
            </details>
          )}
          <div className="flex min-h-0 flex-1 flex-col">
            {view === "chat" ? (
              <Thread
                agents={targets.agents}
                commands={commands}
                onOpenPanel={onOpenSettings}
                attachmentsEnabled={
                  session.supports_vision ?? profile?.supports_vision ?? true
                }
                identity={session.identity}
                activity={activity}
                onOpenGem={onOpenProperties}
                remoteAgents={targets.remote}
                workspace={session.workspace ?? ""}
                device={session.device ?? ""}
                locked={locked}

                historyStatus={historyStatus}
                onLoadOlder={onLoadOlder}
                onRetryHistory={onRetryHistory}
                historyStartIndex={historyStartIndex}
              />
            ) : (
              <TrajectoryView api={api} sessionId={session.session_id} refreshSignal={refreshSignal} />
            )}
          </div>
        </div>
        <SessionPanel
          api={api}
          session={session}
          profiles={profiles}
          pool={targets.agents}
          usage={usageLabel}
          status={transportError || status}
          statusError={Boolean(transportError)}
          onStatus={setStatus}
          onSessionUpdated={onSessionUpdated}
          onOpenSettings={() => onOpenSettings()}
          onHistoryCleared={onHistoryCleared}
          view={view}
          onViewChange={setView}
          sheetOpen={panelSheetOpen}
          onSheetOpenChange={setPanelSheetOpen}
        />
      </div>
    </>
  );
}

export default function App({ contained = false }: { contained?: boolean } = {}) {
  const [pageRoute, setPageRoute] = useState(() => readMainPageRoute(window.location));
  const learnRoute = pageRoute.kind === "learn" ? pageRoute.learn : null;
  const whatsNewActive = pageRoute.kind === "whats-new";
  const pageOpen = pageRoute.kind !== "chat";
  const previousPageOpenRef = useRef(pageOpen);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [agents, setAgents] = useState<AgentPoolEntry[]>([]);
  const [liveAgents, setLiveAgents] = useState<LiveAgent[]>([]);
  // What New Session can start in the chosen folder: every folder has its own hub, so a
  // gem running elsewhere is free here. `agents` stays the whole computer's live state.
  const [launchPool, setLaunchPool] = useState<AgentPoolEntry[]>([]);
  const [network, setNetwork] = useState<AgentNetwork>({ device: "", remote: [] });
  const [offline, setOffline] = useState<OfflineAgent[]>([]);
  const hiddenAgents = useHiddenAgents(api);
  const sidebarOrder = useSidebarOrder(api);
  const [bundles, setBundles] = useState<AgentBundleEntry[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("default");
  const [selectedIdentity, setSelectedIdentity] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");
  const workspacePathRef = useRef(workspacePath);
  workspacePathRef.current = workspacePath;
  const [studioOpen, setStudioOpen] = useState(false);
  // Null: the whole Gem Studio; a gem: that session's Properties.
  const [studioFocus, setStudioFocus] = useState<StudioFocus | null>(null);
  const [profilesOpen, setProfilesOpen] = useState(false);
  const [appsOpen, setAppsOpen] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [setupGuideOpen, setSetupGuideOpen] = useState(false);
  // Settings (PanelHost): one open state for the sidebar button, the toolbar
  // button and the composer's /config-style commands.
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    const syncPageRoute = () => {
      const nextRoute = readMainPageRoute(window.location);
      setPageRoute(nextRoute);
      if (nextRoute.kind !== "chat") {
        setStudioOpen(false);
        setStudioFocus(null);
        setProfilesOpen(false);
        setAppsOpen(false);
        setAgentsOpen(false);
        setSetupGuideOpen(false);
        setSettingsOpen(false);
      }
    };
    window.addEventListener("popstate", syncPageRoute);
    return () => window.removeEventListener("popstate", syncPageRoute);
  }, []);

  useEffect(() => {
    const leavingPage = previousPageOpenRef.current && !pageOpen;
    previousPageOpenRef.current = pageOpen;
    if (leavingPage) {
      window.requestAnimationFrame(() => document.getElementById("app-main-pane")?.focus());
    }
  }, [pageOpen]);

  const returnToChat = useCallback(() => {
    if (readMainPageRoute(window.location).kind === "chat") return;
    window.history.pushState(null, "", pagePath("/", window.location.pathname));
    setPageRoute({ kind: "chat" });
  }, []);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("session");
  const [settingsIntent, setSettingsIntent] = useState<{
    action: PanelIntent;
    nonce: number;
  } | null>(null);
  const intentNonceRef = useRef(0);
  // The Type a new session runs, shown in New Session. Until the user picks
  // one it is the agent's own bundle: the hub moves an unpinned gem onto it
  // (plugins/hub/plugin.py, "Reconcile agent bundle to hub role"), so showing
  // "default" there was false.
  const [pickedBundle, setPickedBundle] = useState<string | null>(null);
  // Everyone the chosen folder can start: its own list, then the other rosters' personas.
  const { library } = useRosters(api, workspacePath.trim());
  const launchChoices = useMemo(() => buildLaunchChoices(launchPool, library), [launchPool, library]);
  const pickedAgent = launchChoices.find((choice) => choice.ref === selectedIdentity);
  const gemBundle = pickedAgent?.bundle;
  // A Type picked in one folder may not exist in the next (a project-local agent).
  const offered = (name?: string | null): name is string =>
    Boolean(name) && (name === "default" || bundles.some((bundle) => bundle.name === name));
  const selectedBundle =
    (offered(pickedBundle) ? pickedBundle : null) ?? (offered(gemBundle) ? gemBundle : "default");
  // What New Session will use, for the empty state's greeting.
  const nextGem = pickedAgent;
  const selectedModelLabel =
    profiles.find((profile) => profile.name === selectedProfile)?.model ||
    selectedProfile ||
    "default";
  const [activeId, setActiveId] = useState<string | null>(null);
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;
  const [busy, setBusy] = useState(true);
  const [busyMessage, setBusyMessage] = useState("Connecting to kollab…");
  const [error, setError] = useState<string | null>(null);
  const [creationError, setCreationError] = useState<string | null>(null);
  // The sidebar row being opened, and why it could not be: a failed open keeps
  // the chat that was open, so the reason shows under the row clicked.
  const [opening, setOpening] = useState<{ id: string; error?: string } | null>(null);
  const [initialState, setInitialState] = useState<EngineState | null>(null);

  const [historyStatus, setHistoryStatus] = useState<{
    sessionId: string;
    kind: "loading" | "loading-older" | "error" | "empty";
    message: string;
  } | null>(null);
  const historyPagesRef = useRef(new Map<string, HistoryPage>());
  type PendingPermissions = NonNullable<Parameters<typeof buildInitialState>[3]>;
  const historyContextRef = useRef(new Map<string, {
    sessions: Session[];
    pendingPermissions: PendingPermissions;
  }>());
  const olderLoadRef = useRef(new Set<string>());

  const olderLoadQueuedRef = useRef(new Set<string>());
  const loadOlderHistoryRef = useRef<((sessionId: string, prefetch?: boolean) => Promise<void>) | null>(null);
  const historyPrefetchRef = useRef(new Set<string>());
  const [refreshSignal, setRefreshSignal] = useState(0);
  // Bumped to remount the runtime: it reads its initial state only on mount.
  const [runtimeEpoch, setRuntimeEpoch] = useState(0);
  const [sessionView, setSessionView] = useState<SessionView>("chat");
  const [activeActivity, setActiveActivity] = useState<Activity | null>(null);
  // Folded from the open session's events, so a hub-driven turn still animates.
  const eventActivityRef = useRef(NO_LIVE_ACTIVITY);
  const [eventActivity, setEventActivity] = useState<Activity | null>(null);

  const [contextCompactionStatus, setContextCompactionStatus] =
    useState<ContextCompactionStatus | null>(null);
  const activeSession = useMemo(
    () => sessions.find((session) => session.session_id === activeId),
    [activeId, sessions],
  );
  const operationRef = useRef(0);
  // True once a chat is open or being opened, by the person or by the restore on load: the
  // restore never switches away from it, even when startup runs again (a saved edit's Fast Refresh).
  const openedRef = useRef(false);
  const recoveryRef = useRef<{
    sessionId: string;
    controller: AbortController;
  } | null>(null);
  const refreshSignalRef = useRef(0);
  // Sessions whose thread was built from the engine's disk seed (stale).
  const staleHistoryRef = useRef(new Set<string>());

  const abortRecovery = useCallback(() => {
    recoveryRef.current?.controller.abort();
    recoveryRef.current = null;
  }, []);

  // The persistent active-session stream below also covers pending turns after
  // reload, so do not open a second recovery stream for the same session.
  const recoverPendingTurn = useCallback(
    (_sessionId: string, _pending?: boolean) => {
      abortRecovery();
    },
    [abortRecovery],
  );

  useEffect(() => abortRecovery, [abortRecovery]);


  useEffect(() => {
    setContextCompactionStatus(null);
  }, [activeId]);

  const { stamp: stampHidden, sync: syncHidden } = hiddenAgents;
  const { stamp: stampOrder, sync: syncOrder } = sidebarOrder;
  const loadSessions = useCallback(async () => {
    const hiddenStamp = stampHidden();
    const orderStamp = stampOrder();
    const result = await api.listSessions();
    const next = result.sessions || [];
    setSessions(next);
    setNetwork(result.network ?? { device: "", remote: [] });
    setOffline(result.offline ?? []);
    syncHidden(hiddenStamp, result.hidden);
    syncOrder(orderStamp, {
      order: result.order ?? [],
      revision: result.order_revision ?? 0,
      warning: result.order_warning ?? null,
    });
    // keep the profile pickers (sidebar + new-session form + this dialog)
    // in sync after any CRUD operation
    api
      .listProfiles()
      .then((p) => setProfiles(p.profiles || []))
      .catch(() => {});
    return next;
  }, [stampHidden, syncHidden, stampOrder, syncOrder]);

  const applyLaunchPool = useCallback((next: AgentPoolEntry[]) => {
    setLaunchPool((current) => (JSON.stringify(current) === JSON.stringify(next) ? current : next));
  }, []);

  // An agent taken in the folder cannot be the one New Session starts, so the pick
  // falls to the first free one (the next free agent in that folder).
  useEffect(() => {
    setSelectedIdentity((current) => keepPick(current, launchChoices));
  }, [launchChoices]);

  const refreshAgentPool = useCallback(async () => {
    try {
      const [result, launch] = await Promise.all([
        api.listAgentPool(true),
        api.listLaunchPool(workspacePathRef.current.trim()),
      ]);
      const next = result.agents || [];
      setAgents(next);
      setLiveAgents(result.live || []);
      applyLaunchPool(launch.agents || []);
      return next;
    } catch {
      setAgents([]);
      setLiveAgents([]);
      setLaunchPool([]);
      return [];
    }
  }, [applyLaunchPool]);

  // Another folder lists other free agents: ask again when the pick changes.
  useEffect(() => {
    let stale = false;
    api
      .listLaunchPool(workspacePath.trim())
      .then((result) => {
        if (!stale) applyLaunchPool(result.agents || []);
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [workspacePath, applyLaunchPool]);

  // A folder's own .kollab/agents add Types the others lack: ask again when the pick changes.
  useEffect(() => {
    let stale = false;
    api
      .listAgentBundles(workspacePath.trim())
      .then((result) => {
        if (!stale) setBundles(result.bundles || []);
      })
      .catch(() => {
        if (!stale) setBundles([]);
      });
    return () => {
      stale = true;
    };
  }, [workspacePath]);

  const loadState = useCallback(
    async (sessionId: string, nextSessions: Session[]) => {
      setHistoryStatus({ sessionId, kind: "loading", message: "Loading conversation history…" });
      try {
        const [loaded, permissions] = await Promise.all([
          loadLatestHistoryPage(sessionId, api, historyPageCache),
          api.getPermissions(sessionId).catch(() => ({ pending_prompts: [] })),
        ]);
        const page = loaded.page;
        const pendingPermissions = permissions.pending_prompts || [];
        historyPagesRef.current.set(sessionId, page);
        historyContextRef.current.set(sessionId, { sessions: nextSessions, pendingPermissions });
        if (loaded.error) {
          staleHistoryRef.current.add(sessionId);
          setHistoryStatus({
            sessionId,
            kind: "error",
            message: "Couldn't refresh history; showing the saved conversation. Retry to update.",
          });
        } else if (page.stale) {
          staleHistoryRef.current.add(sessionId);
          setHistoryStatus({
            sessionId,
            kind: "loading",
            message: "Saved history is still syncing with the engine…",
          });
        } else {
          staleHistoryRef.current.delete(sessionId);
          setHistoryStatus(page.history.length ? null : {
            sessionId,
            kind: "empty",
            message: "No conversation messages yet.",
          });
        }
        return buildInitialState(
          sessionId,
          nextSessions,
          page.history,
          pendingPermissions,
          page.last_turn_error ?? null,
          page.start_index,
        );
      } catch (reason) {
        setHistoryStatus({
          sessionId,
          kind: "error",
          message: "Couldn't load conversation history. Check the connection and retry.",
        });
        throw reason;
      }
    },
    [],
  );

  const loadOlderHistory = useCallback(async (sessionId: string, prefetch = false) => {
    if (olderLoadRef.current.has(sessionId)) {
      if (!prefetch) olderLoadQueuedRef.current.add(sessionId);
      return;
    }
    const current = historyPagesRef.current.get(sessionId);
    if (!current || !current.has_more_older) return;
    olderLoadRef.current.add(sessionId);
    if (!prefetch && activeIdRef.current === sessionId) {
      setHistoryStatus({ sessionId, kind: "loading-older", message: "Loading older messages…" });
    }
    try {
      const older = await loadOlderHistoryPage(
        sessionId,
        current,
        api,
        historyPageCache,
      );
      if (!older) {
        throw new Error("History paging cursor is missing. Retry loading the conversation.");
      }
      if (!older.history.length) {
        const exhausted = { ...current, has_more_older: false, older_cursor: null };
        historyPagesRef.current.set(sessionId, exhausted);
        if (!prefetch && activeIdRef.current === sessionId) setHistoryStatus(null);
        return;
      }
      if (prefetch) return;

      const updated = mergeOlderHistoryPage(current, older);
      if (updated.start_index >= current.start_index) {
        throw new Error("The engine returned no older history messages.");
      }
      historyPagesRef.current.set(sessionId, updated);
      await historyPageCache.store(sessionId, updated);
      const context = historyContextRef.current.get(sessionId);
      if (context && activeIdRef.current === sessionId) {
        const nextState = buildInitialState(
          sessionId,
          context.sessions,
          updated.history,
          context.pendingPermissions,
          updated.last_turn_error ?? null,
          updated.start_index,
        );
        setInitialState((previous) =>
          previous?.sessionId === sessionId ? nextState : previous,
        );
      }
      if (activeIdRef.current === sessionId) setHistoryStatus(null);
    } catch {
      if (!prefetch && activeIdRef.current === sessionId) {
        setHistoryStatus({
          sessionId,
          kind: "error",
          message: "Couldn't load older messages. Scroll up or retry to try again.",
        });
      }
    } finally {
      olderLoadRef.current.delete(sessionId);
      if (
        olderLoadQueuedRef.current.delete(sessionId) &&
        activeIdRef.current === sessionId
      ) {
        void loadOlderHistoryRef.current?.(sessionId, false);
      }
    }
  }, []);
  loadOlderHistoryRef.current = loadOlderHistory;

  const retryHistory = useCallback(() => {
    const sessionId = activeIdRef.current;
    if (!sessionId) return;
    void loadState(sessionId, sessions).then((nextState) => {
      if (activeIdRef.current === sessionId) setInitialState(nextState);
    }).catch(() => {});
  }, [loadState, sessions]);


  useEffect(() => {
    if (!activeId || initialState?.sessionId !== activeId) return;
    const page = historyPagesRef.current.get(activeId);
    if (!page?.history.length || !page.has_more_older) return;
    const key = `${activeId}:${page.start_index}:${page.end_index}:${page.older_cursor ?? "end"}`;
    if (historyPrefetchRef.current.has(key)) return;
    historyPrefetchRef.current.add(key);
    const frame = window.requestAnimationFrame(() => {
      void loadOlderHistory(activeId, true);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeId, initialState?.sessionId, initialState?.synced?.history, initialState?.synced?.start, loadOlderHistory]);


  useEffect(() => {
    const controller = new AbortController();
    const retryTimerRef = { current: null as number | null };
    const refreshActiveState = async (sessionId: string) => {
      try {
        // A thread built from the engine's disk seed is no prefix of the
        // daemon's history, so the daemon's answer replaces it whole.
        const seeded = staleHistoryRef.current.has(sessionId);
        const nextSessions = await loadSessions();
        if (controller.signal.aborted || activeId !== sessionId) return;
        const nextState = await loadState(sessionId, nextSessions);
        if (controller.signal.aborted || activeId !== sessionId) return;
        // Still the seed: the daemon's answer comes as history_updated.
        if (seeded && staleHistoryRef.current.has(sessionId)) return;
        setInitialState(nextState);
        if (seeded) setRuntimeEpoch((epoch) => epoch + 1);
        refreshSignalRef.current += 1;
        setRefreshSignal(refreshSignalRef.current);
      } catch {
        // Retry on the next event or polling cycle.
      }
    };
    const resetEventActivity = () => {
      eventActivityRef.current = NO_LIVE_ACTIVITY;
      setEventActivity(null);
    };
    const followEvents = async () => {
      resetEventActivity();
      while (!controller.signal.aborted && activeId) {
        try {
          await api.streamEvents(activeId, controller.signal, (event) => {
            const type = event.type ?? "";

            const compactionStatus = parseContextCompactionStatus(event);
            if (compactionStatus !== undefined) {
              setContextCompactionStatus(compactionStatus);
            }
            // Same-value sets bail out, so a token stream does not re-render App.
            eventActivityRef.current = nextLiveActivity(eventActivityRef.current, event);
            setEventActivity(liveActivity(eventActivityRef.current));
            // hub_message / history_updated: another agent's or window's
            // message just landed in the history, mid-turn or not.
            // permission_request: a turn this page did not start (typed in the
            // agent's terminal) waits on a prompt this page can answer too.
            if (refreshesOnEvent(type)) void refreshActiveState(activeId);
          }, () => {
            // A reconnect may have missed the last turn's end.
            resetEventActivity();
            // Open, so nothing slips past from here. An event fired while the
            // stream was down (or before it existed, like the history_updated
            // that replaces a seed) left no trace: reconcile from history.
            void refreshActiveState(activeId);
          });
        } catch {
          if (controller.signal.aborted) break;
          await waitForRetry(controller.signal, 1000, retryTimerRef);
          continue;
        }
        await waitForRetry(controller.signal, 50, retryTimerRef);
      }
    };
    void followEvents();
    return () => {
      controller.abort();
      if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
    };
  }, [activeId, loadSessions, loadState]);

  useEffect(() => {
    const controller = new AbortController();
    const pollTimerRef = { current: null as number | null };
    const poll = async () => {
      while (!controller.signal.aborted) {
        try {
          const watched = activeIdRef.current;
          const next = await loadSessions();
          if (!controller.signal.aborted) {
            setSessions(next);
            // Deleted elsewhere (another tab, the CLI, a dead daemon): close it
            // instead of retrying its streams every second. Only when the open
            // session did not change mid-request, so a new one is never closed.
            if (watched && watched === activeIdRef.current && !next.some((item) => item.session_id === watched)) {
              abortRecovery();
              setActiveId(null);
              setInitialState(null);
            } else {
              setInitialState((state) => state ? { ...state, sessions: next } : state);
            }
          }
          // Live hub state for the sidebar gems; skip the update when nothing
          // moved so the chat does not re-render every poll.
          const { agents: pool = [], live: running = [] } = await api.listAgentPool(true);
          if (!controller.signal.aborted) {
            setAgents((current) => (JSON.stringify(current) === JSON.stringify(pool) ? current : pool));
            setLiveAgents((current) => (JSON.stringify(current) === JSON.stringify(running) ? current : running));
          }
          const launch = (await api.listLaunchPool(workspacePathRef.current.trim())).agents || [];
          if (!controller.signal.aborted) applyLaunchPool(launch);
        } catch {
          // Keep the last known sidebar while the daemon is unavailable.
        }
        await waitForRetry(controller.signal, 3000, pollTimerRef);
      }
    };
    void poll();
    return () => {
      controller.abort();
      if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
    };
  }, [abortRecovery, applyLaunchPool, loadSessions]);

  // A page served from an older (or newer) bundle than the running engine can
  // hit renamed API fields. Warn instead of failing; reload clears it.
  const [versionMismatch, setVersionMismatch] = useState<string | null>(null);
  const [updateInfo, setUpdateInfo] = useState<Awaited<ReturnType<EngineApi["checkForUpdate"]>> | null>(null);
  const [updateBusy, setUpdateBusy] = useState(false);

  useEffect(() => {
    let active = true;
    void api.checkForUpdate().then((result) => { if (active && result.available) setUpdateInfo(result); }).catch(() => {});
    return () => { active = false; };
  }, []);

  const applyAvailableUpdate = async () => {
    setUpdateBusy(true);
    try {
      const result = await api.applyUpdate();
      const desktop = (window as { kollabDesktop?: { update?: string } }).kollabDesktop;
      // Kollab.app updates whole: inside its window the app installs the update.
      if (result.method === "app" && desktop?.update) window.location.href = desktop.update;
      else if (result.success && result.changed) window.location.reload();
      else window.alert(result.message);
    } catch (error) {
      window.alert(errorText(error));
    } finally { setUpdateBusy(false); }
  };
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const config = await api.loadConfig();
        if (!mounted) return;
        const bundle = __APP_VERSION__;
        const engine = (config?.engine_version || "").trim();
        if (!engine || !bundle || engine === bundle) return;
        setVersionMismatch(
          `This page (v${bundle}) does not match the running engine (v${engine}). Reload after the engine finishes updating.`,
        );
      } catch {
        // The session bootstrap effect surfaces WebUI host/origin denials.
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    // Startup runs once: a run that finds a chat open or being opened leaves it alone.
    if (openedRef.current) return;
    let mounted = true;
    const operation = ++operationRef.current;
    (async () => {
      setBusy(true);
      setBusyMessage("Connecting to kollab…");
      await api.loadConfig();
      const [result, profileResult, agentResult] = await Promise.all([
        loadSessions(),
        api.listProfiles().catch(() => ({ profiles: [], active: undefined })),
        refreshAgentPool(),
      ]);
      if (!mounted || operation !== operationRef.current) return;
      const nextProfiles = profileResult.profiles || [];
      setProfiles(nextProfiles);
      const nextAgents = agentResult || [];
      setAgents(nextAgents);
      setSelectedProfile(
        profileResult.active || nextProfiles[0]?.name || "default",
      );
      // Restore this engine's newest chat unless the person has opened one.
      const first = chatToRestore(result, openedRef.current);
      if (first) {
        setBusyMessage("Restoring session…");
        const firstState = await loadState(first.session_id, result);
        if (!mounted || operation !== operationRef.current || openedRef.current) return;
        openedRef.current = true;
        setActiveId(first.session_id);
        setInitialState(firstState);
        recoverPendingTurn(first.session_id, hasPendingPermission(firstState));
      } else if (noGuideYet()) {
        // First run: nothing to restore and no dismissal on record.
        setSetupGuideOpen(true);
      }
      setError(null);
    })()
      .catch((reason) => {
        if (mounted && operation === operationRef.current) {
          setError(errorText(reason));
        }
      })
      .finally(() => {
        if (mounted && operation === operationRef.current) setBusy(false);
      });
    return () => {
      mounted = false;
    };
  }, [loadSessions, loadState, recoverPendingTurn, refreshAgentPool]);

  /** New Session's picks, or an offline agent's own (`pick`). Resolves true once its chat is open. */
  const createSession = async (pick: { identity?: string; agent?: string; workspace?: string } = {}): Promise<boolean> => {
    openedRef.current = true;
    abortRecovery();
    const operation = ++operationRef.current;
    setBusy(true);
    setBusyMessage(
      "Starting kollab (first start can take ~10 seconds)…",
    );
    setError(null);
    setCreationError(null);
    try {
      const session = await api.createSession({
        profile: selectedProfile || "default",
        // Pin what New Session shows. "default" is only a placeholder until
        // such a bundle exists (the first daemon start seeds it).
        agent: pick.identity
          ? pick.agent
          : bundles.some((bundle) => bundle.name === selectedBundle)
            ? selectedBundle
            : undefined,
        identity: pick.identity || selectedIdentity || undefined,
        approval_mode: "trust_all",
        workspace: pick.workspace || workspacePath.trim() || undefined,
      });
      const result = await loadSessions();
      await refreshAgentPool();
      if (operation !== operationRef.current) return false;
      const nextState = await loadState(session.session_id, result);
      if (operation !== operationRef.current) return false;
      setActiveId(session.session_id);
      setInitialState(nextState);
      recoverPendingTurn(session.session_id, hasPendingPermission(nextState));
      return true;
    } catch (reason) {
      if (operation === operationRef.current) {
        setError(errorText(reason));
        setCreationError(errorText(reason));
      }
    } finally {
      if (operation === operationRef.current) {
        setBusy(false);
        setBusyMessage("");
      }
    }
    return false;
  };

  /** Start an agent that ran here before, as itself: its vault brings back what it knew. */
  const startOffline = (identity: string, workspace: string) => {
    const bundle =
      identity === "koordinator" ? identity : agents.find((agent) => agent.name === identity)?.agent_type;
    return createSession({
      identity,
      agent: bundle && bundles.some((item) => item.name === bundle) ? bundle : undefined,
      workspace,
    });
  };

  /** Open an agent on another computer. With no agent here on its network, start the
   * koordinator of that network's folder to carry it first (GET /sessions `network`). */
  const selectRemote = async (handle: string): Promise<boolean> => {
    openedRef.current = true;
    const on = networkOf(network, handle);
    if (on.live) return selectSession(handle);
    setOpening({ id: handle });
    try {
      const carrier = {
        profile: selectedProfile || "default",
        approval_mode: "trust_all",
        workspace: on.folder || undefined,
      };
      await api
        .createSession({ ...carrier, identity: "koordinator", agent: "koordinator" })
        .catch(() => api.createSession(carrier));
      const deadline = Date.now() + 45_000;
      let liveSince = 0;
      while (Date.now() < deadline) {
        const result = await api.listSessions();
        const now = networkOf(result.network ?? { device: "", remote: [] }, handle);
        const remote = now.live ? now.agents : [];
        if (remote.some((agent) => agent.handle === handle && agent.state !== "offline")) {
          await loadSessions();
          return selectSession(handle);
        }
        // A carrier that just joined answers before its relay directory arrives (read every
        // 15 s, plugins/hub/relay_agent.py:remote_agents), and until then every saved agent
        // reads offline: only give up once it has had time to list (seen live: ~18 s).
        // ponytail: fixed grace; a "directory read" flag in network_status could end it early.
        if (now.live && !liveSince) liveSince = Date.now();
        if (liveSince && Date.now() - liveSince > 25_000 && remote.some((agent) => agent.handle === handle)) break;
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
      await loadSessions();
      setOpening({ id: handle, error: `${handle} is not on the network right now.` });
    } catch (reason) {
      setOpening({ id: handle, error: errorText(reason) });
    }
    return false;
  };

  const deleteSession = async (sessionId: string) => {
    openedRef.current = true;
    abortRecovery();
    const operation = ++operationRef.current;
    setBusy(true);
    setBusyMessage("Stopping session…");
    try {
      await api.deleteSession(sessionId);
      const result = await loadSessions();
      await refreshAgentPool();
      if (operation !== operationRef.current) return;
      const next = [...result].reverse().find((session) => !session.external);
      if (!next) {
        setActiveId(null);
        setInitialState(null);
      } else if (
        sessionId === activeId ||
        !result.some((item) => item.session_id === activeId)
      ) {
        // If the active session was removed, select the most recent remaining
        // session. Deleting any other session must not unexpectedly navigate
        // away from the conversation the user is currently viewing.
        const nextState = await loadState(next.session_id, result);
        if (operation !== operationRef.current) return;
        setActiveId(next.session_id);
        setInitialState(nextState);
        recoverPendingTurn(next.session_id, hasPendingPermission(nextState));
      } else {
        // Keep the active runtime and its history mounted; only refresh the
        // session list embedded in transport state.
        setInitialState((state) =>
          state ? { ...state, sessions: result } : state,
        );
        if (sessionId !== activeId) abortRecovery();
      }
    } catch (reason) {
      if (operation === operationRef.current) {
        setError(errorText(reason));
      }
    } finally {
      if (operation === operationRef.current) {
        setBusy(false);
        setBusyMessage("");
      }
    }
  };

  /** Resolves true once the chat is open (false if it failed or a newer click took over). */
  const selectSession = async (sessionId: string): Promise<boolean> => {
    openedRef.current = true;
    abortRecovery();
    const operation = ++operationRef.current;
    setBusy(true);
    setBusyMessage("Loading conversation…");
    setOpening({ id: sessionId });
    try {
      // An agent on another computer (agent@device) is listed once the engine
      // opens it, which asking for it does.
      const nextSessions = sessions.some((item) => item.session_id === sessionId)
        ? sessions
        : [...sessions, await api.getSession(sessionId)];
      const nextState = await loadState(sessionId, nextSessions);
      if (operation !== operationRef.current) return false;
      setSessions(nextSessions);
      setActiveId(sessionId);
      setInitialState(nextState);
      setOpening(null);
      recoverPendingTurn(sessionId, hasPendingPermission(nextState));
      return true;
    } catch (reason) {
      if (operation === operationRef.current) {
        setOpening({ id: sessionId, error: errorText(reason) });
      }
    } finally {
      if (operation === operationRef.current) {
        setBusy(false);
        setBusyMessage("");
      }
    }
    return false;
  };

  // The engine already emptied the conversation; reload it and remount the
  // runtime so the open thread resets in place.
  const resetThread = async (sessionId: string) => {
    const operation = ++operationRef.current;
    const result = await loadSessions();
    const nextState = await loadState(sessionId, result);
    if (operation !== operationRef.current) return;
    setInitialState(nextState);
    setRuntimeEpoch((epoch) => epoch + 1);
    refreshSignalRef.current += 1;
    setRefreshSignal(refreshSignalRef.current);
  };

  const openSettings = useCallback((request?: PanelOpenRequest) => {
    setSettingsTab(request?.tab ?? "session");
    setSettingsIntent(
      request?.intent
        ? { action: request.intent, nonce: ++intentNonceRef.current }
        : null,
    );
    setSettingsOpen(true);
  }, []);

  const openProperties = (sessionId: string, tab?: "chat") => {
    const session = sessions.find((item) => item.session_id === sessionId);
    const gem = session?.identity;
    if (!session || !gem) return;
    // Presence is the truth for the switch: whether the agent is live, and
    // whether it is on the mesh.
    const agent = liveAgentOf(session, liveAgents);
    setStudioFocus({
      gem,
      where: session.device || session.workspace,
      tab,
      chat: {
        session,
        hub: agent ? !agent.solo : null,
        setHub: async (enabled) => {
          const result = await api.setSessionHub(sessionId, enabled);
          // An engine that predates the switch answers ok and ignores it.
          if (result.hub !== enabled) throw new Error("Restart kollab --web-ui to use this switch.");
          await refreshAgentPool();
        },
      },
    });
    setStudioOpen(true);
  };

  const handleSessionUpdated = useCallback((updated: Session) => {
    setSessions((current) =>
      current.map((item) =>
        item.session_id === updated.session_id ? updated : item,
      ),
    );
  }, []);

  const aliveKey = agents.filter((agent) => agent.active).map((agent) => agent.name).join(",");

  return (
    <GemAppearanceProvider api={api} refreshKey={aliveKey}>
      <AvatarImageProvider api={api}>
      <SidebarProvider className={`bg-muted/40 ${contained ? "h-full min-h-0" : ""}`}>
        <AppSidebar
          learnActive={learnRoute !== null}
          whatsNewActive={whatsNewActive}
          sessions={sessions}
          profiles={profiles}
          api={api}
          agents={agents}
          liveAgents={liveAgents}
          launchChoices={launchChoices}
          network={network}
          offline={offline}
          hiddenAgents={hiddenAgents}
          sidebarOrder={sidebarOrder}
          bundles={bundles}
          selectedProfile={selectedProfile}
          selectedIdentity={selectedIdentity}
          workspacePath={workspacePath}
          onWorkspaceChange={setWorkspacePath}
          selectedBundle={selectedBundle}
          activeId={activeId}
          activeActivity={activeActivity}
          opening={opening}
          busy={busy}
          creationError={creationError}
          onProfileChange={setSelectedProfile}
          onIdentityChange={(ref) => {
            // A new pick brings its own Type along.
            setSelectedIdentity(ref);
            setPickedBundle(null);
          }}
          onBundleChange={setPickedBundle}
          onApps={() => setAppsOpen(true)}
          onAgents={() => setAgentsOpen(true)}
          onSettings={() => openSettings()}
          onLearn={() => navigateToLearn()}
          onWhatsNew={navigateToWhatsNew}
          onStudio={() => {
            setStudioFocus(null);
            setStudioOpen(true);
          }}
          onManageProfiles={() => setProfilesOpen(true)}
          onSelectSession={(sessionId) => {
            returnToChat();
            return selectSession(sessionId);
          }}
          onSelectRemote={(...args) => {
            returnToChat();
            return selectRemote(...args);
          }}
          onStartOffline={(...args) => {
            returnToChat();
            return startOffline(...args);
          }}
          onCreate={() => {
            returnToChat();
            return createSession();
          }}
          onDelete={(id) => void deleteSession(id)}
          onProperties={openProperties}
        />
        <GemStudio open={studioOpen} onOpenChange={setStudioOpen} agents={agents} focus={studioFocus} />
        <AgentLibrary
          api={api}
          open={agentsOpen}
          onOpenChange={setAgentsOpen}
          workspace={workspacePath.trim()}
          profile={selectedProfile}
          onLaunched={() => {
            void loadSessions();
            void refreshAgentPool();
          }}
        />
        <SetupGuide
          api={api}
          open={setupGuideOpen}
          onOpenChange={setSetupGuideOpen}
          onOpenSettings={(tab) => openSettings({ tab })}
          onStartSession={() => void createSession()}
        />
        {activeSession ? (
          <AppsDialog
            api={api}
            sessionId={activeSession.session_id}
            open={appsOpen}
            onOpenChange={setAppsOpen}
            onChanged={() => void loadSessions()}
          />
        ) : null}
        <ProfilesDialog
          api={api}
          profiles={profiles}
          open={profilesOpen}
          onOpenChange={setProfilesOpen}
          onSaved={async () => {
            await loadSessions();
          }}
        />
        {activeSession ? (
          <PanelHost
            key={activeSession.session_id}
            api={api}
            session={activeSession}
            displayName={agents.find((agent) => agent.name === activeSession.identity)?.display_name}
            profiles={profiles}
            open={settingsOpen}
            onOpenChange={setSettingsOpen}
            tab={settingsTab}
            onTabChange={setSettingsTab}
            intent={settingsIntent}
            onChanged={() => void loadSessions()}
            onSessionUpdated={handleSessionUpdated}
          />
        ) : null}
        {learnRoute ? <LearnDialog route={learnRoute} onNavigate={navigateToLearn} onExit={returnToChat} /> : null}
        <SidebarInset
          id="app-main-pane"
          tabIndex={-1}
          className={`${contained ? "h-[calc(100%-1rem)] max-h-[calc(100%-1rem)]" : "h-[calc(100svh-1rem)] max-h-[calc(100svh-1rem)]"} min-h-0 overflow-hidden focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring`}
        >
          {whatsNewActive ? <WhatsNewPage onExit={returnToChat} /> : null}
          <div className={whatsNewActive ? "hidden" : "contents"}>
          {versionMismatch ? (
            <div
              role="status"
              className="bg-amber-500/10 text-amber-600 dark:text-amber-400 px-4 py-2 text-sm"
            >
              {versionMismatch}
            </div>
          ) : null}
          {updateInfo?.release ? (
            <div role="status" className="bg-primary/5 flex flex-wrap items-center gap-2 px-4 py-2 text-sm">
              <span>Update available: v{updateInfo.release.version}</span>
              <a className="underline" href={updateInfo.release.url} target="_blank" rel="noreferrer">Release notes</a>
              <button className="bg-primary/10 hover:bg-primary/20 rounded-md px-2 py-0.5 text-xs font-medium" disabled={updateBusy} onClick={() => void applyAvailableUpdate()}>{updateBusy ? "Updating…" : "Update now"}</button>
            </div>
          ) : null}
          {contextCompactionStatus ? (
            <div
              role={
                isUrgentContextCompactionStatus(contextCompactionStatus.status)
                  ? "alert"
                  : "status"
              }
              aria-live={
                isUrgentContextCompactionStatus(contextCompactionStatus.status)
                  ? "assertive"
                  : "polite"
              }
              className="bg-amber-500/10 text-amber-700 dark:text-amber-300 flex flex-wrap items-baseline gap-x-2 gap-y-1 px-4 py-2 text-sm"
            >
              <strong>
                {contextCompactionStatusTitle(contextCompactionStatus.status)}
              </strong>
              <span>{contextCompactionStatus.message}</span>
              {contextCompactionStatus.action ? (
                <span className="font-medium">{contextCompactionStatus.action}</span>
              ) : null}
            </div>
          ) : null}
          {activeSession && initialState ? (
            <EngineRuntimeProvider
              key={`${activeId}:${runtimeEpoch}`}
              api={api}
              sessionId={activeSession.session_id}
              initialState={initialState}
              onStale={() => void resetThread(activeSession.session_id)}
            >
              <RuntimeShell
                session={activeSession}
                profiles={profiles}
                agents={agents}
                liveAgents={liveAgents}
                remoteAgents={network.remote}
                refreshSignal={refreshSignal}
                onSessionUpdated={handleSessionUpdated}
                onOpenSettings={openSettings}
                onHistoryCleared={() => resetThread(activeSession.session_id)}
                onActivity={setActiveActivity}
                eventActivity={eventActivity}
                onOpenProperties={() => openProperties(activeSession.session_id)}
                view={sessionView}
                onViewChange={setSessionView}
                locked={busy ? busyMessage || "Loading…" : ""}

                historyStatus={historyStatus?.sessionId === activeSession.session_id
                  ? { kind: historyStatus.kind, message: historyStatus.message }
                  : undefined}
                onLoadOlder={() => loadOlderHistory(activeSession.session_id)}
                onRetryHistory={retryHistory}
                historyStartIndex={initialState.synced?.start ?? 0}
              />
            </EngineRuntimeProvider>
          ) : (
            <>
              <ShellHeader className="flex min-h-12 shrink-0 items-center gap-2 px-3 md:hidden">
                <SidebarTrigger className="-ml-1" />
                <span className="text-muted-foreground text-sm">No session</span>
              </ShellHeader>
              {/* The next free gem greets here, as it will once its session starts. */}
              <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
                {nextGem ? (
                  <Avatar
                    gem={nextGem.name}
                    avatar={nextGem.avatar}
                    displayName={nextGem.display_name}
                    folder={workspacePath.trim()}
                    caste={nextGem.caste}
                    color={nextGem.color}
                    state={busy ? "working" : "idle"}
                    live
                    season="auto"
                    follow
                    size={112}
                    label={agentName(nextGem.name, nextGem.display_name)}
                  />
                ) : (
                  <KollabLogo className="text-foreground/80 mb-2 h-10 w-auto" />
                )}
                <h1 className="text-2xl font-semibold tracking-tight">
                  {nextGem ? agentName(nextGem.name, nextGem.display_name) : "Start a Session"}
                </h1>
                <p
                  className={
                    error
                      ? "text-destructive max-w-md text-sm"
                      : "text-muted-foreground max-w-md text-sm"
                  }
                >
                  {error ||
                    (busy
                      ? busyMessage
                      : `Ready on ${selectedModelLabel}. Start a session to begin.`)}
                </p>
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <Button
                    type="button"
                    onClick={() => void createSession()}
                    disabled={busy}
                  >
                    {busy ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
                    {busy ? "Starting…" : "Start Session"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    data-testid="guided-setup-open"
                    onClick={() => setSetupGuideOpen(true)}
                    disabled={busy}
                  >
                    <Waypoints className="size-4" />
                    Guided setup
                  </Button>
                </div>
              </div>
            </>
          )}
          </div>
        </SidebarInset>
      </SidebarProvider>
      </AvatarImageProvider>
    </GemAppearanceProvider>
  );
}
