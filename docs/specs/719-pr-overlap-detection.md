# Pull request overlap detection

Issue: #719
Status: Implemented
Owner: Kollab repository automation and Koordinator

## Problem

Open pull requests can implement the same issue or overlap in their branch or changed lines. CI passing independently does not detect duplicate intent or conflicting edits, so the duplication can survive until review or merge.

## Goal and non-goals

On pull request open, reopen, and push events, identify likely duplicate or overlapping open pull requests, comment on both, and prompt Koordinator to consolidate or close one with a reason. Before dispatching a lane for an issue, Koordinator checks for an existing open pull request for that issue.

The detector flags candidates only. It does not close, merge, or rewrite pull requests. Existing pairs require a separate coordination decision.

## What people see

Both pull requests receive one comment per pair. It lists the shared issue, branch relationship, or overlapping file/hunk evidence and asks Koordinator to compare intent and consolidate or close one with a reason. Repeated events do not create duplicate comments.

## Contract

- `.github/workflows/pr-overlap.yml` runs from the trusted base branch on `opened`, `reopened`, and `synchronize` pull request events.
- `.github/scripts/pr_overlap_detector.py` compares open PRs in the same repository. It recognizes `Fixes #N`, `Closes #N`, `Resolves #N`, and `Refs #N` in titles and bodies; identical head branches; either direction of a stacked head/base branch; at least two shared changed files; or overlapping old-line hunk ranges in one shared file.
- Pair markers make comments idempotent across retries and either PR event order.
- The action has read access to repository contents and write access only to PR issue comments. It does not execute PR code.
- Koordinator's repository intake instruction checks `gh pr list --state open` and inspects matching PRs before dispatching a new lane for the same issue.

## Scenarios

- If a second PR references the same issue, then both receive a comment naming that issue.
- If two PRs share a head branch or one PR uses the other's head as its base, then both receive a branch-overlap comment.
- If two PRs share two or more changed files, or one shared file has overlapping old-line hunks, then both receive a changed-file overlap comment.
- If PRs only share the ordinary `main` base, or one shared file has disjoint hunks, then no overlap is reported on that evidence alone.
- If the action retries or the opposite PR event runs later, then the pair marker prevents duplicate comments.
- If GitHub returns an API error or malformed data, then the workflow fails visibly and does not claim a clean comparison.
- If no candidate overlaps, then the workflow reports that no overlap was found and posts no comment.

## Failure modes

1. GitHub API access fails: fail the action with the API error; do not publish a no-overlap result.
2. A patch is absent for a binary or oversized file: shared file alone is not enough unless at least two files overlap; issue or branch evidence can still flag the pair.
3. More than one page of open PRs or changed files exists: use `gh api --paginate --slurp` and compare all pages.
4. Two workflow events race: serialize runs per repository and use stable pair markers.
5. A branch name is missing: skip branch comparison and retain issue/file checks.
6. An open PR has no issue reference and disjoint changes: do not flag it based on the common base branch alone.

## Decisions

- A shared base branch alone is excluded because it is the normal shape for unrelated PRs.
- Two shared files or one overlapping patch hunk is the threshold for file-based evidence; issue and branch evidence are independent and sufficient.
- The workflow flags only. Humans and Koordinator decide consolidation or closure; automation does not make destructive PR changes.

## Test plan

- Red-first unit tests exercise linked issue references, same and stacked branches, overlapping hunks, multiple shared files, ordinary shared base branches, disjoint hunks, and stable comment markers.
- Run the workflow by opening or pushing to a test PR in an authorized repository; confirm both comments and retry idempotency. This requires GitHub runtime access and is not performed by the builder.

## Docs and changelog fragment

- Update `docs/features/agents.md` and the repository-coordinator issue intake guidance.
- Add `changes/719.added.md`.

## Open questions

None.
