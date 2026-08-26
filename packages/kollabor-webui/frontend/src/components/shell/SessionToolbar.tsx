import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  Eraser,
  Plug,
  RefreshCw,
  Settings2,
  Terminal,
  Users,
} from "lucide-react";
import type {
  EngineApi,
  HubAgent,
  McpServerConfig,
  Profile,
  Session,
  SessionMcp,
  SessionState,
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
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  APPROVAL_MODE_OPTIONS,
  formatApprovalMode,
  normalizeApprovalMode,
} from "@/utils/approval-mode";
import { formatSessionName } from "@/utils/session-display";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function SessionToolbar({
  api,
  session,
  profiles,
  onStatus,
  onSessionUpdated,
}: {
  api: EngineApi;
  session: Session;
  profiles: Profile[];
  onStatus: (message: string) => void;
  onSessionUpdated: (session: Session) => void;
}) {
  const [mode, setMode] = useState(() =>
    normalizeApprovalMode(session.approval_mode),
  );
  const [mcp, setMcp] = useState<SessionMcp | null>(null);
  const [mcpDefinitions, setMcpDefinitions] = useState<
    Record<string, McpServerConfig>
  >({});
  const [agents, setAgents] = useState<HubAgent[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [mcpBusy, setMcpBusy] = useState<string | null>(null);
  const [mcpOpen, setMcpOpen] = useState(false);
  const [hubOpen, setHubOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<SessionState | null>(null);
  const [settingsBusy, setSettingsBusy] = useState(false);

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

  useEffect(() => {
    setMode(normalizeApprovalMode(session.approval_mode));
    void loadMcp();
  }, [loadMcp, session.approval_mode]);

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

  const loadAgents = async () => {
    setAgentsLoading(true);
    try {
      const result = await api.listHubAgents(true);
      setAgents(result.agents || []);
    } catch (error) {
      fail(error);
    } finally {
      setAgentsLoading(false);
    }
  };

  const loadSettings = async () => {
    setSettingsBusy(true);
    try {
      setSettings(await api.getSessionState(session.session_id));
    } catch (error) {
      fail(error);
    } finally {
      setSettingsBusy(false);
    }
  };

  const changeProfile = async (name: string) => {
    try {
      const updated = await api.setSessionProfile(session.session_id, name);
      onSessionUpdated(updated);
      onStatus(`Model: ${updated.model || name}`);
      await loadSettings();
    } catch (error) {
      fail(error);
    }
  };

  const clearHistory = async () => {
    try {
      await api.clearHistory(session.session_id);
      onStatus("History cleared; reload the session to refresh messages.");
    } catch (error) {
      fail(error);
    }
  };

  const servers = Object.entries(mcp?.servers || {});
  const configuredServerNames = Object.keys(mcpDefinitions);
  const allServerNames = Array.from(
    new Set([...configuredServerNames, ...servers.map(([name]) => name)]),
  );
  const sessionLabel = formatSessionName(session.name, session.session_id);
  const workspace = String(
    settings?.system?.cwd || session.workspace || "current project",
  );
  const connectedCount = servers.filter(
    ([, info]) => info.status === "connected",
  ).length;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        value={session.profile || profiles[0]?.name || "default"}
        onValueChange={(next) => void changeProfile(next)}
        disabled={!profiles.length}
      >
        <SelectTrigger size="sm" className="max-w-[15rem]" aria-label="Model">
          <SelectValue placeholder="Model" />
        </SelectTrigger>
        <SelectContent>
          {profiles.map((profile) => (
            <SelectItem key={profile.name} value={profile.name}>
              {profile.model || profile.name}
              {profile.model && profile.name !== profile.model
                ? ` · ${profile.name}`
                : ""}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={mode} onValueChange={(next) => void changeMode(next)}>
        <SelectTrigger size="sm" className="w-[11rem]" aria-label="Approval mode">
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

      {/* MCP servers */}
      <Dialog
        open={mcpOpen}
        onOpenChange={(open) => {
          setMcpOpen(open);
          if (open) void loadMcp();
        }}
      >
        <DialogTrigger asChild>
          <Button variant="outline" size="sm">
            <Plug className="size-4" />
            MCP
            <Badge variant="secondary">
              {connectedCount}/{allServerNames.length}
            </Badge>
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>MCP servers</DialogTitle>
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
                  const connected = info.status === "connected";
                  const tools = Array.isArray(info.tools) ? info.tools : [];
                  return (
                    <div
                      key={name}
                      className="flex flex-col gap-2 rounded-lg border p-3"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex min-w-0 flex-col gap-1">
                          <span className="truncate text-sm font-medium">
                            {name}
                          </span>
                          <span className="text-muted-foreground text-xs">
                            {definition.description || "No description"}
                          </span>
                          <span className="text-muted-foreground flex items-center gap-1 text-[11px]">
                            <Terminal className="size-3" />
                            {definition.command || "configured by agent"}
                          </span>
                        </div>
                        <Badge variant={connected ? "default" : "outline"}>
                          {connected ? "connected" : "offline"}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-muted-foreground text-xs">
                          {info.tool_count || tools.length || 0} tools
                          {info.error ? ` · ${info.error}` : ""}
                        </span>
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
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Online agents */}
      <Dialog
        open={hubOpen}
        onOpenChange={(open) => {
          setHubOpen(open);
          if (open) void loadAgents();
        }}
      >
        <DialogTrigger asChild>
          <Button variant="outline" size="sm">
            <Users className="size-4" />
            Online
            <Badge variant="secondary">{agents.length}</Badge>
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Who is online</DialogTitle>
            <DialogDescription>
              Type <code className="rounded bg-muted px-1 py-0.5">@identity message</code> to message one agent, or <code className="rounded bg-muted px-1 py-0.5">@broadcast message</code> to reach everyone.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            {agentsLoading ? (
              <p className="text-muted-foreground text-sm">Looking for online agents…</p>
            ) : agents.length ? (
              <ScrollArea className="max-h-56 rounded-md border p-2">
                <div className="flex flex-col gap-1">
                  {agents.map((agent) => {
                    const identity = String(
                      agent.identity || agent.id || agent.agent_id || "",
                    );
                    return (
                      <div
                        key={identity}
                        className="flex items-center gap-3 rounded-md px-2 py-2"
                      >
                        <span
                          className="size-2 shrink-0 rounded-full bg-emerald-500 shadow-[0_0_0_3px_rgb(16_185_129/0.12)]"
                          aria-label="online"
                          title="online"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center justify-between gap-2 text-sm font-medium">
                            <span className="truncate">{identity || "agent"}</span>
                            <span className="text-muted-foreground text-xs">online</span>
                          </span>
                          <span className="text-muted-foreground block truncate text-xs">
                            {agent.agent_name || agent.profile_name || "agent"}
                          </span>
                        </span>
                      </div>
                    );
                  })}
                </div>
              </ScrollArea>
            ) : (
              <p className="text-muted-foreground rounded-md border border-dashed p-4 text-sm">
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
              Refresh agents
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Settings */}
      <Dialog
        open={settingsOpen}
        onOpenChange={(open) => {
          setSettingsOpen(open);
          if (open) void loadSettings();
        }}
      >
        <DialogTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            aria-label="Session settings trigger"
          >
            <Settings2 className="size-4" />
            Settings
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Session settings</DialogTitle>
            <DialogDescription>
              Live settings for this daemon. Changes apply to the current
              session and do not rewrite your saved profile.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 text-sm">
            <div className="grid grid-cols-[7rem_1fr] gap-2">
              <span className="text-muted-foreground">Session</span>
              <span className="break-all font-mono">
                {sessionLabel}
              </span>
              <span className="text-muted-foreground">Agent</span>
              <span>{session.identity || session.agent || "unassigned"}</span>
              <span className="text-muted-foreground">Workspace</span>
              <span className="break-all">{workspace}</span>
              <span className="text-muted-foreground">Model</span>
              <span>{session.model || session.profile || "unavailable"}</span>
            </div>
            <div className="bg-muted/30 rounded-md border p-3 text-xs">
              {settingsBusy ? (
                <span className="text-muted-foreground">Refreshing daemon state…</span>
              ) : settings ? (
                <div className="grid gap-1.5">
                  <div className="flex items-center gap-2 font-medium">
                    <CheckCircle2 className="size-3.5 text-emerald-500" />
                    engine connected
                  </div>
                  <span className="text-muted-foreground">
                    pid {String(settings.system?.daemon_pid || session.daemon_pid || "—")} ·{" "}
                    {String(settings.system?.git_branch || "no git branch")}
                  </span>
                  <span className="text-muted-foreground">
                    hub {String(settings.hub?.my_identity || session.identity || "unassigned")} ·{" "}
                    {String(settings.processing?.is_processing ? "working" : "idle")}
                  </span>
                  {settings.agent?.description ? (
                    <span className="text-muted-foreground">
                      {String(settings.agent.description)}
                    </span>
                  ) : null}
                </div>
              ) : (
                <span className="text-muted-foreground">No live state available.</span>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => void loadSettings()}
            >
              <RefreshCw className="size-4" />
              Refresh
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Clear history */}
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button variant="ghost" size="sm">
            <Eraser className="size-4" />
            Clear
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear conversation history?</AlertDialogTitle>
            <AlertDialogDescription>
              Removes every message from {sessionLabel}. The session keeps
              running. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void clearHistory()}>
              Clear history
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
