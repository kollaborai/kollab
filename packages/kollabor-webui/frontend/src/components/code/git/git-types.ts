/**
 * Shapes of the engine's /code/git responses. They mirror mentiko's app/api/git/route.ts, so
 * mentiko's callers keep their field names. The git panel and the branch and stash selectors read these.
 */

/** One path from `git status --porcelain`. */
export interface GitStatusEntry {
  path: string;
  name: string;
  /** Has staged changes. */
  staged: boolean;
  /** Has unstaged changes. */
  unstaged: boolean;
  /** New file not yet added. */
  untracked: boolean;
  /** Raw XY from `git status --porcelain`. */
  statusCode: string;
}

export interface GitStatusResult {
  branch: string;
  upstream: string | null;
  ahead: number;
  behind: number;
  files: GitStatusEntry[];
}

/** Reply to stage, unstage, stage_all, unstage_all, commit and push. */
export interface GitStatusMutation {
  ok: boolean;
  status?: GitStatusResult;
}

export interface GitLogEntry {
  hash: string;
  shortHash: string;
  message: string;
  author: string;
  date: string;
  refs: string;
}

export interface GitBranch {
  /** e.g. "feature/new-ui" or "origin/main". */
  name: string;
  /** Currently checked out. */
  isCurrent: boolean;
  /** A remote branch (name contains a remote). */
  isRemote: boolean;
  /** Remote branch this local branch tracks. */
  tracking?: string;
  /** Short hash of the last commit. */
  lastCommit?: string;
  /** Human-readable date, e.g. "2 days ago". */
  lastCommitDate?: string;
}

export interface GitBranchListResult {
  branches: GitBranch[];
  /** Name of the checked-out branch. */
  current: string;
  /** The repository's default branch. */
  defaultBranch?: string;
}

export interface GitBranchCreateResult {
  ok: boolean;
  branch?: string;
  error?: string;
  current?: string;
}

export interface GitBranchSwitchResult {
  ok: boolean;
  current?: string;
  previous?: string;
  error?: string;
  /** Set when the switch happened with uncommitted changes. */
  hasUncommittedChanges?: boolean;
}

export interface GitBranchDeleteResult {
  ok: boolean;
  deleted?: string;
  error?: string;
  /** True when `git branch -D` (force delete) was used. */
  forceUsed?: boolean;
}

export interface GitBranchCurrentResult {
  ok: boolean;
  current?: string;
  error?: string;
}

export interface GitStash {
  /** "0", "1", ... or the full "stash@{0}". */
  id: string;
  /** Branch the stash was created on. */
  branch: string;
  /** User message, or the default "WIP on <branch>: <hash>". */
  message: string;
  /** ISO timestamp or a relative date. */
  date: string;
  /** Full object ID of the stashed commit. */
  commitHash?: string;
}

export interface GitStashListResult {
  ok: boolean;
  /** Newest first. */
  stashes: GitStash[];
  error?: string;
}

export interface GitStashCreateResult {
  ok: boolean;
  stashId?: string;
  message?: string;
  error?: string;
}

export interface GitStashApplyResult {
  ok: boolean;
  appliedStashId?: string;
  /** Repo-relative paths with merge conflicts. */
  conflicts?: string[];
  conflictCount?: number;
  /** True when conflicts remain after the apply. */
  hasUnmergedPaths?: boolean;
  error?: string;
  /** Working-directory status after the apply. */
  status?: GitStatusResult;
}

export interface GitStashDropResult {
  ok: boolean;
  droppedId?: string;
  error?: string;
}

export interface GitStashShowResult {
  ok: boolean;
  /** Unified diff; may be empty. */
  diff: string;
  stashId?: string;
  error?: string;
}
