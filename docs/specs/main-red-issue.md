# Main red CI issue

Issue: #1428
Status: Implemented
Owner: Kollab maintainers

## Problem

When CI fails on `main`, maintainers need a clear record of the failed jobs, the run, the merge that introduced the failure, and the revert command. Without it, the failing merge is difficult to identify and queued work can stall.

## Goal and non-goals

Create one priority issue for each failed CI commit on `main`, and leave one link to that issue on the associated merged PR. The workflow reports the failure; it does not push changes or revert a commit.

## What people see

The issue title is `main CI red at <short SHA> after #<PR number>` when a merged PR is found, or `main CI red at <short SHA>` otherwise. Its body includes the workflow run URL, failed job names, a link to the merged PR when available, and `git revert -m 1 <full SHA>`. The associated PR receives a comment linking to the issue.

## Contract

- `.github/workflows/main-red.yml` listens for completed runs of `CI` and proceeds only when the run failed, its head branch is `main`, and its event was `push` or `workflow_dispatch`.
- The job checks out the repository default branch, with checkout credentials disabled. Its permissions are `contents: read`, `issues: write`, `pull-requests: read`, and `actions: read`.
- `scripts/main_red_issue.py` reads the run, failed jobs, and associated PR; it creates an issue marked with `<!-- main-red:<full SHA> -->` and applies `bug`, `priority/p0`, and `area/ci` labels.
- The marker deduplicates across open and closed issues. For overlapping calls, the lowest numbered matching issue remains open; duplicate issues are closed. Before returning, processing ensures the surviving issue is linked from the PR comment when the comment API is available.
- If no merged PR is found, the issue is still created without a PR link or comment.

## Scenarios

- If a `CI` run fails on `main` from `push`, then one marked priority issue is opened with the failed job names and run URL.
- If that commit has a merged PR, then its issue names and links to the PR, includes the revert command, and the PR receives a link to the issue.
- If no PR is found for the commit, then the issue is still opened with the run, failed jobs, and revert command.
- If the same commit is processed again, including after its issue is closed, then the existing marked issue is reused and no new issue is created.
- If overlapping calls create duplicate issues, then the lowest numbered issue stays open and duplicate issues close; the surviving issue's PR comment is ensured.
- If posting the PR comment fails, then processing fails visibly; a later run retries the comment without creating another issue.
- If the run succeeds, is for another branch, or came from another event, then the job does not open an issue.

## Failure modes

- The run cannot be read: the command exits with the GitHub CLI error and does not create an issue.
- The run has an invalid or missing SHA or URL: validation raises an error before issue creation.
- The jobs API reports no failed job names: the issue says no failed job names were reported and still records the run.
- The associated PR lookup returns no PR: issue creation continues without PR-specific text or comment.
- Issue creation fails: the command exits with the API error; a later workflow attempt can retry.
- Comment lookup or creation fails: the command exits with the API error; the issue marker lets a later attempt retry the missing comment.
- Concurrent processing creates more than one issue: follow-up lookup closes duplicates and uses the lowest-numbered matching issue.

## Decisions

- Issue #1428 defines the user outcome: report the failure and its revert path without allowing the automation to change the branch.
- The merged PR is discovered from the failed commit SHA so an absent PR does not prevent reporting the failure.
- A hidden full-SHA marker is the persistent deduplication key; closed issues remain part of the deduplication search.
- Overlapping issue creation is reconciled by issue number, retaining the earliest issue and closing later duplicates.

## Test plan

- `test_opens_one_issue_and_comments_on_the_related_pr_once` checks the issue content, labels, deduplication, and PR comment.
- `test_opens_issue_without_a_pr_link_when_sha_has_no_pr` checks the no-PR case.
- `test_closed_issue_still_deduplicates_the_same_commit` checks closed-issue deduplication.
- `test_retry_after_a_failed_comment_still_posts_the_pr_comment_once` checks a failed comment retry.
- `test_overlapping_runs_for_one_commit_leave_one_open_issue` and `test_overlapping_duplicate_cleanup_retries_comment_on_surviving_issue` check overlapping calls.
- `test_workflow_runs_only_for_failed_main_ci_and_checks_out_the_default_branch` checks workflow filters, permissions, and checkout ref.
- Tests use a fake GitHub CLI callable; no live repository mutation is part of the test plan.

## Docs and changelog fragment

This spec defines the behavior. The user-facing change is listed in `changes/1428.added.md`.

## Open questions

None.
