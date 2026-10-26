import { Avatar } from "@/components/agents/Avatar";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  CircleAlert,
  Eraser,
  MessageSquare,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  ShieldOff,
  Terminal,
  Trash2,
  Users,
  Waypoints,
} from "lucide-react";
import type {
  AgentPoolEntry,
  EngineApi,
  HubAgent,
  McpServerConfig,
  Profile,
  Session,
  SessionMcp,
} from "@/api";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { CliBadge } from "@/components/shell/CliBadge";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import { cliHarness } from "@/utils/cli-harness";
import { Separator } from "@/components/ui/separator";
import {
  APPROVAL_MODE_OPTIONS,
  formatApprovalMode,
  normalizeApprovalMode,
} from "@/utils/approval-mode";
import { agentStatus, bundleName } from "@/components/panels/panel-model";
import { agentName, formatSessionName, sessionHeading } from "@/utils/session-display";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type SessionView = "chat" | "trajectory";

const VIEWS = [
  ["chat", "Chat", MessageSquare],
  ["trajectory", "Trajectory", Waypoints],
] as const;

// Whether the docked panel is open, per browser. Open unless closed here before.
const PANEL_KEY = "kollab.session-panel";

function storedOpen(): boolean {
  try {
    return window.localStorage.getItem(PANEL_KEY) !== "closed";
  } catch {
    return true;
  }
}

function storeOpen(open: boolean) {
  try {
    window.localStorage.setItem(PANEL_KEY, open ? "open" : "closed");
  } catch {
    // Private windows and blocked storage just forget the choice.
  }
}

// A panel row: small text, the width of the panel.
const ROW = "h-7 w-full justify-start gap-2 px-2 text-xs font-normal";

/**
 * The session's own column: who it is, Chat or Trajectory, and its controls.
 * Docked at the chat's right edge and open by default; closed, it leaves a
 * slim rail. On a phone it is a sheet the chat header opens.
 */
export function SessionPanel({
  api,
  session,
  profiles,
  pool,
  usage,
  status,
  statusError = false,
  onStatus,
  onSessionUpdated,
  onOpenSettings,
  onHistoryCleared,
  view,
  onViewChange,
  sheetOpen,
  onSheetOpenChange,
}: {
  api: EngineApi;
  session: Session;
  profiles: Profile[];
  /** Who the agents are (pictures and persona names), by name. */
  pool: readonly AgentPoolEntry[];
  usage?: string | null;
  status?: string | null;
  statusError?: boolean;
  onStatus: (message: string) => void;
  onSessionUpdated: (session: Session) => void;
  onOpenSettings: () => void;
  /** Called once the engine has cleared the history; resets the open thread. */
  onHistoryCleared: () => Promise<void>;
  view: SessionView;
  onViewChange: (view: SessionView) => void;
  /** The phone sheet, opened from the chat header. */
  sheetOpen: boolean;
  onSheetOpenChange: (open: boolean) => void;
}) {
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(storedOpen);
  const [clearOpen, setClearOpen] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const [mode, setMode] = useState(() =>
    normalizeApprovalMode(session.approval_mode),
  );
  const [mcp, setMcp] = useState<SessionMcp | null>(null);
  const [mcpDefinitions, setMcpDefinitions] = useState<
    Record<string, McpServerConfig>
  >({});
  const [agents, setAgents] = useState<HubAgent[] | null>(null);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [mcpBusy, setMcpBusy] = useState<string | null>(null);
  const [mcpOpen, setMcpOpen] = useState(false);
  const [pendingMcpDelete, setPendingMcpDelete] = useState<string | null>(null);
  const [mcpDraft, setMcpDraft] = useState<{
    original: string | null;
    name: string;
    command: string;
    description: string;
    enabled: boolean;
    env: string;
  } | null>(null);
  const [hubOpen, setHubOpen] = useState(false);

  const fail = useCallback(
    (error: unknown) =>
      onStatus(error instanceof Error ? error.message : String(error)),
    [onStatus],
  );

  const loadMcp = useCallback(async () => {
    try {
      const [sessionMcp, configured] = await Promise.all([
        api.getSessionMcp(session.session_id),
        api.listMcpServers().catch(() => ({ servers: {} })),
      ]);
      setMcp(sessionMcp);
      setMcpDefinitions(configured.servers || {});
    } catch (error) {
      fail(error);
      setMcp(null);
    }
  }, [api, fail, session.session_id]);

  const loadAgents = useCallback(async () => {
    setAgentsLoading(true);
    try {
      const result = await api.listHubAgents(true);
      setAgents(result.agents || []);
    } catch (error) {
      fail(error);
    } finally {
      setAgentsLoading(false);
    }
  }, [api, fail]);

  useEffect(() => {
    setMode(normalizeApprovalMode(session.approval_mode));
    void loadMcp();
  }, [loadMcp, session.approval_mode]);

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  const changeMode = async (next: string) => {
    // Optimistic so the Select reflects the click immediately; reconciled with
    // whatever the daemon reports back.
    setMode(next);
    try {
      const response = await api.setApprovalMode(session.session_id, next);
      const resolved = normalizeApprovalMode(response.mode || next);
      setMode(resolved);
      onStatus(`Approval mode: ${formatApprovalMode(resolved)}`);
    } catch (error) {
      setMode(normalizeApprovalMode(session.approval_mode));
      fail(error);
    }
  };

  const toggleMcp = async (serverName: string, connected: boolean) => {
    setMcpBusy(serverName);
    try {
      if (connected) await api.disconnectMcp(session.session_id, serverName);
      else await api.connectMcp(session.session_id, serverName);
      await loadMcp();
      onStatus(`${serverName}: ${connected ? "disconnected" : "connected"}`);
    } catch (error) {
      fail(error);
    } finally {
      setMcpBusy(null);
    }
  };

  const startEditServer = (name: string, definition: McpServerConfig) => {
    setMcpDraft({
      original: name,
      name,
      command: definition.command || "",
      description: definition.description || "",
      enabled: definition.enabled ?? true,
      env: definition.env
        ? Object.entries(definition.env)
            .map(([k, v]) => `${k}=${v}`)
            .join("\n")
        : "",
    });
  };

  const saveServer = async () => {
    if (!mcpDraft) return;
    const env: Record<string, string> = {};
    for (const line of mcpDraft.env.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const eq = trimmed.indexOf("=");
      if (eq > 0) env[trimmed.slice(0, eq)] = trimmed.slice(eq + 1);
    }
    const body = {
      type: "stdio",
      command: mcpDraft.command,
      description: mcpDraft.description,
      enabled: mcpDraft.enabled,
      env,
    };
    try {
      if (mcpDraft.original) {
        await api.updateMcpServer(mcpDraft.original, body);
      } else {
        await api.createMcpServer({ name: mcpDraft.name, ...body });
      }
      setMcpDraft(null);
      await loadMcp();
      onStatus(
        mcpDraft.original
          ? `${mcpDraft.name}: updated`
          : `${mcpDraft.name}: added`,
      );
    } catch (error) {
      fail(error);
    }
  };

  const deleteServer = async (serverName: string) => {
    setMcpBusy(serverName);
    try {
      await api.deleteMcpServer(serverName);
      await loadMcp();
      onStatus(`${serverName}: deleted`);
    } catch (error) {
      fail(error);
    } finally {
      setMcpBusy(null);
    }
  };

  const changeProfile = async (name: string) => {
    try {
      const updated = await api.setSessionProfile(session.session_id, name);
      onSessionUpdated(updated);
      onStatus(`Model: ${updated.model || name}`);
    } catch (error) {
      fail(error);
    }
  };

  const clearHistory = async () => {
    try {
      await api.clearHistory(session.session_id);
      await onHistoryCleared();
    } catch (error) {
      fail(error);
    }
  };

  const servers = Object.entries(mcp?.servers || {});
  const configuredServerNames = Object.keys(mcpDefinitions);
  const allServerNames = Array.from(
    new Set([...configuredServerNames, ...servers.map(([name]) => name)]),
  );
  const sessionLabel = sessionHeading(
    session,
    pool.find((agent) => agent.name === session.identity)?.display_name,
  );
  const connectedCount = servers.filter(
    ([, info]) => info.status === "connected",
  ).length;

  const activeProfile = profiles.find(
    (profile) => profile.name === (session.profile || profiles[0]?.name),
  );
  const toggle = (next: boolean) => {
    setOpen(next);
    storeOpen(next);
  };
  // A dialog opens over the chat; on a phone the sheet steps aside for it.
  const openDialog = (show: () => void) => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    onSheetOpenChange(false);
    show();
  };
  // Closing a dialog returns focus to the row that opened it, while it is shown.
  const restoreFocus = (event: Event) => {
    if (!opener.current?.isConnected) return;
    event.preventDefault();
    opener.current.focus();
  };

  const body = (action: ReactNode) => (
    <div className="flex min-w-0 flex-col gap-3 text-xs">
      <div className="flex min-w-0 items-start gap-1">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold" title={sessionLabel}>{sessionLabel}</h2>
          <p className="text-muted-foreground truncate" title={session.session_id}>
            {formatSessionName(session.name, session.session_id)}
          </p>
        </div>
        {action}
      </div>
      <CliBadge harness={cliHarness(session)} className="text-muted-foreground" />
      {session.acp_waiting && <p className="text-amber-600">Waiting for Approval</p>}
      {status && <p className={statusError ? "text-destructive break-words" : "text-muted-foreground break-words"}>{status}</p>}
      {usage && <p className="text-muted-foreground break-words tabular-nums" title="Current harness context usage">{usage}</p>}
      <div className="flex flex-col gap-0.5" role="group" aria-label="Session view">
        {VIEWS.map(([id, label, Icon]) => (
          <Button
            key={id}
            type="button"
            variant="ghost"
            size="sm"
            aria-pressed={view === id}
            data-testid={`${id}-tab`}
            onClick={() => {
              onViewChange(id);
              onSheetOpenChange(false);
            }}
            className={cn(ROW, view === id && "bg-accent text-accent-foreground font-medium")}
          >
            <Icon className="size-3.5" />
            <span>{label}</span>
          </Button>
        ))}
      </div>
      <Separator />
      <div className="flex min-w-0 flex-col gap-1">
        <p className="text-muted-foreground font-medium">Model and Profile</p>
        <Select
          value={session.profile || profiles[0]?.name || "default"}
          onValueChange={(next) => void changeProfile(next)}
          disabled={!profiles.length}
        >
          <SelectTrigger
            size="sm"
            className="h-7 w-full min-w-0 text-xs"
            aria-label="Model"
          >
            <SelectValue placeholder="Model" className="min-w-0 flex-1">
              {activeProfile ? (
                <span className="min-w-0 truncate">
                  {activeProfile.model || activeProfile.name}
                  {activeProfile.model && activeProfile.name !== activeProfile.model ? (
                    <span className="text-muted-foreground"> · {activeProfile.name}</span>
                  ) : null}
                </span>
              ) : undefined}
            </SelectValue>
          </SelectTrigger>
          <SelectContent className="max-w-[calc(100vw-1.5rem)]">
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
      </div>
      <div className="flex min-w-0 flex-col gap-1">
        <p className="text-muted-foreground font-medium">Approval Mode</p>
        <Select value={mode} onValueChange={(next) => void changeMode(next)}>
          <SelectTrigger
            size="sm"
            className="h-7 w-full text-xs"
            aria-label="Approval mode"
            title={formatApprovalMode(mode)}
          >
            {/* Trust All skips every approval prompt: an open shield says so. */}
            {mode === "trust_all" ? (
              <ShieldOff className="size-3.5 text-amber-500" />
            ) : (
              <ShieldCheck className="size-3.5" />
            )}
            <SelectValue>{formatApprovalMode(mode)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {APPROVAL_MODE_OPTIONS.map(({ value, label }) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {[
        session.approval_mode_warning,
        session.persistence_warning,
        session.deletion_warning,
      ].some(Boolean) ? (
        <div className="flex flex-col gap-1">
          {[
            session.approval_mode_warning,
            session.persistence_warning,
            session.deletion_warning,
          ]
            .filter(Boolean)
            .map((warning) => (
              <p key={warning} className="text-destructive text-xs">
                {warning}
              </p>
            ))}
        </div>
      ) : null}
      <Separator />
      <div className="flex flex-col gap-0.5">
        <Button type="button" variant="ghost" size="sm" className={ROW} onClick={() => openDialog(() => { setMcpOpen(true); void loadMcp(); })}>
          <Plug className="size-3.5" /><span>MCP Servers</span>
          <span className="text-muted-foreground ml-auto tabular-nums">{connectedCount}/{allServerNames.length}</span>
        </Button>
        <Button type="button" variant="ghost" size="sm" className={ROW} onClick={() => openDialog(() => { setHubOpen(true); void loadAgents(); })}>
          <Users className="size-3.5" /><span>Who Is Online</span>
          <span className="text-muted-foreground ml-auto tabular-nums">{agents === null ? "…" : agents.length}</span>
        </Button>
        <Button type="button" variant="ghost" size="sm" className={ROW} onClick={() => openDialog(onOpenSettings)}>
          <Settings2 className="size-3.5" /><span>Settings</span>
        </Button>
        <Button type="button" variant="ghost" size="sm" className={ROW} onClick={() => openDialog(() => setClearOpen(true))}>
          <Eraser className="size-3.5" /><span>Clear History</span>
        </Button>
      </div>
    </div>
  );

  return (
    <>
      <span className="sr-only" role={statusError ? "alert" : "status"}>{status || (session.acp_waiting ? "Waiting for Approval" : "")}</span>
      {isMobile ? (
        <Sheet open={sheetOpen} onOpenChange={onSheetOpenChange}>
          <SheetContent side="right" className="bg-sidebar top-2 right-2 bottom-auto h-auto max-h-[calc(100%-1rem)] w-72 max-w-[85vw] gap-0 overflow-y-auto rounded-xl p-4 pt-10 shadow-lg">
            <SheetTitle className="sr-only">Session Controls</SheetTitle>
            <SheetDescription className="sr-only">{sessionLabel}</SheetDescription>
            {body(null)}
          </SheetContent>
        </Sheet>
      ) : open ? (
        <aside aria-label="Session Controls" className="bg-sidebar text-sidebar-foreground m-2 ml-0 flex max-h-[calc(100%-1rem)] w-60 shrink-0 flex-col self-start overflow-y-auto rounded-xl p-3 shadow-lg">
          {body(
            <TooltipIconButton tooltip="Hide Session Panel" side="left" className="text-muted-foreground size-7 shrink-0 p-1.5" onClick={() => toggle(false)}>
              <PanelRightClose className="size-4" />
            </TooltipIconButton>,
          )}
        </aside>
      ) : (
        <aside aria-label="Session Controls" className="bg-sidebar text-sidebar-foreground m-2 ml-0 flex max-h-[calc(100%-1rem)] w-10 shrink-0 flex-col items-center gap-1 self-start overflow-y-auto rounded-xl py-2 shadow-lg">
          <TooltipIconButton tooltip="Show Session Panel" side="left" className="size-7 p-1.5" onClick={() => toggle(true)}>
            {statusError ? <CircleAlert className="text-destructive size-4" /> : <PanelRightOpen className="size-4" />}
          </TooltipIconButton>
          {VIEWS.map(([id, label, Icon]) => (
            <TooltipIconButton
              key={id}
              tooltip={label}
              side="left"
              aria-pressed={view === id}
              data-testid={`${id}-tab`}
              className={cn("size-7 p-1.5", view === id ? "bg-accent text-accent-foreground" : "text-muted-foreground")}
              onClick={() => onViewChange(id)}
            >
              <Icon className="size-4" />
            </TooltipIconButton>
          ))}
        </aside>
      )}

      {/* MCP servers */}
      <Dialog
        open={mcpOpen}
        onOpenChange={(open) => {
          setMcpOpen(open);
          if (open) void loadMcp();
        }}
      >
        <DialogContent className="sm:max-w-lg" onCloseAutoFocus={restoreFocus}>
          <DialogHeader>
            <DialogTitle>MCP Servers</DialogTitle>
            <DialogDescription>
              {connectedCount} connected · {allServerNames.length} configured.
              Tool access is scoped to this session.
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-[50vh]">
            <div className="flex flex-col gap-2 pr-3">
              {allServerNames.length ? (
                allServerNames.map((name) => {
                  const info = mcp?.servers?.[name] || {
                    status: "disconnected",
                  };
                  const definition = mcpDefinitions[name] || {};
                  // The engine only edits and removes servers in the global MCP
                  // config; a server an agent brought along is not listed there.
                  const editable = configuredServerNames.includes(name);
                  const connected = info.status === "connected";
                  const tools = Array.isArray(info.tools) ? info.tools : [];
                  return (
                    <div
                      key={name}
                      className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex min-w-0 flex-col gap-1">
                          <span className="truncate text-sm font-medium">
                            {name}
                          </span>
                          {definition.description ? (
                            <span className="text-muted-foreground text-xs">
                              {definition.description}
                            </span>
                          ) : null}
                          {/* A long command path must wrap anywhere, or its
                              width pushes the dialog past a phone screen. */}
                          <span className="text-muted-foreground flex items-start gap-1 text-[0.6875rem] break-all">
                            <Terminal className="mt-0.5 size-3 shrink-0" />
                            {definition.command || "configured by agent"}
                          </span>
                        </div>
                        <Badge
                          className="shrink-0"
                          variant={connected ? "default" : "outline"}
                        >
                          {connected ? "Connected" : "Offline"}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-muted-foreground text-xs">
                          {info.tool_count || tools.length || 0} tools
                          {info.error ? ` · ${info.error}` : ""}
                        </span>
                        <div className="flex items-center gap-1">
                          {editable ? (
                            <>
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                aria-label={`Edit ${name}`}
                                onClick={() => startEditServer(name, definition)}
                              >
                                <Pencil className="size-4" />
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                aria-label={`Delete ${name}`}
                                disabled={mcpBusy === name}
                                onClick={() => setPendingMcpDelete(name)}
                              >
                                <Trash2 className="size-4" />
                              </Button>
                            </>
                          ) : null}
                          <Button
                            type="button"
                            size="sm"
                            variant={connected ? "outline" : "default"}
                            disabled={mcpBusy === name}
                            onClick={() => void toggleMcp(name, connected)}
                          >
                            {mcpBusy === name
                              ? "…"
                              : connected
                                ? "Disconnect"
                                : "Connect"}
                          </Button>
                        </div>
                      </div>
                      {tools.length ? (
                        <div className="flex flex-wrap gap-1">
                          {tools.slice(0, 8).map((tool, index) => (
                            <Badge key={`${name}-${index}`} variant="secondary">
                              {typeof tool === "string"
                                ? tool
                                : String(
                                    (tool as Record<string, unknown>).name ||
                                      "tool",
                                  )}
                            </Badge>
                          ))}
                          {tools.length > 8 ? (
                            <Badge variant="secondary">+{tools.length - 8}</Badge>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  );
                })
              ) : (
                <p className="text-muted-foreground text-sm">
                  No MCP servers configured.
                </p>
              )}
            </div>
          </ScrollArea>
          {mcpDraft ? (
            <div className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold">
                  {mcpDraft.original ? `Edit ${mcpDraft.original}` : "Add Server"}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setMcpDraft(null)}
                >
                  Cancel
                </Button>
              </div>
              <Input
                aria-label="Server name"
                placeholder="name (e.g. github)"
                value={mcpDraft.name}
                disabled={Boolean(mcpDraft.original)}
                onChange={(e) =>
                  setMcpDraft({ ...mcpDraft, name: e.target.value })
                }
              />
              <Input
                aria-label="Server command"
                placeholder="command (e.g. npx -y @modelcontextprotocol/server-github)"
                value={mcpDraft.command}
                onChange={(e) =>
                  setMcpDraft({ ...mcpDraft, command: e.target.value })
                }
              />
              <Input
                aria-label="Server description"
                placeholder="description"
                value={mcpDraft.description}
                onChange={(e) =>
                  setMcpDraft({ ...mcpDraft, description: e.target.value })
                }
              />
              <textarea
                className="bg-muted/50 placeholder:text-muted-foreground min-h-16 rounded-md px-2 py-1.5 text-sm"
                aria-label="Environment variables"
                placeholder={"env vars, one per line:\nGITHUB_TOKEN=…"}
                value={mcpDraft.env}
                onChange={(e) =>
                  setMcpDraft({ ...mcpDraft, env: e.target.value })
                }
              />
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={mcpDraft.enabled}
                  onChange={(e) =>
                    setMcpDraft({ ...mcpDraft, enabled: e.target.checked })
                  }
                />
                Enabled
              </label>
              <Button
                type="button"
                size="sm"
                disabled={!mcpDraft.command.trim() || (!mcpDraft.original && !mcpDraft.name.trim())}
                onClick={() => void saveServer()}
              >
                Save Server
              </Button>
            </div>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void loadMcp()}
            >
              <RefreshCw className="size-4" />
              Refresh
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setMcpDraft({
                  original: null,
                  name: "",
                  command: "",
                  description: "",
                  enabled: true,
                  env: "",
                })
              }
            >
              <Plus className="size-4" />
              Add Server
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={pendingMcpDelete !== null}
        onOpenChange={(open) => !open && setPendingMcpDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete This MCP Server?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingMcpDelete} will be removed from your MCP configuration.
              Sessions already connected to it keep the connection until they
              disconnect. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                // Read the name before clearing state; the dialog unmounts its
                // content on close.
                const name = pendingMcpDelete;
                setPendingMcpDelete(null);
                if (name) void deleteServer(name);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Online agents */}
      <Dialog
        open={hubOpen}
        onOpenChange={(open) => {
          setHubOpen(open);
          if (open) void loadAgents();
        }}
      >
        <DialogContent className="sm:max-w-md" onCloseAutoFocus={restoreFocus}>
          <DialogHeader>
            <DialogTitle>Who Is Online</DialogTitle>
            <DialogDescription>
              Type <code className="rounded bg-muted px-1 py-0.5">@identity message</code> to message one agent, or <code className="rounded bg-muted px-1 py-0.5">@broadcast message</code> to reach everyone.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            {agentsLoading ? (
              <p className="text-muted-foreground text-sm">Looking for online agents…</p>
            ) : agents === null ? (
              <p className="text-muted-foreground rounded-md bg-muted/30 p-4 text-sm">
                Online agent status is unavailable. Refresh to try again.
              </p>
            ) : agents.length ? (
              <details className="group rounded-md bg-muted/30">
                <summary className="text-muted-foreground flex cursor-pointer list-none items-center justify-between px-3 py-2 text-xs font-medium [&::-webkit-details-marker]:hidden">
                  <span>Online agents</span><span className="group-open:rotate-180 transition-transform">⌄</span>
                </summary>
              <ScrollArea className="max-h-56 p-2">
                <div className="flex flex-col gap-1">
                  {agents.map((agent) => {
                    const identity = String(
                      agent.identity || agent.id || agent.agent_id || "",
                    );
                    const look = pool.find((entry) => entry.name === identity);
                    return (
                      <div
                        key={String(agent.agent_id || agent.id || identity)}
                        className="flex items-center gap-3 rounded-md px-2 py-2"
                      >
                        <Avatar
                          gem={identity}
                          avatar={look?.avatar}
                          displayName={look?.display_name}
                          folder={String(agent.project || "")}
                          where={String(agent.project || "") || session.device || session.workspace}
                          caste={look?.caste}
                          color={look?.color}
                          state="idle"
                          live
                          season="auto"
                          size={28}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center justify-between gap-2 text-sm font-medium">
                            <span className="truncate">{identity ? agentName(identity, look?.display_name) : "Agent"}</span>
                            <span className="text-muted-foreground text-xs">{agentStatus(agent.state)}</span>
                          </span>
                          <span className="text-muted-foreground block truncate text-xs">
                            {agent.agent_name ? bundleName(agent.agent_name) : agent.profile_name || "agent"}
                          </span>
                        </span>
                      </div>
                    );
                  })}
                </div>
              </ScrollArea>
              </details>
            ) : (
              <p className="text-muted-foreground rounded-md bg-muted/30 p-4 text-sm">
                No online agents are advertising a presence right now.
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void loadAgents()}
              disabled={agentsLoading}
            >
              <RefreshCw className="size-4" />
              Refresh Agents
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Clear history */}
      <AlertDialog open={clearOpen} onOpenChange={setClearOpen}>
        <AlertDialogContent onCloseAutoFocus={restoreFocus}>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear Conversation History?</AlertDialogTitle>
            <AlertDialogDescription>
              Removes every message from {sessionLabel}. The session keeps
              running. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void clearHistory()}>
              Clear History
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
