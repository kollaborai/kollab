import { editorOwner, editorOwnerEpoch } from "@/components/code/workspace-tabs";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Archive, Clock, GitBranch, Loader2, Minus, Plus, RefreshCw } from "lucide-react";
import { useEditorStore } from "@/components/code/editor-store";
import { BranchSelector } from "@/components/code/git/branch-selector";
import { StashSelector } from "@/components/code/git/stash-selector";
import { Button } from "@/components/ui/button";
import { codeFetch, OUTDATED_NOTICE, unwrapCodeData } from "@/lib/code-fetch";
import { DaemonNotice } from "@/components/code/DaemonNotice";
import type {
  GitLogEntry,
  GitStatusEntry,
  GitStatusMutation,
  GitStatusResult,
} from "./git-types";

const POLL_MS = 5000;

/** Absolute path of a repo-relative path inside the workspace folder. */
function absolutePath(workspace: string, relativePath: string): string {
  return workspace.endsWith("/") ? `${workspace}${relativePath}` : `${workspace}/${relativePath}`;
}

// ── status badge ────────────────────────────────────────────────────────────

function StatusBadge({ code }: { code: string }) {
  const x = code[0];
  const y = code[1];
  let label = "?";
  let tone = "text-muted-foreground";

  if (x === "?" && y === "?") {
    label = "U";
    tone = "text-highlight";
  } else if (x === "A") {
    label = "A";
    tone = "text-highlight";
  } else if (x === "R") {
    label = "R";
  } else if (x === "D" || y === "D") {
    label = "D";
    tone = "text-destructive";
  } else if (x === "M" || y === "M") {
    label = "M";
    tone = "text-amber-600";
  }

  return <span className={`w-3 shrink-0 font-mono text-[9px] font-bold ${tone}`}>{label}</span>;
}

// ── file row ────────────────────────────────────────────────────────────────

function FileRow({
  file,
  onStage,
  onUnstage,
  onOpen,
}: {
  file: GitStatusEntry;
  onStage: (path: string) => void;
  onUnstage: (path: string) => void;
  onOpen: (file: GitStatusEntry) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const canStage = file.unstaged || file.untracked;
  const canUnstage = file.staged;
  const dir = file.path.includes("/") ? file.path.replace(`/${file.name}`, "") : null;

  return (
    <div
      className="flex cursor-pointer items-center gap-1.5 px-3 py-0.5 transition-colors hover:bg-accent"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={() => onOpen(file)}
    >
      <StatusBadge code={file.statusCode} />
      <span className="flex-1 truncate font-mono text-[11px] text-foreground/80" title={file.path}>
        {file.name}
        {dir && <span className="ml-1 text-[9px] text-muted-foreground">{dir}</span>}
      </span>
      {hovered && (
        <div className="flex shrink-0 items-center gap-0.5">
          {canStage && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onStage(file.path);
              }}
              title="Stage file"
              className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition-colors hover:text-highlight"
            >
              <Plus className="h-3 w-3" />
            </button>
          )}
          {canUnstage && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onUnstage(file.path);
              }}
              title="Unstage file"
              className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition-colors hover:text-amber-600"
            >
              <Minus className="h-3 w-3" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── section header ──────────────────────────────────────────────────────────

function SectionHeader({
  label,
  count,
  action,
}: {
  label: string;
  count: number;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex shrink-0 items-center justify-between px-3 py-1">
      <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="flex items-center gap-1">
        <span className="font-mono text-[10px] text-muted-foreground">{count}</span>
        {action && (
          <button
            type="button"
            onClick={action.onClick}
            title={action.label}
            className="rounded px-1.5 py-0.5 text-[9px] text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            {action.label}
          </button>
        )}
      </div>
    </div>
  );
}

// ── view tab ────────────────────────────────────────────────────────────────

function TabButton({
  title,
  active,
  onClick,
  children,
}: {
  title: string;
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={active}
      onClick={onClick}
      className={`flex h-6 items-center justify-center gap-1 rounded px-1.5 text-[10px] transition-colors ${
        active
          ? "bg-accent font-medium text-accent-foreground"
          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
      }`}
    >
      {children}
    </button>
  );
}

// ── log view ────────────────────────────────────────────────────────────────

function LogView({
  entries,
  onCommitClick,
}: {
  entries: GitLogEntry[];
  onCommitClick: (entry: GitLogEntry) => void;
}) {
  if (!entries.length) {
    return <div className="px-3 py-4 text-center text-xs text-muted-foreground">No commits yet</div>;
  }

  return (
    <div className="flex-1 overflow-y-auto">
      {entries.map((entry) => (
        <div
          key={entry.hash}
          onClick={() => onCommitClick(entry)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onCommitClick(entry);
            }
          }}
          role="button"
          tabIndex={0}
          title="Open commit diff"
          className="cursor-pointer px-3 py-1.5 transition-colors hover:bg-accent focus:bg-accent focus:outline-none"
        >
          <div className="flex items-center gap-2">
            <span className="w-12 shrink-0 font-mono text-[9px] text-muted-foreground">
              {entry.shortHash}
            </span>
            {entry.refs && (
              <span className="max-w-[60px] shrink-0 truncate font-mono text-[9px] text-highlight">
                {entry.refs.split(",")[0]?.trim()}
              </span>
            )}
          </div>
          <div className="mt-0.5 truncate text-[11px] text-foreground/80">{entry.message}</div>
          <div className="mt-0.5 flex items-center gap-2">
            <span className="text-[9px] text-muted-foreground">{entry.author}</span>
            <span className="text-[9px] text-muted-foreground">{entry.date}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── main component ──────────────────────────────────────────────────────────

type ActiveView = "status" | "stash" | "log";

export interface GitPanelProps {
  sessionId: string;
  /** Absolute path of the folder the chat runs in (the session's workspace). */
  workspace: string;
  /** Open a file (absolute path) in the editor. */
  onOpenFile: (absPath: string) => void;
  /** Open a diff tab: the file's absolute path and its HEAD content (null when the file is new). */
  onOpenDiff: (absPath: string, headContent: string | null) => void;
}

export function GitPanel({ sessionId, workspace, onOpenFile, onOpenDiff }: GitPanelProps) {
  // A commit's patch is a read-only tab with no file on disk behind it, and the diff props
  // cannot open one, so that tab goes to the editor store directly.
  const openVirtualFile = useEditorStore((s) => s.openVirtualFile);
  const setFileLoading = useEditorStore((s) => s.setFileLoading);
  const activePaneId = useEditorStore((s) => s.activePaneId);

  const [status, setStatus] = useState<GitStatusResult | null>(null);
  const [logEntries, setLogEntries] = useState<GitLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [commitMsg, setCommitMsg] = useState("");
  const [activeView, setActiveView] = useState<ActiveView>("status");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // POST /api/git with the action body and the folder; the envelope's data comes back or its error throws.
  const gitPost = useCallback(
    async <T,>(body: Record<string, unknown>): Promise<T> => {
      const res = await codeFetch("/api/git", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace, ...body }),
      }, { sessionId, root: workspace });
      const payload: unknown = await res.json();
      return unwrapCodeData<T>(payload);
    },
    [sessionId, workspace],
  );

  const refresh = useCallback(async () => {
    try {
      setStatus(await gitPost<GitStatusResult>({ action: "status" }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to read git status");
    } finally {
      setLoading(false);
    }
  }, [gitPost]);

  const refreshLog = useCallback(async () => {
    try {
      const data = await gitPost<{ entries: GitLogEntry[] }>({ action: "log" });
      setLogEntries(data.entries ?? []);
    } catch {
      // The log is secondary; the status view carries the error when it matters.
    }
  }, [gitPost]);

  // First load, then poll the status while the panel is mounted.
  useEffect(() => {
    void refresh();
    void refreshLog();
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [refresh, refreshLog]);

  // Opening the log view reloads it.
  useEffect(() => {
    if (activeView === "log") void refreshLog();
  }, [activeView, refreshLog]);

  // Stage, unstage, commit and push all answer with the new status; a failed call sets the banner.
  const mutate = useCallback(
    async (body: Record<string, unknown>, failure: string): Promise<GitStatusMutation | null> => {
      setBusy(true);
      try {
        const result = await gitPost<GitStatusMutation>(body);
        if (result.status) setStatus(result.status);
        return result;
      } catch (e) {
        const message = e instanceof Error ? e.message : failure;
        setError(body.action === "push" ? `${message} Check the remote before retrying; the push may have completed.` : message);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [gitPost],
  );

  const handleStage = useCallback(
    (path: string) => void mutate({ action: "stage", paths: [path] }, "Stage failed"),
    [mutate],
  );

  const handleUnstage = useCallback(
    (path: string) => void mutate({ action: "unstage", paths: [path] }, "Unstage failed"),
    [mutate],
  );

  const handleStageAll = useCallback(
    () => void mutate({ action: "stage_all" }, "Stage all failed"),
    [mutate],
  );

  const handleUnstageAll = useCallback(
    () => void mutate({ action: "unstage_all" }, "Unstage all failed"),
    [mutate],
  );

  const handleCommit = useCallback(async () => {
    if (!commitMsg.trim()) return;
    const result = await mutate({ action: "commit", message: commitMsg }, "Commit failed");
    if (!result) return;
    if (result.ok) {
      setCommitMsg("");
      void refreshLog();
    } else {
      setError("Commit failed. Make sure files are staged.");
    }
  }, [commitMsg, mutate, refreshLog]);

  const handlePush = useCallback(async () => {
    const result = await mutate({ action: "push" }, "Push failed");
    if (result && !result.ok) setError("Push failed. Check the remote and your auth.");
  }, [mutate]);

  // A file opens as a diff against HEAD. A file new to the repo has no HEAD version, so its
  // diff base is null. If HEAD cannot be read, the file opens plain.
  const handleFileOpen = useCallback(
    async (file: GitStatusEntry) => {
      const absPath = absolutePath(workspace, file.path);
      if (file.untracked || file.statusCode.startsWith("A")) {
        onOpenDiff(absPath, null);
        return;
      }
      const owner = editorOwner();
      const epoch = editorOwnerEpoch();
      try {
        const head = await gitPost<{ content: string }>({ action: "show", path: file.path });
        if (owner === editorOwner() && epoch === editorOwnerEpoch()) onOpenDiff(absPath, head.content);
      } catch {
        if (owner === editorOwner() && epoch === editorOwnerEpoch()) onOpenFile(absPath);
      }
    },
    [gitPost, onOpenDiff, onOpenFile, workspace],
  );

  // A commit's full patch opens as a read-only diff tab in the editor.
  const handleCommitClick = useCallback(
    async (entry: GitLogEntry) => {
      if (!activePaneId) return;
      // NUL cannot occur in a filesystem path; full hashes avoid prefix collisions.
      const virtualPath = `\0commit:${entry.hash}`;
      const name = `${entry.shortHash} ${entry.message}`.slice(0, 60);

      const owner = editorOwner();
      const epoch = editorOwnerEpoch();
      openVirtualFile(activePaneId, virtualPath, name, "diff", "");
      setFileLoading(virtualPath, true);
      const bufferId = useEditorStore.getState().fileCache.get(virtualPath)?.bufferId;
      const isCurrent = () => owner === editorOwner() && epoch === editorOwnerEpoch() &&
        useEditorStore.getState().fileCache.get(virtualPath)?.bufferId === bufferId;
      try {
        const data = await gitPost<{ content?: string }>({
          action: "show_commit",
          commitHash: entry.hash,
        });
        if (isCurrent()) openVirtualFile(activePaneId, virtualPath, name, "diff", data.content ?? "");
      } catch (e) {
        if (!isCurrent()) return;
        const reason = e instanceof Error ? e.message : String(e);
        openVirtualFile(
          activePaneId,
          virtualPath,
          name,
          "diff",
          `Failed to load commit ${entry.shortHash}: ${reason}`,
        );
      } finally {
        if (isCurrent()) setFileLoading(virtualPath, false);
      }
    },
    [activePaneId, gitPost, openVirtualFile, setFileLoading],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error && !status) {
    if (error === OUTDATED_NOTICE) return <DaemonNotice />;
    return <div className="px-3 py-4 text-center text-[11px] text-destructive">{error}</div>;
  }

  const stagedFiles = status?.files.filter((f) => f.staged) ?? [];
  const changedFiles = status?.files.filter((f) => f.unstaged && !f.untracked) ?? [];
  const untrackedFiles = status?.files.filter((f) => f.untracked) ?? [];
  const totalChanges = status?.files.length ?? 0;

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 px-2 py-1.5">
        <BranchSelector sessionId={sessionId} workspace={workspace} />
      </div>

      {/* view tabs and header actions */}
      <div className="flex shrink-0 items-center justify-between px-3 py-1.5">
        <div className="flex items-center gap-0.5">
          <TabButton title="Changes" active={activeView === "status"} onClick={() => setActiveView("status")}>
            <span className="font-mono">{totalChanges}</span>
          </TabButton>
          <TabButton title="Stashes" active={activeView === "stash"} onClick={() => setActiveView("stash")}>
            <Archive className="h-3 w-3" />
          </TabButton>
          <TabButton title="Log" active={activeView === "log"} onClick={() => setActiveView("log")}>
            <Clock className="h-3 w-3" />
          </TabButton>
        </div>
        <div className="flex items-center gap-1">
          {activeView === "status" && (
            <>
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={busy}
                title="Refresh"
                className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:opacity-40"
              >
                <RefreshCw className="h-3 w-3" />
              </button>
              {totalChanges > 0 && (
                <button
                  type="button"
                  onClick={handleStageAll}
                  disabled={busy}
                  title="Stage all"
                  className="rounded px-1.5 py-0.5 text-[9px] text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:opacity-40"
                >
                  Stage All
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {error && activeView === "status" && (
        <div className="mx-3 mb-1 rounded bg-destructive/10 px-2 py-1 text-[10px] text-destructive">
          {error}
        </div>
      )}

      {activeView === "stash" && (
        <div className="flex-1 overflow-hidden">
          <StashSelector sessionId={sessionId} workspace={workspace} onStashApplied={() => void refresh()} />
        </div>
      )}

      {activeView === "log" && (
        <div className="flex-1 overflow-hidden">
          <LogView entries={logEntries} onCommitClick={handleCommitClick} />
        </div>
      )}

      {activeView === "status" && (
        <>
          <div className="flex-1 overflow-y-auto">
            {stagedFiles.length > 0 && (
              <>
                <SectionHeader
                  label="Staged"
                  count={stagedFiles.length}
                  action={{ label: "Unstage All", onClick: handleUnstageAll }}
                />
                {stagedFiles.map((f) => (
                  <FileRow
                    key={f.path}
                    file={f}
                    onStage={handleStage}
                    onUnstage={handleUnstage}
                    onOpen={handleFileOpen}
                  />
                ))}
              </>
            )}

            {changedFiles.length > 0 && (
              <>
                <SectionHeader
                  label="Changes"
                  count={changedFiles.length}
                  action={{ label: "Stage All", onClick: handleStageAll }}
                />
                {changedFiles.map((f) => (
                  <FileRow
                    key={f.path}
                    file={f}
                    onStage={handleStage}
                    onUnstage={handleUnstage}
                    onOpen={handleFileOpen}
                  />
                ))}
              </>
            )}

            {untrackedFiles.length > 0 && (
              <>
                <SectionHeader
                  label="Untracked"
                  count={untrackedFiles.length}
                  action={{ label: "Stage All", onClick: handleStageAll }}
                />
                {untrackedFiles.map((f) => (
                  <FileRow
                    key={f.path}
                    file={f}
                    onStage={handleStage}
                    onUnstage={handleUnstage}
                    onOpen={handleFileOpen}
                  />
                ))}
              </>
            )}

            {totalChanges === 0 && (
              <div className="px-3 py-6 text-center text-xs text-muted-foreground">No changes</div>
            )}
          </div>

          {/* commit and push: a shaded section, no divider line */}
          <div className="flex shrink-0 flex-col gap-1.5 bg-muted/40 p-2">
            <div className="flex items-center justify-between">
              <div className="flex min-w-0 items-center gap-1.5">
                <GitBranch className="h-3 w-3 shrink-0 text-muted-foreground" />
                <span className="truncate font-mono text-[10px] text-muted-foreground">
                  {status?.branch ?? "…"}
                </span>
              </div>
              {status?.upstream && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => void handlePush()}
                  className="h-6 gap-1 px-2 text-[10px] text-muted-foreground"
                >
                  {status.ahead > 0 && <span className="text-highlight">{status.ahead}</span>}
                  Push
                </Button>
              )}
            </div>

            <textarea
              value={commitMsg}
              onChange={(e) => setCommitMsg(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void handleCommit();
              }}
              placeholder="Commit message"
              rows={2}
              className="w-full resize-none rounded-md bg-muted/50 px-2 py-1.5 font-mono text-[11px] text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-muted-foreground"
            />

            <Button
              type="button"
              size="sm"
              disabled={busy || !commitMsg.trim() || stagedFiles.length === 0}
              onClick={() => void handleCommit()}
              className="h-7 text-xs"
            >
              Commit
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
