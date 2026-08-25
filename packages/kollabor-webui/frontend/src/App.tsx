import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Thread } from "./components/Thread";
import { TrajectoryView } from "./components/trajectory/TrajectoryView";
import { AppSidebar } from "@/components/shell/AppSidebar";
import { SessionToolbar } from "@/components/shell/SessionToolbar";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { EngineApi, type Profile, type Session } from "./api";
import {
  EngineRuntimeProvider,
  buildInitialState,
  useEngineRuntimeState,
  type EngineState,
} from "./runtime";

const api = new EngineApi();
type SessionView = "chat" | "trajectory";

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
}: {
  session: Session;
  profiles: Profile[];
}) {
  const runtimeState = useEngineRuntimeState();
  const [status, setStatus] = useState<string | null>(null);
  const profile = profiles.find((item) => item.name === session.profile);
  const model = profile?.model;
  const [view, setView] = useState<SessionView>("chat");
  // `thread.extras` is absent on first render; runtime.tsx guards the hook, and
  // this optional chain keeps App.tsx safe even if that guard is ever removed.
  const transportError = runtimeState?.state?.error;

  return (
    <>
      <header className="bg-background sticky top-0 z-10 flex shrink-0 flex-col gap-2 border-b px-3 py-2">
        <div className="flex items-center gap-2">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 h-4" />
          <div className="flex min-w-0 flex-col">
            <span className="truncate font-mono text-sm font-medium">
              {session.session_id}
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
            {status || transportError || "assistant transport"}
          </span>
        </div>
        <SessionToolbar api={api} session={session} onStatus={setStatus} />
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        {view === "chat" ? (
          <Thread />
        ) : (
          <TrajectoryView api={api} sessionId={session.session_id} />
        )}
      </div>
    </>
  );
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("default");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [busyMessage, setBusyMessage] = useState("Connecting to the engine…");
  const [error, setError] = useState<string | null>(null);
  const [initialState, setInitialState] = useState<EngineState | null>(null);
  const activeSession = useMemo(
    () => sessions.find((session) => session.session_id === activeId),
    [activeId, sessions],
  );
  const operationRef = useRef(0);
  const recoveryRef = useRef<{
    sessionId: string;
    controller: AbortController;
  } | null>(null);

  const abortRecovery = useCallback(() => {
    recoveryRef.current?.controller.abort();
    recoveryRef.current = null;
  }, []);

  const recoverPendingTurn = useCallback(
    (sessionId: string, pending?: boolean) => {
      abortRecovery();
      if (pending === false) return;
      const controller = new AbortController();
      recoveryRef.current = { sessionId, controller };

      // A daemon does not replay permission_request after reload. The prompt was
      // restored from /permissions while loading initial state; now follow
      // /events so this tab remains subscribed to the blocked turn until
      // turn_complete. The assistant transport request created by
      // PermissionToolUI has its own subscription for the answer and remaining
      // output.
      void api
        .streamEvents(sessionId, controller.signal)
        .catch((reason) => {
          if (
            !controller.signal.aborted &&
            recoveryRef.current?.controller === controller
          ) {
            setError(reason instanceof Error ? reason.message : String(reason));
          }
        })
        .finally(() => {
          if (recoveryRef.current?.controller === controller) {
            recoveryRef.current = null;
          }
        });
    },
    [abortRecovery],
  );

  useEffect(() => abortRecovery, [abortRecovery]);

  const loadSessions = useCallback(async () => {
    const result = await api.listSessions();
    const next = result.sessions || [];
    setSessions(next);
    return next;
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
    let mounted = true;
    const operation = ++operationRef.current;
    (async () => {
      setBusy(true);
      setBusyMessage("Connecting to the engine…");
      await api.loadConfig();
      const [result, profileResult] = await Promise.all([
        loadSessions(),
        api.listProfiles().catch(() => ({ profiles: [], active: undefined })),
      ]);
      if (!mounted || operation !== operationRef.current) return;
      const nextProfiles = profileResult.profiles || [];
      setProfiles(nextProfiles);
      setSelectedProfile(
        profileResult.active || nextProfiles[0]?.name || "default",
      );
      const first = result.at(-1);
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
  }, [loadSessions, loadState, recoverPendingTurn]);

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
        approval_mode: "confirm_all",
      });
      const result = await loadSessions();
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
      if (operation !== operationRef.current) return;
      const next = result.at(-1);
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

  return (
    <SidebarProvider>
      <AppSidebar
        sessions={sessions}
        profiles={profiles}
        selectedProfile={selectedProfile}
        activeId={activeId}
        busy={busy}
        onProfileChange={setSelectedProfile}
        onSelectSession={(id) => void selectSession(id)}
        onCreate={() => void createSession()}
        onDelete={(id) => void deleteSession(id)}
      />
      <SidebarInset className="min-h-svh">
        {activeSession && initialState ? (
          <EngineRuntimeProvider
            key={activeId}
            api={api}
            sessionId={activeSession.session_id}
            initialState={initialState}
          >
            <RuntimeShell session={activeSession} profiles={profiles} />
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
