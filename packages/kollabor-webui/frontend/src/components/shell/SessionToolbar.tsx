import { GemAvatar } from "@/components/gems/GemAvatar";
import { titleCase } from "@/components/panels/panel-model";
import {
  type ComponentProps,
  forwardRef,
  useCallback,
  useEffect,
  useState,
} from "react";
import {
  Eraser,
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
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import {
  APPROVAL_MODE_OPTIONS,
  formatApprovalMode,
  normalizeApprovalMode,
} from "@/utils/approval-mode";
import { sessionHeading } from "@/utils/session-display";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// Header controls: borderless until hovered, so the bar reads as one strip.
const GHOST_TRIGGER =
  "h-8 gap-1.5 border-transparent bg-transparent px-2 shadow-none hover:bg-accent dark:bg-transparent dark:hover:bg-accent/50";

/** A slim header control: a ghost icon (plus a count) whose tooltip names it. */
const ToolbarButton = forwardRef<
  HTMLButtonElement,
  ComponentProps<typeof Button> & { label: string }
>(function ToolbarButton({ label, className, children, ...props }, ref) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          ref={ref}
          type="button"
          variant="ghost"
          size="sm"
          aria-label={label}
          className={cn("text-muted-foreground hover:text-foreground h-8 gap-1 px-2", className)}
          {...props}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
});

export function SessionToolbar({
  api,
  session,
  profiles,
  onStatus,
  onSessionUpdated,
  onOpenSettings,
  onHistoryCleared,
}: {
  api: EngineApi;
  session: Session;
  profiles: Profile[];
  onStatus: (message: string) => void;
  onSessionUpdated: (session: Session) => void;
  onOpenSettings: () => void;
  /** Called once the engine has cleared the history; resets the open thread. */
  onHistoryCleared: () => Promise<void>;
}) {
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
  const sessionLabel = sessionHeading(session);
  const connectedCount = servers.filter(
    ([, info]) => info.status === "connected",
  ).length;

  const activeProfile = profiles.find(
    (profile) => profile.name === (session.profile || profiles[0]?.name),
  );

  return (
    <div className="ml-auto flex flex-wrap items-center gap-0.5">
      <Select
        value={session.profile || profiles[0]?.name || "default"}
        onValueChange={(next) => void changeProfile(next)}
        disabled={!profiles.length}
      >
        <SelectTrigger
          size="sm"
          className={cn(GHOST_TRIGGER, "max-w-[7.5rem] sm:max-w-[15rem]")}
          aria-label="Model"
        >
          {/* Below xl the model shows alone: "gpt-6-luna · openai-oauth" cut to
              "gpt-6-luna ·" on phones and wrapped the header at tablet width.
              The list below still names each profile. One
              wrapping span: SelectValue is a gap-2 flex row, which doubled the
              space before the dot. */}
          <SelectValue placeholder="Model">
            {activeProfile ? (
              <span className="min-w-0 truncate">
                {activeProfile.model || activeProfile.name}
                {activeProfile.model && activeProfile.name !== activeProfile.model ? (
                  <span className="text-muted-foreground max-xl:hidden"> · {activeProfile.name}</span>
                ) : null}
              </span>
            ) : undefined}
          </SelectValue>
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

      <Select value={mode} onValueChange={(next) => void changeMode(next)}>
        <SelectTrigger
          size="sm"
          className={GHOST_TRIGGER}
          aria-label="Approval mode"
          title={formatApprovalMode(mode)}
        >
          {/* Trust All skips every approval prompt: an open shield says so. */}
          {mode === "trust_all" ? (
            <ShieldOff className="size-4 text-amber-500" />
          ) : (
            <ShieldCheck className="size-4" />
          )}
          <span className="hidden sm:inline">
            <SelectValue>{formatApprovalMode(mode)}</SelectValue>
          </span>
        </SelectTrigger>
        <SelectContent>
          {APPROVAL_MODE_OPTIONS.map(({ value, label }) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Separator orientation="vertical" className="mx-1 hidden data-[orientation=vertical]:h-4 sm:block" />

      {/* MCP servers */}
      <Dialog
        open={mcpOpen}
        onOpenChange={(open) => {
          setMcpOpen(open);
          if (open) void loadMcp();
        }}
      >
        <DialogTrigger asChild>
          <ToolbarButton label="MCP Servers">
            <Plug className="size-4" />
            <span className="text-xs tabular-nums">
              {connectedCount}/{allServerNames.length}
            </span>
          </ToolbarButton>
        </DialogTrigger>
        <DialogContent className="sm:max-w-lg">
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
                      className="flex flex-col gap-2 rounded-lg border p-3"
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
                          <span className="text-muted-foreground flex items-start gap-1 text-[11px] break-all">
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
            <div className="flex flex-col gap-2 rounded-lg border p-3">
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
        <DialogTrigger asChild>
          <ToolbarButton label="Who Is Online">
            <Users className="size-4" />
            <span className="text-xs tabular-nums">
              {agents === null ? "…" : agents.length}
            </span>
          </ToolbarButton>
        </DialogTrigger>
        <DialogContent className="sm:max-w-md">
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
              <p className="text-muted-foreground rounded-md border border-dashed p-4 text-sm">
                Online agent status is unavailable. Refresh to try again.
              </p>
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
                        <GemAvatar
                          gem={identity}
                          where={String(agent.project || "") || session.device || session.workspace}
                          state="idle"
                          live
                          season="auto"
                          size={28}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center justify-between gap-2 text-sm font-medium">
                            <span className="truncate">{titleCase(identity) || "Agent"}</span>
                            <span className="text-muted-foreground text-xs">Online</span>
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
              Refresh Agents
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Settings: the dialog lives in App (PanelHost) so every entry point shares it */}
      <ToolbarButton label="Settings" onClick={onOpenSettings}>
        <Settings2 className="size-4" />
      </ToolbarButton>

      {/* Clear history */}
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <ToolbarButton label="Clear History">
            <Eraser className="size-4" />
          </ToolbarButton>
        </AlertDialogTrigger>
        <AlertDialogContent>
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
    </div>
  );
}
