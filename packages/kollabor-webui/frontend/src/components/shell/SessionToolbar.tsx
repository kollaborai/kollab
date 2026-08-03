import { useCallback, useEffect, useState } from "react";
import { Eraser, Plug, RefreshCw, Send } from "lucide-react";
import type { EngineApi, HubAgent, Session, SessionMcp } from "@/api";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const APPROVAL_MODES = [
  "confirm_all",
  "default",
  "auto_approve_edits",
  "trust_all",
] as const;

// GET /sessions serialises approval_mode as the ApprovalMode enum's integer
// value (auto(): DEFAULT=1, CONFIRM_ALL=2, AUTO_APPROVE_EDITS=3, TRUST_ALL=4),
// while POST /permissions/mode takes the lowercase string. /permissions returns
// the SCREAMING_CASE name. Normalise all three so the Select never renders a
// raw "2".
const MODE_BY_ORDINAL: Record<string, string> = {
  "1": "default",
  "2": "confirm_all",
  "3": "auto_approve_edits",
  "4": "trust_all",
};

function normalizeApprovalMode(value: unknown): string {
  if (value === null || value === undefined) return "confirm_all";
  const raw = String(value);
  return MODE_BY_ORDINAL[raw] ?? raw.toLowerCase();
}

export function SessionToolbar({
  api,
  session,
  onStatus,
}: {
  api: EngineApi;
  session: Session;
  onStatus: (message: string) => void;
}) {
  const [mode, setMode] = useState(() =>
    normalizeApprovalMode(session.approval_mode),
  );
  const [mcp, setMcp] = useState<SessionMcp | null>(null);
  const [agents, setAgents] = useState<HubAgent[]>([]);
  const [hubTarget, setHubTarget] = useState("");
  const [hubContent, setHubContent] = useState("");
  const [mcpBusy, setMcpBusy] = useState<string | null>(null);
  const [mcpOpen, setMcpOpen] = useState(false);
  const [hubOpen, setHubOpen] = useState(false);
  const [hubSending, setHubSending] = useState(false);

  const fail = useCallback(
    (error: unknown) =>
      onStatus(error instanceof Error ? error.message : String(error)),
    [onStatus],
  );

  const loadMcp = useCallback(async () => {
    try {
      setMcp(await api.getSessionMcp(session.session_id));
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
      onStatus(`Approval mode: ${resolved}`);
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
    try {
      const result = await api.listHubAgents(true);
      setAgents(result.agents || []);
    } catch (error) {
      fail(error);
    }
  };

  const sendHubMessage = async () => {
    if (!hubTarget.trim() || !hubContent.trim()) return;
    setHubSending(true);
    try {
      await api.sendHubMessage(hubTarget.trim(), hubContent.trim());
      setHubContent("");
      onStatus(`Hub message sent to ${hubTarget.trim()}`);
      setHubOpen(false);
    } catch (error) {
      fail(error);
    } finally {
      setHubSending(false);
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
  const connectedCount = servers.filter(
    ([, info]) => info.status === "connected",
  ).length;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select value={mode} onValueChange={(next) => void changeMode(next)}>
        <SelectTrigger size="sm" className="w-[11rem]" aria-label="Approval mode">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {APPROVAL_MODES.map((value) => (
            <SelectItem key={value} value={value}>
              {value}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* MCP servers */}
      <Dialog open={mcpOpen} onOpenChange={setMcpOpen}>
        <DialogTrigger asChild>
          <Button variant="outline" size="sm">
            <Plug className="size-4" />
            MCP
            <Badge variant="secondary">
              {connectedCount}/{servers.length}
            </Badge>
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>MCP servers</DialogTitle>
            <DialogDescription>
              Connect or disconnect MCP servers for this session.
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-[50vh]">
            <div className="flex flex-col gap-2 pr-3">
              {servers.length ? (
                servers.map(([name, info]) => {
                  const connected = info.status === "connected";
                  return (
                    <div
                      key={name}
                      className="flex items-center justify-between gap-3 rounded-md border p-2.5"
                    >
                      <div className="flex min-w-0 flex-col">
                        <span className="truncate text-sm font-medium">
                          {name}
                        </span>
                        <span className="text-muted-foreground text-xs">
                          {connected ? "connected" : "offline"}
                          {info.tool_count ? ` · ${info.tool_count} tools` : ""}
                        </span>
                      </div>
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

      {/* Hub */}
      <Dialog open={hubOpen} onOpenChange={setHubOpen}>
        <DialogTrigger asChild>
          <Button variant="outline" size="sm">
            <Send className="size-4" />
            Hub
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Send a hub message</DialogTitle>
            <DialogDescription>
              Message another agent on the kollab mesh.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="hub-target">Agent identity</Label>
              <Input
                id="hub-target"
                value={hubTarget}
                onChange={(event) => setHubTarget(event.target.value)}
                placeholder="lapis"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="hub-content">Message</Label>
              <Input
                id="hub-content"
                value={hubContent}
                onChange={(event) => setHubContent(event.target.value)}
                placeholder="status?"
              />
            </div>
            {agents.length ? (
              <ScrollArea className="max-h-40 rounded-md border">
                <pre className="p-2 text-xs">
                  {JSON.stringify(agents, null, 2)}
                </pre>
              </ScrollArea>
            ) : null}
          </div>
          <DialogFooter className="sm:justify-between">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void loadAgents()}
            >
              <RefreshCw className="size-4" />
              Refresh agents
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={hubSending || !hubTarget.trim() || !hubContent.trim()}
              onClick={() => void sendHubMessage()}
            >
              {hubSending ? "Sending…" : "Send"}
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
              Removes every message from {session.session_id}. The session keeps
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
