import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, PanelLeft, Plus, Terminal, X } from "lucide-react";

import { DaemonNotice } from "@/components/code/DaemonNotice";
import { FloatingFileSidebar, useFileSidebarVisibility } from "@/components/code/floating-sidebar";

import { TerminalViewer } from "@/components/terminal/terminal-viewer";
import {
  DAEMON_OUTDATED_CODE,
  envHiddenLabel,
  envHiddenTitle,
  shellName,
  type TerminalEnvReport,
} from "@/components/terminal/terminal-env";
import { useTerminalWsConnection, type TerminalWsStatus } from "@/components/terminal/use-terminal-ws-connection";
import { Button } from "@/components/ui/button";
import { codeFetch, setCodeSession, unwrapCodeData } from "@/lib/code-fetch";
import { cn } from "@/lib/utils";

/** Terminal view: one tab per terminal session, and each tab owns one ws connection. */
export interface TerminalViewProps {
  /** The session whose daemon runs the shells: a session opened from another computer gets that computer's shell. */
  sessionId: string;
  /** The computer the session runs on; empty for this one. Shown before the folder so the machine is plain. */
  device?: string;
  workspace: string;
}

interface TerminalTabState {
  id: number;
  /** Bumped by Retry. The tab remounts, so its connection is closed and a new session starts. */
  generation: number;
}

/** What a tab tells the view about its connection: enough for the header and nothing more. */
interface TerminalTabReport {
  status: TerminalWsStatus;
  reconnecting: boolean;
  refusal: string | null;
}

interface TerminalTabProps {
  id: number;
  sessionId: string;
  workspace: string;
  active: boolean;
  onReport: (id: number, report: TerminalTabReport) => void;
  /** Hands the panel the function that ends this tab's shell; null when the tab unmounts. */
  onRegister: (id: number, endSession: (() => void) | null) => void;
  onRetry: (id: number) => void;
}

/**
 * One terminal tab. It owns one ws connection and draws its viewer. Unmounting the tab only
 * disconnects: the shell keeps running on the server until the user closes the tab with its X.
 * Inactive tabs stay mounted so their sessions keep streaming while another tab is shown.
 */
function TerminalTab({ id, sessionId, workspace, active, onReport, onRegister, onRetry }: TerminalTabProps) {
  const connection = useTerminalWsConnection({ sessionId, workspace });
  const { status, errorMessage, errorCode, reconnecting, refusal, close } = connection;

  useEffect(() => {
    onReport(id, { status, reconnecting, refusal });
  }, [id, onReport, status, reconnecting, refusal]);

  useEffect(() => {
    onRegister(id, close);
    return () => onRegister(id, null);
  }, [id, close, onRegister]);

  return (
    <div className={cn("absolute inset-0", !active && "hidden")}>
      <TerminalViewer connection={connection} />
      {status !== "attached" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background/80 px-6 text-center">
          {status === "connecting" && (
            <>
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              <p className="text-xs text-muted-foreground" aria-live="polite">
                {reconnecting ? "Reconnecting..." : "Connecting..."}
              </p>
            </>
          )}
          {status === "closed" && <p className="text-xs text-muted-foreground">Shell ended.</p>}
          {status === "error" &&
            (errorCode === DAEMON_OUTDATED_CODE ? (
              <DaemonNotice />
            ) : (
              <p className="text-xs text-muted-foreground">
                {errorMessage ?? "Could not reach the terminal server."}
              </p>
            ))}
          {(status === "closed" || status === "error") && (
            <Button type="button" variant="ghost" size="sm" onClick={() => onRetry(id)}>
              Retry
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/** What the header reads once per session from the daemon. `sessionId` says which session it belongs to. */
interface TerminalHeaderInfo {
  sessionId: string;
  shell: string;
  env: TerminalEnvReport | null;
}

/** GET {session}/code/{path}. A failed read is null: the header just leaves that part out. */
async function readCodeData<T>(path: string): Promise<T | null> {
  try {
    const res = await codeFetch(path);
    if (!res.ok) return null;
    return unwrapCodeData<T>(await res.json());
  } catch {
    return null;
  }
}

/** The shell's configured path (settings) and the hidden-variable report (env). Both reads run together. */
async function readTerminalHeader(): Promise<{ shell: string; env: TerminalEnvReport | null }> {
  const [settings, env] = await Promise.all([
    readCodeData<{ shell?: unknown }>("/code/settings"),
    readCodeData<TerminalEnvReport>("/code/terminal/env"),
  ]);
  // The setting is empty by default; the daemon's report holds the shell it really started.
  const configured = typeof settings?.shell === "string" ? settings.shell : "";
  return { shell: configured || env?.shell || "", env };
}

export function TerminalView({ sessionId, workspace, device }: TerminalViewProps) {
  const [tabs, setTabs] = useState<TerminalTabState[]>(() => [{ id: 1, generation: 0 }]);
  const [activeId, setActiveId] = useState(1);
  const [reports, setReports] = useState<Partial<Record<number, TerminalTabReport>>>({});
  const [header, setHeader] = useState<TerminalHeaderInfo | null>(null);
  const nextIdRef = useRef(2);
  // Each tab's end-session function. Closing or retrying a tab calls it; closing the panel does not.
  const sessionEndersRef = useRef(new Map<number, () => void>());

  // Read once per mount and per session. setCodeSession first: codeFetch resolves the session from it.
  useEffect(() => {
    let current = true;
    setCodeSession(sessionId);
    void readTerminalHeader().then((info) => {
      if (current) setHeader({ sessionId, ...info });
    });
    return () => {
      current = false;
    };
  }, [sessionId]);

  const reportTab = useCallback((id: number, next: TerminalTabReport) => {
    setReports((current) => {
      const prev = current[id];
      if (
        prev &&
        prev.status === next.status &&
        prev.reconnecting === next.reconnecting &&
        prev.refusal === next.refusal
      ) {
        return current;
      }
      return { ...current, [id]: next };
    });
  }, []);

  const registerTab = useCallback((id: number, endSession: (() => void) | null) => {
    if (endSession) sessionEndersRef.current.set(id, endSession);
    else sessionEndersRef.current.delete(id);
  }, []);

  const newTab = () => {
    const id = nextIdRef.current;
    nextIdRef.current += 1;
    setTabs((current) => [...current, { id, generation: 0 }]);
    setActiveId(id);
  };

  const closeTab = (id: number) => {
    const index = tabs.findIndex((tab) => tab.id === id);
    if (index < 0) return;
    // The user closed this tab, so its shell ends on the server.
    sessionEndersRef.current.get(id)?.();
    const remaining = tabs.filter((tab) => tab.id !== id);
    setTabs(remaining);
    if (id === activeId) {
      // the tab that takes the closed tab's place, or the last one when it was the final tab
      setActiveId(remaining[Math.min(index, remaining.length - 1)]?.id ?? 0);
    }
  };

  const retryTab = (id: number) => {
    // A retry starts a new shell, so the old session is ended first.
    sessionEndersRef.current.get(id)?.();
    setTabs((current) =>
      current.map((tab) => (tab.id === id ? { ...tab, generation: tab.generation + 1 } : tab))
    );
  };

  const activeTab = tabs.find((tab) => tab.id === activeId);
  const activeReport = activeTab ? reports[activeTab.id] : undefined;
  // With no tab open there is no session, so the window reads "closed".
  const activeStatus: TerminalWsStatus = activeTab ? (activeReport?.status ?? "connecting") : "closed";

  const sidebar = useFileSidebarVisibility();
  const folder = workspace.split("/").filter(Boolean).slice(-2).join("/");
  const folderLabel = device ? `${device}:${folder}` : folder;
  // A header read that belongs to another session (the view moved) is not shown.
  const info = header?.sessionId === sessionId ? header : null;
  const shell = shellName(info?.shell ?? "");
  const envLabel = envHiddenLabel(info?.env ?? null);
  const envTitle = envHiddenTitle(info?.env ?? null);

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      data-terminal-view=""
      data-status={activeStatus}
    >
      <div className="flex shrink-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 px-3 py-1.5 text-xs font-medium">
        {sidebar.narrow && (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Terminals sidebar"
            aria-pressed={sidebar.visible}
            title="Terminals sidebar"
            className={cn(sidebar.visible ? "bg-accent text-accent-foreground" : "text-muted-foreground")}
            onClick={sidebar.toggle}
          >
            <PanelLeft className="h-4 w-4" />
          </Button>
        )}
        <span className="min-w-0 truncate">
          Terminal <span className="font-mono text-[10px] font-normal text-muted-foreground">{folderLabel}</span>
        </span>
        {shell && (
          <span className="max-w-[8rem] shrink-0 truncate font-mono text-[10px] font-normal text-muted-foreground">
            {shell}
          </span>
        )}
        {envLabel && (
          <span className="shrink-0 text-[10px] font-normal text-muted-foreground" title={envTitle}>
            {envLabel}
          </span>
        )}
        {activeReport?.reconnecting && (
          <span className="shrink-0 text-[10px] font-normal text-muted-foreground" aria-live="polite">
            Reconnecting
          </span>
        )}
        {activeStatus === "attached" && activeReport?.refusal && (
          <span className="min-w-0 break-words text-[10px] font-normal text-muted-foreground" title={activeReport.refusal}>
            {activeReport.refusal}
          </span>
        )}
      </div>
      <div className="relative flex min-h-0 flex-1 gap-2 overflow-hidden p-2">
        <FloatingFileSidebar visible={sidebar.visible} narrow={sidebar.narrow}>
          <div className="flex shrink-0 items-center gap-0.5 px-3 py-1">
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label="New terminal"
              title="New terminal"
              className="text-muted-foreground"
              onClick={newTab}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex flex-col gap-0.5 px-2 pb-2" role="tablist" aria-label="Terminals">
            {tabs.map((tab, index) => {
              const label = `Terminal ${index + 1}`;
              const isActive = tab.id === activeId;
              const status = reports[tab.id]?.status ?? "connecting";
              return (
                <div
                  key={tab.id}
                  className={cn(
                    "group flex items-center gap-1 rounded-md py-1 pl-2 pr-1 text-xs",
                    isActive
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )}
                >
                  <button
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    onClick={() => setActiveId(tab.id)}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  >
                    <Terminal className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">{label}</span>
                    {status !== "attached" && (
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        {status === "connecting" ? "connecting" : "ended"}
                      </span>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => closeTab(tab.id)}
                    aria-label={`Close ${label}`}
                    title="Close terminal"
                    className="rounded p-0.5 hover:bg-muted"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              );
            })}
          </div>
        </FloatingFileSidebar>

        <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
          {tabs.map((tab) => (
            <TerminalTab
              key={`${tab.id}:${tab.generation}`}
              id={tab.id}
              sessionId={sessionId}
              workspace={workspace}
              active={tab.id === activeId}
              onReport={reportTab}
              onRegister={registerTab}
              onRetry={retryTab}
            />
          ))}
          {tabs.length === 0 && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
              <p className="text-xs text-muted-foreground">No terminal open.</p>
              <Button type="button" variant="ghost" size="sm" onClick={newTab}>
                New terminal
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
