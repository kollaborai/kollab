# Coordination and continuity

Read when work spans agents, issues, PRs, worktrees, or multiple turns.

## Ownership and checkpoints

For each accepted work item, record its source issue/PR or authorized task, the exact outcome, owner, role/agent actually available, file scope, worktree path and branch, starting SHA, checkpoint cadence, acceptance evidence, and dependencies. A planned assignment is not a claim; an assignee is not an accepted owner until acknowledged. Keep one owner per write path and arrange a handoff before changing ownership.

Delegate independent work only when file/contract boundaries are clear. Give each worker exact paths, source artifacts to inspect, allowed operations, tests/evidence, and a stop condition. Keep source-review ownership separate from implementation. Do not describe a role skill or agent as loaded or a review as passed without session or artifact evidence.

Honor the requested model and reasoning effort. Verify the running session's actual model from available metadata before agent routing or model-sensitive review; if unavailable, disclose that rather than substituting silently. Keep each checkpoint short and evidence-based: complete, in progress, or blocked with the concrete reason and next owner/action.

## Safe continuation

Before risky branch, merge, or cleanup operations, record local/remote refs and privately preserve unique commits plus staged, unstaged, and untracked bytes. Verify the refs, binary patches/copies, and hashes before continuing. Never overwrite concurrent work or use broad rollback commands. Existing review worktrees and branches remain until an owner explicitly authorizes exact cleanup targets.

For direct task handoffs, include a stable task ID where the task ledger provides one, the exact worktree/path, starting commit, file ownership, authorization, and acceptance check. When resuming, compare the reported commit and dirty-file state with the actual checkout; a worker's summary is evidence to inspect, not verification. Keep incomplete work pending and unverified rather than rewriting its state as done.

## Evidence and communication

Maintain a dated, bounded evidence ledger. Distinguish issue/board state, source review, checks, runtime acceptance, and user authorization. Recompute counts from complete paginated source data, record the query and `as of` time, and refresh after material changes or before writes—not indefinitely. Keep machine-readable state, rendered summaries, and exports consistent; link each material conclusion to the source record or diff.

Send progress updates at useful checkpoints: what changed, what was verified, the next gate, and any blocker. Public comments, review submissions, mentions, emails, and other external messages stay as drafts unless the user explicitly authorizes sending. Never put secrets or private security details in public artifacts.
