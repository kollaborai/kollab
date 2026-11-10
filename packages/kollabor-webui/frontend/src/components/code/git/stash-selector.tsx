import { codeFetch, unwrapCodeData } from "@/lib/code-fetch";

import { useState, useCallback, useEffect, useRef, type RefObject } from "react";
import {
  Archive,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { cva } from "class-variance-authority";
import { cn } from "@/lib/utils";
import type {
  GitStash,
  GitStashApplyResult,
  GitStashCreateResult,
  GitStashDropResult,
  GitStashListResult,
} from "@/components/code/git/git-types";

// ── types ──────────────────────────────────────────────────────────────────

interface StashSelectorProps {
  sessionId: string;
  /** The chat's folder; every git request sends it as `workspace`. */
  workspace: string;
  onStashApplied?: (stashId: string) => void;
}

// ── styles ─────────────────────────────────────────────────────────────────
// The web UI draws no lines: sections separate by shade and spacing, not borders.

const stashRowVariants = cva(
  "group flex items-center gap-3 px-3 py-1.5 transition-colors cursor-default hover:bg-foreground/5",
  {
    variants: {
      loading: {
        true: "opacity-60 pointer-events-none",
        false: "",
      },
    },
  }
);

const conflictBadgeVariants = cva(
  "inline-flex items-center gap-1 rounded-sm px-2 py-1 text-[10px] font-medium",
  {
    variants: {
      severity: {
        none: "bg-highlight/15 text-highlight",
        conflict: "bg-destructive/15 text-destructive",
      },
    },
    defaultVariants: {
      severity: "none",
    },
  }
);

const FOCUS_RING =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-foreground/50";
const OVERLAY = "fixed inset-0 z-50 flex items-center justify-center bg-background/80";
const DIALOG = "mx-4 w-96 max-w-full rounded-md bg-card shadow-lg";
const DIALOG_FOOTER = "flex items-center justify-end gap-2 bg-muted/30 px-4 py-3";
const GHOST_BUTTON =
  "rounded px-3 py-1.5 text-[10px] font-medium text-foreground/60 transition-colors hover:bg-foreground/5 hover:text-foreground/90 disabled:cursor-not-allowed disabled:opacity-50";
const ERROR_NOTICE = "rounded bg-destructive/15 px-3 py-2 text-[10px] text-destructive";

// ── focus trap hook ────────────────────────────────────────────────────────

function useFocusTrap(
  containerRef: RefObject<HTMLDivElement | null>,
  active: boolean,
  onEscape?: () => void
) {
  // The parent passes a new arrow on every render. Keeping it in a ref stops this effect
  // from re-running per render, which would pull focus back to the first control on each keystroke.
  const onEscapeRef = useRef(onEscape);
  useEffect(() => {
    onEscapeRef.current = onEscape;
  }, [onEscape]);

  useEffect(() => {
    if (!active || !containerRef.current) return;

    const container = containerRef.current;
    const previouslyFocused = document.activeElement as HTMLElement | null;

    // Focus the first focusable element
    const focusableSelectors =
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const firstFocusable = container.querySelector<HTMLElement>(focusableSelectors);
    firstFocusable?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onEscapeRef.current?.();
        return;
      }
      if (e.key !== "Tab") return;

      const focusables = Array.from(
        container.querySelectorAll<HTMLElement>(focusableSelectors)
      ).filter((el) => !el.hasAttribute("disabled"));

      if (focusables.length === 0) return;

      const first = focusables[0];
      const last = focusables[focusables.length - 1];

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [active, containerRef]);
}

// ── api helper ─────────────────────────────────────────────────────────────

/** The `error.message` of a `{ success: false, error }` envelope, when it has one. */
function envelopeMessage(body: unknown): string | null {
  if (!body || typeof body !== "object" || !("error" in body)) return null;
  const error = body.error;
  if (!error || typeof error !== "object" || !("message" in error)) return null;
  return typeof error.message === "string" ? error.message : null;
}

/** POST one git action for the folder. Resolves to the envelope's `data`; rejects with its message. */
async function gitPost<T>(
  workspace: string,
  sessionId: string,
  action: string,
  payload?: Record<string, unknown>
): Promise<T> {
  const res = await codeFetch("/api/git", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace, action, ...payload }),
  }, { sessionId, root: workspace });

  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(envelopeMessage(body) ?? `HTTP ${res.status}: git error`);
  }
  return unwrapCodeData<T>(body);
}

// ── stash row ──────────────────────────────────────────────────────────────

function StashRow({
  stash,
  loading,
  onApply,
  onDrop,
}: {
  stash: GitStash;
  loading?: boolean;
  onApply: (stashId: string) => void;
  onDrop: (stashId: string) => void;
}) {
  const stashLabel = stash.message || `stash@{${stash.id}}`;

  return (
    <div
      role="listitem"
      className={stashRowVariants({ loading })}
    >
      {/* Stash ID */}
      <span className="w-20 shrink-0 font-mono text-[9px] text-foreground/40" aria-hidden="true">
        {`stash@{${stash.id}}`}
      </span>

      {/* Branch */}
      <span className="w-24 shrink-0 truncate text-[10px] text-muted-foreground" title={stash.branch}>
        {stash.branch}
      </span>

      {/* Message */}
      <span className="flex-1 truncate text-[10px] text-foreground/70" title={stash.message}>
        {stash.message}
      </span>

      {/* Date */}
      <span className="w-20 shrink-0 text-right text-[10px] text-foreground/25">
        {stash.date}
      </span>

      {/* Actions — always in DOM; visible on hover or focus-within for keyboard users */}
      <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
        <button
          type="button"
          onClick={() => onApply(stash.id)}
          aria-label={`Apply ${stashLabel}`}
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded text-foreground/25 transition-colors hover:bg-foreground/5 hover:text-highlight",
            FOCUS_RING
          )}
        >
          <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => onDrop(stash.id)}
          disabled
          title="Stash deletion is unavailable while concurrent Git activity can change stash positions."
          aria-label={`Delete ${stashLabel} unavailable`}
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded text-foreground/25 transition-colors hover:bg-foreground/5 hover:text-destructive",
            FOCUS_RING
          )}
        >
          <Trash2 className="h-3 w-3" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

// ── create stash dialog ────────────────────────────────────────────────────

interface CreateStashDialogProps {
  open: boolean;
  loading: boolean;
  error?: string | null;
  onSubmit: (message: string) => void;
  onOpenChange: (open: boolean) => void;
}

function CreateStashDialog({
  open,
  loading,
  error,
  onSubmit,
  onOpenChange,
}: CreateStashDialogProps) {
  const [message, setMessage] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const maxChars = 200;
  const isValid = message.length <= maxChars;
  const charCountId = "create-stash-char-count";
  const errorId = "create-stash-error";
  const titleId = "create-stash-dialog-title";
  const textareaId = "create-stash-message";

  useFocusTrap(containerRef, open, () => onOpenChange(false));

  const handleSubmit = () => {
    if (!isValid) return;
    onSubmit(message.trim());
    setMessage("");
  };

  if (!open) return null;

  return (
    <div className={OVERLAY}>
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={DIALOG}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 pt-3">
          <h2 id={titleId} className="text-[12px] font-medium text-foreground/90">Create Stash</h2>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="Close create stash dialog"
            className={cn("p-1 text-foreground/40 transition-colors hover:text-foreground/70", FOCUS_RING)}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Content */}
        <div className="space-y-3 p-4">
          <div>
            <label
              htmlFor={textareaId}
              className="text-[10px] font-medium uppercase text-foreground/60"
            >
              Message (optional)
            </label>
            <textarea
              id={textareaId}
              value={message}
              onChange={(e) => setMessage(e.currentTarget.value)}
              placeholder="e.g., Fix login bug"
              disabled={loading}
              aria-describedby={`${charCountId}${error ? ` ${errorId}` : ""}${!isValid ? ` ${charCountId}` : ""}`}
              aria-invalid={!isValid}
              className={cn(
                "mt-2 w-full resize-none rounded bg-foreground/5 px-3 py-2 text-[11px] text-foreground placeholder:text-foreground/30 transition-colors focus:outline-none focus:ring-1 focus:ring-foreground/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-foreground/40",
                loading && "opacity-50"
              )}
              rows={3}
            />
            <div className="mt-1 flex items-center justify-between" aria-live="polite">
              <span id={charCountId} className="text-[9px] text-foreground/40">
                {message.length} / {maxChars}
              </span>
              {!isValid && (
                <span className="text-[9px] text-destructive" role="alert">
                  Max {maxChars} characters
                </span>
              )}
            </div>
          </div>

          {error && (
            <div id={errorId} role="alert" className={ERROR_NOTICE}>
              {error}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className={DIALOG_FOOTER}>
          <button
            type="button"
            onClick={() => {
              onOpenChange(false);
              setMessage("");
            }}
            disabled={loading}
            className={GHOST_BUTTON}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={loading || !isValid}
            aria-disabled={loading || !isValid}
            className={cn(
              "rounded bg-highlight/20 px-3 py-1.5 text-[10px] font-medium text-highlight transition-colors hover:bg-highlight/30 disabled:cursor-not-allowed disabled:opacity-50",
              FOCUS_RING
            )}
          >
            {loading ? "Creating..." : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── apply stash confirm dialog (pre-apply confirmation) ─────────────────────

interface ApplyStashConfirmDialogProps {
  open: boolean;
  stashId: string;
  stash?: GitStash;
  onConfirm: () => void;
  onOpenChange: (open: boolean) => void;
}

function ApplyStashConfirmDialog({
  open,
  stashId,
  stash,
  onConfirm,
  onOpenChange,
}: ApplyStashConfirmDialogProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const titleId = "apply-stash-confirm-title";

  useFocusTrap(containerRef, open, () => onOpenChange(false));

  if (!open) return null;

  const stashLabel = stash?.message || `stash@{${stashId}}`;

  return (
    <div className={OVERLAY}>
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={DIALOG}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 pt-3">
          <h2 id={titleId} className="text-[12px] font-medium text-foreground/90">Apply Stash</h2>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="Close apply stash dialog"
            className={cn("p-1 text-foreground/40 transition-colors hover:text-foreground/70", FOCUS_RING)}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Content */}
        <div className="space-y-3 p-4">
          <p className="text-[11px] text-foreground/70">
            Apply <strong className="font-mono">{stashLabel}</strong> to the working tree?
          </p>
          <div
            role="note"
            className="flex items-start gap-2 rounded bg-amber-500/15 px-3 py-2 text-[10px] text-amber-600"
          >
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>Applying a stash can cause merge conflicts if the working tree has diverged.</span>
          </div>
        </div>

        {/* Actions */}
        <div className={DIALOG_FOOTER}>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className={GHOST_BUTTON}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            autoFocus
            className={cn(
              "rounded bg-highlight/20 px-3 py-1.5 text-[10px] font-medium text-highlight transition-colors hover:bg-highlight/30",
              FOCUS_RING
            )}
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}

// ── apply stash dialog (conflict view) ──────────────────────────────────────

interface ApplyStashDialogProps {
  open: boolean;
  stashId: string;
  stash?: GitStash;
  loading: boolean;
  conflicts?: string[] | null;
  conflictCount: number;
  error?: string | null;
  onOpenChange: (open: boolean) => void;
}

function ApplyStashDialog({
  open,
  stashId,
  stash,
  loading,
  conflicts,
  conflictCount,
  error,
  onOpenChange,
}: ApplyStashDialogProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const titleId = "apply-stash-dialog-title";

  useFocusTrap(containerRef, open, () => onOpenChange(false));

  if (!open) return null;

  const hasConflicts = conflicts && conflicts.length > 0;
  const stashLabel = stash?.message || `stash@{${stashId}}`;

  return (
    <div className={OVERLAY}>
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-busy={loading}
        className={DIALOG}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 pt-3">
          <h2 id={titleId} className="text-[12px] font-medium text-foreground/90">
            {hasConflicts ? "Apply Stash: Conflicts Detected" : "Apply Stash"}
          </h2>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="Close apply stash dialog"
            className={cn("p-1 text-foreground/40 transition-colors hover:text-foreground/70", FOCUS_RING)}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Content */}
        <div className="max-h-96 space-y-4 overflow-y-auto p-4">
          {loading && (
            <div role="status" aria-label={`Applying ${stashLabel}`} className="flex justify-center py-4">
              <Loader2 className="h-4 w-4 animate-spin text-foreground/50" aria-hidden="true" />
            </div>
          )}

          {!loading && hasConflicts ? (
            <>
              <div>
                <div
                  role="alert"
                  className={conflictBadgeVariants({ severity: "conflict" })}
                >
                  <CircleAlert className="h-3 w-3" aria-hidden="true" />
                  {" "}{conflictCount} {conflictCount === 1 ? "file has" : "files have"} merge conflicts
                </div>
              </div>

              <div>
                <p className="mb-2 text-[10px] font-medium uppercase text-foreground/60" id="conflicted-files-label">
                  Conflicted Files
                </p>
                <ul
                  role="list"
                  aria-labelledby="conflicted-files-label"
                  className="space-y-1"
                >
                  {conflicts?.map((file) => (
                    <li
                      key={file}
                      className="flex items-center gap-2 rounded bg-foreground/5 px-3 py-1.5 text-[10px]"
                    >
                      <span className="h-3 w-3 shrink-0 rounded-full bg-destructive" aria-hidden="true" />
                      <span className="flex-1 truncate font-mono text-foreground/70" title={file}>
                        {file}
                      </span>
                      <span className="shrink-0 text-destructive/70">Conflict</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="space-y-1 rounded bg-amber-500/15 px-3 py-2 text-[10px] text-amber-600">
                <p className="font-medium">Resolution Steps:</p>
                <ol className="ml-1 list-inside list-decimal space-y-1">
                  <li>Edit conflicted files and resolve</li>
                  <li>Stage resolved files</li>
                  <li>Commit to complete the merge</li>
                </ol>
              </div>
            </>
          ) : !loading && error ? (
            <div role="alert" className={ERROR_NOTICE}>
              {error}
            </div>
          ) : !loading ? (
            <div role="status" className="flex items-center gap-2 rounded bg-highlight/15 px-3 py-2 text-[10px] text-highlight">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Stash applied successfully</span>
            </div>
          ) : null}
        </div>

        {/* Actions */}
        <div className={DIALOG_FOOTER}>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            disabled={loading}
            className={GHOST_BUTTON}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ── drop stash confirmation dialog ─────────────────────────────────────────

interface DropStashConfirmDialogProps {
  open: boolean;
  stashId: string;
  stash?: GitStash;
  loading: boolean;
  error?: string | null;
  onConfirm: () => void;
  onOpenChange: (open: boolean) => void;
}

function DropStashConfirmDialog({
  open,
  stashId,
  stash,
  loading,
  error,
  onConfirm,
  onOpenChange,
}: DropStashConfirmDialogProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const titleId = "drop-stash-dialog-title";
  const errorId = "drop-stash-error";

  useFocusTrap(containerRef, open, () => onOpenChange(false));

  if (!open) return null;

  const stashLabel = stash?.message || `stash@{${stashId}}`;

  return (
    <div className={OVERLAY}>
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={DIALOG}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 pt-3">
          <h2 id={titleId} className="text-[12px] font-medium text-foreground/90">Delete Stash</h2>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="Close delete stash dialog"
            className={cn("p-1 text-foreground/40 transition-colors hover:text-foreground/70", FOCUS_RING)}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Content */}
        <div className="space-y-4 p-4">
          <p className="text-[11px] text-foreground/70">
            Delete <strong>{stashLabel}</strong>? This action cannot be undone.
          </p>

          {stash && (
            <dl className="space-y-2 rounded bg-foreground/5 px-3 py-2">
              <div className="flex items-center gap-2">
                <dt className="w-16 text-[10px] font-medium text-foreground/50">Stash:</dt>
                <dd className="font-mono text-[10px] text-foreground/70">{`stash@{${stash.id}}`}</dd>
              </div>
              <div className="flex items-center gap-2">
                <dt className="w-16 text-[10px] font-medium text-foreground/50">Message:</dt>
                <dd className="truncate text-[10px] text-foreground/70">{stash.message}</dd>
              </div>
              <div className="flex items-center gap-2">
                <dt className="w-16 text-[10px] font-medium text-foreground/50">Branch:</dt>
                <dd className="text-[10px] text-foreground/70">{stash.branch}</dd>
              </div>
              <div className="flex items-center gap-2">
                <dt className="w-16 text-[10px] font-medium text-foreground/50">Date:</dt>
                <dd className="text-[10px] text-foreground/70">{stash.date}</dd>
              </div>
            </dl>
          )}

          {error && (
            <div
              id={errorId}
              role="alert"
              className={ERROR_NOTICE}
            >
              {error}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className={DIALOG_FOOTER}>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            disabled={loading}
            className={GHOST_BUTTON}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            aria-describedby={error ? errorId : undefined}
            className={cn(
              "rounded bg-destructive/20 px-3 py-1.5 text-[10px] font-medium text-destructive transition-colors hover:bg-destructive/30 disabled:cursor-not-allowed disabled:opacity-50",
              FOCUS_RING
            )}
          >
            {loading ? "Deleting..." : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── main stash panel ───────────────────────────────────────────────────────

export function StashSelector({
  sessionId,
  workspace,
  onStashApplied,
}: StashSelectorProps) {
  // ── state management ──────────────────────────────────────────────────────
  const [stashes, setStashes] = useState<GitStash[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Keep the selected object's immutable identity and label across list refreshes.
  const [applyConfirm, setApplyConfirm] = useState<GitStash | null>(null);
  // Apply results dialog (conflicts / success / error).
  const [showApplyDialog, setShowApplyDialog] = useState<GitStash | null>(null);
  const [applyLoading, setApplyLoading] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applyConflicts, setApplyConflicts] = useState<string[] | null>(null);
  const [conflictCount, setConflictCount] = useState(0);

  const [showDropDialog, setShowDropDialog] = useState<GitStash | null>(null);
  const [dropLoading, setDropLoading] = useState(false);
  const [dropError, setDropError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // True while any mutation is in flight — pauses background polling so we
  // don't clobber the list mid-operation.
  const isMutating = createLoading || applyLoading || dropLoading;

  // ── fetch stashes ─────────────────────────────────────────────────────────
  const fetchStashes = useCallback(async () => {
    try {
      const result = await gitPost<GitStashListResult>(workspace, sessionId, "list_stashes");
      setStashes(result.stashes || []);
      setError(null);
    } catch (e) {
      setError(String(e));
      setStashes([]);
    } finally {
      setLoading(false);
    }
  }, [workspace, sessionId]);

  // ── create stash ──────────────────────────────────────────────────────────
  const handleCreateStash = useCallback(
    async (message: string) => {
      setCreateLoading(true);
      setCreateError(null);
      try {
        const result = await gitPost<GitStashCreateResult>(workspace, sessionId, "create_stash", {
          stashMessage: message,
        });

        if (!result.ok) {
          setCreateError(result.error || "Failed to create stash");
          return;
        }

        setShowCreateDialog(false);
        await fetchStashes();
      } catch (e) {
        setCreateError(String(e));
      } finally {
        setCreateLoading(false);
      }
    },
    [workspace, fetchStashes]
  );

  // ── apply stash ───────────────────────────────────────────────────────────
  // Opens a confirmation dialog first; the actual apply runs only after the
  // user confirms (see runApplyStash).
  const requestApplyStash = useCallback((stash: GitStash) => {
    if (!stash.commitHash || !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/i.test(stash.commitHash)) {
      setError("The stash identity is unavailable. Refresh the list before applying it.");
      return;
    }
    setError(null);
    setApplyConfirm({ ...stash });
  }, []);

  const runApplyStash = useCallback(
    async (stash: GitStash) => {
      // Confirmation done — close confirm dialog, open results dialog.
      setApplyConfirm(null);
      setShowApplyDialog(stash);
      setApplyLoading(true);
      setApplyError(null);
      setApplyConflicts(null);
      setConflictCount(0);

      try {
        const result = await gitPost<GitStashApplyResult>(workspace, sessionId, "apply_stash", {
          stashCommit: stash.commitHash,
        });

        if (!result.ok && result.conflicts && result.conflicts.length > 0) {
          setApplyConflicts(result.conflicts);
          setConflictCount(result.conflictCount || result.conflicts.length);
        } else if (!result.ok) {
          setApplyError(result.error || "Failed to apply stash");
        } else {
          onStashApplied?.(stash.id);
          await fetchStashes();
          setTimeout(() => {
            setShowApplyDialog(null);
          }, 2000);
        }
      } catch (e) {
        setApplyError(String(e));
      } finally {
        setApplyLoading(false);
      }
    },
    [workspace, sessionId, fetchStashes, onStashApplied]
  );

  // ── drop stash ────────────────────────────────────────────────────────────
  const handleDropStash = useCallback(
    async (stash: GitStash) => {
      setDropLoading(true);
      setDropError(null);

      try {
        if (!stash.commitHash || !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/i.test(stash.commitHash)) {
          throw new Error("The stash identity is unavailable. Refresh the list before deleting it.");
        }
        const result = await gitPost<GitStashDropResult>(workspace, sessionId, "drop_stash", {
          stashCommit: stash.commitHash,
        });

        if (!result.ok) {
          setDropError(result.error || "Failed to delete stash");
          return;
        }

        setShowDropDialog(null);
        await fetchStashes();
      } catch (e) {
        setDropError(String(e));
      } finally {
        setDropLoading(false);
      }
    },
    [workspace, sessionId, fetchStashes]
  );

  // ── effects ───────────────────────────────────────────────────────────────
  useEffect(() => {
    fetchStashes();
  }, [fetchStashes]);

  // Background polling — paused while any mutation is in flight so the list
  // isn't refreshed out from under an ongoing create/apply/drop.
  useEffect(() => {
    if (isMutating) return;

    pollRef.current = setInterval(() => {
      fetchStashes();
    }, 5000);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchStashes, isMutating]);

  // ── render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full flex-col overflow-hidden bg-card text-foreground/70">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between bg-muted/30 px-3 py-2">
        <div className="flex items-center gap-2">
          <Archive className="h-4 w-4 text-foreground/50" aria-hidden="true" />
          <span className="text-[11px] font-medium text-foreground/70" aria-live="polite" aria-atomic="true">
            {stashes.length} {stashes.length === 1 ? "stash" : "stashes"}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => fetchStashes()}
            disabled={loading}
            aria-label="Refresh stash list"
            className={cn(
              "rounded p-1 text-foreground/40 transition-colors hover:bg-foreground/5 hover:text-foreground/70 disabled:opacity-50",
              FOCUS_RING
            )}
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => {
              setShowCreateDialog(true);
              setCreateError(null);
            }}
            disabled={loading}
            aria-label="Create new stash"
            className={cn(
              "rounded p-1 text-foreground/40 transition-colors hover:bg-foreground/5 hover:text-highlight disabled:opacity-50",
              FOCUS_RING
            )}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div
            role="status"
            aria-label="Loading stashes"
            className="flex h-32 items-center justify-center"
          >
            <Loader2 className="h-4 w-4 animate-spin text-foreground/50" aria-hidden="true" />
          </div>
        )}

        {error && !loading && (
          <div className="space-y-3 p-4">
            <div role="alert" className={ERROR_NOTICE}>
              {error}
            </div>
            <button
              type="button"
              onClick={() => fetchStashes()}
              className={cn(
                "w-full rounded bg-destructive/20 px-3 py-1.5 text-[10px] font-medium text-destructive transition-colors hover:bg-destructive/30",
                FOCUS_RING
              )}
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !error && stashes.length === 0 && (
          <div
            role="status"
            className="flex h-32 items-center justify-center text-foreground/40"
          >
            <span className="text-[11px]">No stashes yet</span>
          </div>
        )}

        {!loading && !error && stashes.length > 0 && (
          <div role="list" aria-label="Git stashes">
            <p className="px-3 py-2 text-xs text-muted-foreground">Stash deletion is unavailable while concurrent Git activity can change stash positions.</p>
            {stashes.map((stash) => (
              <StashRow
                key={stash.id}
                stash={stash}
                onApply={() => requestApplyStash(stash)}
                onDrop={() => setShowDropDialog({ ...stash })}
              />
            ))}
          </div>
        )}
      </div>

      {/* Dialogs */}
      <CreateStashDialog
        open={showCreateDialog}
        loading={createLoading}
        error={createError}
        onSubmit={handleCreateStash}
        onOpenChange={setShowCreateDialog}
      />

      {applyConfirm && (
        <ApplyStashConfirmDialog
          open={true}
          stashId={applyConfirm.id}
          stash={applyConfirm}
          onConfirm={() => runApplyStash(applyConfirm)}
          onOpenChange={(open) => {
            if (!open) setApplyConfirm(null);
          }}
        />
      )}

      {showApplyDialog && (
        <ApplyStashDialog
          open={true}
          stashId={showApplyDialog.id}
          stash={showApplyDialog}
          loading={applyLoading}
          conflicts={applyConflicts}
          conflictCount={conflictCount}
          error={applyError}
          onOpenChange={(open) => {
            if (!open) {
              setShowApplyDialog(null);
              setApplyConflicts(null);
              setApplyError(null);
            }
          }}
        />
      )}

      {showDropDialog && (
        <DropStashConfirmDialog
          open={true}
          stashId={showDropDialog.id}
          stash={showDropDialog}
          loading={dropLoading}
          error={dropError}
          onConfirm={() => handleDropStash(showDropDialog)}
          onOpenChange={(open) => {
            if (!open) {
              setShowDropDialog(null);
              setDropError(null);
            }
          }}
        />
      )}
    </div>
  );
}
