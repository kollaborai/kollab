import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, PanelLeft, Plus, Terminal, X } from "lucide-react";

import { FloatingFileSidebar, useFileSidebarVisibility } from "@/components/code/floating-sidebar";

import { TerminalViewer } from "@/components/terminal/terminal-viewer";
import { useTerminalWsConnection, type TerminalWsStatus } from "@/components/terminal/use-terminal-ws-connection";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Terminal view: one tab per terminal session, and each tab owns one ws connection. */
export interface TerminalViewProps {
  /** The session whose daemon runs the shells: a session opened from another computer gets that computer's shell. */
  sessionId: string;
  workspace: string;
}

interface TerminalTabState {
  id: number;
  /** Bumped by Retry. The tab remounts, so its connection is closed and a new session starts. */
  generation: number;
}

interface TerminalTabProps {
  id: number;
  sessionId: string;
  workspace: string;
  active: boolean;
  onStatus: (id: number, status: TerminalWsStatus) => void;
  /** Hands the panel the function that ends this tab's shell; null when the tab unmounts. */
  onRegister: (id: number, endSession: (() => void) | null) => void;
  onRetry: (id: number) => void;
}

/**
 * One terminal tab. It owns one ws connection and draws its viewer. Unmounting the tab only
 * disconnects: the shell keeps running on the server until the user closes the tab with its X.
 * Inactive tabs stay mounted so their sessions keep streaming while another tab is shown.
 */
function TerminalTab({ id, sessionId, workspace, active, onStatus, onRegister, onRetry }: TerminalTabProps) {
  const connection = useTerminalWsConnection({ sessionId, workspace });
  const { status, errorMessage, close } = connection;

  useEffect(() => {
    onStatus(id, status);
  }, [id, onStatus, status]);

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
              <p className="text-xs text-muted-foreground">Connecting...</p>
            </>
          )}
          {(status === "closed" || status === "error") && (
            <>
              <p className="text-xs text-muted-foreground">
                {status === "closed" ? "Shell ended." : (errorMessage ?? "Could not reach the terminal server.")}
              </p>
              <Button type="button" variant="ghost" size="sm" onClick={() => onRetry(id)}>
                Retry
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function TerminalView({ sessionId, workspace }: TerminalViewProps) {
  const [tabs, setTabs] = useState<TerminalTabState[]>(() => [{ id: 1, generation: 0 }]);
  const [activeId, setActiveId] = useState(1);
  const [statuses, setStatuses] = useState<Partial<Record<number, TerminalWsStatus>>>({});
  const nextIdRef = useRef(2);
  // Each tab's end-session function. Closing or retrying a tab calls it; closing the panel does not.
  const sessionEndersRef = useRef(new Map<number, () => void>());

  const reportStatus = useCallback((id: number, next: TerminalWsStatus) => {
    setStatuses((current) => (current[id] === next ? current : { ...current, [id]: next }));
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
  // With no tab open there is no session, so the window reads "closed".
  const activeStatus: TerminalWsStatus = activeTab ? (statuses[activeTab.id] ?? "connecting") : "closed";

  const sidebar = useFileSidebarVisibility();
  const folderLabel = workspace.split("/").filter(Boolean).slice(-2).join("/");

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      data-terminal-view=""
      data-status={activeStatus}
    >
      <div className="flex shrink-0 items-center gap-1.5 px-3 py-1.5 text-xs font-medium">
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
              const status = statuses[tab.id] ?? "connecting";
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
              onStatus={reportStatus}
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
