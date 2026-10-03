import { useCallback, useEffect, useState } from "react";
import {
  Eraser,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  Settings2,
  Terminal,
  Trash2,
  Users,
} from "lucide-react";
import type {
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
  AlertDialogTrigger,
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
  onOpenSettings,
}: {
  api: EngineApi;
  session: Session;
  profiles: Profile[];
  onStatus: (message: string) => void;
  onSessionUpdated: (session: Session) => void;
  onOpenSettings: () => void;
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
    try {
      await api.deleteMcpServer(serverName);
      await loadMcp();
      onStatus(`${serverName}: deleted`);
    } catch (error) {
      fail(error);
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
                        <div className="flex items-center gap-1">
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
                            onClick={() => void deleteServer(name)}
                          >
                            <Trash2 className="size-4" />
                          </Button>
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
            <div className="flex flex-col gap-2 rounded-lg border p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold">
                  {mcpDraft.original ? `Edit ${mcpDraft.original}` : "Add server"}
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
                className="border-input bg-background placeholder:text-muted-foreground min-h-16 rounded-md border px-2 py-1.5 text-sm"
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
                enabled
              </label>
              <Button
                type="button"
                size="sm"
                disabled={!mcpDraft.command.trim() || (!mcpDraft.original && !mcpDraft.name.trim())}
                onClick={() => void saveServer()}
              >
                Save server
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
              Add server
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

      {/* Settings: the dialog lives in App (PanelHost) so every entry point shares it */}
      <Button
        variant="outline"
        size="sm"
        aria-label="Session settings trigger"
        onClick={onOpenSettings}
      >
        <Settings2 className="size-4" />
        Settings
      </Button>

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
