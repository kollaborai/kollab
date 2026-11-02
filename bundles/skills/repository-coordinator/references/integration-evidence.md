# Integration evidence

Read this reference for overlapping PRs, merge-wave decisions, or an approval-ready report. Keep evidence proportional to the risk; do not create a new process or test suite solely to fill a template.

## Per-PR evidence row

Capture these fields from live GitHub and the exact checked-out refs:

- PR URL, state/draft, author, base branch/SHA, head branch/SHA, and observation time.
- Owner, worktree path, and claimed file scope for active writers; note staged, dirty, and untracked files that affect the candidate.
- Changed paths grouped by code, tests, docs, configuration, metadata, and generated output. Link the exact diff or source hunk that supports each material claim.
- Independent source-review decision and reviewer; exact-head check names/results; unresolved review threads; live acceptance evidence when required.
- Dependencies, overlaps, proposed wave/order, and the concrete authorization covering the action. Record a blocker only when evidence identifies one.

A PR's branch ancestry can show that a commit is reachable, but it does not prove that the intended code, test, or metadata is present in the current PR diff or merge candidate. Confirm content through the tree/diff and, where useful, blob identity. Do not infer containment from timestamps, titles, labels, or file lists alone.

Before risky integration work, record refs and preserve unique commit objects in a private bundle or dedicated refs. Preserve staged and unstaged tracked changes separately; copy untracked files with their exact bytes and record hashes. Verify the saved refs, patches, copies, and hashes before proceeding. Do not stage another owner's work merely to preserve it; if exact preservation is uncertain, stop and request an owner decision.

## Hunk and behavior matrix

For each pair that touches the same path or behavior, record:

| Question | Evidence to capture |
| --- | --- |
| What does each PR change? | Exact old/new hunk or data contract and its source/test paths. |
| Is one change contained or replaced? | Current-base diff plus the specific matching hunk/blob in the other PR. Explain what no longer needs to land separately. |
| Are the changes complementary? | The invariant each contributes and the combined behavior that depends on both. Keep both regression tests and associated metadata. |
| What order is valid? | Dependency, branch/base relationship, or source interaction that requires the order. If none, say independent. |
| What still needs proof? | Combined positive and negative cases, exact CI, runtime/acceptance, or unresolved review. |

Do not call two PRs duplicates just because they share a file. Do not call them compatible just because their hunks do not overlap: inspect the shared behavior and the tests' coverage.

## Candidate and test provenance

- Record the current PR head tree separately from the prospective tree after combining the planned PRs. `git merge-tree` or a conflict-free merge reports textual compatibility only; it does not establish runtime behavior.
- Run relevant regressions against the combined candidate when interactions matter. Include a positive case and a negative/denial/failure case where the contract has both. Confirm the test command is running from the intended worktree and that imports resolve to its source, not an installed package, main checkout, or other worktree. Record the resolved module path when source shadowing is plausible.
- If no safe combined candidate or runtime test is available, state the exact missing proof and keep the merge recommendation conditional. Do not mutate the shared `main` source to construct a candidate.

## Changed head or base

A new head or base creates a new gate snapshot. Re-fetch the current base and head, recompute the PR diff, and refresh configured required checks and review-thread state. Compare old/new file lists and blob IDs to distinguish unchanged feature content from changed code, tests, or metadata. Unchanged blobs can support a scoped source re-review; they do not transfer CI results to the new commit. Never label a previous head's green checks as current-head checks.

## Merge-stage ledger and final proof

Use separate stage entries such as `observed`, `source reviewed`, `combined candidate checked`, `authorized`, `merged`, and `main synchronized`. Each entry needs an actual timestamp and evidence pointer; a tracker state alone cannot advance the stage.

After a merge, record the host's merge commit and verify the local integration checkout can fast-forward from `origin/main`. After the fast-forward, compare local and remote main SHAs and inspect status. Recheck hashes for pre-existing untracked files that matter to preservation. Report blockers and unverified live acceptance plainly. Do not infer a clean final state from labels, a successful merge button, or a clean merge tree.
