/**
 * Branch dropdown for the git panel: list, create, switch, check out a remote branch and delete
 * local branches in the chat's folder. Ported from mentiko's components/editor/branch-selector.tsx.
 */
import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  ChevronDown,
  Download,
  GitBranch as GitBranchIcon,
  Trash2,
  X,
} from "lucide-react";
import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";
import { cn } from "@/lib/utils";
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
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type {
  GitBranch,
  GitBranchCreateResult,
  GitBranchDeleteResult,
  GitBranchListResult,
  GitBranchSwitchResult,
} from "./git-types";

interface BranchSelectorProps {
  sessionId: string;
  /** Absolute path of the chat's folder (the workspace the git panel works in). */
  workspace: string;
  /** Called after a branch switch succeeds. */
  onBranchSwitch?: (branchName: string) => void;
}

/** The branch whose deletion is waiting for confirmation. */
interface DeleteTarget {
  branchName: string;
  force: boolean;
}

/** `error.message` of an engine error envelope, when the body has one. */
function errorText(body: unknown): string | undefined {
  if (typeof body !== "object" || body === null || !("error" in body)) return undefined;
  const error: unknown = body.error;
  if (typeof error !== "object" || error === null || !("message" in error)) return undefined;
  return typeof error.message === "string" ? error.message : undefined;
}

/** Git ref format check for a new branch name; returns the reason it is invalid, or null. */
function validateBranchName(name: string): string | null {
  if (!name.trim()) return null; // empty is fine on first render
  if (name.length > 255) return "Branch name too long (max 255 characters)";
  if (/[~^:?*\[\\@{}]/.test(name)) return "Contains invalid characters: ~ ^ : ? * [ \\ @ { }";
  if (name.startsWith(".") || name.endsWith(".")) return "Cannot start or end with dot";
  if (name.includes("..")) return "Cannot contain consecutive dots";
  if (name === "@") return "Cannot be a single @";
  if (name.includes("@{")) return "Cannot contain @{";
  if (name.endsWith(".lock")) return "Cannot end with .lock";
  return null;
}

export function BranchSelector({ sessionId, workspace, onBranchSwitch }: BranchSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [branches, setBranches] = useState<GitBranch[]>([]);
  const [currentBranch, setCurrentBranch] = useState("");
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createInput, setCreateInput] = useState("");
  const [createValidationError, setCreateValidationError] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<DeleteTarget | null>(null);

  const localBranches = branches.filter((b) => !b.isRemote);
  const remoteBranches = branches.filter((b) => b.isRemote);
  const canCreateBranch = createInput.trim().length > 0 && !createValidationError;
  const isDeleting =
    showDeleteConfirm !== null && actionLoading === showDeleteConfirm.branchName;

  // POST /api/git (codeFetch maps it to the engine's /code/git), with the folder as `workspace`.
  const gitPost = useCallback(
    async <T,>(action: string, payload: Record<string, unknown>): Promise<T> => {
      const res = await codeFetch("/api/git", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace, action, ...payload }),
      }, { sessionId, root: workspace });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        throw new Error(errorText(body) ?? `HTTP ${res.status}: git error`);
      }
      return unwrapCodeData<T>(body);
    },
    [sessionId, workspace]
  );

  const refreshBranches = useCallback(async () => {
    try {
      setLoading(true);
      const result = await gitPost<GitBranchListResult>("list_branches", {});
      setBranches(result.branches);
      setCurrentBranch(result.current);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load branches");
    } finally {
      setLoading(false);
    }
  }, [gitPost]);

  const handleCreateInputChange = (value: string) => {
    setCreateInput(value);
    if (!value.trim()) {
      setCreateValidationError(null);
      return;
    }
    const exists = branches.some(
      (b) => b.name.toLowerCase() === value.trim().toLowerCase()
    );
    setCreateValidationError(exists ? "Branch already exists" : validateBranchName(value));
  };

  /** Create a new branch from the current HEAD. */
  const handleCreateBranch = async (event: FormEvent) => {
    event.preventDefault();
    if (!canCreateBranch) return;
    try {
      setActionLoading("__create__");
      const result = await gitPost<GitBranchCreateResult>("create_branch", {
        branchName: createInput.trim(),
      });
      if (result.ok) {
        setCreateInput("");
        setCreateValidationError(null);
        setError(null);
        await refreshBranches();
      } else {
        setError(result.error || "Failed to create branch");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create branch");
    } finally {
      setActionLoading(null);
    }
  };

  const handleSwitchBranch = async (branchName: string) => {
    if (branchName === currentBranch) {
      setIsOpen(false);
      return;
    }
    try {
      setActionLoading(branchName);
      const result = await gitPost<GitBranchSwitchResult>("switch_branch", { branchName });
      if (result.ok) {
        setCurrentBranch(result.current || branchName);
        setError(null);
        setIsOpen(false);
        await refreshBranches();
        onBranchSwitch?.(branchName);
      } else {
        setError(result.error || "Failed to switch branch");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to switch branch");
    } finally {
      setActionLoading(null);
    }
  };

  /** Check out a remote-tracking branch as a local branch; git's DWIM creates the tracking branch. */
  const handleCheckoutRemote = async (remoteBranchName: string) => {
    const localName = remoteBranchName.replace(/^[^/]+\//, "");
    if (!localName || localName === remoteBranchName) {
      setError(`Cannot parse remote branch: ${remoteBranchName}`);
      return;
    }
    await handleSwitchBranch(localName);
  };

  /** Delete a branch; a branch with unmerged commits asks again for a force delete. */
  const handleDeleteBranch = async (branchName: string, force = false) => {
    try {
      setActionLoading(branchName);
      const result = await gitPost<GitBranchDeleteResult>("delete_branch", {
        branchName,
        force,
      });
      if (result.ok) {
        setError(null);
        setShowDeleteConfirm(null);
        await refreshBranches();
      } else {
        const isNotMerged = result.error?.toLowerCase().includes("not fully merged") ?? false;
        if (!force && isNotMerged) {
          setShowDeleteConfirm({ branchName, force: true });
          setError("Branch has unmerged changes. Use force delete to remove it.");
        } else {
          setError(result.error || "Failed to delete branch");
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete branch");
    } finally {
      setActionLoading(null);
    }
  };

  // Load the current branch on mount; otherwise the header label stays on "loading…".
  useEffect(() => {
    refreshBranches();
  }, [refreshBranches]);

  useEffect(() => {
    if (isOpen && branches.length === 0) {
      refreshBranches();
    }
  }, [isOpen, branches.length, refreshBranches]);

  // While the menu is open, re-read the branches every 10 s in case another tool changed them.
  useEffect(() => {
    if (!isOpen) return;
    const interval = setInterval(() => {
      refreshBranches();
    }, 10000);
    return () => clearInterval(interval);
  }, [isOpen, refreshBranches]);

  return (
    <>
      <DropdownMenu open={isOpen} onOpenChange={setIsOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex h-6 min-w-0 items-center gap-1.5 rounded-md px-2 text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring"
            title="Switch branch"
            aria-label="Switch git branch"
            aria-expanded={isOpen}
            aria-haspopup="menu"
            data-testid="branch-selector-trigger"
          >
            <GitBranchIcon className="h-3 w-3 shrink-0" />
            <span className="max-w-[120px] truncate font-mono text-[10px]">
              {currentBranch || "loading…"}
            </span>
            <ChevronDown
              className={cn(
                "h-3 w-3 shrink-0 transition-transform",
                isOpen && "rotate-180"
              )}
            />
          </button>
        </DropdownMenuTrigger>

        <DropdownMenuContent
          className="w-72"
          aria-label="Available branches"
        >
          {error && (
            <div className="mx-1 mb-1 flex items-start justify-between gap-2 rounded-md bg-destructive/10 px-2 py-1.5 text-[10px] text-destructive">
              <span>{error}</span>
              <button
                type="button"
                onClick={() => setError(null)}
                className="shrink-0 rounded-sm p-0.5 text-destructive/70 hover:text-destructive"
                aria-label="Dismiss error"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          )}

          {loading && branches.length === 0 ? (
            <div className="px-2 py-6 text-center text-[10px] text-muted-foreground">
              Loading…
            </div>
          ) : (
            <>
              <DropdownMenuGroup>
                <form
                  onSubmit={handleCreateBranch}
                  className="space-y-1 rounded-md bg-muted/60 p-1.5"
                >
                  <input
                    type="text"
                    value={createInput}
                    onChange={(e) => handleCreateInputChange(e.target.value)}
                    // Keep typing (Enter, Space, letters) away from the menu's own key handling.
                    onKeyDown={(e) => e.stopPropagation()}
                    placeholder="New branch name…"
                    className="w-full rounded-sm bg-background px-2 py-1 font-mono text-[11px] text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring"
                    autoFocus
                    autoComplete="off"
                  />
                  {createValidationError && (
                    <span className="block text-[10px] text-destructive">
                      {createValidationError}
                    </span>
                  )}
                  <Button
                    type="submit"
                    size="xs"
                    variant="secondary"
                    className="w-full"
                    disabled={!canCreateBranch || actionLoading !== null}
                  >
                    {actionLoading === "__create__" ? "Creating…" : "Create"}
                  </Button>
                </form>
              </DropdownMenuGroup>

              {localBranches.length > 0 && (
                <DropdownMenuGroup className="mt-1">
                  <span className="block px-2 pb-0.5 pt-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                    Local ({localBranches.length})
                  </span>
                  {localBranches.map((branch) => (
                    <BranchItem
                      key={branch.name}
                      branch={branch}
                      isCurrent={currentBranch === branch.name}
                      isLoading={actionLoading === branch.name}
                      onSwitch={handleSwitchBranch}
                      onDelete={() => {
                        setIsOpen(false);
                        setShowDeleteConfirm({ branchName: branch.name, force: false });
                      }}
                    />
                  ))}
                </DropdownMenuGroup>
              )}

              {remoteBranches.length > 0 && (
                <DropdownMenuGroup className="mt-1">
                  <span className="block px-2 pb-0.5 pt-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                    Remote ({remoteBranches.length})
                  </span>
                  {remoteBranches.map((branch) => (
                    <BranchItem
                      key={branch.name}
                      branch={branch}
                      isRemote
                      isLoading={actionLoading === branch.name}
                      onSwitch={handleCheckoutRemote}
                      onCheckout={() => handleCheckoutRemote(branch.name)}
                    />
                  ))}
                </DropdownMenuGroup>
              )}

              {localBranches.length === 0 && remoteBranches.length === 0 && (
                <div className="px-2 py-4 text-center text-[10px] text-muted-foreground">
                  No branches found
                </div>
              )}
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog
        open={showDeleteConfirm !== null}
        onOpenChange={(open) => {
          if (!open && actionLoading === null) setShowDeleteConfirm(null);
        }}
      >
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete branch?</AlertDialogTitle>
            <AlertDialogDescription>
              {showDeleteConfirm?.force
                ? `This will force delete "${showDeleteConfirm.branchName}" even if it has unmerged changes.`
                : `This will delete the branch "${showDeleteConfirm?.branchName ?? ""}".`}
            </AlertDialogDescription>
            {error && showDeleteConfirm && (
              <p className="text-sm text-destructive">{error}</p>
            )}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={isDeleting}
              onClick={(event) => {
                // Keep the dialog open while the request runs; it closes when the delete succeeds.
                event.preventDefault();
                if (showDeleteConfirm) {
                  void handleDeleteBranch(showDeleteConfirm.branchName, showDeleteConfirm.force);
                }
              }}
            >
              {isDeleting ? "Deleting…" : showDeleteConfirm?.force ? "Force Delete" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// ── sub-components ──────────────────────────────────────────────────────────

interface BranchItemProps {
  branch: GitBranch;
  isCurrent?: boolean;
  isRemote?: boolean;
  isLoading?: boolean;
  onSwitch: (name: string) => void;
  onDelete?: (name: string) => void;
  onCheckout?: () => void;
}

/** One branch row: its name and metadata, plus a checkout (remote) or delete (local) icon. */
function BranchItem({
  branch,
  isCurrent,
  isRemote,
  isLoading,
  onSwitch,
  onDelete,
  onCheckout,
}: BranchItemProps) {
  return (
    <DropdownMenuItem
      disabled={isLoading}
      className={cn(
        "flex cursor-pointer items-center justify-between gap-2 rounded-sm px-2 py-1.5",
        isCurrent && "bg-accent",
        isLoading && "pointer-events-none opacity-50"
      )}
      onClick={() => onSwitch(branch.name)}
      aria-current={isCurrent ? "true" : "false"}
      aria-label={`Branch ${branch.name}${isCurrent ? " (current)" : ""}${branch.tracking ? ` tracking ${branch.tracking}` : ""}${isRemote ? " (remote, click to checkout)" : ""}`}
      data-branch={branch.name}
      data-is-current={isCurrent}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          {isCurrent && (
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-highlight"
              title="Current branch"
              aria-hidden="true"
            />
          )}
          <div className="truncate font-mono text-xs text-foreground">{branch.name}</div>
          {isRemote && (
            <span className="shrink-0 text-[10px] text-muted-foreground">remote</span>
          )}
        </div>
        {branch.tracking && (
          <div className="ml-3 text-[10px] text-muted-foreground">
            tracking {branch.tracking}
          </div>
        )}
        {branch.lastCommitDate && (
          <div className="ml-3 text-[10px] text-muted-foreground">
            {branch.lastCommitDate}
          </div>
        )}
      </div>

      {isRemote ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onCheckout?.();
          }}
          className="shrink-0 rounded-sm p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-highlight"
          title={`Checkout ${branch.name} as local branch`}
          aria-label={`Checkout remote branch ${branch.name} as a local branch`}
        >
          <Download className="h-3 w-3" />
        </button>
      ) : onDelete ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete(branch.name);
          }}
          className="shrink-0 rounded-sm p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-destructive"
          title={`Delete ${branch.name}`}
          aria-label={`Delete branch ${branch.name}`}
        >
          <Trash2 className="h-3 w-3" />
        </button>
      ) : null}
    </DropdownMenuItem>
  );
}
