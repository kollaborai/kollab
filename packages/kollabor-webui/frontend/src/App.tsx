import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuiState } from "@assistant-ui/react";
import { Thread } from "./components/Thread";
import { TrajectoryView } from "./components/trajectory/TrajectoryView";
import { useThreadActivity } from "@/components/gems/activity";
import type { Activity } from "@/components/gems/gem-face";
import { AppSidebar } from "@/components/shell/AppSidebar";
import { PanelHost } from "@/components/panels/PanelHost";
import type {
  PanelIntent,
  PanelOpenRequest,
  SettingsTab,
} from "@/components/panels/panel-model";
import { ProfilesDialog } from "@/components/shell/ProfilesDialog";
import { SessionToolbar } from "@/components/shell/SessionToolbar";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import {
  EngineApi,
  type AgentBundleEntry,
  type AgentPoolEntry,
  DEFAULT_SLASH_COMMANDS,
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
import { formatSessionName } from "@/utils/session-display";

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
type SessionView = "chat" | "trajectory";
const RESTART_COMMAND = /^\/(restart|new|clear)(\s|$)/i;

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
  onSessionUpdated,
  onOpenSettings,
  onHistoryCleared,
  onActivity,
  refreshSignal,
  view,
  onViewChange: setView,
}: {
  session: Session;
  profiles: Profile[];
  agents: AgentPoolEntry[];
  onSessionUpdated: (session: Session) => void;
  onOpenSettings: (request?: PanelOpenRequest) => void;
  /** Reloads this session's conversation after the engine cleared it. */
  onHistoryCleared: () => Promise<void>;
  /** Reports what this session's gem should act out in the sidebar. */
  onActivity: (activity: Activity | null) => void;
  refreshSignal: number;
  /** Held by App so a runtime remount (Clear history) keeps the open view. */
  view: SessionView;
  onViewChange: (view: SessionView) => void;
}) {
  const runtimeState = useEngineRuntimeState();
  const [status, setStatus] = useState<string | null>(null);
  const profile = profiles.find((item) => item.name === session.profile);
  const model = session.model || profile?.model;
  const sessionLabel = formatSessionName(session.name, session.session_id);
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
  const activity = useThreadActivity(Boolean(transportError));

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
      <header className="bg-background sticky top-0 z-10 flex shrink-0 flex-col gap-2 border-b px-3 py-2">
        <div className="flex items-center gap-2">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 h-4" />
          <div className="flex min-w-0 flex-col">
            <span className="truncate font-mono text-sm font-medium">
              {sessionLabel}
            </span>
            <span
              className="text-muted-foreground truncate text-xs"
              title={model || session.profile || "default"}
            >
              {model || "model unavailable"} · {session.profile || "default"}
            </span>
          </div>
          <div
            className="bg-muted flex rounded-md p-0.5"
            role="group"
            aria-label="Session view"
          >
            <Button
              type="button"
              variant={view === "chat" ? "secondary" : "ghost"}
              size="xs"
              aria-pressed={view === "chat"}
              data-testid="chat-tab"
              onClick={() => setView("chat")}
            >
              Chat
            </Button>
            <Button
              type="button"
              variant={view === "trajectory" ? "secondary" : "ghost"}
              size="xs"
              aria-pressed={view === "trajectory"}
              data-testid="trajectory-tab"
              onClick={() => setView("trajectory")}
            >
              Trajectory
            </Button>
          </div>
          <span
            className={
              transportError
                ? "text-destructive ml-auto truncate text-xs"
                : "text-muted-foreground ml-auto truncate text-xs"
            }
          >
            {status || transportError || null}
          </span>
        </div>
        <SessionToolbar
          api={api}
          session={session}
          profiles={profiles}
          onStatus={setStatus}
          onSessionUpdated={onSessionUpdated}
          onOpenSettings={() => onOpenSettings()}
          onHistoryCleared={onHistoryCleared}
        />
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        {view === "chat" ? (
          <Thread
            agents={agents}
            commands={commands}
            onOpenPanel={onOpenSettings}
            attachmentsEnabled={
              session.supports_vision ?? profile?.supports_vision ?? true
            }
            identity={session.identity}
          />
        ) : (
          <TrajectoryView api={api} sessionId={session.session_id} refreshSignal={refreshSignal} />
        )}
      </div>
    </>
  );
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [agents, setAgents] = useState<AgentPoolEntry[]>([]);
  const [bundles, setBundles] = useState<AgentBundleEntry[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("default");
  const [selectedIdentity, setSelectedIdentity] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");
  const [profilesOpen, setProfilesOpen] = useState(false);
  // Settings (PanelHost): one open state for the sidebar button, the toolbar
  // button and the composer's /config-style commands.
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("session");
  const [settingsIntent, setSettingsIntent] = useState<{
    action: PanelIntent;
    nonce: number;
  } | null>(null);
  const intentNonceRef = useRef(0);
  const [selectedBundle, setSelectedBundle] = useState("default");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [busyMessage, setBusyMessage] = useState("Connecting to the engine…");
  const [error, setError] = useState<string | null>(null);
  const [initialState, setInitialState] = useState<EngineState | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  // Bumped to remount the runtime: it reads its initial state only on mount.
  const [runtimeEpoch, setRuntimeEpoch] = useState(0);
  const [sessionView, setSessionView] = useState<SessionView>("chat");
  const [activeActivity, setActiveActivity] = useState<Activity | null>(null);
  const activeSession = useMemo(
    () => sessions.find((session) => session.session_id === activeId),
    [activeId, sessions],
  );
  const operationRef = useRef(0);
  const recoveryRef = useRef<{
    sessionId: string;
    controller: AbortController;
  } | null>(null);
  const refreshSignalRef = useRef(0);

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

  const loadSessions = useCallback(async () => {
    const result = await api.listSessions();
    const next = result.sessions || [];
    setSessions(next);
    // keep the profile pickers (sidebar + new-session form + this dialog)
    // in sync after any CRUD operation
    api
      .listProfiles()
      .then((p) => setProfiles(p.profiles || []))
      .catch(() => {});
    return next;
  }, []);

  const refreshAgentPool = useCallback(async () => {
    try {
      const result = await api.listAgentPool(true);
      const next = result.agents || [];
      setAgents(next);
      setSelectedIdentity((current) =>
        next.some((agent) => agent.name === current && agent.available)
          ? current
          : next.find((agent) => agent.available)?.name || "",
      );
      return next;
    } catch {
      setAgents([]);
      setSelectedIdentity("");
      return [];
    }
  }, []);

  const loadState = useCallback(
    async (sessionId: string, nextSessions: Session[]) => {
      const [history, permissions] = await Promise.all([
        api.getHistory(sessionId),
        api.getPermissions(sessionId),
      ]);
      return buildInitialState(
        sessionId,
        nextSessions,
        history.history || [],
        permissions.pending_prompts || [],
      );
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    const retryTimerRef = { current: null as number | null };
    const refreshActiveState = async (sessionId: string) => {
      try {
        const nextSessions = await loadSessions();
        if (controller.signal.aborted || activeId !== sessionId) return;
        const nextState = await loadState(sessionId, nextSessions);
        if (controller.signal.aborted || activeId !== sessionId) return;
        setInitialState(nextState);
        refreshSignalRef.current += 1;
        setRefreshSignal(refreshSignalRef.current);
      } catch {
        // Retry on the next event or polling cycle.
      }
    };
    const followEvents = async () => {
      while (!controller.signal.aborted && activeId) {
        try {
          await api.streamEvents(activeId, controller.signal, (event) => {
            if (event.type === "turn_complete" || event.type === "error") {
              void refreshActiveState(activeId);
            }
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
          const next = await loadSessions();
          if (!controller.signal.aborted) {
            setSessions(next);
            setInitialState((state) => state ? { ...state, sessions: next } : state);
          }
          // Live hub state for the sidebar gems; skip the update when nothing
          // moved so the chat does not re-render every poll.
          const pool = (await api.listAgentPool(true)).agents || [];
          if (!controller.signal.aborted) {
            setAgents((current) => (JSON.stringify(current) === JSON.stringify(pool) ? current : pool));
          }
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
  }, [loadSessions]);

  useEffect(() => {
    let mounted = true;
    const operation = ++operationRef.current;
    (async () => {
      setBusy(true);
      setBusyMessage("Connecting to the engine…");
      await api.loadConfig();
      const [result, profileResult, agentResult, bundleResult] = await Promise.all([
        loadSessions(),
        api.listProfiles().catch(() => ({ profiles: [], active: undefined })),
        refreshAgentPool(),
        api.listAgentBundles().catch(() => ({ bundles: [] })),
      ]);
      if (!mounted || operation !== operationRef.current) return;
      const nextProfiles = profileResult.profiles || [];
      setProfiles(nextProfiles);
      const nextAgents = agentResult || [];
      setAgents(nextAgents);
      setBundles(bundleResult.bundles || []);
      setSelectedProfile(
        profileResult.active || nextProfiles[0]?.name || "default",
      );
      setSelectedIdentity(nextAgents.find((agent) => agent.available)?.name || "");
      const first = [...result].reverse().find((session) => session.attachable !== false);
      if (first) {
        setBusyMessage("Restoring session…");
        const firstState = await loadState(first.session_id, result);
        if (!mounted || operation !== operationRef.current) return;
        setActiveId(first.session_id);
        setInitialState(firstState);
        recoverPendingTurn(first.session_id, hasPendingPermission(firstState));
      }
      setError(null);
    })()
      .catch((reason) => {
        if (mounted && operation === operationRef.current) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (mounted && operation === operationRef.current) setBusy(false);
      });
    return () => {
      mounted = false;
    };
  }, [loadSessions, loadState, recoverPendingTurn, refreshAgentPool]);

  const createSession = async () => {
    abortRecovery();
    const operation = ++operationRef.current;
    setBusy(true);
    setBusyMessage(
      "Starting daemon (plugin discovery can take ~10 seconds)…",
    );
    setError(null);
    try {
      const session = await api.createSession({
        profile: selectedProfile || "default",
        agent: selectedBundle !== "default" ? selectedBundle : undefined,
        identity: selectedIdentity || undefined,
        approval_mode: "trust_all",
        workspace: workspacePath.trim() || undefined,
      });
      const result = await loadSessions();
      await refreshAgentPool();
      if (operation !== operationRef.current) return;
      const nextState = await loadState(session.session_id, result);
      if (operation !== operationRef.current) return;
      setActiveId(session.session_id);
      setInitialState(nextState);
      recoverPendingTurn(session.session_id, hasPendingPermission(nextState));
    } catch (reason) {
      if (operation === operationRef.current) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (operation === operationRef.current) {
        setBusy(false);
        setBusyMessage("");
      }
    }
  };

  const deleteSession = async (sessionId: string) => {
    abortRecovery();
    const operation = ++operationRef.current;
    setBusy(true);
    setBusyMessage("Stopping session…");
    try {
      await api.deleteSession(sessionId);
      const result = await loadSessions();
      await refreshAgentPool();
      if (operation !== operationRef.current) return;
      const next = [...result].reverse().find((session) => session.attachable !== false);
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
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (operation === operationRef.current) {
        setBusy(false);
        setBusyMessage("");
      }
    }
  };

  const selectSession = async (sessionId: string) => {
    abortRecovery();
    const operation = ++operationRef.current;
    setBusy(true);
    setBusyMessage("Loading conversation…");
    try {
      const nextState = await loadState(sessionId, sessions);
      if (operation !== operationRef.current) return;
      setActiveId(sessionId);
      setInitialState(nextState);
      recoverPendingTurn(sessionId, hasPendingPermission(nextState));
    } catch (reason) {
      if (operation === operationRef.current) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (operation === operationRef.current) {
        setBusy(false);
        setBusyMessage("");
      }
    }
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

  const handleSessionUpdated = useCallback((updated: Session) => {
    setSessions((current) =>
      current.map((item) =>
        item.session_id === updated.session_id ? updated : item,
      ),
    );
  }, []);

  return (
    <SidebarProvider>
      <AppSidebar
        sessions={sessions}
        profiles={profiles}
        agents={agents}
        bundles={bundles}
        selectedProfile={selectedProfile}
        selectedIdentity={selectedIdentity}
        workspacePath={workspacePath}
        onWorkspaceChange={setWorkspacePath}
        selectedBundle={selectedBundle}
        activeId={activeId}
        activeActivity={activeActivity}
        busy={busy}
        onProfileChange={setSelectedProfile}
        onIdentityChange={setSelectedIdentity}
        onBundleChange={setSelectedBundle}
        onSettings={() => openSettings()}
        onManageProfiles={() => setProfilesOpen(true)}
        onSelectSession={(id) => void selectSession(id)}
        onCreate={() => void createSession()}
        onDelete={(id) => void deleteSession(id)}
      />
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
      <SidebarInset className="h-svh max-h-svh min-h-svh overflow-hidden">
        {activeSession && initialState ? (
          <EngineRuntimeProvider
            key={`${activeId}:${runtimeEpoch}`}
            api={api}
            sessionId={activeSession.session_id}
            initialState={initialState}
          >
            <RuntimeShell
              session={activeSession}
              profiles={profiles}
              agents={agents}
              refreshSignal={refreshSignal}
              onSessionUpdated={handleSessionUpdated}
              onOpenSettings={openSettings}
              onHistoryCleared={() => resetThread(activeSession.session_id)}
              onActivity={setActiveActivity}
              view={sessionView}
              onViewChange={setSessionView}
            />
          </EngineRuntimeProvider>
        ) : (
          <>
            <header className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
              <SidebarTrigger className="-ml-1" />
              <Separator orientation="vertical" className="mr-1 h-4" />
              <span className="text-muted-foreground text-sm">No session</span>
            </header>
            <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
              <h1 className="text-2xl font-semibold">Start a kollab session</h1>
              <p
                className={
                  error
                    ? "text-destructive max-w-md text-sm"
                    : "text-muted-foreground max-w-md text-sm"
                }
              >
                {error || (busy ? busyMessage : "Create a session to begin.")}
              </p>
              <Button
                type="button"
                onClick={() => void createSession()}
                disabled={busy}
              >
                {busy ? busyMessage || "Connecting…" : "Create session"}
              </Button>
            </div>
          </>
        )}
      </SidebarInset>
    </SidebarProvider>
  );
}
